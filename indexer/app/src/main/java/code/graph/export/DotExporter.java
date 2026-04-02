package code.graph.export;

import code.graph.model.CodeGraph;
import code.graph.model.CodeNode;
import code.graph.model.CodeRelationship;

import java.io.IOException;
import java.io.Writer;
import java.util.Map;

/**
 * Exports a CodeGraph in DOT (Graphviz) format.
 */
public class DotExporter implements GraphExporter {

    @Override
    public void export(CodeGraph graph, Writer writer) throws IOException {
        writer.write("digraph code_mem_graph {\n");
        writer.write("  rankdir=LR;\n");
        writer.write("  node [shape=box, style=filled, fontname=\"Helvetica\"];\n\n");

        // Write nodes
        for (Map.Entry<String, CodeNode> entry : graph.getNodes().entrySet()) {
            CodeNode node = entry.getValue();
            String color = colorForType(node.type().name());
            writer.write("  \"%s\" [label=\"%s\\n(%s)\", fillcolor=\"%s\"];\n".formatted(
                    escapeDot(node.id()),
                    escapeDot(node.name()),
                    node.type().name(),
                    color));
        }

        writer.write("\n");

        // Write edges
        for (CodeRelationship rel : graph.getRelationships()) {
            writer.write("  \"%s\" -> \"%s\" [label=\"%s\"];\n".formatted(
                    escapeDot(rel.sourceId()),
                    escapeDot(rel.targetId()),
                    rel.type().name()));
        }

        writer.write("}\n");
    }

    private static String escapeDot(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static String colorForType(String type) {
        return switch (type) {
            case "PACKAGE" -> "#E8F5E9";
            case "CLASS" -> "#BBDEFB";
            case "INTERFACE" -> "#C8E6C9";
            case "ENUM" -> "#FFF9C4";
            case "RECORD" -> "#F0F4C3";
            case "METHOD", "CONSTRUCTOR" -> "#FFE0B2";
            case "FIELD", "PARAMETER" -> "#F5F5F5";
            case "ANNOTATION_TYPE" -> "#E1BEE7";
            default -> "#FFFFFF";
        };
    }
}
