package code.graph.model;

import java.util.HashMap;
import java.util.Map;

/**
 * Represents a relationship (edge) in the code knowledge graph.
 */
public record CodeRelationship(
        String sourceId,
        String targetId,
        RelationshipType type,
        Map<String, Object> properties
) {
    public CodeRelationship(String sourceId, String targetId, RelationshipType type) {
        this(sourceId, targetId, type, new HashMap<>());
    }

    public CodeRelationship withProperty(String key, Object value) {
        properties.put(key, value);
        return this;
    }
}
