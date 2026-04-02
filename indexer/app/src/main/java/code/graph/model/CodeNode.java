package code.graph.model;

import java.util.HashMap;
import java.util.Map;

/**
 * Represents a node in the code knowledge graph (package, class, method, field, etc.).
 */
public record CodeNode(
        String id,
        NodeType type,
        String name,
        String qualifiedName,
        Map<String, Object> properties
) {
    public CodeNode(String id, NodeType type, String name, String qualifiedName) {
        this(id, type, name, qualifiedName, new HashMap<>());
    }

    public CodeNode withProperty(String key, Object value) {
        properties.put(key, value);
        return this;
    }
}
