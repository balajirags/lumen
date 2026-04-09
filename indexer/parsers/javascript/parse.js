#!/usr/bin/env node
/**
 * JavaScript/React/TypeScript parser using Babel.
 * Parses source code and writes a CodeGraph to KuzuDB, Neo4j, or JSON stdout.
 * 
 * Usage: node parse.js <directory> [options]
 *   --backend kuzu|neo4j|json   Graph backend (default: kuzu)
 *   --db-path <path>            KuzuDB database path (default: <bin-dir>/kuzu_db/<repo-name>-db)
 *   --clear                     Clear existing graph before writing
 *   --extensions .js,.jsx,...   File extensions to include
 *   --neo4j-uri <uri>           Neo4j URI (default: bolt://localhost:7687)
 *   --neo4j-user <user>         Neo4j username (default: neo4j)
 *   --neo4j-password <pass>     Neo4j password
 *   --neo4j-database <db>       Neo4j database (default: neo4j)
 */

const fs = require('fs');
const path = require('path');
const parser = require('@babel/parser');
const traverse = require('@babel/traverse').default;

const DEFAULT_EXTENSIONS = ['.js', '.jsx', '.ts', '.tsx', '.mjs'];

class CodeGraphBuilder {
    constructor() {
        this.nodes = {};
        this.relationships = [];
        this.internalModules = new Set();
    }

    addNode(id, type, name, qualifiedName, properties = {}) {
        if (!this.nodes[id]) {
            this.nodes[id] = { id, type, name, qualifiedName, properties };
        }
    }

    hasNode(id) {
        return id in this.nodes;
    }

    addRelationship(sourceId, targetId, type, properties = {}) {
        this.relationships.push({ sourceId, targetId, type, properties });
    }

    toJSON() {
        return {
            nodes: Object.values(this.nodes),
            relationships: this.relationships
        };
    }
}

// ─── CPG: Statement classification & helpers ────────────────────────────────

function classifyStmt(node) {
    const map = {
        IfStatement: 'IF',
        ForStatement: 'FOR',
        ForInStatement: 'FOR_IN',
        ForOfStatement: 'FOR_OF',
        WhileStatement: 'WHILE',
        DoWhileStatement: 'DO_WHILE',
        SwitchStatement: 'SWITCH',
        ReturnStatement: 'RETURN',
        ThrowStatement: 'THROW',
        TryStatement: 'TRY',
        WithStatement: 'WITH',
        BreakStatement: 'BREAK',
        ContinueStatement: 'CONTINUE',
        LabeledStatement: 'LABELED',
        VariableDeclaration: 'ASSIGNMENT',
        ExpressionStatement: 'EXPRESSION',
        EmptyStatement: 'EMPTY',
        DebuggerStatement: 'DEBUGGER',
    };
    return map[node.type] || 'OTHER';
}

function stmtCode(node, code) {
    if (!node.start && node.start !== 0) return node.type;
    const snippet = code.slice(node.start, node.end);
    if (snippet.length > 200) return snippet.slice(0, 200) + '...';
    return snippet;
}

function collectReads(node) {
    const reads = new Set();
    walkAst(node, (n) => {
        if (n.type === 'Identifier' && !isWriteTarget(n)) {
            reads.add(n.name);
        }
    });
    return reads;
}

function collectWrites(node) {
    const writes = new Set();
    walkAst(node, (n) => {
        if (n.type === 'VariableDeclarator' && n.id?.type === 'Identifier') {
            writes.add(n.id.name);
        }
        if (n.type === 'AssignmentExpression' && n.left?.type === 'Identifier') {
            writes.add(n.left.name);
        }
        if (n.type === 'UpdateExpression' && n.argument?.type === 'Identifier') {
            writes.add(n.argument.name);
        }
    });
    return writes;
}

/** Returns true if an Identifier node is a write-target (left side of assignment, var decl, etc.) */
function isWriteTarget(node) {
    const parent = node._parent;
    if (!parent) return false;
    if (parent.type === 'VariableDeclarator' && parent.id === node) return true;
    if (parent.type === 'AssignmentExpression' && parent.left === node) return true;
    if (parent.type === 'UpdateExpression') return true;
    // Function/method parameter names
    if (parent.type === 'FunctionDeclaration' || parent.type === 'FunctionExpression' ||
        parent.type === 'ArrowFunctionExpression') {
        if (parent.params && parent.params.includes(node)) return true;
    }
    // Property keys, import specifiers, etc.
    if (parent.type === 'Property' && parent.key === node && !parent.computed) return true;
    if (parent.type === 'MemberExpression' && parent.property === node && !parent.computed) return true;
    if (parent.type === 'ImportSpecifier' || parent.type === 'ImportDefaultSpecifier' ||
        parent.type === 'ImportNamespaceSpecifier') return true;
    return false;
}

