package code.graph.parser;

import code.graph.model.*;
import com.google.gson.*;

import java.io.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;

/**
 * Parses PHP source files using nikic/php-parser.
 * Shells out to parsers/php/parse.php for AST parsing.
 */
public class PhpSourceParser implements SourceParser {

    private static final Set<String> EXTENSIONS = Set.of(".php", ".phtml", ".php5", ".php7", ".php8");
    private static final String PARSER_SCRIPT = "parsers/php/parse.php";

    private final Path workspaceRoot;

    public PhpSourceParser(Path workspaceRoot) {
        this.workspaceRoot = workspaceRoot;
    }

    @Override
    public CodeGraph parseDirectory(Path root) throws IOException {
        String phpCmd = findPhpCommand();
        if (phpCmd == null) {
            throw new IOException("PHP is required for PHP parsing. Please install PHP 7.4+.");
        }

        Path scriptPath = findParserScript();
        ProcessBuilder pb = new ProcessBuilder(
                phpCmd, "-d", "memory_limit=1G",
                scriptPath.toString(), root.toString(), "--backend", "json");
        pb.redirectErrorStream(false);

        System.out.printf("Parsing PHP files in: %s%n", root);

        Process process = pb.start();

        String output;
        String errors;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
             BufferedReader errorReader = new BufferedReader(new InputStreamReader(process.getErrorStream()))) {

            output = reader.lines().reduce("", (a, b) -> a + b + "\n");
            errors = errorReader.lines().reduce("", (a, b) -> a + b + "\n");
        }

        boolean finished;
        try {
            finished = process.waitFor(5, TimeUnit.MINUTES);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            process.destroyForcibly();
            throw new IOException("PHP parser interrupted", e);
        }

        if (!finished) {
            process.destroyForcibly();
            throw new IOException("PHP parser timed out");
        }

        if (!errors.isEmpty()) {
            for (String line : errors.split("\n")) {
                if (!line.isEmpty()) System.out.println(line);
            }
        }

        int exitCode = process.exitValue();
        if (exitCode != 0) {
            throw new IOException("PHP parser failed with exit code " + exitCode + ": " + errors);
        }

