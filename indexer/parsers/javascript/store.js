/**
 * Graph store implementations for JavaScript parser.
 * Supports KuzuDB (embedded) and Neo4j backends.
 */

const path = require('path');
const fs = require('fs');

function withNormalizedRelProps(definition) {
    const idx = definition.lastIndexOf(')');
    if (idx === -1) return definition;
    return `${definition.slice(0, idx)}, language STRING, kind STRING, normKind STRING${definition.slice(idx)}`;
}

function inferLanguage() {
    return 'javascript';
}

function inferNormKind(type) {
    const normalized = String(type || '').toUpperCase();
    if (['PACKAGE', 'MODULE'].includes(normalized)) return 'CodeUnit';
    if (['METHOD', 'CONSTRUCTOR', 'FUNCTION', 'ARROW_FUNCTION', 'HOOK', 'ASYNC_FUNCTION', 'GENERATOR', 'EXTENSION_FUNCTION', 'SUSPEND_FUNCTION', 'LAMBDA'].includes(normalized)) return 'Callable';
    if (['CLASS', 'INTERFACE', 'ENUM', 'RECORD', 'DATA_CLASS', 'SEALED_CLASS', 'SEALED_INTERFACE', 'OBJECT_DECL', 'TYPE_ALIAS'].includes(normalized)) return 'TypeLike';
    if (['FIELD', 'PROPERTY', 'PARAMETER'].includes(normalized)) return 'DataMember';
    if (normalized === 'COMPONENT') return 'UiComponent';
    if (normalized === 'FILE') return 'SourceFile';
    if (['ANNOTATION_TYPE', 'DECORATOR'].includes(normalized)) return 'AnnotationLike';
    if (normalized === 'STATEMENT') return 'Statement';
    return 'CodeElement';
}

function inferRelNormKind(type) {
    const normalized = String(type || '').toUpperCase();
    if (['CONTAINS', 'SOURCE_FILE'].includes(normalized)) return 'Contains';
    if (normalized === 'CALLS') return 'Calls';
    if (['IMPORTS', 'EXPORTS', 'PROP_DEPENDENCY', 'DELEGATES_TO'].includes(normalized)) return 'DependsOn';
    if (['EXTENDS', 'IMPLEMENTS', 'OVERRIDES', 'SEALED_SUBTYPE', 'COMPANION_OF', 'EXTENSION_OF'].includes(normalized)) return 'TypeRelation';
    if (['RENDERS', 'USES_HOOK'].includes(normalized)) return 'UsesUi';
    if (['HAS_PARAMETER', 'OF_TYPE', 'RETURNS', 'THROWS', 'DECORATES', 'HAS_ANNOTATION', 'YIELDS', 'SUSPENDS'].includes(normalized)) return 'SemanticRelation';
    if (['AST_CHILD', 'CFG_NEXT', 'DATA_FLOW'].includes(normalized)) return 'FlowRelation';
    return 'CodeRelation';
}

// ─── KuzuDB Store ───────────────────────────────────────────────────────────

class KuzuStore {
    constructor(dbPath) {
        this.dbPath = path.resolve(dbPath);
        fs.mkdirSync(path.dirname(this.dbPath), { recursive: true });
        const kuzu = require('kuzu');
        // Cap buffer pool at 512 MB; default (0) uses ~80% of system memory
        // which causes OOM kills in memory-constrained Docker environments.
        this.db = new kuzu.Database(this.dbPath, 512 * 1024 * 1024);
        this.conn = new kuzu.Connection(this.db);
        console.error(`KuzuDB: opening database at ${this.dbPath}`);
    }

