package code.graph.store;

import code.graph.model.CodeGraph;

/**
 * Interface for persisting a CodeGraph to a graph database.
 */
public interface GraphStore extends AutoCloseable {

    /**
     * Initialize the schema (node labels, relationship types, indexes).
     */
    void initSchema();

    /**
     * Clear all existing data in the graph store.
     */
    void clear();

    /**
     * Persist the given CodeGraph to the store.
     */
    void save(CodeGraph graph);

    /**
     * Return a summary of the stored graph (node/relationship counts).
     */
    String summary();
}