        return parseJsonOutput(output);
    }

    private CodeGraph parseJsonOutput(String json) {
        CodeGraph graph = new CodeGraph();
        Gson gson = new Gson();

        JsonObject root = gson.fromJson(json, JsonObject.class);

        JsonArray nodes = root.getAsJsonArray("nodes");
        for (JsonElement elem : nodes) {
            JsonObject node = elem.getAsJsonObject();
            String id            = node.get("id").getAsString();
            String typeStr       = node.get("type").getAsString();
            String name          = node.get("name").getAsString();
            String qualifiedName = node.get("qualifiedName").getAsString();

            NodeType type = mapNodeType(typeStr);
            if (type == null) continue;

            CodeNode codeNode = new CodeNode(id, type, name, qualifiedName);

            if (node.has("properties")) {
                JsonObject props = node.getAsJsonObject("properties");
                for (Map.Entry<String, JsonElement> entry : props.entrySet()) {
                    JsonElement value = entry.getValue();
                    if (value.isJsonPrimitive()) {
                        JsonPrimitive prim = value.getAsJsonPrimitive();
                        if (prim.isNumber()) {
                            String raw = prim.getAsString();
                            Object numVal = raw.contains(".") ? prim.getAsDouble() : prim.getAsInt();
                            codeNode = codeNode.withProperty(entry.getKey(), numVal);
                        } else if (prim.isBoolean()) {
                            codeNode = codeNode.withProperty(entry.getKey(), prim.getAsBoolean());
                        } else {
                            codeNode = codeNode.withProperty(entry.getKey(), prim.getAsString());
                        }
                    }
                }
            }

            graph.addNode(codeNode);
        }

        JsonArray relationships = root.getAsJsonArray("relationships");
        for (JsonElement elem : relationships) {
            JsonObject rel   = elem.getAsJsonObject();
            String sourceId  = rel.get("sourceId").getAsString();
            String targetId  = rel.get("targetId").getAsString();
            String typeStr   = rel.get("type").getAsString();

            RelationshipType type = mapRelationshipType(typeStr);
            if (type == null) continue;

            CodeRelationship codeRel = new CodeRelationship(sourceId, targetId, type);

            if (rel.has("properties")) {
                JsonObject props = rel.getAsJsonObject("properties");
                for (Map.Entry<String, JsonElement> entry : props.entrySet()) {
                    JsonElement value = entry.getValue();
                    if (value.isJsonPrimitive()) {
                        JsonPrimitive prim = value.getAsJsonPrimitive();
                        if (prim.isNumber()) {
                            String raw = prim.getAsString();
                            Object numVal = raw.contains(".") ? prim.getAsDouble() : prim.getAsInt();
                            codeRel = codeRel.withProperty(entry.getKey(), numVal);
                        } else if (prim.isBoolean()) {
                            codeRel = codeRel.withProperty(entry.getKey(), prim.getAsBoolean());
                        } else {
                            codeRel = codeRel.withProperty(entry.getKey(), prim.getAsString());
                        }
                    }
                }
            }

            graph.addRelationship(codeRel);
        }

        return graph;
    }

    private NodeType mapNodeType(String type) {
        return switch (type.toUpperCase()) {
            case "MODULE"      -> NodeType.MODULE;
            case "CLASS"       -> NodeType.CLASS;
            case "INTERFACE"   -> NodeType.INTERFACE;
            case "FUNCTION"    -> NodeType.FUNCTION;
            case "METHOD"      -> NodeType.METHOD;
            case "CONSTRUCTOR" -> NodeType.CONSTRUCTOR;
            case "FIELD"       -> NodeType.FIELD;
            case "FILE"        -> NodeType.FILE;
            case "DECORATOR"   -> NodeType.DECORATOR;
            default -> {
                System.err.printf("Warning: unknown PHP node type '%s', skipping%n", type);
                yield null;
            }
        };
    }

    private RelationshipType mapRelationshipType(String type) {
        return switch (type.toUpperCase()) {
            case "CONTAINS"       -> RelationshipType.CONTAINS;
            case "IMPORTS"        -> RelationshipType.IMPORTS;
            case "CALLS"          -> RelationshipType.CALLS;
            case "EXTENDS"        -> RelationshipType.EXTENDS;
            case "IMPLEMENTS"     -> RelationshipType.IMPLEMENTS;
            case "SOURCE_FILE"    -> RelationshipType.SOURCE_FILE;
            case "HAS_ANNOTATION" -> RelationshipType.HAS_ANNOTATION;
            case "OF_TYPE"        -> RelationshipType.OF_TYPE;
            default -> {
                System.err.printf("Warning: unknown PHP rel type '%s', skipping%n", type);
                yield null;
            }
        };
    }

    private String findPhpCommand() {
        for (String cmd : new String[]{"php", "php8", "php8.2", "php8.1", "php7.4"}) {
            try {
                ProcessBuilder pb = new ProcessBuilder(cmd, "--version");
                Process p = pb.start();
                boolean finished = p.waitFor(10, TimeUnit.SECONDS);
                if (finished && p.exitValue() == 0) {
                    try (BufferedReader reader = new BufferedReader(
                            new InputStreamReader(p.getInputStream()))) {
                        String version = reader.readLine();
                        if (version != null && version.startsWith("PHP")) {
                            return cmd;
                        }
                    }
                }
            } catch (Exception e) {
                // try next
            }
        }
        return null;
    }

    private Path findParserScript() throws IOException {
        Path scriptPath = workspaceRoot.resolve(PARSER_SCRIPT);
        if (Files.exists(scriptPath)) return scriptPath;

        scriptPath = Path.of(PARSER_SCRIPT).toAbsolutePath();
        if (Files.exists(scriptPath)) return scriptPath;

        throw new IOException("Cannot find PHP parser at " + PARSER_SCRIPT);
    }

    @Override
    public Set<String> getSupportedExtensions() {
        return EXTENSIONS;
    }

    @Override
    public String getLanguageName() {
        return "PHP";
    }

    @Override
    public boolean supportsIncremental() {
        return false;
    }
}