    async initSchema() {
        const nodeTypes = [
            'Package', 'Class', 'Interface', 'Enum', 'Record', 'AnnotationType',
            'Method', 'Constructor', 'Field', 'Parameter', 'File', 'Statement',
            'Module', 'Function', 'ArrowFunction', 'Component', 'Hook', 'JSXElement',
            'Decorator', 'Generator', 'AsyncFunction', 'Comprehension',
            'DataClass', 'SealedClass', 'SealedInterface', 'ObjectDecl',
            'CompanionObject', 'ExtensionFunction', 'SuspendFunction',
            'Property', 'Lambda', 'InitBlock', 'TypeAlias'
        ];

        for (const t of nodeTypes) {
            await this._query(`CREATE NODE TABLE IF NOT EXISTS ${t} (id STRING, name STRING, qualifiedName STRING, visibility STRING, isAbstract BOOLEAN, isStatic BOOLEAN, isFinal BOOLEAN, returnType STRING, lineNumber INT64, endLineNumber INT64, statementType STRING, code STRING, type STRING, external BOOLEAN, path STRING, language STRING, kind STRING, normKind STRING, PRIMARY KEY (id))`);
        }

        const relDefs = [
            "CREATE REL TABLE IF NOT EXISTS CONTAINS (FROM Package TO Class, FROM Package TO Interface, FROM Package TO Enum, FROM Package TO Record, FROM Class TO Method, FROM Class TO Constructor, FROM Class TO Field, FROM Interface TO Method, FROM Interface TO Field, FROM Module TO Function, FROM Module TO ArrowFunction, FROM Module TO Component, FROM Module TO Hook, FROM Module TO Class, FROM Class TO Function, FROM Class TO ArrowFunction, FROM DataClass TO Method, FROM DataClass TO Property, FROM SealedClass TO Method, FROM SealedClass TO Property, FROM ObjectDecl TO Method, FROM ObjectDecl TO Property, FROM CompanionObject TO Method, FROM CompanionObject TO Property, FROM Class TO Property, FROM Class TO InitBlock, FROM Package TO TypeAlias, FROM Package TO ObjectDecl)",
            "CREATE REL TABLE IF NOT EXISTS EXTENDS (FROM Class TO Class, FROM Interface TO Interface, FROM Component TO Class, FROM DataClass TO Class, FROM SealedClass TO Class)",
            "CREATE REL TABLE IF NOT EXISTS IMPLEMENTS (FROM Class TO Interface, FROM DataClass TO Interface, FROM SealedClass TO Interface, FROM ObjectDecl TO Interface)",
            "CREATE REL TABLE IF NOT EXISTS CALLS (FROM Method TO Method, FROM Method TO Function, FROM Method TO AsyncFunction, FROM Constructor TO Method, FROM Constructor TO Function, FROM Constructor TO AsyncFunction, FROM Function TO Function, FROM Function TO Method, FROM Function TO AsyncFunction, FROM ArrowFunction TO Function, FROM ArrowFunction TO Method, FROM ArrowFunction TO AsyncFunction, FROM Component TO Function, FROM Component TO AsyncFunction, FROM Hook TO Function, FROM Hook TO AsyncFunction, FROM AsyncFunction TO Function, FROM AsyncFunction TO Method, FROM AsyncFunction TO AsyncFunction, FROM ExtensionFunction TO Method, FROM ExtensionFunction TO Function, FROM SuspendFunction TO Method, FROM SuspendFunction TO Function, FROM Lambda TO Method, FROM Lambda TO Function, FROM InitBlock TO Method, lineNumber INT64, resolved BOOLEAN)",
            "CREATE REL TABLE IF NOT EXISTS RETURNS (FROM Method TO Class, FROM Method TO Interface, FROM Method TO Enum, FROM Method TO Record, FROM ExtensionFunction TO Class, FROM SuspendFunction TO Class)",
            "CREATE REL TABLE IF NOT EXISTS HAS_PARAMETER (FROM Method TO Parameter, FROM Constructor TO Parameter, FROM Function TO Parameter, FROM ArrowFunction TO Parameter, FROM ExtensionFunction TO Parameter, FROM SuspendFunction TO Parameter, FROM Lambda TO Parameter)",
            "CREATE REL TABLE IF NOT EXISTS OF_TYPE (FROM Field TO Class, FROM Field TO Interface, FROM Field TO Enum, FROM Field TO Record, FROM Parameter TO Class, FROM Parameter TO Interface, FROM Parameter TO Enum, FROM Parameter TO Record, FROM Property TO Class, FROM Property TO Interface)",
            "CREATE REL TABLE IF NOT EXISTS HAS_ANNOTATION (FROM Class TO AnnotationType, FROM Interface TO AnnotationType, FROM Method TO AnnotationType, FROM Constructor TO AnnotationType, FROM Field TO AnnotationType, FROM Property TO AnnotationType, FROM ExtensionFunction TO AnnotationType)",
            "CREATE REL TABLE IF NOT EXISTS OVERRIDES (FROM Method TO Method, FROM ExtensionFunction TO Method)",
            "CREATE REL TABLE IF NOT EXISTS THROWS (FROM Method TO Class)",
            "CREATE REL TABLE IF NOT EXISTS SOURCE_FILE (FROM Class TO File, FROM Interface TO File, FROM Enum TO File, FROM Record TO File, FROM AnnotationType TO File, FROM Module TO File, FROM Component TO File, FROM Function TO File, FROM DataClass TO File, FROM SealedClass TO File, FROM SealedInterface TO File, FROM ObjectDecl TO File, FROM ExtensionFunction TO File)",
            "CREATE REL TABLE IF NOT EXISTS AST_CHILD (FROM Method TO Statement, FROM Constructor TO Statement, FROM Statement TO Statement, FROM Function TO Statement, FROM ArrowFunction TO Statement, FROM AsyncFunction TO Statement, FROM Generator TO Statement, FROM Component TO Statement, FROM Hook TO Statement, ast_order INT64)",
            "CREATE REL TABLE IF NOT EXISTS CFG_NEXT (FROM Method TO Statement, FROM Constructor TO Statement, FROM Statement TO Statement, FROM Function TO Statement, FROM ArrowFunction TO Statement, FROM AsyncFunction TO Statement, FROM Generator TO Statement, FROM Component TO Statement, FROM Hook TO Statement, backEdge BOOLEAN)",
            "CREATE REL TABLE IF NOT EXISTS DATA_FLOW (FROM Statement TO Statement, variable STRING)",
            "CREATE REL TABLE IF NOT EXISTS IMPORTS (FROM Module TO Module, importedName STRING, localName STRING)",
            "CREATE REL TABLE IF NOT EXISTS EXPORTS (FROM Module TO Function, FROM Module TO ArrowFunction, FROM Module TO AsyncFunction, FROM Module TO Component, FROM Module TO Class)",
            "CREATE REL TABLE IF NOT EXISTS RENDERS (FROM Component TO Component, FROM Function TO Component, FROM ArrowFunction TO Component, lineNumber INT64)",
            "CREATE REL TABLE IF NOT EXISTS USES_HOOK (FROM Component TO Hook, FROM Function TO Hook, FROM ArrowFunction TO Hook, lineNumber INT64)",
            "CREATE REL TABLE IF NOT EXISTS PROP_DEPENDENCY (FROM Component TO Component)",
            "CREATE REL TABLE IF NOT EXISTS DECORATES (FROM Decorator TO Function, FROM Decorator TO Method, FROM Decorator TO Class, FROM Decorator TO AsyncFunction)",
            "CREATE REL TABLE IF NOT EXISTS YIELDS (FROM Generator TO Class)",
            "CREATE REL TABLE IF NOT EXISTS EXTENSION_OF (FROM ExtensionFunction TO Class, FROM ExtensionFunction TO Interface, FROM ExtensionFunction TO DataClass)",
            "CREATE REL TABLE IF NOT EXISTS DELEGATES_TO (FROM Property TO Class, FROM Property TO Interface)",
            "CREATE REL TABLE IF NOT EXISTS SEALED_SUBTYPE (FROM SealedClass TO Class, FROM SealedClass TO DataClass, FROM SealedClass TO ObjectDecl, FROM SealedInterface TO Class, FROM SealedInterface TO Interface)",
            "CREATE REL TABLE IF NOT EXISTS COMPANION_OF (FROM CompanionObject TO Class, FROM CompanionObject TO DataClass, FROM CompanionObject TO SealedClass)",
            "CREATE REL TABLE IF NOT EXISTS SUSPENDS (FROM SuspendFunction TO SuspendFunction, FROM SuspendFunction TO Method)"
        ];

        for (const def of relDefs) {
            await this._query(withNormalizedRelProps(def));
        }
    }

