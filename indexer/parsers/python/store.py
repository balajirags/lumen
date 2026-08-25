"""
Graph store implementations for Python parser.
Supports KuzuDB (embedded) and Neo4j backends.
"""

import os

_KUZU_MAX_BUFFER_BYTES = 2 * 1024 ** 3  # 2 GB ceiling
_KUZU_MIN_BUFFER_BYTES = 512 * 1024 ** 2  # 512 MB floor

def _kuzu_buffer_pool_size() -> int:
    try:
        total = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
        if total > 0:
            return max(_KUZU_MIN_BUFFER_BYTES, min(int(total * 0.8), _KUZU_MAX_BUFFER_BYTES))
    except Exception:
        pass
    return _KUZU_MIN_BUFFER_BYTES


def with_normalized_rel_props(definition):
    idx = definition.rfind(')')
    if idx == -1:
        return definition
    return definition[:idx] + ", language STRING, kind STRING, normKind STRING" + definition[idx:]

# ─── KuzuDB Store ───────────────────────────────────────────────────────────

NODE_TYPES = [
    'Package', 'Class', 'Interface', 'Enum', 'Record', 'AnnotationType',
    'Method', 'Constructor', 'Field', 'Parameter', 'File', 'Statement',
    'Module', 'Function', 'ArrowFunction', 'Component', 'Hook', 'JSXElement',
    'Decorator', 'Generator', 'AsyncFunction', 'Comprehension',
    'DataClass', 'SealedClass', 'SealedInterface', 'ObjectDecl',
    'CompanionObject', 'ExtensionFunction', 'SuspendFunction',
    'Property', 'Lambda', 'InitBlock', 'TypeAlias',
]

