/**
 * Outbound HTTP call detection for JavaScript / TypeScript (Node.js, NestJS, Angular, React).
 *
 * Emits `EXTERNAL_API` nodes (id `extapi:<VERB> <url>`) and `CALLS_EXTERNAL` edges so the
 * knowledge graph records which code calls which remote HTTP endpoint. The node id format
 * matches the Java indexer so both languages converge on the same nodes in a shared KuzuDB.
 *
 * Purely syntactic: clients are recognised from imports / require() / TS type annotations,
 * URLs are resolved from literals, templates, constants (including cross-file imports) and
 * `.env*` files. Unknown parts become `{name}` placeholders, unresolved env vars `${NAME}`.
 */

const fs = require('fs');
const path = require('path');

const HTTP_VERBS = new Set(['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']);
const VERB_METHODS = new Set(['get', 'post', 'put', 'delete', 'patch', 'head', 'options']);

// module specifier -> client family
const LIB_BY_MODULE = {
    axios: 'axios',
    got: 'got',
    ky: 'ky',
    'ky-universal': 'ky',
    superagent: 'superagent',
    needle: 'needle',
    request: 'request',
    'request-promise': 'request',
    'request-promise-native': 'request',
    undici: 'undici',
    ofetch: 'fetch',
    'node-fetch': 'fetch',
    'cross-fetch': 'fetch',
    'isomorphic-fetch': 'fetch',
    'whatwg-fetch': 'fetch',
    http: 'http',
    'node:http': 'http',
    https: 'https',
    'node:https': 'https',
};

const CLIENT_LABEL = {
    axios: 'axios',
    got: 'got',
    ky: 'ky',
    superagent: 'superagent',
    needle: 'needle',
    request: 'request',
    undici: 'undici',
    fetch: 'fetch',
    http: 'node http',
    https: 'node https',
    'nest-http': 'NestJS HttpService',
    'angular-http': 'Angular HttpClient',
    xhr: 'XMLHttpRequest',
};

// TS types / class names that denote an injectable HTTP client
const TYPED_CLIENTS = {
    HttpService: 'nest-http',
    HttpClient: 'angular-http',
};

const ENV_FILE = /^\.env(\..+)?$/;
const ENV_SKIP_DIRS = new Set(['node_modules', 'dist', 'build', 'target', 'coverage', 'vendor', '.git']);
const TEST_PATH = /(^|\/)(__tests__|__mocks__|tests?|specs?|e2e|cypress)\/|\.(test|spec|stories)\.[cm]?[jt]sx?$/i;
const MAX_CONST_DEPTH = 6;
// Families whose verb helpers take (url, data, config) for post/put/patch; others take (url, options).
const DATA_ARG_FAMILIES = new Set(['axios', 'nest-http', 'angular-http']);
const BODY_VERBS = new Set(['post', 'put', 'patch']);
const NON_FIELD_MEMBERS = new Set(['map', 'forEach', 'filter', 'find', 'reduce', 'some', 'every', 'length', 'push', 'join', 'slice', 'concat', 'includes', 'sort', 'then', 'catch', 'toString']);
const MAX_SHAPE_FIELDS = 25;

const TOKEN_RE = /\u0001([^\u0001]+)\u0001/g;

