package code.graph.parser;

import code.graph.model.*;

import java.util.*;
import java.util.stream.Collectors;

/**
 * Post-processing step that applies label propagation over the call/render graph
 * to detect cohesive functional clusters and materialise them as Domain nodes
 * with IN_DOMAIN edges.
 *
 * <p>Language-aware: Java/Kotlin uses CALLS+CONTAINS adjacency; JS/TS additionally
 * uses RENDERS, USES_HOOK, and IMPORTS adjacency to compensate for sparse CALLS data.
 *
 * <p>Test nodes are excluded so that Domain clusters reflect production structure only.
 */
public class DomainDetector {

    private static final int LABEL_PROP_ITERATIONS = 10;

    /** Spring stereotype annotations that hint at the architectural layer. */
    private static final Map<String, String> SPRING_LAYER = Map.of(
            "RestController", "Controller",
            "Controller",     "Controller",
            "Service",        "Service",
            "Repository",     "Repository",
            "Component",      "Component",
            "Configuration",  "Configuration"
    );

    public void detect(CodeGraph graph) {
        Set<String> productionNodes = getProductionNodes(graph);
        if (productionNodes.isEmpty()) return;

        Map<String, Set<String>> adj = buildAdjacency(graph, productionNodes);

        // Label propagation: each node starts as its own label
        Map<String, String> labels = new HashMap<>();
        for (String n : productionNodes) labels.put(n, n);

        for (int iter = 0; iter < LABEL_PROP_ITERATIONS; iter++) {
            boolean changed = false;
            for (String node : productionNodes) {
                String dominant = dominantNeighbourLabel(adj, labels, node);
                if (dominant != null && !dominant.equals(labels.get(node))) {
                    labels.put(node, dominant);
                    changed = true;
                }
            }
            if (!changed) break; // converged early
        }

        // Group by final label → community members
        Map<String, List<String>> communities = new HashMap<>();
        for (Map.Entry<String, String> e : labels.entrySet()) {
            communities.computeIfAbsent(e.getValue(), k -> new ArrayList<>()).add(e.getKey());
        }

        // Emit Domain nodes and IN_DOMAIN edges
        for (Map.Entry<String, List<String>> entry : communities.entrySet()) {
            List<String> members = entry.getValue();
            if (members.size() < 2) continue; // skip singleton clusters

            String domainName     = deriveDomainName(graph, members);
            String heuristicLabel = deriveHeuristicLabel(graph, members);
            String language       = deriveLanguage(graph, members);
            double cohesion       = computeCohesion(graph, members);

            String domainId = "domain:" + language + ":" + domainName.toLowerCase().replace(" ", "-");

            // Deduplicate domain IDs: if two communities resolve to the same name, append index
            int suffix = 0;
            String candidateId = domainId;
            while (graph.hasNode(candidateId)) {
                suffix++;
                candidateId = domainId + "-" + suffix;
            }
            domainId = candidateId;

            CodeNode domainNode = new CodeNode(domainId, NodeType.DOMAIN, domainName, domainName)
                    .withProperty("heuristicLabel", heuristicLabel)
                    .withProperty("cohesion", cohesion)
                    .withProperty("memberCount", (long) members.size())
                    .withProperty("language", language);
            graph.addNode(domainNode);

            for (String memberId : members) {
                graph.addRelationship(
                        new CodeRelationship(memberId, domainId, RelationshipType.IN_DOMAIN));
            }
        }

        // Back-fill Workflow.type = "cross-domain" or "intra-domain"
        updateWorkflowTypes(graph);
    }

    // -------------------------------------------------------------------------
    // Node selection
    // -------------------------------------------------------------------------

    private Set<String> getProductionNodes(CodeGraph graph) {
        return graph.getNodes().values().stream()
                .filter(n -> isClusterableType(n.type()))
                .filter(n -> !Boolean.TRUE.equals(n.properties().get("external")))
                .filter(n -> !isTestNode(n))
                .map(CodeNode::id)
                .collect(Collectors.toSet());
    }

    private boolean isClusterableType(NodeType t) {
        return switch (t) {
            // Java/Kotlin: include METHOD so CALLS edges between methods form adjacency between classes
            case CLASS, INTERFACE, ENUM, METHOD -> true;
            // JS/TS: components and functions are the natural unit
            case COMPONENT, MODULE, ASYNC_FUNCTION, FUNCTION, ARROW_FUNCTION -> true;
            default -> false;
        };
    }

    private boolean isTestNode(CodeNode n) {
        String path = n.properties().get("path") instanceof String s ? s : null;
        String qn   = n.qualifiedName() != null ? n.qualifiedName() : "";
        return (path != null && path.contains("/test/")) ||
               qn.endsWith("Test") || qn.endsWith("IT") || qn.endsWith("Spec") ||
               qn.contains("Test.") || qn.contains(".test.");
    }