REL_DEFINITIONS = [
    "CREATE REL TABLE IF NOT EXISTS CONTAINS (FROM Package TO Class, FROM Package TO Interface, FROM Package TO Enum, FROM Package TO Record, FROM Class TO Method, FROM Class TO Constructor, FROM Class TO Field, FROM Interface TO Method, FROM Interface TO Field, FROM Module TO Function, FROM Module TO ArrowFunction, FROM Module TO Component, FROM Module TO Hook, FROM Module TO Class, FROM Class TO Function, FROM Class TO ArrowFunction, FROM DataClass TO Method, FROM DataClass TO Property, FROM SealedClass TO Method, FROM SealedClass TO Property, FROM ObjectDecl TO Method, FROM ObjectDecl TO Property, FROM CompanionObject TO Method, FROM CompanionObject TO Property, FROM Class TO Property, FROM Class TO InitBlock, FROM Package TO TypeAlias, FROM Package TO ObjectDecl)",
    "CREATE REL TABLE IF NOT EXISTS EXTENDS (FROM Class TO Class, FROM Interface TO Interface, FROM Component TO Class, FROM DataClass TO Class, FROM SealedClass TO Class)",
    "CREATE REL TABLE IF NOT EXISTS IMPLEMENTS (FROM Class TO Interface, FROM DataClass TO Interface, FROM SealedClass TO Interface, FROM ObjectDecl TO Interface)",
    "CREATE REL TABLE IF NOT EXISTS CALLS (FROM Method TO Method, FROM Constructor TO Method, FROM Function TO Function, FROM Function TO Method, FROM ArrowFunction TO Function, FROM ArrowFunction TO Method, FROM Component TO Function, FROM Hook TO Function, FROM AsyncFunction TO Function, FROM ExtensionFunction TO Method, FROM ExtensionFunction TO Function, FROM SuspendFunction TO Method, FROM SuspendFunction TO Function, FROM Lambda TO Method, FROM Lambda TO Function, FROM InitBlock TO Method, lineNumber INT64, resolved BOOLEAN)",
    "CREATE REL TABLE IF NOT EXISTS RETURNS (FROM Method TO Class, FROM Method TO Interface, FROM Method TO Enum, FROM Method TO Record, FROM ExtensionFunction TO Class, FROM SuspendFunction TO Class)",
    "CREATE REL TABLE IF NOT EXISTS HAS_PARAMETER (FROM Method TO Parameter, FROM Constructor TO Parameter, FROM Function TO Parameter, FROM ArrowFunction TO Parameter, FROM ExtensionFunction TO Parameter, FROM SuspendFunction TO Parameter, FROM Lambda TO Parameter)",
    "CREATE REL TABLE IF NOT EXISTS OF_TYPE (FROM Field TO Class, FROM Field TO Interface, FROM Field TO Enum, FROM Field TO Record, FROM Parameter TO Class, FROM Parameter TO Interface, FROM Parameter TO Enum, FROM Parameter TO Record, FROM Property TO Class, FROM Property TO Interface)",
    "CREATE REL TABLE IF NOT EXISTS HAS_ANNOTATION (FROM Class TO AnnotationType, FROM Interface TO AnnotationType, FROM Method TO AnnotationType, FROM Constructor TO AnnotationType, FROM Field TO AnnotationType, FROM Property TO AnnotationType, FROM ExtensionFunction TO AnnotationType, value STRING)",
    "CREATE REL TABLE IF NOT EXISTS OVERRIDES (FROM Method TO Method, FROM ExtensionFunction TO Method)",
    "CREATE REL TABLE IF NOT EXISTS THROWS (FROM Method TO Class)",
    "CREATE REL TABLE IF NOT EXISTS SOURCE_FILE (FROM Class TO File, FROM Interface TO File, FROM Enum TO File, FROM Record TO File, FROM AnnotationType TO File, FROM Module TO File, FROM Component TO File, FROM Function TO File, FROM DataClass TO File, FROM SealedClass TO File, FROM SealedInterface TO File, FROM ObjectDecl TO File, FROM ExtensionFunction TO File)",
    "CREATE REL TABLE IF NOT EXISTS AST_CHILD (FROM Method TO Statement, FROM Constructor TO Statement, FROM Statement TO Statement, FROM Function TO Statement, FROM ArrowFunction TO Statement, FROM AsyncFunction TO Statement, FROM Generator TO Statement, ast_order INT64)",
    "CREATE REL TABLE IF NOT EXISTS CFG_NEXT (FROM Method TO Statement, FROM Constructor TO Statement, FROM Statement TO Statement, FROM Function TO Statement, FROM AsyncFunction TO Statement, FROM Generator TO Statement, backEdge BOOLEAN)",
    "CREATE REL TABLE IF NOT EXISTS DATA_FLOW (FROM Statement TO Statement, variable STRING)",
    "CREATE REL TABLE IF NOT EXISTS IMPORTS (FROM Module TO Module, importedName STRING, localName STRING)",
    "CREATE REL TABLE IF NOT EXISTS EXPORTS (FROM Module TO Function, FROM Module TO ArrowFunction, FROM Module TO Component, FROM Module TO Class)",
    "CREATE REL TABLE IF NOT EXISTS RENDERS (FROM Component TO Component, FROM Function TO Component, FROM ArrowFunction TO Component, lineNumber INT64)",
    "CREATE REL TABLE IF NOT EXISTS USES_HOOK (FROM Component TO Hook, FROM Function TO Hook, FROM ArrowFunction TO Hook, lineNumber INT64)",
    "CREATE REL TABLE IF NOT EXISTS PROP_DEPENDENCY (FROM Component TO Component)",
    "CREATE REL TABLE IF NOT EXISTS DECORATES (FROM Decorator TO Function, FROM Decorator TO Method, FROM Decorator TO Class, FROM Decorator TO AsyncFunction)",
    "CREATE REL TABLE IF NOT EXISTS YIELDS (FROM Generator TO Class)",
    "CREATE REL TABLE IF NOT EXISTS EXTENSION_OF (FROM ExtensionFunction TO Class, FROM ExtensionFunction TO Interface, FROM ExtensionFunction TO DataClass)",
    "CREATE REL TABLE IF NOT EXISTS DELEGATES_TO (FROM Property TO Class, FROM Property TO Interface)",
    "CREATE REL TABLE IF NOT EXISTS SEALED_SUBTYPE (FROM SealedClass TO Class, FROM SealedClass TO DataClass, FROM SealedClass TO ObjectDecl, FROM SealedInterface TO Class, FROM SealedInterface TO Interface)",
    "CREATE REL TABLE IF NOT EXISTS COMPANION_OF (FROM CompanionObject TO Class, FROM CompanionObject TO DataClass, FROM CompanionObject TO SealedClass)",
    "CREATE REL TABLE IF NOT EXISTS SUSPENDS (FROM SuspendFunction TO SuspendFunction, FROM SuspendFunction TO Method)",
]