/** Load URL-valued entries from `.env*` files. Non-URL values are ignored so secrets never leak. */
function loadEnvUrls(root) {
    const urls = {};
    const explicit = [];
    const walk = (dir, depth) => {
        let entries;
        try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return; }
        for (const entry of entries) {
            if (entry.isDirectory()) {
                if (depth < 2 && !ENV_SKIP_DIRS.has(entry.name) && !entry.name.startsWith('.')) {
                    walk(path.join(dir, entry.name), depth + 1);
                }
            } else if (entry.isFile() && ENV_FILE.test(entry.name)) {
                explicit.push(path.join(dir, entry.name));
            }
        }
    };
    walk(root, 0);
    // plain `.env` wins over `.env.example` / `.env.<profile>`
    explicit.sort((a, b) => path.basename(a).length - path.basename(b).length);
    for (const file of explicit.reverse()) {
        let text;
        try { text = fs.readFileSync(file, 'utf-8'); } catch (e) { continue; }
        for (const rawLine of text.split(/\r?\n/)) {
            const line = rawLine.trim();
            if (!line || line.startsWith('#')) continue;
            const m = line.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
            if (!m) continue;
            let value = m[2].trim().replace(/^['"]|['"]$/g, '');
            if (/^https?:\/\//i.test(value) && !/^https?:\/\/[^/@]*:[^/@]*@/.test(value)) {
                urls[m[1]] = value;
            }
        }
    }
    return urls;
}

function hostOf(url) {
    if (!url) return '(unknown)';
    const scheme = url.indexOf('://');
    if (scheme >= 0) {
        const rest = url.slice(scheme + 3);
        const m = rest.match(/^[^/?#]*/);
        const host = m ? m[0] : rest;
        const at = host.lastIndexOf('@');
        return at >= 0 ? host.slice(at + 1) : host;
    }
    if (url.startsWith('${') || url.startsWith('{')) {
        const close = url.indexOf('}');
        if (close > 0) return url.slice(0, close + 1);
    }
    return url.startsWith('/') ? '(relative)' : '(unknown)';
}

function joinUrl(base, p) {
    if (!base) return p;
    if (!p) return base;
    const baseSlash = base.endsWith('/');
    const pathSlash = p.startsWith('/');
    if (baseSlash && pathSlash) return base + p.slice(1);
    if (!baseSlash && !pathSlash) return `${base}/${p}`;
    return base + p;
}

function isAbsoluteUrl(url) {
    return !!url && (url.includes('://') || url.startsWith('${') || url.startsWith('{'));
}

/** A URL that is only a placeholder (e.g. `{url}`) carries no information and is skipped. */
function isBarePlaceholder(url) {
    return /^\$?\{[^}]*\}$/.test(url);
}

class OutboundDetector {
    /**
     * @param {object} graph     CodeGraphBuilder
     * @param {object} envUrls   result of loadEnvUrls()
     */
    constructor(graph, envUrls) {
        this.graph = graph;
        this.envUrls = envUrls || {};
        // `<moduleName>.<exportName>` -> string with unresolved import tokens
        this.projectConsts = new Map();
        // calls waiting for cross-file constants: { caller, verb, url, client, line, confidence, language }
        this.pending = [];
        this.callCount = 0;
    }

    /** Prepare per-file lookup state. Must be called before `detect` for the file. */
    beginFile(ast, state, resolveModule) {
        const ctx = {
            state,
            resolveModule,
            skip: TEST_PATH.test(state.filePath),
            imports: new Map(),    // local identifier -> { source, importedName, isRelative }
            libs: new Map(),       // local identifier -> client family
            consts: new Map(),     // identifier / this.x / obj.prop -> init AST node
            instances: new Map(),  // identifier / this.x -> { kind, baseUrl }
            xhr: new Set(),
        };
        this.collect(ast, ctx);
        this.registerExports(ast, ctx);
        return ctx;
    }

    // ── Pre-pass ───────────────────────────────────────────────────────────

    collect(root, ctx) {
        const visit = (node) => {
            if (!node || typeof node.type !== 'string') return;
            switch (node.type) {
                case 'ImportDeclaration': this.collectImport(node, ctx); break;
                case 'VariableDeclarator': this.collectDeclarator(node, ctx); break;
                case 'AssignmentExpression': this.collectAssignment(node, ctx); break;
                case 'ClassProperty':
                case 'ClassPrivateProperty': this.collectClassProperty(node, ctx); break;
                case 'TSParameterProperty': this.collectParamProperty(node, ctx); break;
                case 'Identifier':
                    if (node.typeAnnotation) this.collectTypedName(node.name, node.typeAnnotation, ctx);
                    break;
                default: break;
            }
            for (const key of Object.keys(node)) {
                if (key === '_parent' || key === 'loc' || key === 'leadingComments' || key === 'trailingComments') continue;
                const child = node[key];
                if (Array.isArray(child)) {
                    for (const item of child) if (item && typeof item.type === 'string') visit(item);
                } else if (child && typeof child.type === 'string') {
                    visit(child);
                }
            }
        };
        visit(root);
    }

    collectImport(node, ctx) {
        const source = node.source.value;
        const isRelative = source.startsWith('.') || source.startsWith('/');
        for (const spec of node.specifiers) {
            const importedName = spec.type === 'ImportDefaultSpecifier' ? 'default'
                : spec.type === 'ImportNamespaceSpecifier' ? '*'
                    : (spec.imported && (spec.imported.name || spec.imported.value)) || spec.local.name;
            ctx.imports.set(spec.local.name, { source, importedName, isRelative });
        }
        const family = LIB_BY_MODULE[source];
        if (!family) return;
        for (const spec of node.specifiers) {
            if (spec.type === 'ImportDefaultSpecifier' || spec.type === 'ImportNamespaceSpecifier') {
                ctx.libs.set(spec.local.name, family);
            } else if (spec.type === 'ImportSpecifier') {
                const imported = spec.imported.name || spec.imported.value;
                // import { get } from 'axios' etc. are rare; only handle fetch-like named exports
                if (family === 'fetch' || (family === 'undici' && ['fetch', 'request'].includes(imported))) {
                    ctx.libs.set(spec.local.name, family === 'undici' && imported === 'request' ? 'undici-request' : 'fetch');
                } else if (family === 'axios' && imported === 'default') {
                    ctx.libs.set(spec.local.name, family);
                }
            }
        }
    }

    requireFamily(init) {
        if (!init) return null;
        let call = init;
        if (call.type === 'AwaitExpression') call = call.argument;
        if (call && call.type === 'MemberExpression') call = call.object; // require('x').default
        if (!call || call.type !== 'CallExpression') return null;
        if (call.callee.type === 'Identifier' && call.callee.name === 'require' &&
            call.arguments[0] && call.arguments[0].type === 'StringLiteral') {
            return LIB_BY_MODULE[call.arguments[0].value] || null;
        }
        return null;
    }

    keyOf(node) {
        if (!node) return null;
        if (node.type === 'Identifier') return node.name;
        if (node.type === 'MemberExpression' && !node.computed && node.object.type === 'ThisExpression' &&
            node.property.type === 'Identifier') return `this.${node.property.name}`;
        return null;
    }

    clientKindOf(init, ctx) {
        init = this.unwrap(init);
        if (!init) return null;
        if (init.type === 'CallExpression' && init.callee.type === 'MemberExpression' &&
            init.callee.object.type === 'Identifier' && !init.callee.computed) {
            const family = ctx.libs.get(init.callee.object.name);
            const method = init.callee.property.name;
            if (family === 'axios' && method === 'create') {
                return { kind: 'axios', baseUrl: this.objectString(init.arguments[0], ['baseURL', 'baseUrl'], ctx) };
            }
            if (family === 'got' && method === 'extend') {
                return { kind: 'got', baseUrl: this.objectString(init.arguments[0], ['prefixUrl', 'baseUrl'], ctx) };
            }
            if (family === 'ky' && (method === 'create' || method === 'extend')) {
                return { kind: 'ky', baseUrl: this.objectString(init.arguments[0], ['prefixUrl', 'baseUrl'], ctx) };
            }
        }
        if (init.type === 'NewExpression') {
            if (init.callee.type === 'Identifier' && init.callee.name === 'XMLHttpRequest') return { kind: 'xhr', baseUrl: null };
            if (init.callee.type === 'Identifier') {
                const family = ctx.libs.get(init.callee.name);
                if (family === 'axios') {
                    return { kind: 'axios', baseUrl: this.objectString(init.arguments[0], ['baseURL', 'baseUrl'], ctx) };
                }
            }
        }
        return null;
    }

    collectDeclarator(node, ctx) {
        if (node.id.type === 'Identifier') {
            const family = this.requireFamily(node.init);
            if (family) ctx.libs.set(node.id.name, family);
            const inst = this.clientKindOf(node.init, ctx);
            if (inst) {
                ctx.instances.set(node.id.name, inst);
                if (inst.kind === 'xhr') ctx.xhr.add(node.id.name);
            }
            if (node.init && this.isStringish(node.init)) ctx.consts.set(node.id.name, node.init);
            if (node.init && node.init.type === 'ObjectExpression') this.collectObjectConsts(node.id.name, node.init, ctx);
            if (node.id.typeAnnotation) this.collectTypedName(node.id.name, node.id.typeAnnotation, ctx);
        } else if (node.id.type === 'ObjectPattern') {
            const family = this.requireFamily(node.init);
            if (family === 'fetch') {
                for (const prop of node.id.properties) {
                    if (prop.type === 'ObjectProperty' && prop.value.type === 'Identifier') ctx.libs.set(prop.value.name, 'fetch');
                }
            }
        }
    }

    collectAssignment(node, ctx) {
        if (node.operator !== '=') return;
        const key = this.keyOf(node.left);
        if (!key) return;
        const inst = this.clientKindOf(node.right, ctx);
        if (inst) ctx.instances.set(key, inst);
        if (this.isStringish(node.right)) ctx.consts.set(key, node.right);
        else if (node.right.type === 'ObjectExpression') this.collectObjectConsts(key, node.right, ctx);
    }

    collectClassProperty(node, ctx) {
        if (!node.key || node.key.type !== 'Identifier' || node.computed) return;
        const key = `this.${node.key.name}`;
        if (node.value) {
            const inst = this.clientKindOf(node.value, ctx);
            if (inst) ctx.instances.set(key, inst);
            if (this.isStringish(node.value)) ctx.consts.set(key, node.value);
        }
        if (node.typeAnnotation) this.collectTypedName(key, node.typeAnnotation, ctx);
    }

    collectParamProperty(node, ctx) {
        const param = node.parameter && node.parameter.type === 'AssignmentPattern' ? node.parameter.left : node.parameter;
        if (!param || param.type !== 'Identifier' || !param.typeAnnotation) return;
        this.collectTypedName(`this.${param.name}`, param.typeAnnotation, ctx);
    }

    collectTypedName(name, annotation, ctx) {
        const t = annotation.typeAnnotation || annotation;
        if (t.type !== 'TSTypeReference') return;
        const typeName = t.typeName && (t.typeName.name || (t.typeName.right && t.typeName.right.name));
        const kind = TYPED_CLIENTS[typeName];
        if (kind) ctx.instances.set(name, { kind, baseUrl: null });
    }

    collectObjectConsts(prefix, objectNode, ctx) {
        for (const prop of objectNode.properties) {
            if (prop.type !== 'ObjectProperty' || prop.computed) continue;
            const name = prop.key.name || prop.key.value;
            if (!name) continue;
            if (this.isStringish(prop.value)) ctx.consts.set(`${prefix}.${name}`, prop.value);
        }
    }

    isStringish(node) {
        node = this.unwrap(node);
        if (!node) return false;
        switch (node.type) {
            case 'StringLiteral':
            case 'TemplateLiteral':
                return true;
            case 'BinaryExpression': return node.operator === '+';
            case 'LogicalExpression': return this.isStringish(node.left) || this.isStringish(node.right) || this.isEnvAccess(node.left) !== null;
            case 'MemberExpression': return this.isEnvAccess(node) !== null;
            case 'CallExpression': return this.configKey(node) !== null;
            case 'NewExpression': return node.callee.type === 'Identifier' && node.callee.name === 'URL';
            case 'ConditionalExpression': return this.isStringish(node.consequent);
            default: return false;
        }
    }

    // ── Cross-file constants ───────────────────────────────────────────────

    /** Register top-level constants/exports of this file so other files can resolve imports of them. */
    registerExports(ast, ctx) {
        const mod = ctx.state.moduleName;
        // `import './config'` resolves to the module name `config`, which a `config/index.ts` also answers to
        const aliases = mod.endsWith('.index') ? [mod, mod.slice(0, -'.index'.length)] : [mod];
        const reg = (name, node) => {
            if (!this.isStringish(node)) return;
            const value = this.resolveUrl(node, ctx, 0);
            for (const alias of aliases) this.projectConsts.set(`${alias}.${name}`, value);
        };
        const regObject = (name, obj) => {
            for (const prop of obj.properties) {
                if (prop.type !== 'ObjectProperty' || prop.computed) continue;
                const key = prop.key.name || prop.key.value;
                if (key) reg(name ? `${name}.${key}` : key, prop.value);
            }
        };
        for (const stmt of ast.program.body) {
            let decl = stmt;
            if (stmt.type === 'ExportNamedDeclaration' && stmt.declaration) decl = stmt.declaration;
            if (decl.type === 'VariableDeclaration') {
                for (const d of decl.declarations) {
                    if (d.id.type !== 'Identifier' || !d.init) continue;
                    reg(d.id.name, d.init);
                    if (d.init.type === 'ObjectExpression') regObject(d.id.name, d.init);
                }
            } else if (stmt.type === 'ExportDefaultDeclaration') {
                const d = stmt.declaration;
                if (d.type === 'ObjectExpression') regObject('default', d);
                else if (d.type === 'Identifier') {
                    for (const [key, node] of ctx.consts) {
                        if (key.startsWith(`${d.name}.`)) reg(`default.${key.slice(d.name.length + 1)}`, node);
                    }
                }
            } else if (stmt.type === 'ExpressionStatement' && stmt.expression.type === 'AssignmentExpression') {
                const left = stmt.expression.left;
                const isModuleExports = left.type === 'MemberExpression' && left.object.type === 'Identifier' &&
                    left.object.name === 'module' && left.property.name === 'exports';
                if (isModuleExports && stmt.expression.right.type === 'ObjectExpression') {
                    regObject('default', stmt.expression.right);
                    regObject('', stmt.expression.right);
                }
            }
        }
    }

    // ── URL resolution ─────────────────────────────────────────────────────

    unwrap(node) {
        while (node && (node.type === 'TSAsExpression' || node.type === 'TSNonNullExpression' ||
            node.type === 'ParenthesizedExpression' || node.type === 'TSTypeAssertion' || node.type === 'TSSatisfiesExpression')) {
            node = node.expression;
        }
        return node;
    }

    /** Returns the env var name for `process.env.X` / `import.meta.env.X` / `process.env['X']`, else null. */
    isEnvAccess(node) {
        node = this.unwrap(node);
        if (!node || node.type !== 'MemberExpression') return null;
        const obj = node.object;
        const isEnvObject = obj.type === 'MemberExpression' && !obj.computed && obj.property.name === 'env' &&
            ((obj.object.type === 'Identifier' && obj.object.name === 'process') ||
                (obj.object.type === 'MetaProperty') ||
                (obj.object.type === 'MemberExpression' && obj.object.property.name === 'meta'));
        if (!isEnvObject) return null;
        if (!node.computed && node.property.type === 'Identifier') return node.property.name;
        if (node.computed && node.property.type === 'StringLiteral') return node.property.value;
        return null;
    }

    /** `configService.get('X')` / `config.get('x')` style lookups -> key, else null. */
    configKey(node) {
        if (node.type !== 'CallExpression' || node.callee.type !== 'MemberExpression' || node.callee.computed) return null;
        const method = node.callee.property.name;
        if (!['get', 'getOrThrow'].includes(method)) return null;
        const objText = this.simpleName(node.callee.object);
        if (!/config|env|settings/i.test(objText)) return null;
        const arg = node.arguments[0];
        return arg && arg.type === 'StringLiteral' ? arg.value : null;
    }

    simpleName(node) {
        node = this.unwrap(node);
        if (!node) return 'dynamic';
        switch (node.type) {
            case 'Identifier': return node.name;
            case 'ThisExpression': return 'this';
            case 'MemberExpression':
                if (!node.computed && node.property.type === 'Identifier') return `${this.simpleName(node.object)}.${node.property.name}`;
                return this.simpleName(node.object);
            case 'CallExpression': return this.simpleName(node.callee);
            case 'OptionalMemberExpression':
                return `${this.simpleName(node.object)}.${node.property.name || 'dynamic'}`;
            default: return 'dynamic';
        }
    }

    envValue(name, fallback) {
        if (this.envUrls[name]) return this.envUrls[name];
        if (fallback != null) return fallback;
        return `\${${name}}`;
    }

    /** Resolve an expression to a URL-ish string. Unknown parts become `{name}` placeholders. */
    resolveUrl(node, ctx, depth) {
        node = this.unwrap(node);
        if (!node) return '{dynamic}';
        if (depth > MAX_CONST_DEPTH) return `{${this.simpleName(node)}}`;
        switch (node.type) {
            case 'StringLiteral': return node.value;
            case 'TemplateLiteral': {
                let out = '';
                node.quasis.forEach((q, i) => {
                    out += q.value.cooked != null ? q.value.cooked : q.value.raw;
                    if (i < node.expressions.length) out += this.resolveUrl(node.expressions[i], ctx, depth + 1);
                });
                return out;
            }
            case 'BinaryExpression':
                if (node.operator === '+') return this.resolveUrl(node.left, ctx, depth + 1) + this.resolveUrl(node.right, ctx, depth + 1);
                return `{${this.simpleName(node)}}`;
            case 'LogicalExpression': {
                const envName = this.isEnvAccess(node.left);
                if (envName) {
                    const right = this.unwrap(node.right);
                    const fallback = right && right.type === 'StringLiteral' ? right.value : null;
                    return this.envValue(envName, fallback);
                }
                const left = this.resolveUrl(node.left, ctx, depth + 1);
                return isBarePlaceholder(left) ? this.resolveUrl(node.right, ctx, depth + 1) : left;
            }
            case 'ConditionalExpression': return this.resolveUrl(node.consequent, ctx, depth + 1);
            case 'Identifier': return this.resolveName(node.name, ctx, depth);
            case 'MemberExpression': {
                const envName = this.isEnvAccess(node);
                if (envName) return this.envValue(envName, null);
                const key = this.keyOf(node) || this.memberKey(node);
                if (key) return this.resolveName(key, ctx, depth);
                return `{${this.simpleName(node)}}`;
            }
            case 'NewExpression':
                if (node.callee.type === 'Identifier' && node.callee.name === 'URL' && node.arguments.length) {
                    const p = this.resolveUrl(node.arguments[0], ctx, depth + 1);
                    return node.arguments[1] ? joinUrl(this.resolveUrl(node.arguments[1], ctx, depth + 1), p) : p;
                }
                return '{dynamic}';
            case 'CallExpression': {
                const key = this.configKey(node);
                if (key) return this.envValue(key, null);
                const callee = node.callee;
                const calleeName = callee.type === 'Identifier' ? callee.name : null;
                if (calleeName && ['encodeURIComponent', 'encodeURI', 'String', 'Number', 'escape'].includes(calleeName) && node.arguments[0]) {
                    return this.resolveUrl(node.arguments[0], ctx, depth + 1);
                }
                if (callee.type === 'MemberExpression' && !callee.computed &&
                    ['toString', 'trim', 'toLowerCase', 'toUpperCase'].includes(callee.property.name)) {
                    return this.resolveUrl(callee.object, ctx, depth + 1);
                }
                return `{${this.simpleName(node)}}`;
            }
            default: return `{${this.simpleName(node)}}`;
        }
    }

    /** `obj.prop` (non-this) key, e.g. `config.apiUrl`. */
    memberKey(node) {
        if (node.computed || node.property.type !== 'Identifier') return null;
        const left = node.object.type === 'Identifier' ? node.object.name : this.memberKey(node.object);
        return left ? `${left}.${node.property.name}` : null;
    }

    resolveName(key, ctx, depth) {
        if (ctx.consts.has(key)) return this.resolveUrl(ctx.consts.get(key), ctx, depth + 1);
        // imported binding: leave a token for the end-of-run cross-file pass
        const root = key.split('.')[0];
        const imp = ctx.imports.get(root);
        if (imp && imp.isRelative) {
            const targetModule = ctx.resolveModule(imp.source);
            const rest = key.includes('.') ? key.slice(root.length) : '';
            const exported = imp.importedName === 'default' || imp.importedName === '*'
                ? (imp.importedName === '*' ? rest.replace(/^\./, '') : `default${rest}`)
                : `${imp.importedName}${rest}`;
            return `\u0001${targetModule}.${exported}|${key}\u0001`;
        }
        return `{${key}}`;
    }

    resolveTokens(url, depth = 0) {
        if (!url.includes('\u0001')) return url;
        return url.replace(TOKEN_RE, (_m, body) => {
            const [target, fallback] = body.split('|');
            const value = this.projectConsts.get(target);
            if (value === undefined || depth >= MAX_CONST_DEPTH) return `{${fallback}}`;
            return this.resolveTokens(value, depth + 1);
        });
    }

    objectString(objectNode, names, ctx) {
        objectNode = this.unwrap(objectNode);
        if (!objectNode || objectNode.type !== 'ObjectExpression') return null;
        for (const prop of objectNode.properties) {
            if (prop.type !== 'ObjectProperty' || prop.computed) continue;
            const name = prop.key.name || prop.key.value;
            if (names.includes(name)) return this.resolveUrl(prop.value, ctx, 0);
        }
        return null;
    }

    objectProp(objectNode, name) {
        objectNode = this.unwrap(objectNode);
        if (!objectNode || objectNode.type !== 'ObjectExpression') return null;
        for (const prop of objectNode.properties) {
            if (prop.type !== 'ObjectProperty' || prop.computed) continue;
            if ((prop.key.name || prop.key.value) === name) return prop.value;
        }
        return null;
    }

    verbFromNode(node, ctx) {
        node = this.unwrap(node);
        if (!node) return null;
        if (node.type === 'StringLiteral') return node.value.toUpperCase();
        if (node.type === 'TemplateLiteral' && node.expressions.length === 0) return node.quasis[0].value.cooked.toUpperCase();
        return null;
    }

    // ── Detection ──────────────────────────────────────────────────────────

    /** Inspect one CallExpression. `caller` is the graph id of the enclosing callable/class/module. */
    detect(ctx, node, caller, nodePath) {
        if (ctx.skip) return;
        this.currentPath = nodePath || null;
        const callee = node.callee;
        const args = node.arguments;
        if (callee.type === 'Identifier') return this.detectIdentifierCall(ctx, node, caller, callee.name, args);
        if (callee.type === 'MemberExpression' && !callee.computed && callee.property.type === 'Identifier') {
            return this.detectMemberCall(ctx, node, caller, callee, args);
        }
        return undefined;
    }

    detectIdentifierCall(ctx, node, caller, name, args) {
        let family = ctx.libs.get(name);
        if (!family && (name === 'fetch' || name === '$fetch') && !ctx.imports.has(name)) family = 'fetch';
        if (!family) return;
        switch (family) {
            case 'fetch': {
                if (!args.length) return;
                const url = this.resolveUrl(args[0], ctx, 0);
                const verb = this.verbFromNode(this.objectProp(args[1], 'method'), ctx) || 'GET';
                this.emit(ctx, caller, verb, url, 'fetch', node, undefined, this.payloadFor(ctx, node, 'fetch', null, args[1]));
                break;
            }
            case 'undici-request': {
                if (!args.length) return;
                const verb = this.verbFromNode(this.objectProp(args[1], 'method'), ctx) || 'GET';
                this.emit(ctx, caller, verb, this.resolveUrl(args[0], ctx, 0), 'undici', node);
                break;
            }
            case 'axios':
            case 'got':
            case 'ky':
            case 'needle':
            case 'request':
                this.emitConfigCall(ctx, caller, family, args, null, node);
                break;
            default: break;
        }
    }

    /** axios(config|url, config?) / got(url, opts) / request({url, method}) */
    emitConfigCall(ctx, caller, family, args, baseUrl, node) {
        if (!args.length) return;
        const first = this.unwrap(args[0]);
        let urlNode;
        let optionsNode;
        if (first && first.type === 'ObjectExpression') {
            urlNode = this.objectProp(first, 'url') || this.objectProp(first, 'uri');
            optionsNode = first;
        } else {
            urlNode = first;
            optionsNode = args[1];
        }
        if (!urlNode && !baseUrl && !(optionsNode && this.objectProp(optionsNode, 'baseURL'))) return;
        const verb = this.verbFromNode(this.objectProp(optionsNode, 'method'), ctx) || 'GET';
        const base = baseUrl || this.objectString(optionsNode, ['baseURL', 'baseUrl', 'prefixUrl'], ctx);
        this.emit(ctx, caller, verb, urlNode ? this.resolveUrl(urlNode, ctx, 0) : '', CLIENT_LABEL[family], node, base,
            this.payloadFor(ctx, node, family, null, optionsNode));
    }

    detectMemberCall(ctx, node, caller, callee, args) {
        const method = callee.property.name;
        const receiverKey = this.keyOf(callee.object);
        const receiver = this.unwrap(callee.object);

        // instances: axios.create(), HttpService, HttpClient, XMLHttpRequest
        const inst = receiverKey && ctx.instances.get(receiverKey);
        if (inst) return this.detectInstanceCall(ctx, node, caller, inst, method, args);

        if (receiver && receiver.type === 'Identifier') {
            const family = ctx.libs.get(receiver.name);
            if (family) return this.detectLibCall(ctx, node, caller, family, method, args);
        }

        // this.http.get(...) style typed members registered as `this.<name>` handled above;
        // plain `xhr.open('GET', url)` on an untracked receiver is matched by literal verb shape.
        if (method === 'open' && args.length >= 2 && args[0].type === 'StringLiteral' &&
            HTTP_VERBS.has(args[0].value.toUpperCase()) && receiver && receiver.type === 'Identifier' &&
            ctx.xhr.has(receiver.name)) {
            this.emit(ctx, caller, args[0].value, this.resolveUrl(args[1], ctx, 0), CLIENT_LABEL.xhr, node);
        }
        return undefined;
    }

    detectInstanceCall(ctx, node, caller, inst, method, args) {
        const label = CLIENT_LABEL[inst.kind];
        if (inst.kind === 'xhr') {
            if (method === 'open' && args.length >= 2) {
                this.emit(ctx, caller, this.verbFromNode(args[0], ctx) || 'GET', this.resolveUrl(args[1], ctx, 0), label, node);
            }
            return;
        }
        if (inst.kind === 'angular-http') {
            if (VERB_METHODS.has(method) && args.length) {
                this.emit(ctx, caller, method, this.resolveUrl(args[0], ctx, 0), label, node, undefined,
                    this.verbPayload(ctx, node, inst.kind, method, args));
            } else if (method === 'request' && args.length >= 2) {
                this.emit(ctx, caller, this.verbFromNode(args[0], ctx) || 'GET', this.resolveUrl(args[1], ctx, 0), label, node);
            }
            return;
        }
        // axios / got / ky / nest-http all take (url, ...) for verb helpers and (config) for request()
        if (VERB_METHODS.has(method) && args.length) {
            this.emit(ctx, caller, method, this.resolveUrl(args[0], ctx, 0), label, node, inst.baseUrl,
                this.verbPayload(ctx, node, inst.kind, method, args));
        } else if (method === 'request') {
            this.emitConfigCall(ctx, caller, inst.kind === 'nest-http' ? 'axios' : inst.kind, args, inst.baseUrl, node);
        }
    }

    detectLibCall(ctx, node, caller, family, method, args) {
        const label = CLIENT_LABEL[family];
        if (family === 'http' || family === 'https') {
            if (!['request', 'get'].includes(method) || !args.length) return;
            const url = this.resolveHttpModuleUrl(ctx, args[0], family);
            let verb = 'GET';
            const options = this.unwrap(args[0]).type === 'ObjectExpression' ? args[0] : args[1];
            if (method === 'request') verb = this.verbFromNode(this.objectProp(options, 'method'), ctx) || 'GET';
            this.emit(ctx, caller, verb, url, label, node);
            return;
        }
        if (family === 'fetch' || family === 'undici') {
            if (family === 'undici' && method === 'request' && args.length) {
                const verb = this.verbFromNode(this.objectProp(args[1], 'method'), ctx) || 'GET';
                this.emit(ctx, caller, verb, this.resolveUrl(args[0], ctx, 0), label, node);
            } else if (family === 'undici' && method === 'fetch' && args.length) {
                const verb = this.verbFromNode(this.objectProp(args[1], 'method'), ctx) || 'GET';
                this.emit(ctx, caller, verb, this.resolveUrl(args[0], ctx, 0), label, node);
            }
            return;
        }
        if (VERB_METHODS.has(method) && args.length) {
            this.emit(ctx, caller, method, this.resolveUrl(args[0], ctx, 0), label, node, undefined,
                this.verbPayload(ctx, node, family, method, args));
        } else if (method === 'request' && args.length) {
            this.emitConfigCall(ctx, caller, family, args, null, node);
        }
    }

    resolveHttpModuleUrl(ctx, first, family) {
        const arg = this.unwrap(first);
        if (arg.type !== 'ObjectExpression') return this.resolveUrl(arg, ctx, 0);
        const protocol = this.objectProp(arg, 'protocol');
        const host = this.objectProp(arg, 'hostname') || this.objectProp(arg, 'host');
        const port = this.objectProp(arg, 'port');
        const p = this.objectProp(arg, 'path');
        let scheme = family === 'https' ? 'https' : 'http';
        if (protocol) scheme = this.resolveUrl(protocol, ctx, 0).replace(/:$/, '');
        const hostStr = host ? this.resolveUrl(host, ctx, 0) : '{host}';
        const portStr = port ? `:${this.resolveUrl(port, ctx, 0)}` : '';
        return `${scheme}://${hostStr}${portStr}${p ? this.resolveUrl(p, ctx, 0) : ''}`;
    }

    emit(ctx, caller, verb, url, client, node, baseUrl, payload) {
        let fullUrl = url || '';
        if (baseUrl && !isAbsoluteUrl(fullUrl)) fullUrl = joinUrl(baseUrl, fullUrl);
        else if (baseUrl && !fullUrl) fullUrl = baseUrl;
        if (!fullUrl || isBarePlaceholder(fullUrl)) return;
        // Pending resolution (import tokens) may turn this into something absolute later.
        const hasToken = fullUrl.includes('\u0001');
        if (!hasToken && !isAbsoluteUrl(fullUrl)) return;
        this.pending.push({
            caller,
            verb: String(verb || 'GET').toUpperCase(),
            url: fullUrl,
            client,
            line: node.loc && node.loc.start ? node.loc.start.line : 0,
            language: String(ctx.state.fileLanguage || 'javascript').toLowerCase(),
            payload: payload || {},
        });
    }

    // ── Request / response capture ─────────────────────────────────────────

    /** (url, data?, config?) for axios-style post/put/patch; (url, options) otherwise. */
    verbPayload(ctx, node, family, method, args) {
        if (DATA_ARG_FAMILIES.has(family)) {
            return BODY_VERBS.has(method)
                ? this.payloadFor(ctx, node, family, args[1], args[2])
                : this.payloadFor(ctx, node, family, null, args[1]);
        }
        return this.payloadFor(ctx, node, family, null, args[1]);
    }

    payloadFor(ctx, node, family, dataNode, optionsNode) {
        const out = {};
        try {
            const body = dataNode || this.firstProp(optionsNode, ['data', 'body', 'json', 'form']);
            const requestType = body ? this.shapeOf(ctx, body, 0) : null;
            if (requestType) out.requestType = requestType;

            const params = [];
            for (const key of this.propKeys(this.firstProp(optionsNode, ['params', 'searchParams', 'query', 'qs']))) {
                params.push(`query:${key}`);
            }
            if (params.length) out.params = params.join(',');

            const headers = this.propKeys(this.objectProp(optionsNode, 'headers'));
            if (headers.length) out.requestHeaders = headers.join(',');

            const generic = this.typeArgText(node);
            const responseType = generic || this.inferResponseFields(family, node);
            if (responseType) out.responseType = responseType;
        } catch (e) {
            // payload capture is best-effort and must never block call detection
        }
        return out;
    }

    firstProp(objectNode, names) {
        for (const n of names) {
            const v = this.objectProp(objectNode, n);
            if (v) return v;
        }
        return null;
    }

    propKeys(objectNode) {
        objectNode = this.unwrap(objectNode);
        if (!objectNode || objectNode.type !== 'ObjectExpression') return [];
        const keys = [];
        for (const prop of objectNode.properties) {
            if (prop.type !== 'ObjectProperty' && prop.type !== 'ObjectMethod') continue;
            if (prop.computed) continue;
            const key = prop.key.name || prop.key.value;
            if (key !== undefined) keys.push(String(key));
        }
        return keys.slice(0, MAX_SHAPE_FIELDS);
    }

    /** `{a,b}` for object literals, the TS type name for annotated identifiers, null when unknown. */
    shapeOf(ctx, node, depth) {
        node = this.unwrap(node);
        if (!node || depth > 3) return null;
        if (node.type === 'CallExpression' && node.arguments.length && this.isStringify(node.callee)) {
            return this.shapeOf(ctx, node.arguments[0], depth + 1);
        }
        if (node.type === 'NewExpression' && node.callee.type === 'Identifier') {
            return node.callee.name === 'URLSearchParams' || node.callee.name === 'FormData' ? null : node.callee.name;
        }
        if (node.type === 'ObjectExpression') {
            const keys = this.propKeys(node);
            return keys.length ? `{${keys.join(',')}}` : null;
        }
        if (node.type === 'Identifier' && this.currentPath) {
            const binding = this.currentPath.scope.getBinding(node.name);
            const decl = binding && binding.path && binding.path.node;
            if (!decl) return null;
            const ann = decl.id && decl.id.typeAnnotation && decl.id.typeAnnotation.typeAnnotation
                || decl.typeAnnotation && decl.typeAnnotation.typeAnnotation;
            const typed = ann && this.tsTypeText(ann);
            if (typed) return typed;
            if (decl.type === 'VariableDeclarator' && decl.init) return this.shapeOf(ctx, decl.init, depth + 1);
        }
        return null;
    }

    isStringify(callee) {
        return callee.type === 'MemberExpression' && !callee.computed && callee.property.name === 'stringify'
            && callee.object.type === 'Identifier' && callee.object.name === 'JSON';
    }

    tsTypeText(t) {
        if (!t) return null;
        if (t.type === 'TSTypeReference' && t.typeName && t.typeName.type === 'Identifier') {
            const args = t.typeParameters && t.typeParameters.params;
            if (!args || !args.length) return t.typeName.name;
            const inner = args.map(a => this.tsTypeText(a)).filter(Boolean);
            return inner.length ? `${t.typeName.name}<${inner.join(',')}>` : t.typeName.name;
        }
        if (t.type === 'TSArrayType') {
            const inner = this.tsTypeText(t.elementType);
            return inner ? `List<${inner}>` : null;
        }
        return null;
    }

    /** axios.get<Owner>(...) / http.get<Owner[]>(...) */
    typeArgText(node) {
        const params = node.typeParameters && node.typeParameters.params || node.typeArguments && node.typeArguments.params;
        if (!params || !params.length) return null;
        return this.tsTypeText(params[0]);
    }

    /** Best effort: find `res.data.x` / `const {a,b} = res.data` / `(await res.json()).x` usages of the call's result. */
    inferResponseFields(family, node) {
        const path = this.currentPath;
        if (!path) return null;
        let target = path;
        while (target.parentPath && ['AwaitExpression', 'TSAsExpression', 'ParenthesizedExpression'].includes(target.parentPath.node.type)) {
            target = target.parentPath;
        }
        const fields = new Set();
        const parent = target.parentPath && target.parentPath.node;
        if (parent && parent.type === 'VariableDeclarator' && target.parentPath.get('init') === target) {
            this.collectFromBinding(target.parentPath.get('id'), family, fields, false);
        } else if (parent && parent.type === 'MemberExpression' && parent.property.name === 'then') {
            const call = target.parentPath.parentPath;
            const cb = call && call.node.type === 'CallExpression' ? call.get('arguments.0') : null;
            if (cb && cb.node && /Function/.test(cb.node.type) && cb.node.params.length) {
                this.collectFromBinding(cb.get('params.0'), family, fields, false);
            }
        }
        if (!fields.size) return null;
        return `{${[...fields].slice(0, MAX_SHAPE_FIELDS).join(',')}}`;
    }

    /** `viaWrapper` true means the binding already holds the response body (post `.data` / `.json()`). */
    collectFromBinding(idPath, family, fields, viaWrapper) {
        const node = idPath.node;
        const wrapperProp = family === 'got' || family === 'ky' || family === 'needle' || family === 'request' ? 'body' : 'data';
        if (node.type === 'Identifier') {
            const binding = idPath.scope.getBinding(node.name);
            if (!binding) return;
            for (const ref of binding.referencePaths) this.collectRef(ref, family, fields, viaWrapper, wrapperProp);
        } else if (node.type === 'ObjectPattern' && !viaWrapper) {
            for (const [i, prop] of node.properties.entries()) {
                if (prop.type !== 'ObjectProperty' || (prop.key.name || prop.key.value) !== wrapperProp) continue;
                const valuePath = idPath.get(`properties.${i}.value`);
                if (valuePath.node.type === 'ObjectPattern') this.patternKeys(valuePath.node, fields);
                else this.collectFromBinding(valuePath, family, fields, true);
            }
        } else if (node.type === 'ObjectPattern' && viaWrapper) {
            this.patternKeys(node, fields);
        }
    }

    patternKeys(pattern, fields) {
        for (const prop of pattern.properties) {
            if (prop.type === 'ObjectProperty' && !prop.computed) {
                const key = String(prop.key.name || prop.key.value);
                if (prop.value.type === 'ObjectPattern') {
                    for (const inner of prop.value.properties) {
                        if (inner.type === 'ObjectProperty' && !inner.computed) fields.add(`${key}.${inner.key.name || inner.key.value}`);
                    }
                } else fields.add(key);
            }
        }
    }

    collectRef(ref, family, fields, viaWrapper, wrapperProp) {
        let member = ref.parentPath;
        if (!member || member.node.type !== 'MemberExpression' || member.node.object !== ref.node || member.node.computed) return;
        const first = member.node.property.name;
        if (!viaWrapper) {
            if (first === 'json' || first === 'text') {
                // fetch: await res.json()
                let call = member.parentPath;
                if (!call || call.node.type !== 'CallExpression') return;
                let holder = call;
                while (holder.parentPath && holder.parentPath.node.type === 'AwaitExpression') holder = holder.parentPath;
                const decl = holder.parentPath && holder.parentPath.node;
                if (decl && decl.type === 'VariableDeclarator' && decl.init === holder.node) {
                    this.collectFromBinding(holder.parentPath.get('id'), family, fields, true);
                } else if (holder.parentPath && holder.parentPath.node.type === 'MemberExpression') {
                    this.collectMemberChain(holder, fields);
                }
                return;
            }
            if (first !== wrapperProp) return;
            const wrapped = member.parentPath;
            if (wrapped && wrapped.node.type === 'MemberExpression' && wrapped.node.object === member.node) {
                this.collectMemberChain(member, fields);
            } else if (wrapped && wrapped.node.type === 'VariableDeclarator' && wrapped.node.init === member.node) {
                this.collectFromBinding(wrapped.get('id'), family, fields, true);
            }
            return;
        }
        this.collectMemberChain(ref, fields);
    }

    /** Walks `<base>.a.b.c` upwards from `base` adding `a.b` (two levels max, skipping array/method names). */
    collectMemberChain(base, fields) {
        const names = [];
        let cur = base;
        while (cur.parentPath && cur.parentPath.node.type === 'MemberExpression'
            && cur.parentPath.node.object === cur.node && !cur.parentPath.node.computed
            && cur.parentPath.node.property.type === 'Identifier') {
            const name = cur.parentPath.node.property.name;
            if (NON_FIELD_MEMBERS.has(name)) break;
            names.push(name);
            cur = cur.parentPath;
        }
        if (names.length) fields.add(names.slice(0, 2).join('.'));
    }

    /** Resolve cross-file tokens and write nodes/edges into the graph. Call once after all files. */
    flush() {
        for (const call of this.pending) {
            const url = this.resolveTokens(call.url);
            if (!isAbsoluteUrl(url) || isBarePlaceholder(url)) continue;
            if (!HTTP_VERBS.has(call.verb)) call.verb = 'GET';
            const id = `extapi:${call.verb} ${url}`;
            if (!this.graph.hasNode(id)) {
                this.graph.addNode(id, 'EXTERNAL_API', `${call.verb} ${url}`, url, {
                    httpMethod: call.verb,
                    url,
                    host: hostOf(url),
                    client: call.client,
                    language: call.language,
                });
            }
            this.graph.addRelationship(call.caller, id, 'CALLS_EXTERNAL', {
                lineNumber: call.line,
                client: call.client,
                confidence: url.includes('{') ? 0.7 : 0.9,
                reason: 'http-client',
                ...call.payload,
            });
            this.callCount++;
        }
        this.pending = [];
    }
}

module.exports = { OutboundDetector, loadEnvUrls, hostOf, joinUrl };
