package code.graph.model;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * In-memory representation of the extracted code knowledge graph.
 */
public class CodeGraph {

    private final Map<String, CodeNode> nodes = new LinkedHashMap<>();
    private final List<CodeRelationship> relationships = new ArrayList<>();

    public void addNode(CodeNode node) {
        nodes.put(node.id(), node);
    }

    public void addRelationship(CodeRelationship relationship) {
        relationships.add(relationship);
    }

    public CodeNode getNode(String id) {
        return nodes.get(id);
    }

    public boolean hasNode(String id) {
        return nodes.containsKey(id);
    }

    public Map<String, CodeNode> getNodes() {
        return nodes;
    }

    public List<CodeRelationship> getRelationships() {
        return relationships;
    }

    public void merge(CodeGraph other) {
        nodes.putAll(other.nodes);
        relationships.addAll(other.relationships);
    }

    public int nodeCount() {
        return nodes.size();
    }

    public int relationshipCount() {
        return relationships.size();
    }
}