NODE_TYPE_MAP = {
    'PACKAGE': 'Package', 'CLASS': 'Class', 'INTERFACE': 'Interface',
    'ENUM': 'Enum', 'RECORD': 'Record', 'ANNOTATION_TYPE': 'AnnotationType',
    'METHOD': 'Method', 'CONSTRUCTOR': 'Constructor', 'FIELD': 'Field',
    'PARAMETER': 'Parameter', 'FILE': 'File', 'STATEMENT': 'Statement',
    'MODULE': 'Module', 'FUNCTION': 'Function', 'ARROW_FUNCTION': 'ArrowFunction',
    'COMPONENT': 'Component', 'HOOK': 'Hook', 'JSX_ELEMENT': 'JSXElement',
    'DECORATOR': 'Decorator', 'GENERATOR': 'Generator',
    'ASYNC_FUNCTION': 'AsyncFunction', 'COMPREHENSION': 'Comprehension',
}


def node_type_to_table(node_type):
    if not node_type:
        return None
    return NODE_TYPE_MAP.get(node_type.upper(), node_type)


def _escape(s):
    if s is None:
        return ''
    return str(s).replace('\\', '\\\\').replace("'", "\\'")


def infer_language(_node_type):
    return 'python'


def infer_norm_kind(node_type):
    normalized = str(node_type or '').upper()
    if normalized in {'PACKAGE', 'MODULE'}:
        return 'CodeUnit'
    if normalized in {'METHOD', 'CONSTRUCTOR', 'FUNCTION', 'ARROW_FUNCTION', 'ASYNC_FUNCTION', 'GENERATOR', 'EXTENSION_FUNCTION', 'SUSPEND_FUNCTION', 'LAMBDA'}:
        return 'Callable'
    if normalized in {'CLASS', 'INTERFACE', 'ENUM', 'RECORD', 'DATA_CLASS', 'SEALED_CLASS', 'SEALED_INTERFACE', 'OBJECT_DECL', 'TYPE_ALIAS'}:
        return 'TypeLike'
    if normalized in {'FIELD', 'PROPERTY', 'PARAMETER'}:
        return 'DataMember'
    if normalized == 'FILE':
        return 'SourceFile'
    if normalized in {'DECORATOR', 'ANNOTATION_TYPE'}:
        return 'AnnotationLike'
    if normalized == 'STATEMENT':
        return 'Statement'
    return 'CodeElement'


def infer_rel_norm_kind(rel_type):
    normalized = str(rel_type or '').upper()
    if normalized in {'CONTAINS', 'SOURCE_FILE'}:
        return 'Contains'
    if normalized == 'CALLS':
        return 'Calls'
    if normalized in {'IMPORTS', 'EXPORTS', 'PROP_DEPENDENCY', 'DELEGATES_TO'}:
        return 'DependsOn'
    if normalized in {'EXTENDS', 'IMPLEMENTS', 'OVERRIDES', 'SEALED_SUBTYPE', 'COMPANION_OF', 'EXTENSION_OF'}:
        return 'TypeRelation'
    if normalized in {'HAS_PARAMETER', 'OF_TYPE', 'RETURNS', 'THROWS', 'DECORATES', 'HAS_ANNOTATION', 'YIELDS', 'SUSPENDS'}:
        return 'SemanticRelation'
    if normalized in {'AST_CHILD', 'CFG_NEXT', 'DATA_FLOW'}:
        return 'FlowRelation'
    return 'CodeRelation'


