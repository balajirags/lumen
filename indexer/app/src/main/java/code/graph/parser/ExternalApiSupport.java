package code.graph.parser;

import code.graph.model.CodeGraph;
import code.graph.model.CodeNode;
import code.graph.model.CodeRelationship;
import code.graph.model.NodeType;
import code.graph.model.RelationshipType;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.io.Reader;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Shared helpers for detecting outbound HTTP calls (JVM parsers).
 *
 * Emits {@code ExternalApi} nodes and {@code CALLS_EXTERNAL} edges, and resolves Spring-style
 * {@code ${property:default}} placeholders against application.yml / application.properties
 * so that URLs such as {@code ${payments.base-url}/charges} become concrete when the config is in the repo.
 */
final class ExternalApiSupport {

    static final Set<String> HTTP_VERBS = Set.of("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS");

    private static final Set<String> SKIPPED_DIRS = Set.of(
            "node_modules", "build", "target", ".git", ".gradle", ".idea", "out", "dist", "venv", ".venv", "vendor", "test", "tests");
    private static final Pattern PLACEHOLDER = Pattern.compile("\\$\\{([^}:]+)(?::([^}]*))?}");
    private static final int MAX_PLACEHOLDER_DEPTH = 4;

    private final Map<String, String> config;
    private final Map<String, String> normalizedConfig = new HashMap<>();

    ExternalApiSupport(Map<String, String> config) {
        this.config = config;
        for (var e : config.entrySet()) {
            normalizedConfig.putIfAbsent(normalizeKey(e.getKey()), e.getValue());
        }
    }

    static ExternalApiSupport empty() {
        return new ExternalApiSupport(Map.of());
    }

    // ---------------------------------------------------------------------------------------------
    // Config loading
    // ---------------------------------------------------------------------------------------------

    /** Load flattened properties from application and bootstrap yml/yaml/properties files under root. */
    static ExternalApiSupport load(Path sourceRoot) {
        Path root = projectRoot(sourceRoot);
        List<Path> files = new ArrayList<>();
        try {
            Files.walkFileTree(root, new SimpleFileVisitor<>() {
                @Override
                public FileVisitResult preVisitDirectory(Path dir, BasicFileAttributes attrs) {
                    Path name = dir.getFileName();
                    if (name != null && !dir.equals(root) && SKIPPED_DIRS.contains(name.toString())) {
                        return FileVisitResult.SKIP_SUBTREE;
                    }
                    return FileVisitResult.CONTINUE;
                }

                @Override
                public FileVisitResult visitFile(Path file, BasicFileAttributes attrs) {
                    String n = file.getFileName().toString();
                    boolean named = n.startsWith("application") || n.startsWith("bootstrap");
                    boolean ext = n.endsWith(".yml") || n.endsWith(".yaml") || n.endsWith(".properties");
                    if (named && ext) files.add(file);
                    return FileVisitResult.CONTINUE;
                }
            });
        } catch (IOException e) {
            return empty();
        }
        // Un-profiled files (application.yml) win over profile-specific ones (application-dev.yml).
        files.sort(Comparator.comparingInt((Path p) -> p.getFileName().toString().contains("-") ? 1 : 0)
                .thenComparing(Path::toString));

        Map<String, String> merged = new HashMap<>();
        for (Path file : files) {
            try {
                Map<String, String> flat = file.toString().endsWith(".properties")
                        ? readProperties(file) : readYaml(file);
                flat.forEach(merged::putIfAbsent);
            } catch (Exception ignored) {
                // Unparseable config (templated values, etc.) is simply skipped.
            }
        }
        if (!merged.isEmpty()) {
            System.out.printf("Loaded %d config properties for outbound URL resolution%n", merged.size());
        }
        return new ExternalApiSupport(merged);
    }

    /** The parser is often pointed at src/main/java; config lives next to it in src/main/resources. */
    static Path projectRoot(Path sourceRoot) {
        Path p = sourceRoot.toAbsolutePath().normalize();
        int n = p.getNameCount();
        if (n >= 3 && p.getName(n - 2).toString().equals("main") && p.getName(n - 3).toString().equals("src")) {
            Path project = p.getParent().getParent().getParent();
            if (project != null) return project;
        }
        return p;
    }

    private static Map<String, String> readProperties(Path file) throws IOException {
        Properties props = new Properties();
        try (Reader r = Files.newBufferedReader(file)) {
            props.load(r);
        }
        Map<String, String> out = new HashMap<>();
        props.forEach((k, v) -> out.put(String.valueOf(k), String.valueOf(v)));
        return out;
    }

    private static Map<String, String> readYaml(Path file) throws IOException {
        Map<String, String> out = new HashMap<>();
        try (Reader r = Files.newBufferedReader(file)) {
            for (Object doc : new Yaml().loadAll(r)) {
                flatten("", doc, out);
            }
        }
        return out;
    }

