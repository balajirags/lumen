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
 * Parses Python source files using Python's ast module.
 * Shells out to parsers/python/parse.py for AST parsing.
 */
public class PythonSourceParser implements SourceParser {

    private static final Set<String> EXTENSIONS = Set.of(".py", ".pyw");
    private static final String PARSER_SCRIPT = "parsers/python/parse.py";

    private final Path workspaceRoot;

    public PythonSourceParser(Path workspaceRoot) {
        this.workspaceRoot = workspaceRoot;
    }

    @Override
    public CodeGraph parseDirectory(Path root) throws IOException {
        // Check if Python is available
        String pythonCmd = findPythonCommand();
        if (pythonCmd == null) {
            throw new IOException("Python 3 is required for Python parsing. Please install Python 3.");
        }

        // Run the parser
        Path scriptPath = findParserScript();
        ProcessBuilder pb = new ProcessBuilder(pythonCmd, scriptPath.toString(), root.toString());
        pb.redirectErrorStream(false);

        System.out.printf("Parsing Python files in: %s%n", root);

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
            throw new IOException("Python parser interrupted", e);
        }
        
        if (!finished) {
            process.destroyForcibly();
            throw new IOException("Python parser timed out");
        }

        if (!errors.isEmpty()) {
            // Parser outputs progress to stderr
            for (String line : errors.split("\n")) {
                if (!line.isEmpty()) {
                    System.out.println(line);
                }
            }
        }

        int exitCode = process.exitValue();
        if (exitCode != 0) {
            throw new IOException("Python parser failed with exit code " + exitCode + ": " + errors);
        }

        return parseJsonOutput(output);
    }

    private CodeGraph parseJsonOutput(String json) {
        CodeGraph graph = new CodeGraph();
        Gson gson = new Gson();
        
        JsonObject root = gson.fromJson(json, JsonObject.class);
        
        // Parse nodes
        JsonArray nodes = root.getAsJsonArray("nodes");
        for (JsonElement elem : nodes) {
            JsonObject node = elem.getAsJsonObject();
            String id = node.get("id").getAsString();
            String typeStr = node.get("type").getAsString();
            String name = node.get("name").getAsString();
            String qualifiedName = node.get("qualifiedName").getAsString();
            
            NodeType type = mapNodeType(typeStr);
            
            CodeNode codeNode = new CodeNode(id, type, name, qualifiedName);
            
            // Copy properties
            if (node.has("properties")) {
                JsonObject props = node.getAsJsonObject("properties");
                for (Map.Entry<String, JsonElement> entry : props.entrySet()) {
                    JsonElement value = entry.getValue();
                    if (value.isJsonPrimitive()) {
                        JsonPrimitive prim = value.getAsJsonPrimitive();
                        if (prim.isNumber()) {
                            codeNode = codeNode.withProperty(entry.getKey(), prim.getAsInt());
                        } else if (prim.isBoolean()) {
                            codeNode = codeNode.withProperty(entry.getKey(), prim.getAsBoolean());
                        } else {
                            codeNode = codeNode.withProperty(entry.getKey(), prim.getAsString());
                        }
                    } else if (value.isJsonArray()) {
                        // Handle arrays like decorators list
                        codeNode = codeNode.withProperty(entry.getKey(), value.toString());
                    }
                }
            }
            
            graph.addNode(codeNode);
        }
        
        // Parse relationships
        JsonArray relationships = root.getAsJsonArray("relationships");
        for (JsonElement elem : relationships) {
            JsonObject rel = elem.getAsJsonObject();
            String sourceId = rel.get("sourceId").getAsString();
            String targetId = rel.get("targetId").getAsString();
            String typeStr = rel.get("type").getAsString();
            
            RelationshipType type = mapRelationshipType(typeStr);
            CodeRelationship codeRel = new CodeRelationship(sourceId, targetId, type);
            
            // Copy properties
            if (rel.has("properties")) {
                JsonObject props = rel.getAsJsonObject("properties");
                for (Map.Entry<String, JsonElement> entry : props.entrySet()) {
                    JsonElement value = entry.getValue();
                    if (value.isJsonPrimitive()) {
                        JsonPrimitive prim = value.getAsJsonPrimitive();
                        if (prim.isNumber()) {
                            codeRel = codeRel.withProperty(entry.getKey(), prim.getAsInt());
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
            case "MODULE" -> NodeType.MODULE;
            case "CLASS" -> NodeType.CLASS;
            case "FUNCTION" -> NodeType.FUNCTION;
            case "METHOD" -> NodeType.METHOD;
            case "CONSTRUCTOR" -> NodeType.CONSTRUCTOR;
            case "ASYNC_FUNCTION" -> NodeType.ASYNC_FUNCTION;
            case "GENERATOR" -> NodeType.GENERATOR;
            case "DECORATOR" -> NodeType.DECORATOR;
            case "FILE" -> NodeType.FILE;
            default -> NodeType.CLASS; // fallback
        };
    }

    private RelationshipType mapRelationshipType(String type) {
        return switch (type.toUpperCase()) {
            case "CONTAINS" -> RelationshipType.CONTAINS;
            case "IMPORTS" -> RelationshipType.IMPORTS;
            case "CALLS" -> RelationshipType.CALLS;
            case "EXTENDS" -> RelationshipType.EXTENDS;
            case "DECORATES" -> RelationshipType.DECORATES;
            case "SOURCE_FILE" -> RelationshipType.SOURCE_FILE;
            default -> RelationshipType.CONTAINS; // fallback
        };
    }

    private String findPythonCommand() {
        // Try python3 first, then python
        for (String cmd : new String[]{"python3", "python"}) {
            try {
                ProcessBuilder pb = new ProcessBuilder(cmd, "--version");
                Process p = pb.start();
                boolean finished = p.waitFor(10, TimeUnit.SECONDS);
                if (finished && p.exitValue() == 0) {
                    try (BufferedReader reader = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                        String version = reader.readLine();
                        if (version != null && version.contains("Python 3")) {
                            return cmd;
                        }
                    }
                }
            } catch (Exception e) {
                // Try next command
            }
        }
        return null;
    }

    private Path findParserScript() throws IOException {
        // Look for parsers/python/parse.py relative to workspace root
        Path scriptPath = workspaceRoot.resolve(PARSER_SCRIPT);
        if (Files.exists(scriptPath)) {
            return scriptPath;
        }
        
        // Try current working directory
        scriptPath = Path.of(PARSER_SCRIPT).toAbsolutePath();
        if (Files.exists(scriptPath)) {
            return scriptPath;
        }
        
        throw new IOException("Cannot find Python parser at " + PARSER_SCRIPT);
    }

    @Override
    public Set<String> getSupportedExtensions() {
        return EXTENSIONS;
    }

    @Override
    public String getLanguageName() {
        return "Python";
    }

    @Override
    public boolean supportsIncremental() {
        return false;
    }
}