class KuzuStore:
    def __init__(self, db_path):
        import kuzu
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = kuzu.Database(self.db_path, buffer_pool_size=_kuzu_buffer_pool_size())
        self.conn = kuzu.Connection(self.db)
        print(f"KuzuDB: opening database at {self.db_path}", file=__import__('sys').stderr)

    def init_schema(self):
        for t in NODE_TYPES:
            self._query(
                f"CREATE NODE TABLE IF NOT EXISTS {t} "
                f"(id STRING, name STRING, qualifiedName STRING, "
                f"visibility STRING, isAbstract BOOLEAN, isStatic BOOLEAN, "
                f"isFinal BOOLEAN, returnType STRING, lineNumber INT64, "
                f"endLineNumber INT64, type STRING, external BOOLEAN, "
                f"path STRING, statementType STRING, code STRING, "
                f"language STRING, kind STRING, normKind STRING, "
                f"PRIMARY KEY (id))"
            )
        for rel_def in REL_DEFINITIONS:
            self._query(with_normalized_rel_props(rel_def))

    def clear(self):
        rel_types = [
            'CONTAINS', 'EXTENDS', 'IMPLEMENTS', 'CALLS', 'RETURNS',
            'HAS_PARAMETER', 'OF_TYPE', 'HAS_ANNOTATION', 'OVERRIDES', 'THROWS',
            'SOURCE_FILE', 'AST_CHILD', 'CFG_NEXT', 'DATA_FLOW',
            'IMPORTS', 'EXPORTS', 'RENDERS', 'USES_HOOK', 'PROP_DEPENDENCY',
            'DECORATES', 'YIELDS',
            'EXTENSION_OF', 'DELEGATES_TO', 'SEALED_SUBTYPE', 'COMPANION_OF', 'SUSPENDS',
        ]
        for r in rel_types:
            self._query(f"DROP TABLE {r}")
        for t in NODE_TYPES:
            self._query(f"DROP TABLE {t}")
        self.init_schema()

    def save(self, graph_json):
        node_table_map = {}

        # Insert nodes
        for node in graph_json['nodes']:
            table = node_type_to_table(node['type'])
            node_table_map[node['id']] = table

            cypher = (
                f"CREATE (n:{table} {{id: '{_escape(node['id'])}', "
                f"name: '{_escape(node['name'])}', "
                f"qualifiedName: '{_escape(node['qualifiedName'])}'"
            )

            props = {
                **node.get('properties', {}),
                'language': node.get('properties', {}).get('language', infer_language(node['type'])),
                'kind': table,
                'normKind': node.get('properties', {}).get('normKind', infer_norm_kind(node['type'])),
            }
            valid_keys = {'visibility', 'isAbstract', 'isStatic', 'isFinal',
                          'returnType', 'lineNumber', 'endLineNumber',
                          'type', 'external', 'path',
                          'statementType', 'code', 'language', 'kind', 'normKind'}
            for key, val in props.items():
                if key in valid_keys:
                    if isinstance(val, bool):
                        cypher += f", {key}: {'true' if val else 'false'}"
                    elif isinstance(val, (int, float)):
                        cypher += f", {key}: {val}"
                    else:
                        cypher += f", {key}: '{_escape(str(val))}'"

            cypher += '})'
            self._query(cypher)

        # Insert relationships
        for rel in graph_json['relationships']:
            src_table = node_table_map.get(rel['sourceId'])
            tgt_table = node_table_map.get(rel['targetId'])
            if not src_table or not tgt_table:
                continue

            rel_props = ''
            props = {
                **rel.get('properties', {}),
                'language': infer_language(rel.get('type')),
                'kind': rel.get('type'),
                'normKind': infer_rel_norm_kind(rel.get('type')),
            }
            if props:
                parts = []
                for k, v in props.items():
                    if isinstance(v, bool):
                        parts.append(f"{k}: {'true' if v else 'false'}")
                    elif isinstance(v, (int, float)):
                        parts.append(f"{k}: {v}")
                    else:
                        parts.append(f"{k}: '{_escape(str(v))}'")
                rel_props = ' {' + ', '.join(parts) + '}'

            cypher = (
                f"MATCH (a:{src_table} {{id: '{_escape(rel['sourceId'])}'}}),"
                f" (b:{tgt_table} {{id: '{_escape(rel['targetId'])}'}}) "
                f"CREATE (a)-[:{rel['type']}{rel_props}]->(b)"
            )
            self._query(cypher)

    def summary(self):
        total = 0
        tables = ['Module', 'Function', 'Class', 'Method', 'File',
                   'Decorator', 'Generator', 'AsyncFunction', 'Statement',
                   'Constructor']
        for t in tables:
            try:
                result = self.conn.execute(f"MATCH (n:{t}) RETURN count(n) AS c")
                while result.has_next():
                    row = result.get_next()
                    total += row[0]
            except Exception:
                pass
        return f"KuzuDB graph: {total} nodes"

    def close(self):
        pass

    def _query(self, cypher):
        try:
            self.conn.execute(cypher)
        except Exception:
            pass


