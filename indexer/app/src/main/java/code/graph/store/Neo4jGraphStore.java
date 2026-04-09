package code.graph.store;

import code.graph.model.*;
import org.neo4j.driver.*;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Persists a CodeGraph to a Neo4j database.
 */
public class Neo4jGraphStore implements GraphStore {

    private final Driver driver;
    private final String database;

    public Neo4jGraphStore(String uri, String username, String password, String database) {
        this.driver = GraphDatabase.driver(uri, AuthTokens.basic(username, password));
        this.database = ensureDatabase(database);
        driver.verifyConnectivity();
        System.out.printf("Neo4j: connected to %s, database '%s'%n", uri, this.database);
    }

    /**
     * Create the database if it does not already exist.
     * Falls back to the default 'neo4j' database on Community Edition.
     */
    private String ensureDatabase(String requestedDb) {
        if ("neo4j".equals(requestedDb)) {
            return requestedDb;
        }
        try (var session = driver.session(SessionConfig.builder().withDatabase("system").build())) {
            session.run("CREATE DATABASE $name IF NOT EXISTS", Map.of("name", requestedDb));
            return requestedDb;
        } catch (Exception e) {
            System.err.println("Note: Could not create database '" + requestedDb
                    + "', falling back to 'neo4j': " + e.getMessage());
            return "neo4j";
        }
    }

    private SessionConfig sessionConfig() {
        return SessionConfig.builder().withDatabase(database).build();
    }

    @Override
    public void initSchema() {
        try (var session = driver.session(sessionConfig())) {
            // Create uniqueness constraints (also serve as indexes)
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Package) REQUIRE n.id IS UNIQUE");
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Class) REQUIRE n.id IS UNIQUE");
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Interface) REQUIRE n.id IS UNIQUE");
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Enum) REQUIRE n.id IS UNIQUE");
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Record) REQUIRE n.id IS UNIQUE");
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:AnnotationType) REQUIRE n.id IS UNIQUE");
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Method) REQUIRE n.id IS UNIQUE");
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Constructor) REQUIRE n.id IS UNIQUE");
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Field) REQUIRE n.id IS UNIQUE");
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Parameter) REQUIRE n.id IS UNIQUE");
            // CPG-specific
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:File) REQUIRE n.id IS UNIQUE");
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Statement) REQUIRE n.id IS UNIQUE");
        }
    }

    @Override
    public void clear() {
        try (var session = driver.session(sessionConfig())) {
            session.run("MATCH (n) DETACH DELETE n");
        }
    }

    private static final int BATCH_SIZE = 500;

    @Override
    public void save(CodeGraph graph) {
        try (var session = driver.session(sessionConfig())) {
            // Batch insert nodes — group by label for UNWIND
            Map<String, List<Map<String, Object>>> nodesByLabel = new HashMap<>();
            for (CodeNode node : graph.getNodes().values()) {
                String label = nodeTypeToLabel(node.type());
                Map<String, Object> props = new HashMap<>(node.properties());
                props.put("id", node.id());
                props.put("name", node.name());
                props.put("qualifiedName", node.qualifiedName());
                nodesByLabel.computeIfAbsent(label, k -> new ArrayList<>()).add(props);
            }

            for (var entry : nodesByLabel.entrySet()) {
                String label = entry.getKey();
                List<Map<String, Object>> allNodes = entry.getValue();

                for (int i = 0; i < allNodes.size(); i += BATCH_SIZE) {
                    List<Map<String, Object>> batch = allNodes.subList(i,
                            Math.min(i + BATCH_SIZE, allNodes.size()));
                    String cypher = String.format(
                            "UNWIND $batch AS row MERGE (n:%s {id: row.id}) SET n += row", label);
                    session.run(cypher, Map.of("batch", batch));
                }
            }

            // Batch insert relationships  group by type
            Map<String, List<Map<String, Object>>> relsByType = new HashMap<>();
            for (CodeRelationship rel : graph.getRelationships()) {
                String relType = rel.type().name();
                Map<String, Object> props = new HashMap<>(rel.properties());
                props.put("sourceId", rel.sourceId());
                props.put("targetId", rel.targetId());
                relsByType.computeIfAbsent(relType, k -> new ArrayList<>()).add(props);
            }

            for (var entry : relsByType.entrySet()) {
                String relType = entry.getKey();
                List<Map<String, Object>> allRels = entry.getValue();

                for (int i = 0; i < allRels.size(); i += BATCH_SIZE) {
                    List<Map<String, Object>> batch = allRels.subList(i,
                            Math.min(i + BATCH_SIZE, allRels.size()));

                    String cypher = String.format(
                            "UNWIND $batch AS row " +
                            "MATCH (a {id: row.sourceId}), (b {id: row.targetId}) " +
                            "MERGE (a)-[r:%s]->(b) SET r += row", relType);
                    session.run(cypher, Map.of("batch", batch));
                }
            }
        }
    }

    @Override
    public String summary() {
        try (var session = driver.session(sessionConfig())) {
            var nodeResult = session.run("MATCH (n) RETURN count(n) AS count");
            long nodeCount = nodeResult.single().get("count").asLong();

            var relResult = session.run("MATCH ()-[r]->() RETURN count(r) AS count");
            long relCount = relResult.single().get("count").asLong();

            return String.format("Neo4j graph: %d nodes, %d relationships", nodeCount, relCount);
        }
    }

    @Override
    public void close() {
        driver.close();
    }

    private static String nodeTypeToLabel(NodeType type) {
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
            case WORKFLOW -> "Workflow";
            case DOMAIN -> "Domain";
        };
    }
}
