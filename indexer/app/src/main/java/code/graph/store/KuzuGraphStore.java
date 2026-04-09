package code.graph.store;

import code.graph.model.*;
import com.kuzudb.*;

import java.util.Map;
import java.util.stream.Collectors;

/**
 * Persists a CodeGraph to an embedded KuzuDB database.
 */
public class KuzuGraphStore implements GraphStore {

    private final Database database;
    private final Connection connection;

    public KuzuGraphStore(String dbPath) {
        System.out.printf("KuzuDB: opening database at %s%n", java.nio.file.Path.of(dbPath).toAbsolutePath().normalize());
        this.database = new Database(dbPath);
        this.connection = new Connection(database);
    }

    @Override
    public void initSchema() {
        // Create node tables for each type.
        // WORKFLOW and DOMAIN have custom schemas and are created separately below.
        for (NodeType type : NodeType.values()) {
            if (type == NodeType.WORKFLOW || type == NodeType.DOMAIN) continue;
            String tableName = nodeTypeToTable(type);
            try {
                connection.query(String.format(
                        "CREATE NODE TABLE IF NOT EXISTS %s (id STRING, name STRING, qualifiedName STRING, " +
                                "visibility STRING, isAbstract BOOLEAN, isStatic BOOLEAN, isFinal BOOLEAN, " +
                                "returnType STRING, lineNumber INT64, type STRING, external BOOLEAN, path STRING, " +
                                "language STRING, kind STRING, normKind STRING, " +
                                "PRIMARY KEY (id))",
                        tableName));
            } catch (Exception e) {
                // Table creation may fail if already exists with different schema
            }
        }

        // Create relationship tables
        String[] relDefs = {
                // Core structural relationships
                "CREATE REL TABLE IF NOT EXISTS CONTAINS (FROM Package TO Class, FROM Package TO Interface, FROM Package TO Enum, FROM Package TO Record, FROM Class TO Method, FROM Class TO Constructor, FROM Class TO Field, FROM Interface TO Method, FROM Interface TO Field, FROM Module TO Function, FROM Module TO ArrowFunction, FROM Module TO AsyncFunction, FROM Module TO Generator, FROM Module TO Component, FROM Module TO Hook, FROM Module TO Class, FROM Class TO Function, FROM Class TO ArrowFunction, FROM DataClass TO Method, FROM DataClass TO Property, FROM SealedClass TO Method, FROM SealedClass TO Property, FROM ObjectDecl TO Method, FROM ObjectDecl TO Property, FROM CompanionObject TO Method, FROM CompanionObject TO Property, FROM Class TO Property, FROM Class TO InitBlock, FROM Package TO TypeAlias, FROM Package TO ObjectDecl)",
                "CREATE REL TABLE IF NOT EXISTS EXTENDS (FROM Class TO Class, FROM Interface TO Interface, FROM Component TO Class, FROM DataClass TO Class, FROM SealedClass TO Class)",
                "CREATE REL TABLE IF NOT EXISTS IMPLEMENTS (FROM Class TO Interface, FROM DataClass TO Interface, FROM SealedClass TO Interface, FROM ObjectDecl TO Interface)",
                "CREATE REL TABLE IF NOT EXISTS CALLS (FROM Method TO Method, FROM Constructor TO Method, FROM Function TO Function, FROM Function TO Method, FROM Function TO ArrowFunction, FROM Function TO AsyncFunction, FROM ArrowFunction TO Function, FROM ArrowFunction TO Method, FROM ArrowFunction TO AsyncFunction, FROM ArrowFunction TO ArrowFunction, FROM Component TO Function, FROM Component TO ArrowFunction, FROM Component TO AsyncFunction, FROM Component TO Method, FROM Hook TO Function, FROM Hook TO AsyncFunction, FROM AsyncFunction TO Function, FROM AsyncFunction TO AsyncFunction, FROM AsyncFunction TO Method, FROM AsyncFunction TO ArrowFunction, FROM ExtensionFunction TO Method, FROM ExtensionFunction TO Function, FROM SuspendFunction TO Method, FROM SuspendFunction TO Function, FROM Lambda TO Method, FROM Lambda TO Function, FROM InitBlock TO Method, lineNumber INT64, confidence DOUBLE, reason STRING)",
                "CREATE REL TABLE IF NOT EXISTS RETURNS (FROM Method TO Class, FROM Method TO Interface, FROM Method TO Enum, FROM Method TO Record, FROM ExtensionFunction TO Class, FROM SuspendFunction TO Class)",
                "CREATE REL TABLE IF NOT EXISTS HAS_PARAMETER (FROM Method TO Parameter, FROM Constructor TO Parameter, FROM Function TO Parameter, FROM ArrowFunction TO Parameter, FROM ExtensionFunction TO Parameter, FROM SuspendFunction TO Parameter, FROM Lambda TO Parameter)",
                "CREATE REL TABLE IF NOT EXISTS OF_TYPE (FROM Field TO Class, FROM Field TO Interface, FROM Field TO Enum, FROM Field TO Record, FROM Parameter TO Class, FROM Parameter TO Interface, FROM Parameter TO Enum, FROM Parameter TO Record, FROM Property TO Class, FROM Property TO Interface)",
                "CREATE REL TABLE IF NOT EXISTS HAS_ANNOTATION (FROM Class TO AnnotationType, FROM Interface TO AnnotationType, FROM Method TO AnnotationType, FROM Constructor TO AnnotationType, FROM Field TO AnnotationType, FROM Property TO AnnotationType, FROM ExtensionFunction TO AnnotationType, value STRING)",
                "CREATE REL TABLE IF NOT EXISTS OVERRIDES (FROM Method TO Method, FROM ExtensionFunction TO Method)",
                "CREATE REL TABLE IF NOT EXISTS THROWS (FROM Method TO Class)",
                // CPG-specific relationship tables
                "CREATE REL TABLE IF NOT EXISTS SOURCE_FILE (FROM Class TO File, FROM Interface TO File, FROM Enum TO File, FROM Record TO File, FROM AnnotationType TO File, FROM Module TO File, FROM Component TO File, FROM Function TO File, FROM ArrowFunction TO File, FROM AsyncFunction TO File, FROM Generator TO File, FROM Hook TO File, FROM DataClass TO File, FROM SealedClass TO File, FROM SealedInterface TO File, FROM ObjectDecl TO File, FROM ExtensionFunction TO File)",
                "CREATE REL TABLE IF NOT EXISTS AST_CHILD (FROM Method TO Statement, FROM Constructor TO Statement, FROM Statement TO Statement, FROM Function TO Statement, FROM ArrowFunction TO Statement, FROM ExtensionFunction TO Statement, FROM SuspendFunction TO Statement, FROM InitBlock TO Statement, ast_order INT64)",
                "CREATE REL TABLE IF NOT EXISTS CFG_NEXT (FROM Method TO Statement, FROM Constructor TO Statement, FROM Statement TO Statement, FROM Function TO Statement, FROM ExtensionFunction TO Statement, FROM SuspendFunction TO Statement, FROM InitBlock TO Statement, backEdge BOOLEAN)",
                "CREATE REL TABLE IF NOT EXISTS DATA_FLOW (FROM Statement TO Statement, variable STRING)",
                // JavaScript/React specific
                "CREATE REL TABLE IF NOT EXISTS IMPORTS (FROM Module TO Module, importedName STRING, localName STRING)",
                "CREATE REL TABLE IF NOT EXISTS EXPORTS (FROM Module TO Function, FROM Module TO ArrowFunction, FROM Module TO Component, FROM Module TO Class)",
                "CREATE REL TABLE IF NOT EXISTS RENDERS (FROM Component TO Component, FROM Function TO Component, FROM ArrowFunction TO Component, lineNumber INT64)",
                "CREATE REL TABLE IF NOT EXISTS USES_HOOK (FROM Component TO Hook, FROM Function TO Hook, FROM ArrowFunction TO Hook, lineNumber INT64)",
                "CREATE REL TABLE IF NOT EXISTS PROP_DEPENDENCY (FROM Component TO Component)",
                // Python specific
                "CREATE REL TABLE IF NOT EXISTS DECORATES (FROM Decorator TO Function, FROM Decorator TO Method, FROM Decorator TO Class, FROM Decorator TO AsyncFunction)",
                "CREATE REL TABLE IF NOT EXISTS YIELDS (FROM Generator TO Class)",
                // Kotlin specific
                "CREATE REL TABLE IF NOT EXISTS EXTENSION_OF (FROM ExtensionFunction TO Class, FROM ExtensionFunction TO Interface, FROM ExtensionFunction TO DataClass)",
                "CREATE REL TABLE IF NOT EXISTS DELEGATES_TO (FROM Property TO Class, FROM Property TO Interface)",
                "CREATE REL TABLE IF NOT EXISTS SEALED_SUBTYPE (FROM SealedClass TO Class, FROM SealedClass TO DataClass, FROM SealedClass TO ObjectDecl, FROM SealedInterface TO Class, FROM SealedInterface TO Interface)",
                "CREATE REL TABLE IF NOT EXISTS COMPANION_OF (FROM CompanionObject TO Class, FROM CompanionObject TO DataClass, FROM CompanionObject TO SealedClass)",
                "CREATE REL TABLE IF NOT EXISTS SUSPENDS (FROM SuspendFunction TO SuspendFunction, FROM SuspendFunction TO Method)",
                // Post-processing relationship tables
                "CREATE REL TABLE IF NOT EXISTS WORKFLOW_STEP (FROM Method TO Workflow, FROM Function TO Workflow, FROM AsyncFunction TO Workflow, FROM ArrowFunction TO Workflow, FROM Component TO Workflow, step INT64)",
                "CREATE REL TABLE IF NOT EXISTS IN_DOMAIN (FROM Class TO Domain, FROM Interface TO Domain, FROM Method TO Domain, FROM Component TO Domain, FROM Module TO Domain, FROM AsyncFunction TO Domain, FROM Function TO Domain, FROM ArrowFunction TO Domain)"
        };

        // Post-processing node tables must be created BEFORE the rel tables that reference them
        // (WORKFLOW_STEP references Workflow, IN_DOMAIN references Domain).
        // qualifiedName is included because the generic save() always writes it.
        String[] abstractionNodeDefs = {
                "CREATE NODE TABLE IF NOT EXISTS Workflow (id STRING, name STRING, qualifiedName STRING, entryPointId STRING, terminalId STRING, stepCount INT64, type STRING, httpMethod STRING, httpPath STRING, language STRING, kind STRING, normKind STRING, PRIMARY KEY(id))",
                "CREATE NODE TABLE IF NOT EXISTS Domain (id STRING, name STRING, qualifiedName STRING, heuristicLabel STRING, cohesion DOUBLE, memberCount INT64, language STRING, kind STRING, normKind STRING, PRIMARY KEY(id))"
        };

        for (String nodeDef : abstractionNodeDefs) {
            try {
                connection.query(nodeDef);
            } catch (Exception e) {
                System.err.printf("Warning: Could not create abstraction node table: %s%n", e.getMessage());
            }
        }

        for (String relDef : relDefs) {
            try {
                connection.query(withNormalizedRelProps(relDef));
            } catch (Exception e) {
                System.err.printf("Warning: Could not create relationship: %s%n", e.getMessage());
            }
        }
    }

