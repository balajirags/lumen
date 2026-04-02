package code.graph.export;

import code.graph.model.CodeGraph;
import code.graph.model.CodeNode;
import code.graph.model.CodeRelationship;

import java.io.IOException;
import java.io.Writer;
import java.util.Map;

/**
 * Exports a CodeGraph in GraphML format (XML-based graph interchange format).
 */
public class GraphMlExporter implements GraphExporter {

    @Override
    public void export(CodeGraph graph, Writer writer) throws IOException {
        writer.write("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
        writer.write("<graphml xmlns=\"http://graphml.graphstudio.org/xmlns\"\n");
        writer.write("         xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"\n");
        writer.write("         xsi:schemaLocation=\"http://graphml.graphstudio.org/xmlns http://graphml.graphstudio.org/xmlns/1.0/graphml.xsd\">\n");

        // Key definitions
        writer.write("  <key id=\"nodeType\" for=\"node\" attr.name=\"nodeType\" attr.type=\"string\"/>\n");
        writer.write("  <key id=\"name\" for=\"node\" attr.name=\"name\" attr.type=\"string\"/>\n");
        writer.write("  <key id=\"qualifiedName\" for=\"node\" attr.name=\"qualifiedName\" attr.type=\"string\"/>\n");
        writer.write("  <key id=\"relType\" for=\"edge\" attr.name=\"relType\" attr.type=\"string\"/>\n");

        writer.write("  <graph id=\"code-mem-graph\" edgedefault=\"directed\">\n");

        // Nodes
        for (Map.Entry<String, CodeNode> entry : graph.getNodes().entrySet()) {
            CodeNode node = entry.getValue();
            writer.write("    <node id=\"%s\">\n".formatted(escapeXml(node.id())));
            writer.write("      <data key=\"nodeType\">%s</data>\n".formatted(node.type().name()));
            writer.write("      <data key=\"name\">%s</data>\n".formatted(escapeXml(node.name())));
            writer.write("      <data key=\"qualifiedName\">%s</data>\n".formatted(escapeXml(node.qualifiedName())));
            writer.write("    </node>\n");
        }

        // Edges
        int edgeId = 0;
        for (CodeRelationship rel : graph.getRelationships()) {
            writer.write("    <edge id=\"e%d\" source=\"%s\" target=\"%s\">\n".formatted(
                    edgeId++, escapeXml(rel.sourceId()), escapeXml(rel.targetId())));
            writer.write("      <data key=\"relType\">%s</data>\n".formatted(rel.type().name()));
            writer.write("    </edge>\n");
        }

        writer.write("  </graph>\n");
        writer.write("</graphml>\n");
    }

    private static String escapeXml(String s) {
        return s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("'", "&apos;");
    }
}