/** Walk all AST nodes, calling visitor(node) for each. Sets _parent refs. */
function walkAst(node, visitor) {
    if (!node || typeof node !== 'object') return;
    visitor(node);
    for (const key of Object.keys(node)) {
        if (key === '_parent') continue;
        const child = node[key];
        if (Array.isArray(child)) {
            for (const item of child) {
                if (item && typeof item === 'object' && item.type) {
                    walkAst(item, visitor);
                }
            }
        } else if (child && typeof child === 'object' && child.type) {
            walkAst(child, visitor);
        }
    }
}

/** Set _parent references on all nodes in a Babel AST. */
function setParents(node) {
    if (!node || typeof node !== 'object') return;
    for (const key of Object.keys(node)) {
        if (key === '_parent') continue;
        const child = node[key];
        if (Array.isArray(child)) {
            for (const item of child) {
                if (item && typeof item === 'object' && item.type) {
                    item._parent = node;
                    setParents(item);
                }
            }
        } else if (child && typeof child === 'object' && child.type) {
            child._parent = node;
            setParents(child);
        }
    }
}

// ─── CPG: Enhancer class ────────────────────────────────────────────────────

class CpgEnhancer {
    constructor(graph) {
        this.graph = graph;
        this.stmtCount = 0;
        this.cfgEdgeCount = 0;
        this.dataFlowCount = 0;
    }

    /**
     * Enhance a parsed file's AST with CPG edges.
     * @param {object} ast - Babel AST
     * @param {string} sourceCode - Original source text (for code snippets)
     * @param {string} moduleName - Qualified module name
     * @param {string} filePath - Relative file path
     */
    enhanceFile(ast, sourceCode, moduleName, filePath) {
        setParents(ast);
        const fileId = `file:${filePath}`;

        // Walk the entire AST to find function/method bodies
        walkAst(ast, (node) => {
            if (node.type === 'FunctionDeclaration' && node.id) {
                this._processFunction(node, node.id.name, moduleName, sourceCode);
            }
            if (node.type === 'ClassMethod' || node.type === 'ClassPrivateMethod') {
                this._processClassMethod(node, moduleName, sourceCode);
            }
            if (node.type === 'VariableDeclarator' &&
                node.id?.type === 'Identifier' &&
                (node.init?.type === 'ArrowFunctionExpression' || node.init?.type === 'FunctionExpression')) {
                this._processFunction(node.init, node.id.name, moduleName, sourceCode);
            }
        });
    }

    _processFunction(funcNode, funcName, moduleName, sourceCode) {
        const funcId = `function:${moduleName}.${funcName}`;
        if (!this.graph.hasNode(funcId)) return;

        const body = funcNode.body;
        const stmts = body.type === 'BlockStatement' ? body.body : [body];
        const qualified = `${moduleName}.${funcName}`;
        const counter = [0];
        const varDefs = {};

        this._processStatements(stmts, funcId, qualified, counter, varDefs, [funcId], sourceCode);
    }

    _processClassMethod(methodNode, moduleName, sourceCode) {
        const methodName = methodNode.key?.name || methodNode.key?.value || 'anonymous';
        // Find containing class
        let className = null;
        let current = methodNode._parent;
        while (current) {
            if (current.type === 'ClassDeclaration' && current.id) {
                className = current.id.name;
                break;
            }
            if (current.type === 'ClassExpression' && current.id) {
                className = current.id.name;
                break;
            }
            current = current._parent;
        }
        if (!className) return;

        const nodeType = methodNode.kind === 'constructor' ? 'constructor' : 'method';
        const methodId = `${nodeType}:${moduleName}.${className}.${methodName}`;
        if (!this.graph.hasNode(methodId)) return;

        const body = methodNode.body;
        if (!body || body.type !== 'BlockStatement') return;
        const qualified = `${moduleName}.${className}.${methodName}`;
        const counter = [0];
        const varDefs = {};

        this._processStatements(body.body, methodId, qualified, counter, varDefs, [methodId], sourceCode);
    }

