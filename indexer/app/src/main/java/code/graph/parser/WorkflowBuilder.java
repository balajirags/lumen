package code.graph.parser;

import code.graph.model.*;

import java.util.*;
import java.util.function.BiPredicate;
import java.util.stream.Collectors;

/**
 * Post-processing step that traces end-to-end execution paths through the call graph
 * and materialises them as Workflow nodes with WORKFLOW_STEP edges.
 *
 * <p>Two strategies are applied based on detected language(s):
 * <ul>
 *   <li><b>JVM (Java/Kotlin)</b>: entry points are HTTP-annotated Controller methods;
 *       terminals are Repository methods or event-producer calls.</li>
 *   <li><b>React (JS/TS)</b>: entry points are root Component nodes (no incoming RENDERS);
 *       terminals are leaf AsyncFunction/ArrowFunction nodes with no outgoing calls.</li>
 * </ul>
 */
public class WorkflowBuilder {

    private static final Set<String> HTTP_ANNOTATIONS = Set.of(
            "GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping",
            "RequestMapping"
    );

    public void build(CodeGraph graph) {
        boolean hasJvm = graph.getNodes().values().stream()
                .anyMatch(n -> n.type() == NodeType.CLASS || n.type() == NodeType.METHOD);
        boolean hasJs = graph.getNodes().values().stream()
                .anyMatch(n -> n.type() == NodeType.COMPONENT || n.type() == NodeType.ASYNC_FUNCTION);

        if (hasJvm) buildJvmWorkflows(graph);
        if (hasJs)  buildReactWorkflows(graph);
    }

    // -------------------------------------------------------------------------
    // JVM strategy
    // -------------------------------------------------------------------------

    private void buildJvmWorkflows(CodeGraph graph) {
        // Build annotation index: nodeId -> set of annotation names + values
        Map<String, Map<String, String>> annotationsByNode = buildAnnotationIndex(graph);

        Set<String> entryPoints = graph.getNodes().values().stream()
                .filter(n -> n.type() == NodeType.METHOD)
                .filter(n -> hasHttpAnnotation(annotationsByNode, n.id()))
                .map(CodeNode::id)
                .collect(Collectors.toSet());

        for (String entryId : entryPoints) {
            CodeNode entryNode = graph.getNode(entryId);
            String httpMethod = resolveHttpMethod(annotationsByNode, entryId);
            String httpPath   = resolveHttpPath(annotationsByNode, entryId);
            bfsAndEmit(graph, entryId, this::isJvmTerminal, httpMethod, httpPath, "java");
        }
    }

    private Map<String, Map<String, String>> buildAnnotationIndex(CodeGraph graph) {
        Map<String, Map<String, String>> index = new HashMap<>();
        for (CodeRelationship rel : graph.getRelationships()) {
            if (rel.type() != RelationshipType.HAS_ANNOTATION) continue;
            CodeNode annNode = graph.getNode(rel.targetId());
            if (annNode == null) continue;
            String value = rel.properties().get("value") != null
                    ? rel.properties().get("value").toString() : null;
            index.computeIfAbsent(rel.sourceId(), k -> new LinkedHashMap<>())
                 .put(annNode.name(), value != null ? value : "");
        }
        return index;
    }

    private boolean hasHttpAnnotation(Map<String, Map<String, String>> annIndex, String nodeId) {
        Map<String, String> anns = annIndex.getOrDefault(nodeId, Map.of());
        return anns.keySet().stream().anyMatch(HTTP_ANNOTATIONS::contains);
    }

    private String resolveHttpMethod(Map<String, Map<String, String>> annIndex, String nodeId) {
        Map<String, String> anns = annIndex.getOrDefault(nodeId, Map.of());
        if (anns.containsKey("GetMapping"))    return "GET";
        if (anns.containsKey("PostMapping"))   return "POST";
        if (anns.containsKey("PutMapping"))    return "PUT";
        if (anns.containsKey("DeleteMapping")) return "DELETE";
        if (anns.containsKey("PatchMapping"))  return "PATCH";
        return "HTTP";
    }

    private String resolveHttpPath(Map<String, Map<String, String>> annIndex, String nodeId) {
        Map<String, String> anns = annIndex.getOrDefault(nodeId, Map.of());
        for (String ann : HTTP_ANNOTATIONS) {
            String val = anns.get(ann);
            if (val != null && !val.isBlank()) return val;
        }
        return null;
    }

    private boolean isJvmTerminal(CodeGraph graph, String nodeId) {
        CodeNode n = graph.getNode(nodeId);
        if (n == null) return false;
        String qn = n.qualifiedName() != null ? n.qualifiedName() : "";
        return qn.contains(".repository.") ||
               n.name().toLowerCase().contains("producer") ||
               n.name().toLowerCase().contains("kafkatemplate") ||
               qn.contains("KafkaProducer") ||
               qn.contains("EventPublisher");
    }

    // -------------------------------------------------------------------------
    // React strategy
    // -------------------------------------------------------------------------

