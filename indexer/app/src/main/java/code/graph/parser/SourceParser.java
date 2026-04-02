package code.graph.parser;

import code.graph.model.CodeGraph;

import java.io.IOException;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;

/**
 * Strategy interface for parsing source code into a CodeGraph.
 * Implementations handle specific programming languages.
 */
public interface SourceParser {

    /**
     * Parse all source files under the given root directory.
     *
     * @param root the root directory to scan for source files
     * @return a CodeGraph containing nodes and relationships
     * @throws IOException if an I/O error occurs during parsing
     */
    CodeGraph parseDirectory(Path root) throws IOException;

    /**
     * Get the file extensions this parser handles (e.g., ".java", ".js", ".py").
     *
     * @return set of file extensions including the dot prefix
     */
    Set<String> getSupportedExtensions();

    /**
     * Get the language name for display purposes.
     *
     * @return the language name (e.g., "Java", "JavaScript", "Python")
     */
    String getLanguageName();

    /**
     * Check if this parser supports incremental parsing.
     *
     * @return true if incremental parsing is supported
     */
    default boolean supportsIncremental() {
        return false;
    }
}