    @Override
    public void clear() {
        // Drop and recreate is the simplest approach for KuzuDB
        for (String relType : new String[]{"CONTAINS", "EXTENDS", "IMPLEMENTS", "CALLS", "RETURNS",
                "HAS_PARAMETER", "OF_TYPE", "HAS_ANNOTATION", "OVERRIDES", "THROWS",
                "SOURCE_FILE", "AST_CHILD", "CFG_NEXT", "DATA_FLOW",
                "IMPORTS", "EXPORTS", "RENDERS", "USES_HOOK", "PROP_DEPENDENCY",
                "DECORATES", "YIELDS",
                "EXTENSION_OF", "DELEGATES_TO", "SEALED_SUBTYPE", "COMPANION_OF", "SUSPENDS",
                "WORKFLOW_STEP", "IN_DOMAIN"}) {
            try {
                connection.query("DROP TABLE " + relType);
            } catch (Exception ignored) {
            }
        }
        for (NodeType type : NodeType.values()) {
            try {
                connection.query("DROP TABLE " + nodeTypeToTable(type));
            } catch (Exception ignored) {
            }
        }
        initSchema();
    }

    @Override
    public void save(CodeGraph graph) {
        // Insert nodes
        for (CodeNode node : graph.getNodes().values()) {
            String table = nodeTypeToTable(node.type());
            try {
                String cypher = String.format(
                        "CREATE (n:%s {id: '%s', name: '%s', qualifiedName: '%s'",
                        table,
                        escapeString(node.id()),
                        escapeString(node.name()),
                        escapeString(node.qualifiedName()));

                StringBuilder props = new StringBuilder();
                for (Map.Entry<String, Object> entry : node.properties().entrySet()) {
                    // Only add properties that are in the node table schema
                    String key = entry.getKey();
                    if (key.equals("visibility") || key.equals("isAbstract") || key.equals("isStatic") ||
                        key.equals("isFinal") || key.equals("returnType") || key.equals("lineNumber") ||
                        key.equals("type") || key.equals("external") || key.equals("path") ||
                        // Workflow-specific properties
                        key.equals("entryPointId") || key.equals("terminalId") || key.equals("stepCount") ||
                        key.equals("httpMethod") || key.equals("httpPath") ||
                        // Domain-specific properties
                        key.equals("heuristicLabel") || key.equals("cohesion") || key.equals("memberCount")) {
                        props.append(", ").append(key).append(": ");
                        props.append(formatValue(entry.getValue()));
                    }
                }
                // Prefer explicit language property (set by WorkflowBuilder/DomainDetector) over inferred
                String lang = node.properties().containsKey("language")
                        ? String.valueOf(node.properties().get("language"))
                        : inferLanguage(node.type());
                props.append(", language: ").append(formatValue(lang));
                props.append(", kind: ").append(formatValue(nodeTypeToTable(node.type())));
                props.append(", normKind: ").append(formatValue(inferNormKind(node.type())));

                cypher += props + "})";
                connection.query(cypher);
            } catch (Exception e) {
                // Node insert may fail if duplicate or schema mismatch
            }
        }

        // Insert relationships
        for (CodeRelationship rel : graph.getRelationships()) {
            try {
                String sourceTable = getTableForNodeId(rel.sourceId(), graph);
                String targetTable = getTableForNodeId(rel.targetId(), graph);
                if (sourceTable == null || targetTable == null) continue;

                String relProps = "";
                Map<String, Object> enrichedProperties = new java.util.LinkedHashMap<>(rel.properties());
                CodeNode sourceNode = graph.getNode(rel.sourceId());
                if (sourceNode != null) {
                    enrichedProperties.put("language", inferLanguage(sourceNode.type()));
                }
                enrichedProperties.put("kind", rel.type().name());
                enrichedProperties.put("normKind", inferRelNormKind(rel.type()));
                if (!enrichedProperties.isEmpty()) {
                    relProps = " {" + enrichedProperties.entrySet().stream()
                            .map(e -> e.getKey() + ": " + formatValue(e.getValue()))
                            .collect(Collectors.joining(", ")) + "}";
                }

                String cypher = String.format(
                        "MATCH (a:%s {id: '%s'}), (b:%s {id: '%s'}) CREATE (a)-[:%s%s]->(b)",
                        sourceTable, escapeString(rel.sourceId()),
                        targetTable, escapeString(rel.targetId()),
                        rel.type().name(), relProps);

                connection.query(cypher);
            } catch (Exception e) {
                // Some relationships may fail if the schema doesn't support that combination
            }
        }
    }