    _processStatements(stmts, parentId, qualifiedPrefix, counter, varDefs, cfgPredecessors, sourceCode) {
        let currentPreds = [...cfgPredecessors];

        for (const stmt of stmts) {
            const stmtType = classifyStmt(stmt);
            const code = stmtCode(stmt, sourceCode);
            const line = stmt.loc?.start?.line || -1;
            const endLine = stmt.loc?.end?.line || line;

            const stmtId = `stmt:${qualifiedPrefix}:S${counter[0]}`;
            counter[0]++;

            // STATEMENT node
            this.graph.addNode(stmtId, 'STATEMENT', stmtType,
                `${qualifiedPrefix}:S${counter[0] - 1}`, {
                    statementType: stmtType,
                    code: code,
                    lineNumber: line,
                    endLineNumber: endLine,
                });
            this.stmtCount++;

            // AST_CHILD
            this.graph.addRelationship(parentId, stmtId, 'AST_CHILD', {
                ast_order: counter[0] - 1
            });

            // CFG_NEXT from predecessors
            for (const pred of currentPreds) {
                this.graph.addRelationship(pred, stmtId, 'CFG_NEXT');
                this.cfgEdgeCount++;
            }

            // DATA_FLOW
            this._processDataFlow(stmt, stmtId, varDefs);

            // Handle compound statements for CFG branching
            if (stmt.type === 'IfStatement') {
                currentPreds = this._processIf(stmt, stmtId, qualifiedPrefix, counter, varDefs, sourceCode);
            } else if (stmt.type === 'ForStatement' || stmt.type === 'ForInStatement' || stmt.type === 'ForOfStatement') {
                currentPreds = this._processLoop(stmt.body, stmtId, qualifiedPrefix, counter, varDefs, sourceCode);
            } else if (stmt.type === 'WhileStatement') {
                currentPreds = this._processLoop(stmt.body, stmtId, qualifiedPrefix, counter, varDefs, sourceCode);
            } else if (stmt.type === 'DoWhileStatement') {
                currentPreds = this._processDoWhile(stmt, stmtId, qualifiedPrefix, counter, varDefs, sourceCode);
            } else if (stmt.type === 'SwitchStatement') {
                currentPreds = this._processSwitch(stmt, stmtId, qualifiedPrefix, counter, varDefs, sourceCode);
            } else if (stmt.type === 'TryStatement') {
                currentPreds = this._processTry(stmt, stmtId, qualifiedPrefix, counter, varDefs, sourceCode);
            } else if (stmt.type === 'WithStatement') {
                const innerStmts = stmt.body.type === 'BlockStatement' ? stmt.body.body : [stmt.body];
                currentPreds = this._processStatements(innerStmts, stmtId, qualifiedPrefix, counter, varDefs, [stmtId], sourceCode);
            } else if (stmt.type === 'LabeledStatement') {
                const innerStmts = stmt.body.type === 'BlockStatement' ? stmt.body.body : [stmt.body];
                currentPreds = this._processStatements(innerStmts, stmtId, qualifiedPrefix, counter, varDefs, [stmtId], sourceCode);
            } else if (stmt.type === 'ReturnStatement' || stmt.type === 'ThrowStatement') {
                currentPreds = []; // terminal
            } else if (stmt.type === 'BreakStatement' || stmt.type === 'ContinueStatement') {
                currentPreds = [];
            } else {
                currentPreds = [stmtId];
            }
        }

        return currentPreds;
    }

    _processIf(ifStmt, stmtId, prefix, counter, varDefs, sourceCode) {
        const exits = [];
        // Then branch
        const thenStmts = ifStmt.consequent.type === 'BlockStatement'
            ? ifStmt.consequent.body : [ifStmt.consequent];
        exits.push(...this._processStatements(thenStmts, stmtId, prefix, counter, varDefs, [stmtId], sourceCode));
        // Else branch
        if (ifStmt.alternate) {
            const elseStmts = ifStmt.alternate.type === 'BlockStatement'
                ? ifStmt.alternate.body
                : ifStmt.alternate.type === 'IfStatement'
                    ? [ifStmt.alternate] // else-if chain
                    : [ifStmt.alternate];
            exits.push(...this._processStatements(elseStmts, stmtId, prefix, counter, varDefs, [stmtId], sourceCode));
        } else {
            exits.push(stmtId); // no else → if head is also an exit
        }
        return exits;
    }

