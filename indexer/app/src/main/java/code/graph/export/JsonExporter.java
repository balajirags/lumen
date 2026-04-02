package code.graph.export;

import code.graph.model.CodeGraph;
import code.graph.model.CodeNode;
import code.graph.model.CodeRelationship;

import java.io.IOException;
import java.io.Writer;
import java.util.Map;

/**
 * Exports a CodeGraph in JSON format.
 */
public class JsonExporter implements GraphExporter {

    @Override
    public void export(CodeGraph graph, Writer writer) throws IOException {
        writer.write("{\n");

        // Nodes array
        writer.write("  \"nodes\": [\n");
        var nodeEntries = graph.getNodes().entrySet().stream().toList();
        for (int i = 0; i < nodeEntries.size(); i++) {
            CodeNode node = nodeEntries.get(i).getValue();
            writer.write("    {");
            writer.write("\"id\": %s, ".formatted(jsonString(node.id())));
            writer.write("\"type\": %s, ".formatted(jsonString(node.type().name())));
            writer.write("\"name\": %s, ".formatted(jsonString(node.name())));
            writer.write("\"qualifiedName\": %s".formatted(jsonString(node.qualifiedName())));

            if (!node.properties().isEmpty()) {
                writer.write(", \"properties\": {");
                var props = node.properties().entrySet().stream().toList();
                for (int j = 0; j < props.size(); j++) {
                    var prop = props.get(j);
                    writer.write("%s: %s".formatted(
                            jsonString(prop.getKey()), jsonValue(prop.getValue())));
                    if (j < props.size() - 1) writer.write(", ");
                }
                writer.write("}");
            }

            writer.write("}");
            if (i < nodeEntries.size() - 1) writer.write(",");
            writer.write("\n");
        }
        writer.write("  ],\n");

        // Relationships array
        writer.write("  \"relationships\": [\n");
        var rels = graph.getRelationships();
        for (int i = 0; i < rels.size(); i++) {
            CodeRelationship rel = rels.get(i);
            writer.write("    {");
            writer.write("\"source\": %s, ".formatted(jsonString(rel.sourceId())));
            writer.write("\"target\": %s, ".formatted(jsonString(rel.targetId())));
            writer.write("\"type\": %s".formatted(jsonString(rel.type().name())));

            if (!rel.properties().isEmpty()) {
                writer.write(", \"properties\": {");
                var props = rel.properties().entrySet().stream().toList();
                for (int j = 0; j < props.size(); j++) {
                    var prop = props.get(j);
                    writer.write("%s: %s".formatted(
                            jsonString(prop.getKey()), jsonValue(prop.getValue())));
                    if (j < props.size() - 1) writer.write(", ");
                }
                writer.write("}");
            }

            writer.write("}");
            if (i < rels.size() - 1) writer.write(",");
            writer.write("\n");
        }
        writer.write("  ]\n");

        writer.write("}\n");
    }

    private static String jsonString(String s) {
        return "\"" + s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t") + "\"";
    }

    private static String jsonValue(Object value) {
        if (value == null) return "null";
        if (value instanceof Boolean || value instanceof Number) return value.toString();
        return jsonString(value.toString());
    }
}