    private void buildReactWorkflows(CodeGraph graph) {
        // Root components: Component nodes with no incoming RENDERS edge
        Set<String> renderedTargets = graph.getRelationships().stream()
                .filter(r -> r.type() == RelationshipType.RENDERS)
                .map(CodeRelationship::targetId)
                .collect(Collectors.toSet());

        Set<String> rootComponents = graph.getNodes().values().stream()
                .filter(n -> n.type() == NodeType.COMPONENT)
                .filter(n -> !renderedTargets.contains(n.id()))
                .map(CodeNode::id)
                .collect(Collectors.toSet());

        for (String entryId : rootComponents) {
            bfsAndEmit(graph, entryId, this::isReactTerminal, null, null, "javascript");
        }
    }

    private boolean isReactTerminal(CodeGraph graph, String nodeId) {
        CodeNode n = graph.getNode(nodeId);
        if (n == null) return false;
        if (n.type() != NodeType.ASYNC_FUNCTION && n.type() != NodeType.ARROW_FUNCTION) return false;
        // Terminal: leaf node with no outgoing high-confidence CALLS into the project
        return graph.getRelationships().stream()
                .filter(r -> r.sourceId().equals(nodeId) && r.type() == RelationshipType.CALLS)
                .filter(r -> confidenceOf(r) >= 0.9)
                .noneMatch(r -> graph.getNode(r.targetId()) != null);
    }

    // -------------------------------------------------------------------------
    // Shared BFS core
    // -------------------------------------------------------------------------

    private void bfsAndEmit(CodeGraph graph, String entryId,
                             BiPredicate<CodeGraph, String> isTerminal,
                             String httpMethod, String httpPath, String language) {
        Queue<List<String>> queue = new LinkedList<>();
        Set<String> globalVisited = new HashSet<>();
        queue.add(List.of(entryId));

        while (!queue.isEmpty()) {
            List<String> path = queue.poll();
            String current = path.get(path.size() - 1);
            if (globalVisited.contains(current)) continue;
            globalVisited.add(current);

            List<String> next = new ArrayList<>(getHighConfidenceCallees(graph, current));
            next.addAll(getRendersTargets(graph, current));

            for (String callee : next) {
                if (globalVisited.contains(callee)) continue;
                List<String> newPath = new ArrayList<>(path);
                newPath.add(callee);

                if (isTerminal.test(graph, callee)) {
                    emitWorkflow(graph, newPath, httpMethod, httpPath, language);
                } else {
                    queue.add(newPath);
                }
            }
        }
    }

    private List<String> getHighConfidenceCallees(CodeGraph graph, String nodeId) {
        return graph.getRelationships().stream()
                .filter(r -> r.sourceId().equals(nodeId) && r.type() == RelationshipType.CALLS)
                .filter(r -> confidenceOf(r) >= 0.9)
                .map(CodeRelationship::targetId)
                .filter(id -> graph.getNode(id) != null)
                .collect(Collectors.toList());
    }

    private List<String> getRendersTargets(CodeGraph graph, String nodeId) {
        return graph.getRelationships().stream()
                .filter(r -> r.sourceId().equals(nodeId) && r.type() == RelationshipType.RENDERS)
                .map(CodeRelationship::targetId)
                .filter(id -> graph.getNode(id) != null)
                .collect(Collectors.toList());
    }

    private double confidenceOf(CodeRelationship rel) {
        Object conf = rel.properties().get("confidence");
        if (conf instanceof Number num) return num.doubleValue();
        // Legacy graphs may use boolean resolved
        Object resolved = rel.properties().get("resolved");
        if (Boolean.TRUE.equals(resolved)) return 0.90;
        return 0.50;
    }

    private void emitWorkflow(CodeGraph graph, List<String> path,
                               String httpMethod, String httpPath, String language) {
        if (path.size() < 2) return;

        CodeNode entryNode    = graph.getNode(path.get(0));
        CodeNode terminalNode = graph.getNode(path.get(path.size() - 1));
        if (entryNode == null || terminalNode == null) return;

        String entryName    = entryNode.name();
        String terminalName = terminalNode.name();
        String workflowId   = "workflow:" + entryName + "→" + terminalName + ":" + path.size();

        // Deduplicate: if a workflow with same entry+terminal+steps already exists, skip
        if (graph.hasNode(workflowId)) return;

        String displayName = toDisplayName(entryName) + " → " + toDisplayName(terminalName);

        CodeNode workflowNode = new CodeNode(workflowId, NodeType.WORKFLOW, displayName, displayName)
                .withProperty("entryPointId", entryNode.id())
                .withProperty("terminalId", terminalNode.id())
                .withProperty("stepCount", (long) path.size())
                .withProperty("httpMethod", httpMethod)
                .withProperty("httpPath", httpPath)
                .withProperty("language", language);
        graph.addNode(workflowNode);

        for (int i = 0; i < path.size(); i++) {
            graph.addRelationship(
                    new CodeRelationship(path.get(i), workflowId, RelationshipType.WORKFLOW_STEP)
                            .withProperty("step", (long) (i + 1)));
        }
    }

    private static String toDisplayName(String name) {
        if (name == null || name.isEmpty()) return name;
        return Character.toUpperCase(name.charAt(0)) + name.substring(1);
    }
}