    async clear() {
        const relTypes = [
            'CONTAINS', 'EXTENDS', 'IMPLEMENTS', 'CALLS', 'RETURNS',
            'HAS_PARAMETER', 'OF_TYPE', 'HAS_ANNOTATION', 'OVERRIDES', 'THROWS',
            'SOURCE_FILE', 'AST_CHILD', 'CFG_NEXT', 'DATA_FLOW',
            'IMPORTS', 'EXPORTS', 'RENDERS', 'USES_HOOK', 'PROP_DEPENDENCY',
            'DECORATES', 'YIELDS',
            'EXTENSION_OF', 'DELEGATES_TO', 'SEALED_SUBTYPE', 'COMPANION_OF', 'SUSPENDS'
        ];
        for (const r of relTypes) {
            await this._query(`DROP TABLE ${r}`);
        }
        const nodeTypes = [
            'Package', 'Class', 'Interface', 'Enum', 'Record', 'AnnotationType',
            'Method', 'Constructor', 'Field', 'Parameter', 'File', 'Statement',
            'Module', 'Function', 'ArrowFunction', 'Component', 'Hook', 'JSXElement',
            'Decorator', 'Generator', 'AsyncFunction', 'Comprehension',
            'DataClass', 'SealedClass', 'SealedInterface', 'ObjectDecl',
            'CompanionObject', 'ExtensionFunction', 'SuspendFunction',
            'Property', 'Lambda', 'InitBlock', 'TypeAlias'
        ];
        for (const t of nodeTypes) {
            await this._query(`DROP TABLE ${t}`);
        }
        await this.initSchema();
    }