    @Override
    public String summary() {
        long totalNodes = 0;

        for (NodeType type : NodeType.values()) {
            try {
                var result = connection.query(
                        String.format("MATCH (n:%s) RETURN count(n) AS c", nodeTypeToTable(type)));
                if (result.hasNext()) {
                    FlatTuple tuple = result.getNext();
                    totalNodes += tuple.getValue(0).<Long>getValue();
                }
                result.close();
            } catch (Exception ignored) {
            }
        }

        // Count CALLS relationships
        long callsCount = 0;
        try {
            var result = connection.query("MATCH ()-[r:CALLS]->() RETURN count(r) AS c");
            if (result.hasNext()) {
                FlatTuple tuple = result.getNext();
                callsCount = tuple.getValue(0).<Long>getValue();
            }
            result.close();
        } catch (Exception ignored) {
        }

        return String.format("KuzuDB graph: %d nodes, %d CALLS edges", totalNodes, callsCount);
    }

    @Override
    public void close() throws Exception {
        connection.close();
        database.close();
    }

    private String getTableForNodeId(String nodeId, CodeGraph graph) {
        CodeNode node = graph.getNode(nodeId);
        if (node == null) return null;
        return nodeTypeToTable(node.type());
    }

    private static String nodeTypeToTable(NodeType type) {
        return switch (type) {
            case PACKAGE -> "Package";
            case CLASS -> "Class";
            case INTERFACE -> "Interface";
            case ENUM -> "Enum";
            case RECORD -> "Record";
            case ANNOTATION_TYPE -> "AnnotationType";
            case METHOD -> "Method";
            case CONSTRUCTOR -> "Constructor";
            case FIELD -> "Field";
            case PARAMETER -> "Parameter";
            case FILE -> "File";
            case STATEMENT -> "Statement";
            // JavaScript/TypeScript
            case MODULE -> "Module";
            case FUNCTION -> "Function";
            case ARROW_FUNCTION -> "ArrowFunction";
            case COMPONENT -> "Component";
            case HOOK -> "Hook";
            case JSX_ELEMENT -> "JSXElement";
            // Python
            case DECORATOR -> "Decorator";
            case GENERATOR -> "Generator";
            case ASYNC_FUNCTION -> "AsyncFunction";
            case COMPREHENSION -> "Comprehension";
            // Kotlin
            case DATA_CLASS -> "DataClass";
            case SEALED_CLASS -> "SealedClass";
            case SEALED_INTERFACE -> "SealedInterface";
            case OBJECT_DECL -> "ObjectDecl";
            case COMPANION_OBJECT -> "CompanionObject";
            case EXTENSION_FUNCTION -> "ExtensionFunction";
            case SUSPEND_FUNCTION -> "SuspendFunction";
            case PROPERTY -> "Property";
            case LAMBDA -> "Lambda";
            case INIT_BLOCK -> "InitBlock";
            case TYPE_ALIAS -> "TypeAlias";
            // Post-processing abstractions
            case WORKFLOW -> "Workflow";
            case DOMAIN -> "Domain";
        };
    }