    _processLoop(body, loopStmtId, prefix, counter, varDefs, sourceCode) {
        const bodyStmts = body.type === 'BlockStatement' ? body.body : [body];
        const bodyExits = this._processStatements(bodyStmts, loopStmtId, prefix, counter, varDefs, [loopStmtId], sourceCode);
        // Back edges: body exits → loop head
        for (const exitId of bodyExits) {
            this.graph.addRelationship(exitId, loopStmtId, 'CFG_NEXT', { backEdge: true });
            this.cfgEdgeCount++;
        }
        return [loopStmtId]; // loop head is exit (when condition is false)
    }

    _processDoWhile(stmt, stmtId, prefix, counter, varDefs, sourceCode) {
        const bodyStmts = stmt.body.type === 'BlockStatement' ? stmt.body.body : [stmt.body];
        const bodyExits = this._processStatements(bodyStmts, stmtId, prefix, counter, varDefs, [stmtId], sourceCode);
        // Back edges: body exits → loop head (do-while always re-evaluates)
        for (const exitId of bodyExits) {
            this.graph.addRelationship(exitId, stmtId, 'CFG_NEXT', { backEdge: true });
            this.cfgEdgeCount++;
        }
        return [stmtId];
    }

    _processSwitch(switchStmt, stmtId, prefix, counter, varDefs, sourceCode) {
        const exits = [];
        let hasDefault = false;
        for (const caseClause of switchStmt.cases) {
            if (caseClause.test === null) hasDefault = true;
            if (caseClause.consequent.length > 0) {
                exits.push(...this._processStatements(
                    caseClause.consequent, stmtId, prefix, counter, varDefs, [stmtId], sourceCode
                ));
            }
        }
        if (!hasDefault) exits.push(stmtId); // no default → switch head is also an exit
        return exits;
    }

    _processTry(tryStmt, stmtId, prefix, counter, varDefs, sourceCode) {
        const exits = [];
        // Try block
        exits.push(...this._processStatements(
            tryStmt.block.body, stmtId, prefix, counter, varDefs, [stmtId], sourceCode
        ));
        // Catch clause
        if (tryStmt.handler) {
            exits.push(...this._processStatements(
                tryStmt.handler.body.body, stmtId, prefix, counter, varDefs, [stmtId], sourceCode
            ));
        }
        // Finally
        if (tryStmt.finalizer) {
            const finallyExits = this._processStatements(
                tryStmt.finalizer.body, stmtId, prefix, counter, varDefs, [stmtId], sourceCode
            );
            return finallyExits; // finally overrides all exits
        }
        return exits;
    }

    _processDataFlow(stmt, stmtId, varDefs) {
        const reads = collectReads(stmt);
        for (const varName of reads) {
            const defStmtId = varDefs[varName];
            if (defStmtId) {
                this.graph.addRelationship(defStmtId, stmtId, 'DATA_FLOW', { variable: varName });
                this.dataFlowCount++;
            }
        }
        const writes = collectWrites(stmt);
        for (const varName of writes) {
            varDefs[varName] = stmtId;
        }
    }
}

function collectFiles(dir, extensions) {
    const files = [];
    
    function walk(currentDir) {
        const entries = fs.readdirSync(currentDir, { withFileTypes: true });
        for (const entry of entries) {
            const fullPath = path.join(currentDir, entry.name);
            if (entry.isDirectory()) {
                // Skip node_modules, build directories, etc.
                if (!['node_modules', 'dist', 'build', '.git', 'coverage'].includes(entry.name)) {
                    walk(fullPath);
                }
            } else if (entry.isFile() && extensions.some(ext => entry.name.endsWith(ext))) {
                files.push(fullPath);
            }
        }
    }
    
    walk(dir);
    return files;
}