# ─── Neo4j Store ────────────────────────────────────────────────────────────

class Neo4jStore:
    def __init__(self, uri, username, password, database='neo4j'):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self.database = database
        print(f"Neo4j: connecting to {uri}, database '{self.database}'",
              file=__import__('sys').stderr)

    def init_schema(self):
        labels = [
            'Package', 'Class', 'Interface', 'Enum', 'Record', 'AnnotationType',
            'Method', 'Constructor', 'Field', 'Parameter', 'File', 'Statement',
            'Module', 'Function', 'Decorator', 'Generator', 'AsyncFunction',
        ]
        with self.driver.session(database=self.database) as session:
            for label in labels:
                session.run(
                    f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                )

    def clear(self):
        with self.driver.session(database=self.database) as session:
            session.run('MATCH (n) DETACH DELETE n')

    def save(self, graph_json):
        # Group nodes by label
        nodes_by_label = {}
        for node in graph_json['nodes']:
            label = node_type_to_table(node['type'])
            if label not in nodes_by_label:
                nodes_by_label[label] = []
            props = {'id': node['id'], 'name': node['name'],
                     'qualifiedName': node['qualifiedName']}
            props.update(node.get('properties', {}))
            nodes_by_label[label].append(props)

        with self.driver.session(database=self.database) as session:
            for label, batch in nodes_by_label.items():
                for i in range(0, len(batch), 500):
                    chunk = batch[i:i+500]
                    session.run(
                        f"UNWIND $batch AS row MERGE (n:{label} {{id: row.id}}) SET n += row",
                        batch=chunk,
                    )

            # Group relationships
            node_type_lookup = {n['id']: n['type'] for n in graph_json['nodes']}
            rels_by_key = {}
            for rel in graph_json['relationships']:
                src_type = node_type_lookup.get(rel['sourceId'])
                tgt_type = node_type_lookup.get(rel['targetId'])
                if not src_type or not tgt_type:
                    continue
                src_label = node_type_to_table(src_type)
                tgt_label = node_type_to_table(tgt_type)
                key = f"{src_label}|{tgt_label}|{rel['type']}"
                if key not in rels_by_key:
                    rels_by_key[key] = {
                        'src_label': src_label, 'tgt_label': tgt_label,
                        'type': rel['type'], 'batch': []
                    }
                entry = {'sourceId': rel['sourceId'], 'targetId': rel['targetId']}
                entry.update(rel.get('properties', {}))
                rels_by_key[key]['batch'].append(entry)

            for info in rels_by_key.values():
                for i in range(0, len(info['batch']), 500):
                    chunk = info['batch'][i:i+500]
                    session.run(
                        f"UNWIND $batch AS row "
                        f"MATCH (a:{info['src_label']} {{id: row.sourceId}}), "
                        f"(b:{info['tgt_label']} {{id: row.targetId}}) "
                        f"CREATE (a)-[:{info['type']}]->(b)",
                        batch=chunk,
                    )

    def summary(self):
        with self.driver.session(database=self.database) as session:
            result = session.run('MATCH (n) RETURN count(n) AS c')
            count = result.single()['c']
            return f"Neo4j graph: {count} nodes"

    def close(self):
        self.driver.close()


# ─── Factory ────────────────────────────────────────────────────────────────

def create_store(backend, db_path=None, neo4j_uri=None, neo4j_user=None,
                 neo4j_password=None, neo4j_database=None):
    if backend == 'neo4j':
        return Neo4jStore(
            uri=neo4j_uri or 'bolt://localhost:7687',
            username=neo4j_user or 'neo4j',
            password=neo4j_password or '',
            database=neo4j_database or 'neo4j',
        )
    return KuzuStore(db_path or 'kuzu_db/default-db')