    private static String withNormalizedRelProps(String relDef) {
        int idx = relDef.lastIndexOf(')');
        if (idx == -1) {
            return relDef;
        }
        return relDef.substring(0, idx) + ", language STRING, kind STRING, normKind STRING" + relDef.substring(idx);
    }

    private static String inferLanguage(NodeType type) {
        return switch (type) {
            // ASYNC_FUNCTION and GENERATOR are shared between JS and Python.
            // Default to "javascript" here; Python nodes get overridden by the explicit
            // 'language' property set by parse.py (preferred in save() over this inferred value).
            case MODULE, FUNCTION, ARROW_FUNCTION, COMPONENT, HOOK, JSX_ELEMENT,
                 ASYNC_FUNCTION, GENERATOR -> "javascript";
            case DECORATOR, COMPREHENSION -> "python";
            case DATA_CLASS, SEALED_CLASS, SEALED_INTERFACE, OBJECT_DECL, COMPANION_OBJECT,
                    EXTENSION_FUNCTION, SUSPEND_FUNCTION, PROPERTY, LAMBDA, INIT_BLOCK, TYPE_ALIAS -> "kotlin";
            default -> "java";
        };
    }

    private static String inferNormKind(NodeType type) {
        return switch (type) {
            case PACKAGE, MODULE -> "CodeUnit";
            case METHOD, CONSTRUCTOR, FUNCTION, ARROW_FUNCTION, HOOK, ASYNC_FUNCTION, GENERATOR,
                    EXTENSION_FUNCTION, SUSPEND_FUNCTION, LAMBDA -> "Callable";
            case CLASS, INTERFACE, ENUM, RECORD, DATA_CLASS, SEALED_CLASS, SEALED_INTERFACE,
                    OBJECT_DECL, TYPE_ALIAS -> "TypeLike";
            case FIELD, PROPERTY, PARAMETER -> "DataMember";
            case COMPONENT -> "UiComponent";
            case FILE -> "SourceFile";
            case ANNOTATION_TYPE, DECORATOR -> "AnnotationLike";
            case STATEMENT -> "Statement";
            case WORKFLOW -> "Workflow";
            case DOMAIN -> "Domain";
            default -> "CodeElement";
        };
    }