    async save(graphJson) {
        const nodeTableMap = {};

        // Insert nodes
        for (const node of graphJson.nodes) {
            const table = nodeTypeToTable(node.type);
            nodeTableMap[node.id] = table;

            const escaped = (s) => (s || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
            let cypher = `CREATE (n:${table} {id: '${escaped(node.id)}', name: '${escaped(node.name)}', qualifiedName: '${escaped(node.qualifiedName)}'`;

            const props = {
                ...(node.properties || {}),
                language: node.properties?.language || inferLanguage(),
                kind: table,
                normKind: node.properties?.normKind || inferNormKind(node.type),
            };
            for (const [key, val] of Object.entries(props)) {
                if (['visibility', 'isAbstract', 'isStatic', 'isFinal', 'returnType', 'lineNumber', 'endLineNumber', 'statementType', 'code', 'type', 'external', 'path', 'language', 'kind', 'normKind'].includes(key)) {
                    if (typeof val === 'boolean') cypher += `, ${key}: ${val}`;
                    else if (typeof val === 'number') cypher += `, ${key}: ${val}`;
                    else cypher += `, ${key}: '${escaped(String(val))}'`;
                }
            }
            cypher += '})';
            await this._query(cypher);
        }

        // Insert relationships
        for (const rel of graphJson.relationships) {
            const srcTable = nodeTableMap[rel.sourceId];
            const tgtTable = nodeTableMap[rel.targetId];
            if (!srcTable || !tgtTable) continue;

            const escaped = (s) => (s || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
            let relProps = '';
            const relProperties = {
                ...(rel.properties || {}),
                language: inferLanguage(),
                kind: rel.type,
                normKind: inferRelNormKind(rel.type),
            };
            if (Object.keys(relProperties).length > 0) {
                const parts = Object.entries(relProperties).map(([k, v]) => {
                    if (typeof v === 'boolean') return `${k}: ${v}`;
                    if (typeof v === 'number') return `${k}: ${v}`;
                    return `${k}: '${escaped(String(v))}'`;
                });
                relProps = ` {${parts.join(', ')}}`;
            }

            const cypher = `MATCH (a:${srcTable} {id: '${escaped(rel.sourceId)}'}), (b:${tgtTable} {id: '${escaped(rel.targetId)}'}) CREATE (a)-[:${rel.type}${relProps}]->(b)`;
            await this._query(cypher);
        }
    }

    async summary() {
        let totalNodes = 0;
        const tables = ['Module', 'Function', 'ArrowFunction', 'Component', 'Hook', 'Class', 'Method', 'Constructor', 'File', 'Statement'];
        for (const t of tables) {
            try {
                const result = await this.conn.query(`MATCH (n:${t}) RETURN count(n) AS c`);
                const rows = await result.getAll();
                if (rows.length > 0) totalNodes += Number(rows[0].c);
            } catch (e) { /* table may not exist */ }
        }
        return `KuzuDB graph: ${totalNodes} nodes`;
    }

    async close() {
        // kuzu Node.js driver handles cleanup
    }

    async _query(cypher) {
        try {
            await this.conn.query(cypher);
        } catch (e) {
            // Silently skip expected errors (table exists, schema mismatch, etc.)
        }
    }
}

// ─── Neo4j Store ────────────────────────────────────────────────────────────

class Neo4jStore {
    constructor(uri, username, password, database) {
        const neo4j = require('neo4j-driver');
        this.driver = neo4j.driver(uri, neo4j.auth.basic(username, password));
        this.database = database || 'neo4j';
        console.error(`Neo4j: connecting to ${uri}, database '${this.database}'`);
    }

    async initSchema() {
        const session = this.driver.session({ database: this.database });
        try {
            const labels = [
                'Package', 'Class', 'Interface', 'Enum', 'Record', 'AnnotationType',
                'Method', 'Constructor', 'Field', 'Parameter', 'File', 'Statement',
                'Module', 'Function', 'ArrowFunction', 'Component', 'Hook'
            ];
            for (const label of labels) {
                await session.run(`CREATE CONSTRAINT IF NOT EXISTS FOR (n:${label}) REQUIRE n.id IS UNIQUE`);
            }
        } finally {
            await session.close();
        }
    }

    async clear() {
        const session = this.driver.session({ database: this.database });
        try {
            await session.run('MATCH (n) DETACH DELETE n');
        } finally {
            await session.close();
        }
    }

    async save(graphJson) {
        const session = this.driver.session({ database: this.database });
        try {
            // Batch nodes by label
            const nodesByLabel = {};
            for (const node of graphJson.nodes) {
                const label = nodeTypeToTable(node.type);
                if (!nodesByLabel[label]) nodesByLabel[label] = [];
                const props = { id: node.id, name: node.name, qualifiedName: node.qualifiedName, ...(node.properties || {}) };
                nodesByLabel[label].push(props);
            }

            for (const [label, batch] of Object.entries(nodesByLabel)) {
                // Insert in batches of 500
                for (let i = 0; i < batch.length; i += 500) {
                    const chunk = batch.slice(i, i + 500);
                    await session.run(
                        `UNWIND $batch AS row MERGE (n:${label} {id: row.id}) SET n += row`,
                        { batch: chunk }
                    );
                }
            }

            // Insert relationships
            const relsByType = {};
            for (const rel of graphJson.relationships) {
                const srcLabel = nodeTypeToTable(findNodeType(graphJson, rel.sourceId));
                const tgtLabel = nodeTypeToTable(findNodeType(graphJson, rel.targetId));
                if (!srcLabel || !tgtLabel) continue;
                const key = `${srcLabel}|${tgtLabel}|${rel.type}`;
                if (!relsByType[key]) relsByType[key] = { srcLabel, tgtLabel, type: rel.type, batch: [] };
                relsByType[key].batch.push({ sourceId: rel.sourceId, targetId: rel.targetId, ...(rel.properties || {}) });
            }

            for (const { srcLabel, tgtLabel, type, batch } of Object.values(relsByType)) {
                for (let i = 0; i < batch.length; i += 500) {
                    const chunk = batch.slice(i, i + 500);
                    await session.run(
                        `UNWIND $batch AS row MATCH (a:${srcLabel} {id: row.sourceId}), (b:${tgtLabel} {id: row.targetId}) CREATE (a)-[:${type}]->(b)`,
                        { batch: chunk }
                    );
                }
            }
        } finally {
            await session.close();
        }
    }

    async summary() {
        const session = this.driver.session({ database: this.database });
        try {
            const result = await session.run('MATCH (n) RETURN count(n) AS c');
            const count = result.records[0].get('c').toNumber();
            return `Neo4j graph: ${count} nodes`;
        } finally {
            await session.close();
        }
    }

    async close() {
        await this.driver.close();
    }
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function nodeTypeToTable(type) {
    if (!type) return null;
    const map = {
        'PACKAGE': 'Package', 'CLASS': 'Class', 'INTERFACE': 'Interface',
        'ENUM': 'Enum', 'RECORD': 'Record', 'ANNOTATION_TYPE': 'AnnotationType',
        'METHOD': 'Method', 'CONSTRUCTOR': 'Constructor', 'FIELD': 'Field',
        'PARAMETER': 'Parameter', 'FILE': 'File', 'STATEMENT': 'Statement',
        'MODULE': 'Module', 'FUNCTION': 'Function', 'ARROW_FUNCTION': 'ArrowFunction',
        'COMPONENT': 'Component', 'HOOK': 'Hook', 'JSX_ELEMENT': 'JSXElement',
        'DECORATOR': 'Decorator', 'GENERATOR': 'Generator',
        'ASYNC_FUNCTION': 'AsyncFunction', 'COMPREHENSION': 'Comprehension',
    };
    return map[type.toUpperCase()] || type;
}

function findNodeType(graphJson, nodeId) {
    const node = graphJson.nodes.find(n => n.id === nodeId);
    return node ? node.type : null;
}

function createStore(options) {
    if (options.backend === 'neo4j') {
        return new Neo4jStore(
            options.neo4jUri || 'bolt://localhost:7687',
            options.neo4jUser || 'neo4j',
            options.neo4jPassword || '',
            options.neo4jDatabase || 'neo4j'
        );
    }
    return new KuzuStore(options.dbPath);
}

module.exports = { KuzuStore, Neo4jStore, createStore, nodeTypeToTable };