    private static void flatten(String prefix, Object value, Map<String, String> out) {
        if (value instanceof Map<?, ?> map) {
            for (var e : map.entrySet()) {
                String key = prefix.isEmpty() ? String.valueOf(e.getKey()) : prefix + "." + e.getKey();
                flatten(key, e.getValue(), out);
            }
        } else if (value instanceof List<?> list) {
            for (int i = 0; i < list.size(); i++) {
                flatten(prefix + "[" + i + "]", list.get(i), out);
            }
        } else if (value != null && !prefix.isEmpty()) {
            out.putIfAbsent(prefix, String.valueOf(value));
        }
    }

    // ---------------------------------------------------------------------------------------------
    // Placeholder / URL helpers
    // ---------------------------------------------------------------------------------------------

    private static String normalizeKey(String key) {
        return key.toLowerCase(Locale.ROOT).replaceAll("[._\\-]", "");
    }

    /** Replace ${key:default} using loaded config, then the inline default; unresolved ones stay as ${key}. */
    String resolvePlaceholders(String text) {
        return resolvePlaceholders(text, 0);
    }

    private String resolvePlaceholders(String text, int depth) {
        if (text == null || !text.contains("${") || depth > MAX_PLACEHOLDER_DEPTH) return text;
        Matcher m = PLACEHOLDER.matcher(text);
        StringBuilder sb = new StringBuilder();
        while (m.find()) {
            String key = m.group(1).trim();
            String def = m.group(2);
            String value = config.get(key);
            if (value == null) value = normalizedConfig.get(normalizeKey(key));
            if (value == null) value = def;
            m.appendReplacement(sb, Matcher.quoteReplacement(value != null ? value : "${" + key + "}"));
        }
        m.appendTail(sb);
        String result = sb.toString();
        return result.equals(text) ? result : resolvePlaceholders(result, depth + 1);
    }

    static String hostOf(String url) {
        if (url == null || url.isBlank()) return "(unknown)";
        int scheme = url.indexOf("://");
        if (scheme >= 0) {
            String rest = url.substring(scheme + 3);
            int end = rest.length();
            for (int i = 0; i < rest.length(); i++) {
                char c = rest.charAt(i);
                if (c == '/' || c == '?' || c == '#') { end = i; break; }
            }
            String host = rest.substring(0, end);
            int at = host.lastIndexOf('@');
            return at >= 0 ? host.substring(at + 1) : host;
        }
        if (url.startsWith("${")) {
            int close = url.indexOf('}');
            if (close > 0) return url.substring(0, close + 1);
        }
        if (url.startsWith("{")) {
            int close = url.indexOf('}');
            if (close > 0) return url.substring(0, close + 1);
        }
        return url.startsWith("/") ? "(relative)" : "(unknown)";
    }

    static boolean isAbsolute(String url) {
        return url != null && (url.contains("://") || url.startsWith("${") || url.startsWith("{"));
    }

    static String joinUrl(String base, String path) {
        if (base == null || base.isBlank()) return path;
        if (path == null || path.isBlank()) return base;
        boolean baseSlash = base.endsWith("/");
        boolean pathSlash = path.startsWith("/");
        if (baseSlash && pathSlash) return base + path.substring(1);
        if (!baseSlash && !pathSlash) return base + "/" + path;
        return base + path;
    }

    // ---------------------------------------------------------------------------------------------
    // Graph emission
    // ---------------------------------------------------------------------------------------------

    /**
     * Add (or reuse) an ExternalApi node and a CALLS_EXTERNAL edge from the caller.
     *
     * @param language "java", "kotlin" or "python" - stored on the node so toolkit queries can filter
     */
    void addCall(CodeGraph graph, String callerId, String httpMethod, String url, String client,
                 int line, double confidence, String reason, String language) {
        String method = httpMethod == null ? "UNKNOWN" : httpMethod.toUpperCase(Locale.ROOT);
        String resolvedUrl = resolvePlaceholders(url == null || url.isBlank() ? "{dynamic}" : url);
        String apiId = "extapi:" + method + " " + resolvedUrl;
        if (!graph.hasNode(apiId)) {
            graph.addNode(new CodeNode(apiId, NodeType.EXTERNAL_API, method + " " + resolvedUrl, resolvedUrl)
                    .withProperty("httpMethod", method)
                    .withProperty("url", resolvedUrl)
                    .withProperty("host", hostOf(resolvedUrl))
                    .withProperty("client", client)
                    .withProperty("language", language));
        }
        graph.addRelationship(new CodeRelationship(callerId, apiId, RelationshipType.CALLS_EXTERNAL)
                .withProperty("lineNumber", line)
                .withProperty("client", client)
                .withProperty("confidence", confidence)
                .withProperty("reason", reason));
    }
}