    private static String inferRelNormKind(RelationshipType type) {
        return switch (type) {
            case CONTAINS, SOURCE_FILE -> "Contains";
            case CALLS -> "Calls";
            case IMPORTS, EXPORTS, PROP_DEPENDENCY, DELEGATES_TO -> "DependsOn";
            case EXTENDS, IMPLEMENTS, OVERRIDES, SEALED_SUBTYPE, COMPANION_OF, EXTENSION_OF -> "TypeRelation";
            case RENDERS, USES_HOOK -> "UsesUi";
            case HAS_PARAMETER, OF_TYPE, RETURNS, THROWS, DECORATES, HAS_ANNOTATION, YIELDS, SUSPENDS -> "SemanticRelation";
            case AST_CHILD, CFG_NEXT, DATA_FLOW -> "FlowRelation";
            case WORKFLOW_STEP, IN_DOMAIN -> "AbstractionRelation";
        };
    }

    private static String escapeString(String value) {
        if (value == null) return "";
        // Escape backslashes first, then single quotes
        return value.replace("\\", "\\\\").replace("'", "\\'");
    }

    private static String formatValue(Object value) {
        if (value == null) return "null";   // SQL NULL — no quotes, not the string "null"
        if (value instanceof Boolean) return value.toString();
        if (value instanceof Number) return value.toString();
        return "'" + escapeString(String.valueOf(value)) + "'";
    }
}