function parseFile(filePath, rootDir, graph, cpgEnhancer) {
    const code = fs.readFileSync(filePath, 'utf-8');
    const relativePath = path.relative(rootDir, filePath);
    const moduleName = relativePath.replace(/\.[^.]+$/, '').replace(/\//g, '.');
    
    // Add file node
    const fileId = `file:${relativePath}`;
    graph.addNode(fileId, 'FILE', path.basename(filePath), relativePath, {
        path: relativePath,
        language: getLanguage(filePath)
    });
    
    // Add module node
    const moduleId = `module:${moduleName}`;
    graph.addNode(moduleId, 'MODULE', moduleName, moduleName);
    graph.addRelationship(moduleId, fileId, 'SOURCE_FILE');
    graph.internalModules.add(moduleName);

    let ast;
    try {
        ast = parser.parse(code, {
            sourceType: 'module',
            plugins: [
                'jsx',
                'typescript',
                'decorators-legacy',
                'classProperties',
                'classPrivateProperties',
                'classPrivateMethods',
                'exportDefaultFrom',
                'exportNamespaceFrom',
                'dynamicImport',
                'nullishCoalescingOperator',
                'optionalChaining',
                'asyncGenerators',
                'objectRestSpread'
            ]
        });
    } catch (e) {
        console.error(`Parse error in ${filePath}: ${e.message}`);
        return;
    }

    const state = {
        graph,
        moduleId,
        moduleName,
        filePath: relativePath,
        currentClass: null,
        currentFunction: null,
        functionStack: [],
        exports: new Set(),
        imports: new Map() // localName -> { source, importedName }
    };

    traverse(ast, {
        // Track imports
        ImportDeclaration(nodePath) {
            const source = nodePath.node.source.value;
            const isRelative = source.startsWith('.') || source.startsWith('/');
            
            for (const spec of nodePath.node.specifiers) {
                let localName, importedName;
                if (spec.type === 'ImportDefaultSpecifier') {
                    localName = spec.local.name;
                    importedName = 'default';
                } else if (spec.type === 'ImportNamespaceSpecifier') {
                    localName = spec.local.name;
                    importedName = '*';
                } else {
                    localName = spec.local.name;
                    importedName = spec.imported?.name || spec.local.name;
                }
                
                state.imports.set(localName, { source, importedName, isRelative });
                
                // Add import relationship for internal modules
                if (isRelative) {
                    const targetModule = resolveModulePath(source, state.filePath);
                    graph.addRelationship(state.moduleId, `module:${targetModule}`, 'IMPORTS', {
                        importedName,
                        localName
                    });
                }
            }
        },

        // Track exports
        ExportNamedDeclaration(nodePath) {
            if (nodePath.node.declaration) {
                const decl = nodePath.node.declaration;
                if (decl.type === 'FunctionDeclaration' && decl.id) {
                    state.exports.add(decl.id.name);
                } else if (decl.type === 'ClassDeclaration' && decl.id) {
                    state.exports.add(decl.id.name);
                } else if (decl.type === 'VariableDeclaration') {
                    for (const varDecl of decl.declarations) {
                        if (varDecl.id.type === 'Identifier') {
                            state.exports.add(varDecl.id.name);
                        }
                    }
                }
            }
        },

        ExportDefaultDeclaration(nodePath) {
            state.exports.add('default');
        },

        // Class declarations (including React class components)
        ClassDeclaration: {
            enter(nodePath) {
                const node = nodePath.node;
                const className = node.id?.name || 'AnonymousClass';
                const classId = `class:${state.moduleName}.${className}`;
                
                const isComponent = node.superClass && 
                    (node.superClass.name === 'Component' || 
                     node.superClass.name === 'PureComponent' ||
                     (node.superClass.type === 'MemberExpression' && 
                      node.superClass.property?.name === 'Component'));
                
                graph.addNode(classId, isComponent ? 'COMPONENT' : 'CLASS', className, 
                    `${state.moduleName}.${className}`, {
                        lineNumber: node.loc?.start?.line,
                        isReactComponent: isComponent
                    });
                
                graph.addRelationship(state.moduleId, classId, 'CONTAINS');
                
                if (node.superClass) {
                    const superName = node.superClass.name || 
                        (node.superClass.type === 'MemberExpression' ? 
                            `${node.superClass.object?.name}.${node.superClass.property?.name}` : 'Unknown');
                    const superId = `class:${superName}`;
                    graph.addNode(superId, 'CLASS', superName, superName);
                    graph.addRelationship(classId, superId, 'EXTENDS');
                }
                
                state.currentClass = classId;
            },
            exit() {
                state.currentClass = null;
            }
        },

        // Method definitions within classes
        ClassMethod: {
            enter(nodePath) {
                const node = nodePath.node;
                const methodName = node.key?.name || 'anonymous';
                const parentClass = state.currentClass;
                
                if (!parentClass) return;
                
                const methodId = `method:${parentClass.replace('class:', '')}.${methodName}`;
                const nodeType = node.kind === 'constructor' ? 'CONSTRUCTOR' : 'METHOD';
                
                graph.addNode(methodId, nodeType, methodName, methodId.replace('method:', ''), {
                    lineNumber: node.loc?.start?.line,
                    isAsync: node.async,
                    isStatic: node.static,
                    isGetter: node.kind === 'get',
                    isSetter: node.kind === 'set'
                });
                
                graph.addRelationship(parentClass, methodId, 'CONTAINS');
                state.functionStack.push(state.currentFunction);
                state.currentFunction = methodId;
            },
            exit() {
                state.currentFunction = state.functionStack.pop() || null;
            }
        },

        // Function declarations
        FunctionDeclaration: {
            enter(nodePath) {
                const node = nodePath.node;
                const funcName = node.id?.name || 'anonymous';
                const funcId = `function:${state.moduleName}.${funcName}`;
                
                // Check if it's a React component (PascalCase + returns JSX)
                const isComponent = /^[A-Z]/.test(funcName);
                // Check if it's a hook (starts with 'use')
                const isHook = /^use[A-Z]/.test(funcName);
                
                let nodeType = 'FUNCTION';
                if (isHook) nodeType = 'HOOK';
                else if (isComponent) nodeType = 'COMPONENT';
                else if (node.async) nodeType = 'ASYNC_FUNCTION';
                else if (node.generator) nodeType = 'GENERATOR';
                
                graph.addNode(funcId, nodeType, funcName, `${state.moduleName}.${funcName}`, {
                    lineNumber: node.loc?.start?.line,
                    isAsync: node.async,
                    isGenerator: node.generator,
                    paramCount: node.params.length
                });
                
                graph.addRelationship(state.moduleId, funcId, 'CONTAINS');
                
                if (state.exports.has(funcName)) {
                    graph.addRelationship(state.moduleId, funcId, 'EXPORTS');
                }
                
                state.functionStack.push(state.currentFunction);
                state.currentFunction = funcId;
            },
            exit() {
                state.currentFunction = state.functionStack.pop() || null;
            }
        },

        // Arrow functions assigned to variables (common in React)
        VariableDeclarator(nodePath) {
            const node = nodePath.node;
            if (node.init?.type !== 'ArrowFunctionExpression' && 
                node.init?.type !== 'FunctionExpression') return;
            if (node.id?.type !== 'Identifier') return;
            
            const funcName = node.id.name;
            const funcId = `function:${state.moduleName}.${funcName}`;
            
            const isComponent = /^[A-Z]/.test(funcName);
            const isHook = /^use[A-Z]/.test(funcName);
            
            let nodeType = 'ARROW_FUNCTION';
            if (isHook) nodeType = 'HOOK';
            else if (isComponent) nodeType = 'COMPONENT';
            else if (node.init.async) nodeType = 'ASYNC_FUNCTION';
            else if (node.init.generator) nodeType = 'GENERATOR';
            
            graph.addNode(funcId, nodeType, funcName, `${state.moduleName}.${funcName}`, {
                lineNumber: node.loc?.start?.line,
                isAsync: node.init.async,
                isArrow: node.init.type === 'ArrowFunctionExpression'
            });
            
            graph.addRelationship(state.moduleId, funcId, 'CONTAINS');
            
            if (state.exports.has(funcName)) {
                graph.addRelationship(state.moduleId, funcId, 'EXPORTS');
            }
        },

        ArrowFunctionExpression: {
            enter(nodePath) {
                const parent = nodePath.parent;
                if (parent?.type !== 'VariableDeclarator' || parent.id?.type !== 'Identifier') return;
                const funcName = parent.id.name;
                const funcId = `function:${state.moduleName}.${funcName}`;
                state.functionStack.push(state.currentFunction);
                state.currentFunction = funcId;
            },
            exit(nodePath) {
                const parent = nodePath.parent;
                if (parent?.type !== 'VariableDeclarator' || parent.id?.type !== 'Identifier') return;
                state.currentFunction = state.functionStack.pop() || null;
            }
        },

        FunctionExpression: {
            enter(nodePath) {
                const parent = nodePath.parent;
                if (parent?.type !== 'VariableDeclarator' || parent.id?.type !== 'Identifier') return;
                const funcName = parent.id.name;
                const funcId = `function:${state.moduleName}.${funcName}`;
                state.functionStack.push(state.currentFunction);
                state.currentFunction = funcId;
            },
            exit(nodePath) {
                const parent = nodePath.parent;
                if (parent?.type !== 'VariableDeclarator' || parent.id?.type !== 'Identifier') return;
                state.currentFunction = state.functionStack.pop() || null;
            }
        },

        // Call expressions (function calls)
        CallExpression(nodePath) {
            const node = nodePath.node;
            const caller = state.currentFunction || state.currentClass || state.moduleId;
            
            let calleeName;
            let memberObject = '';
            let memberProperty = '';
            if (node.callee.type === 'Identifier') {
                calleeName = node.callee.name;
            } else if (node.callee.type === 'MemberExpression') {
                memberObject = node.callee.object?.name || '';
                memberProperty = node.callee.property?.name || '';
                calleeName = memberObject ? `${memberObject}.${memberProperty}` : memberProperty;
            } else {
                return;
            }

            // Check if calling a hook
            const hookName = /^use[A-Z]/.test(memberProperty) ? memberProperty : calleeName;
            if (/^use[A-Z]/.test(hookName)) {
                const hookId = `hook:${hookName}`;
                graph.addNode(hookId, 'HOOK', hookName, hookName);
                graph.addRelationship(caller, hookId, 'USES_HOOK', {
                    lineNumber: node.loc?.start?.line
                });
                return;
            }

            if (memberObject && memberProperty) {
                const objectImport = state.imports.get(memberObject);
                if (objectImport && objectImport.isRelative) {
                    const targetModule = resolveModulePath(objectImport.source, state.filePath);
                    const targetFuncId = `function:${targetModule}.${memberProperty}`;
                    graph.addRelationship(caller, targetFuncId, 'CALLS', {
                        lineNumber: node.loc?.start?.line,
                        resolved: true
                    });
                    return;
                }
            }

            // Check if it's an imported function
            const importInfo = state.imports.get(calleeName);
            if (importInfo && importInfo.isRelative) {
                const targetModule = resolveModulePath(importInfo.source, state.filePath);
                const targetFuncId = `function:${targetModule}.${importInfo.importedName === 'default' ? calleeName : importInfo.importedName}`;
                graph.addRelationship(caller, targetFuncId, 'CALLS', {
                    lineNumber: node.loc?.start?.line,
                    resolved: true
                });
            } else if (!importInfo) {
                // Local function call
                const targetFuncId = `function:${state.moduleName}.${calleeName}`;
                graph.addRelationship(caller, targetFuncId, 'CALLS', {
                    lineNumber: node.loc?.start?.line,
                    resolved: true
                });
            }
        },

        // JSX elements (React component usage)
        JSXOpeningElement(nodePath) {
            const node = nodePath.node;
            let componentName;
            
            if (node.name.type === 'JSXIdentifier') {
                componentName = node.name.name;
            } else if (node.name.type === 'JSXMemberExpression') {
                componentName = `${node.name.object?.name}.${node.name.property?.name}`;
            } else {
                return;
            }

            // Only track PascalCase components (user-defined, not HTML elements)
            if (!/^[A-Z]/.test(componentName)) return;
            
            const caller = state.currentFunction || state.currentClass || state.moduleId;
            
            // Check if imported
            const importInfo = state.imports.get(componentName);
            let targetId;
            
            if (importInfo && importInfo.isRelative) {
                const targetModule = resolveModulePath(importInfo.source, state.filePath);
                targetId = `component:${targetModule}.${componentName}`;
            } else {
                targetId = `component:${componentName}`;
            }
            
            graph.addNode(targetId, 'COMPONENT', componentName, componentName);
            graph.addRelationship(caller, targetId, 'RENDERS', {
                lineNumber: node.loc?.start?.line
            });
            if (node.attributes && node.attributes.length > 0 && String(caller).startsWith('function:')) {
                graph.addRelationship(caller, targetId, 'PROP_DEPENDENCY');
            }
        }
    });

    // Second pass: CPG enhancement (statement-level CFG + data flow)
    if (cpgEnhancer) {
        cpgEnhancer.enhanceFile(ast, code, moduleName, relativePath);
    }
}

function resolveModulePath(importSource, currentFilePath) {
    // Simple resolution: convert relative imports to module names
    const dir = path.dirname(currentFilePath);
    const resolved = path.normalize(path.join(dir, importSource));
    return resolved.replace(/\.[^.]+$/, '').replace(/\//g, '.').replace(/^\./, '');
}

function getLanguage(filePath) {
    if (filePath.endsWith('.ts') || filePath.endsWith('.tsx')) return 'TypeScript';
    if (filePath.endsWith('.jsx')) return 'JSX';
    return 'JavaScript';
}

function main() {
    const args = process.argv.slice(2);
    if (args.length < 1) {
        console.error('Usage: node parse.js <directory> [--backend kuzu|neo4j|json] [--db-path <path>] [--clear] [--extensions .js,.jsx,.ts,.tsx]');
        process.exit(1);
    }

    const rootDir = path.resolve(args[0]);
    const repoName = path.basename(rootDir);
    let extensions = DEFAULT_EXTENSIONS;
    let backend = 'kuzu';
    let dbPath = null;
    let clearGraph = false;
    let neo4jUri = 'bolt://localhost:7687';
    let neo4jUser = 'neo4j';
    let neo4jPassword = '';
    let neo4jDatabase = 'neo4j';

    for (let i = 1; i < args.length; i++) {
        switch (args[i]) {
            case '--backend': backend = args[++i]; break;
            case '--db-path': dbPath = path.resolve(args[++i]); break;
            case '--clear': clearGraph = true; break;
            case '--extensions': extensions = args[++i].split(',').map(e => e.trim()); break;
            case '--neo4j-uri': neo4jUri = args[++i]; break;
            case '--neo4j-user': neo4jUser = args[++i]; break;
            case '--neo4j-password': neo4jPassword = args[++i]; break;
            case '--neo4j-database': neo4jDatabase = args[++i]; break;
        }
    }

    // --db-path must be a directory; always generate DB file name inside it
    let dbDir;
    if (!dbPath) {
        dbDir = path.resolve(process.cwd(), 'kuzu_db');
    } else {
        dbDir = dbPath;
    }
    if (!fs.existsSync(dbDir)) {
        fs.mkdirSync(dbDir, { recursive: true });
    }
    dbPath = path.resolve(dbDir, `${repoName}-db`);

    if (!fs.existsSync(rootDir)) {
        console.error(`Directory not found: ${rootDir}`);
        process.exit(1);
    }

    const graph = new CodeGraphBuilder();
    const cpgEnhancer = new CpgEnhancer(graph);
    const files = collectFiles(rootDir, extensions);
    
    console.error(`Found ${files.length} JavaScript/TypeScript files to parse`);

    for (const file of files) {
        parseFile(file, rootDir, graph, cpgEnhancer);
    }

    console.error(`CPG: ${cpgEnhancer.stmtCount} statement nodes, ${cpgEnhancer.cfgEdgeCount} CFG edges, ${cpgEnhancer.dataFlowCount} data-flow edges`);

    // Filter relationships to only include internal targets
    const filteredRelationships = graph.relationships.filter(rel => {
        if (rel.type === 'CALLS' || rel.type === 'RENDERS' || rel.type === 'PROP_DEPENDENCY') {
            return graph.nodes[rel.targetId] !== undefined;
        }
        return true;
    });
    graph.relationships = filteredRelationships;

    const graphJson = graph.toJSON();

    if (backend === 'json') {
        console.log(JSON.stringify(graphJson, null, 2));
        return;
    }

    // Write to graph database
    const { createStore } = require('./store');
    (async () => {
        const store = createStore({
            backend, dbPath, neo4jUri, neo4jUser, neo4jPassword, neo4jDatabase
        });
        try {
            await store.initSchema();
            if (clearGraph) await store.clear();
            await store.save(graphJson);
            const summary = await store.summary();
            console.error(summary);
        } finally {
            await store.close();
        }
    })().catch(err => {
        console.error('Error writing to graph database:', err.message);
        process.exit(1);
    });
}

main();
