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
 * Parses JavaScript/React/TypeScript source files using Node.js + Babel.
 * Shells out to parsers/javascript/parse.js for AST parsing.
 */
public class JavaScriptSourceParser implements SourceParser {

    private static final Set<String> EXTENSIONS = Set.of(".js", ".jsx", ".ts", ".tsx", ".mjs");
    private static final String PARSER_SCRIPT = "parsers/javascript/parse.js";

    private final Path workspaceRoot;

    public JavaScriptSourceParser(Path workspaceRoot) {
        this.workspaceRoot = workspaceRoot;
    }

    @Override
    public CodeGraph parseDirectory(Path root) throws IOException {
        // Check if Node.js is available
        if (!isNodeAvailable()) {
            throw new IOException("Node.js is required for JavaScript/React parsing. Please install Node.js.");
        }

        // Check if parser dependencies are installed
        Path parserDir = findParserDirectory();
        Path nodeModules = parserDir.resolve("node_modules");
        if (!Files.exists(nodeModules)) {
            System.out.println("Installing JavaScript parser dependencies...");
            installDependencies(parserDir);
        }

        // Run the parser
        Path scriptPath = parserDir.resolve("parse.js");
        ProcessBuilder pb = new ProcessBuilder("node", scriptPath.toString(), root.toString(), "--backend", "json");
        pb.redirectErrorStream(false);

        System.out.printf("Parsing JavaScript/React files in: %s%n", root);

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
            throw new IOException("JavaScript parser interrupted", e);
        }
        
        if (!finished) {
            process.destroyForcibly();
            throw new IOException("JavaScript parser timed out");
        }

        if (!errors.isEmpty()) {
            // Parser outputs progress to stderr, only show if there's an actual error
            for (String line : errors.split("\n")) {
                if (line.startsWith("Found ") || line.startsWith("Parse error")) {
                    System.out.println(line);
                }
            }
        }

        int exitCode = process.exitValue();
        if (exitCode != 0) {
            throw new IOException("JavaScript parser failed with exit code " + exitCode + ": " + errors);
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
            if (type == null) continue;  // skip unrecognised node types

            CodeNode codeNode = new CodeNode(id, type, name, qualifiedName);
            
            // Copy properties
            if (node.has("properties")) {
                JsonObject props = node.getAsJsonObject("properties");
                for (Map.Entry<String, JsonElement> entry : props.entrySet()) {
                    JsonElement value = entry.getValue();
                    if (value.isJsonPrimitive()) {
                        JsonPrimitive prim = value.getAsJsonPrimitive();
                        if (prim.isNumber()) {
                            // Use double for fractional values (e.g. confidence=0.90), int for whole numbers
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
        
        // Parse relationships
        JsonArray relationships = root.getAsJsonArray("relationships");
        for (JsonElement elem : relationships) {
            JsonObject rel = elem.getAsJsonObject();
            String sourceId = rel.get("sourceId").getAsString();
            String targetId = rel.get("targetId").getAsString();
            String typeStr = rel.get("type").getAsString();
            
            RelationshipType type = mapRelationshipType(typeStr);
            if (type == null) continue;  // skip unrecognised rel types
            CodeRelationship codeRel = new CodeRelationship(sourceId, targetId, type);
            
            // Copy properties
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
            case "MODULE"        -> NodeType.MODULE;
            case "CLASS"         -> NodeType.CLASS;
            case "FUNCTION"      -> NodeType.FUNCTION;
            case "METHOD"        -> NodeType.METHOD;
            case "CONSTRUCTOR"   -> NodeType.CONSTRUCTOR;
            case "ARROW_FUNCTION"-> NodeType.ARROW_FUNCTION;
            case "COMPONENT"     -> NodeType.COMPONENT;
            case "HOOK"          -> NodeType.HOOK;
            case "JSX_ELEMENT"   -> NodeType.JSX_ELEMENT;
            case "ASYNC_FUNCTION"-> NodeType.ASYNC_FUNCTION;
            case "GENERATOR"     -> NodeType.GENERATOR;
            case "FILE"          -> NodeType.FILE;
            case "STATEMENT"     -> NodeType.STATEMENT;  // CPG statement nodes
            case "FIELD"         -> NodeType.FIELD;       // TypeScript class/interface properties
            case "DECORATOR"     -> NodeType.DECORATOR;
            default -> {
                System.err.printf("Warning: unknown JS node type '%s', skipping%n", type);
                yield null;
            }
        };
    }

    private RelationshipType mapRelationshipType(String type) {
        return switch (type.toUpperCase()) {
            case "CONTAINS"     -> RelationshipType.CONTAINS;
            case "IMPORTS"      -> RelationshipType.IMPORTS;
            case "EXPORTS"      -> RelationshipType.EXPORTS;
            case "CALLS"        -> RelationshipType.CALLS;
            case "RENDERS"      -> RelationshipType.RENDERS;
            case "USES_HOOK"    -> RelationshipType.USES_HOOK;
            case "EXTENDS"      -> RelationshipType.EXTENDS;
            case "SOURCE_FILE"  -> RelationshipType.SOURCE_FILE;
            case "OF_TYPE"       -> RelationshipType.OF_TYPE;
            case "HAS_ANNOTATION"-> RelationshipType.HAS_ANNOTATION;
            case "PROP_DEPENDENCY"-> RelationshipType.PROP_DEPENDENCY;
            case "IMPLEMENTS"    -> RelationshipType.IMPLEMENTS;
            case "AST_CHILD"     -> RelationshipType.AST_CHILD;
            case "CFG_NEXT"      -> RelationshipType.CFG_NEXT;
            case "DATA_FLOW"     -> RelationshipType.DATA_FLOW;
            case "DECORATES"     -> RelationshipType.DECORATES;
            case "YIELDS"        -> RelationshipType.YIELDS;
            default -> {
                System.err.printf("Warning: unknown JS rel type '%s', skipping%n", type);
                yield null;
            }
        };
    }

    private boolean isNodeAvailable() {
        try {
            Process p = new ProcessBuilder("node", "--version").start();
            boolean finished = p.waitFor(10, TimeUnit.SECONDS);
            return finished && p.exitValue() == 0;
        } catch (Exception e) {
            return false;
        }
    }

    private Path findParserDirectory() throws IOException {
        // Look for parsers/javascript relative to workspace root
        Path parserDir = workspaceRoot.resolve(PARSER_SCRIPT).getParent();
        if (Files.exists(parserDir)) {
            return parserDir;
        }
        
        // Try current working directory
        parserDir = Path.of(PARSER_SCRIPT).toAbsolutePath().getParent();
        if (Files.exists(parserDir)) {
            return parserDir;
        }
        
        throw new IOException("Cannot find JavaScript parser at " + PARSER_SCRIPT);
    }

    private void installDependencies(Path parserDir) throws IOException {
        try {
            ProcessBuilder pb = new ProcessBuilder("npm", "install");
            pb.directory(parserDir.toFile());
            pb.inheritIO();
            Process p = pb.start();
            boolean finished = p.waitFor(5, TimeUnit.MINUTES);
            if (!finished || p.exitValue() != 0) {
                throw new IOException("Failed to install JavaScript parser dependencies");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("Interrupted while installing dependencies", e);
        }
    }

    @Override
    public Set<String> getSupportedExtensions() {
        return EXTENSIONS;
    }

    @Override
    public String getLanguageName() {
        return "JavaScript/React";
    }

    @Override
    public boolean supportsIncremental() {
        return false;
    }
}
