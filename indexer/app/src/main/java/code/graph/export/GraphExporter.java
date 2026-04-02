package code.graph.export;

import code.graph.model.CodeGraph;

import java.io.IOException;
import java.io.Writer;

/**
 * Exports a CodeGraph into a specific file format.
 */
public interface GraphExporter {
    void export(CodeGraph graph, Writer writer) throws IOException;
}