    // -------------------------------------------------------------------------
    // Adjacency construction (language-aware)
    // -------------------------------------------------------------------------

    private Map<String, Set<String>> buildAdjacency(CodeGraph graph, Set<String> productionNodes) {
        Map<String, Set<String>> adj = new HashMap<>();
        for (String n : productionNodes) adj.put(n, new HashSet<>());

        for (CodeRelationship r : graph.getRelationships()) {
            boolean srcIn = productionNodes.contains(r.sourceId());
            boolean tgtIn = productionNodes.contains(r.targetId());
            if (!srcIn || !tgtIn) continue;

            switch (r.type()) {
                case CALLS -> {
                    if (confidenceOf(r) >= 0.9) {
                        adj.get(r.sourceId()).add(r.targetId());
                        adj.get(r.targetId()).add(r.sourceId());
                    }
                }
                case CONTAINS -> {
                    adj.get(r.sourceId()).add(r.targetId());
                    adj.get(r.targetId()).add(r.sourceId());
                }
                // JS/TS adjacency sources
                case RENDERS, USES_HOOK, IMPORTS -> {
                    adj.get(r.sourceId()).add(r.targetId());
                    adj.get(r.targetId()).add(r.sourceId());
                }
                default -> { /* ignored */ }
            }
        }
        return adj;
    }

    // -------------------------------------------------------------------------
    // Label propagation helpers
    // -------------------------------------------------------------------------

    private String dominantNeighbourLabel(Map<String, Set<String>> adj,
                                           Map<String, String> labels, String node) {
        Map<String, Integer> freq = new HashMap<>();
        Set<String> neighbours = adj.getOrDefault(node, Set.of());
        if (neighbours.isEmpty()) return null;
        for (String nb : neighbours) {
            String lbl = labels.get(nb);
            if (lbl != null) freq.merge(lbl, 1, Integer::sum);
        }
        return freq.entrySet().stream()
                .max(Map.Entry.comparingByValue())
                .map(Map.Entry::getKey)
                .orElse(null);
    }

    // -------------------------------------------------------------------------
    // Domain metadata derivation
    // -------------------------------------------------------------------------

    private String deriveDomainName(CodeGraph graph, List<String> members) {
        // Extract package/directory segment from member paths or qualified names
        Map<String, Integer> segFreq = new HashMap<>();
        for (String id : members) {
            CodeNode n = graph.getNode(id);
            if (n == null) continue;
            String segment = extractSegment(n);
            if (segment != null && !segment.isBlank()) {
                segFreq.merge(segment, 1, Integer::sum);
            }
        }
        return segFreq.entrySet().stream()
                .max(Map.Entry.comparingByValue())
                .map(e -> capitalize(e.getKey()))
                .orElse("Unknown");
    }

    private String extractSegment(CodeNode n) {
        // Java/Kotlin: last meaningful package segment from qualifiedName
        String qn = n.qualifiedName();
        if (qn != null && qn.contains(".")) {
            String[] parts = qn.split("\\.");
            // Return the second-to-last part (package before class name)
            if (parts.length >= 2) return parts[parts.length - 2];
        }
        // JS/TS: directory segment from path
        String path = n.properties().get("path") instanceof String s ? s : null;
        if (path != null) {
            String dir = path.contains("/")
                    ? path.substring(path.lastIndexOf('/') + 1, path.contains(".") ? path.lastIndexOf('.') : path.length())
                    : path;
            return dir;
        }
        return n.name();
    }

    private String deriveHeuristicLabel(CodeGraph graph, List<String> members) {
        String language = deriveLanguage(graph, members);

        if ("java".equals(language) || "kotlin".equals(language)) {
            // Tier 1: Spring stereotype annotation scan
            Map<String, Integer> annFreq = new HashMap<>();
            for (CodeRelationship rel : graph.getRelationships()) {
                if (rel.type() != RelationshipType.HAS_ANNOTATION) continue;
                if (!members.contains(rel.sourceId())) continue;
                CodeNode ann = graph.getNode(rel.targetId());
                if (ann == null) continue;
                String layer = SPRING_LAYER.get(ann.name());
                if (layer != null) annFreq.merge(layer, 1, Integer::sum);
            }
            if (!annFreq.isEmpty()) {
                return annFreq.entrySet().stream()
                        .max(Map.Entry.comparingByValue())
                        .map(Map.Entry::getKey)
                        .orElse("Mixed");
            }
            // Tier 2 (Java/Kotlin fallback): infer from package segment of member qualified names
            boolean hasModel = members.stream().map(graph::getNode).filter(Objects::nonNull)
                    .anyMatch(n -> {
                        String qn = n.qualifiedName() != null ? n.qualifiedName() : "";
                        return qn.contains(".model.") || qn.contains(".entity.") || qn.contains(".dto.");
                    });
            return hasModel ? "Model" : "Mixed";
        }

        // JS/TS/Python: node-type majority voting
        long componentCount = members.stream()
                .map(graph::getNode).filter(Objects::nonNull)
                .filter(n -> n.type() == NodeType.COMPONENT).count();
        long asyncCount = members.stream()
                .map(graph::getNode).filter(Objects::nonNull)
                .filter(n -> n.type() == NodeType.ASYNC_FUNCTION || n.type() == NodeType.ARROW_FUNCTION).count();
        long moduleCount = members.stream()
                .map(graph::getNode).filter(Objects::nonNull)
                .filter(n -> n.type() == NodeType.MODULE).count();
        if (componentCount > 0 && componentCount >= asyncCount && componentCount >= moduleCount) return "UI";
        if (asyncCount > componentCount && asyncCount >= moduleCount) return "DataLayer";
        if (moduleCount > 0) return "Module";
        return "Mixed";
    }

    private String deriveLanguage(CodeGraph graph, List<String> members) {
        // Return the most common language among members
        Map<String, Integer> langFreq = new HashMap<>();
        for (String id : members) {
            CodeNode n = graph.getNode(id);
            if (n == null) continue;
            Object lang = n.properties().get("language");
            if (lang != null) {
                // Normalise to lowercase so 'JavaScript', 'JSX', 'TypeScript' all count as 'javascript'
                String normalised = lang.toString().toLowerCase();
                if (normalised.equals("jsx") || normalised.equals("typescript")) normalised = "javascript";
                langFreq.merge(normalised, 1, Integer::sum);
            } else {
                // Infer from node type
                String inferred = isJsType(n.type()) ? "javascript" : "java";
                langFreq.merge(inferred, 1, Integer::sum);
            }
        }
        return langFreq.entrySet().stream()
                .max(Map.Entry.comparingByValue())
                .map(Map.Entry::getKey)
                .orElse("java");
    }

    private boolean isJsType(NodeType t) {
        return t == NodeType.MODULE || t == NodeType.FUNCTION || t == NodeType.ARROW_FUNCTION
                || t == NodeType.COMPONENT || t == NodeType.HOOK || t == NodeType.ASYNC_FUNCTION;
    }

    private double computeCohesion(CodeGraph graph, List<String> members) {
        Set<String> memberSet = new HashSet<>(members);
        long internal = 0, boundary = 0;
        for (CodeRelationship r : graph.getRelationships()) {
            if (r.type() != RelationshipType.CALLS) continue;
            boolean srcIn = memberSet.contains(r.sourceId());
            boolean tgtIn = memberSet.contains(r.targetId());
            if (srcIn && tgtIn) internal++;
            else if (srcIn || tgtIn) boundary++;
        }
        long total = internal + boundary;
        return total == 0 ? 1.0 : (double) internal / total;
    }

    // -------------------------------------------------------------------------
    // Workflow type back-fill
    // -------------------------------------------------------------------------

    private void updateWorkflowTypes(CodeGraph graph) {
        // Build nodeId -> domainId index
        Map<String, String> nodeToDomain = new HashMap<>();
        for (CodeRelationship r : graph.getRelationships()) {
            if (r.type() == RelationshipType.IN_DOMAIN) {
                nodeToDomain.put(r.sourceId(), r.targetId());
            }
        }

        // For each Workflow, collect the domains of its steps
        Map<String, Set<String>> workflowDomains = new HashMap<>();
        for (CodeRelationship r : graph.getRelationships()) {
            if (r.type() != RelationshipType.WORKFLOW_STEP) continue;
            String domain = nodeToDomain.get(r.sourceId());
            if (domain != null) {
                workflowDomains.computeIfAbsent(r.targetId(), k -> new HashSet<>()).add(domain);
            }
        }

        for (Map.Entry<String, Set<String>> entry : workflowDomains.entrySet()) {
            CodeNode wf = graph.getNode(entry.getKey());
            if (wf == null || wf.type() != NodeType.WORKFLOW) continue;
            String type = entry.getValue().size() > 1 ? "cross-domain" : "intra-domain";
            wf.withProperty("type", type); // CodeNode.properties is a mutable HashMap
        }
    }

    // -------------------------------------------------------------------------
    // Utilities
    // -------------------------------------------------------------------------

    private double confidenceOf(CodeRelationship rel) {
        Object conf = rel.properties().get("confidence");
        if (conf instanceof Number num) return num.doubleValue();
        Object resolved = rel.properties().get("resolved");
        if (Boolean.TRUE.equals(resolved)) return 0.90;
        return 0.50;
    }

    private static String capitalize(String s) {
        if (s == null || s.isEmpty()) return s;
        return Character.toUpperCase(s.charAt(0)) + s.substring(1);
    }
}
