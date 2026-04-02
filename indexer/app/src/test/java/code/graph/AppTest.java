package code.graph;

import code.graph.export.DotExporter;
import code.graph.export.GraphMlExporter;
import code.graph.export.JsonExporter;
import code.graph.model.*;
import code.graph.parser.FileHashCache;
import code.graph.parser.JavaSourceParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.io.StringWriter;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class AppTest {

    @TempDir
    Path tempDir;

    @Test
    void parsesClassWithMethodAndField() throws IOException {
        // Write a sample Java file
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);
        Files.writeString(pkg.resolve("Greeting.java"), """
                package com.example;

                public class Greeting {
                    private String message;

                    public String greet(String name) {
                        return message + " " + name;
                    }
                }
                """);

        JavaSourceParser parser = new JavaSourceParser(tempDir);
        CodeGraph graph = parser.parseDirectory(tempDir);

        // Should have package, class, method, field, and parameter nodes at minimum
        assertTrue(graph.nodeCount() > 0, "Graph should have nodes");
        assertTrue(graph.relationshipCount() > 0, "Graph should have relationships");

        // Check for the class node
        assertTrue(graph.hasNode("type:com.example.Greeting"), "Should have Greeting class node");

        // Check for the method node
        assertTrue(graph.hasNode("method:com.example.Greeting.greet(String)"), "Should have greet method node");

        // Check for the field node
        assertTrue(graph.hasNode("field:com.example.Greeting.message"), "Should have message field node");

        // Check for the package node
        assertTrue(graph.hasNode("pkg:com.example"), "Should have com.example package node");
    }

    @Test
    void parsesInterfaceAndImplementation() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);

        Files.writeString(pkg.resolve("Greeter.java"), """
                package com.example;

                public interface Greeter {
                    String greet();
                }
                """);

        Files.writeString(pkg.resolve("SimpleGreeter.java"), """
                package com.example;

                public class SimpleGreeter implements Greeter {
                    @Override
                    public String greet() {
                        return "Hello";
                    }
                }
                """);

        JavaSourceParser parser = new JavaSourceParser(tempDir);
        CodeGraph graph = parser.parseDirectory(tempDir);

        assertTrue(graph.hasNode("type:com.example.Greeter"), "Should have Greeter interface node");
        assertTrue(graph.hasNode("type:com.example.SimpleGreeter"), "Should have SimpleGreeter class node");

        // Verify IMPLEMENTS relationship exists
        boolean hasImplements = graph.getRelationships().stream()
                .anyMatch(r -> r.type() == RelationshipType.IMPLEMENTS
                        && r.sourceId().contains("SimpleGreeter")
                        && r.targetId().contains("Greeter"));
        assertTrue(hasImplements, "Should have IMPLEMENTS relationship");
    }

    @Test
    void codeGraphMergeWorks() {
        CodeGraph g1 = new CodeGraph();
        g1.addNode(new CodeNode("a", NodeType.CLASS, "A", "com.A"));

        CodeGraph g2 = new CodeGraph();
        g2.addNode(new CodeNode("b", NodeType.CLASS, "B", "com.B"));
        g2.addRelationship(new CodeRelationship("a", "b", RelationshipType.EXTENDS));

        g1.merge(g2);

        assertEquals(2, g1.nodeCount());
        assertEquals(1, g1.relationshipCount());
    }

    @Test
    void detectsMethodCalls() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);

        Files.writeString(pkg.resolve("Service.java"), """
                package com.example;

                public class Service {
                    public String process() {
                        return "done";
                    }
                }
                """);

        Files.writeString(pkg.resolve("Controller.java"), """
                package com.example;

                public class Controller {
                    private Service service;

                    public String handle() {
                        return service.process();
                    }
                }
                """);

        JavaSourceParser parser = new JavaSourceParser(tempDir);
        CodeGraph graph = parser.parseDirectory(tempDir);

        // Verify CALLS relationship exists from handle -> process
        List<CodeRelationship> calls = graph.getRelationships().stream()
                .filter(r -> r.type() == RelationshipType.CALLS)
                .toList();
        assertFalse(calls.isEmpty(), "Should have CALLS relationships");

        boolean hasExpectedCall = calls.stream()
                .anyMatch(r -> r.sourceId().contains("handle") && r.targetId().contains("process"));
        assertTrue(hasExpectedCall, "Controller.handle should call Service.process");
    }

    @Test
    void detectsExtendsRelationship() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);

        Files.writeString(pkg.resolve("Base.java"), """
                package com.example;

                public class Base {
                    public void doWork() {}
                }
                """);

        Files.writeString(pkg.resolve("Child.java"), """
                package com.example;

                public class Child extends Base {
                    public void extra() {}
                }
                """);

        JavaSourceParser parser = new JavaSourceParser(tempDir);
        CodeGraph graph = parser.parseDirectory(tempDir);

        boolean hasExtends = graph.getRelationships().stream()
                .anyMatch(r -> r.type() == RelationshipType.EXTENDS
                        && r.sourceId().contains("Child")
                        && r.targetId().contains("Base"));
        assertTrue(hasExtends, "Should have EXTENDS relationship from Child to Base");
    }

    @Test
    void detectsOverridesRelationship() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);

        Files.writeString(pkg.resolve("Animal.java"), """
                package com.example;

                public class Animal {
                    public String speak() {
                        return "...";
                    }
                }
                """);

        Files.writeString(pkg.resolve("Dog.java"), """
                package com.example;

                public class Dog extends Animal {
                    @Override
                    public String speak() {
                        return "Woof";
                    }
                }
                """);

        JavaSourceParser parser = new JavaSourceParser(tempDir);
        CodeGraph graph = parser.parseDirectory(tempDir);

        List<CodeRelationship> overrides = graph.getRelationships().stream()
                .filter(r -> r.type() == RelationshipType.OVERRIDES)
                .toList();
        assertFalse(overrides.isEmpty(), "Should have OVERRIDES relationships");

        boolean dogOverridesAnimal = overrides.stream()
                .anyMatch(r -> r.sourceId().contains("Dog") && r.targetId().contains("Animal"));
        assertTrue(dogOverridesAnimal, "Dog.speak should override Animal.speak");
    }

    @Test
    void detectsThrowsRelationship() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);

        Files.writeString(pkg.resolve("Risky.java"), """
                package com.example;

                public class Risky {
                    public void doRisky() throws IllegalStateException {
                        throw new IllegalStateException("boom");
                    }
                }
                """);

        JavaSourceParser parser = new JavaSourceParser(tempDir);
        CodeGraph graph = parser.parseDirectory(tempDir);

        boolean hasThrows = graph.getRelationships().stream()
                .anyMatch(r -> r.type() == RelationshipType.THROWS
                        && r.sourceId().contains("doRisky"));
        assertTrue(hasThrows, "Should have THROWS relationship from doRisky");
    }

    @Test
    void detectsAnnotations() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);

        Files.writeString(pkg.resolve("Annotated.java"), """
                package com.example;

                @Deprecated
                public class Annotated {
                    @SuppressWarnings("unchecked")
                    public void doStuff() {}
                }
                """);

        JavaSourceParser parser = new JavaSourceParser(tempDir);
        CodeGraph graph = parser.parseDirectory(tempDir);

        assertTrue(graph.hasNode("annotation:Deprecated"), "Should have Deprecated annotation node");
        assertTrue(graph.hasNode("annotation:SuppressWarnings"), "Should have SuppressWarnings annotation node");

        boolean classAnnotated = graph.getRelationships().stream()
                .anyMatch(r -> r.type() == RelationshipType.HAS_ANNOTATION
                        && r.sourceId().contains("Annotated")
                        && r.targetId().contains("Deprecated"));
        assertTrue(classAnnotated, "Annotated class should have HAS_ANNOTATION to Deprecated");
    }

    @Test
    void parsesEnumAndRecord() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);

        Files.writeString(pkg.resolve("Color.java"), """
                package com.example;

                public enum Color {
                    RED, GREEN, BLUE
                }
                """);

        Files.writeString(pkg.resolve("Point.java"), """
                package com.example;

                public record Point(int x, int y) {}
                """);

        JavaSourceParser parser = new JavaSourceParser(tempDir);
        CodeGraph graph = parser.parseDirectory(tempDir);

        assertTrue(graph.hasNode("type:com.example.Color"), "Should have Color enum node");
        CodeNode colorNode = graph.getNode("type:com.example.Color");
        assertEquals(NodeType.ENUM, colorNode.type(), "Color should be ENUM type");

        assertTrue(graph.hasNode("type:com.example.Point"), "Should have Point record node");
        CodeNode pointNode = graph.getNode("type:com.example.Point");
        assertEquals(NodeType.RECORD, pointNode.type(), "Point should be RECORD type");

        // Record components should be extracted as FIELD nodes
        assertTrue(graph.hasNode("field:com.example.Point.x"), "Should have record component 'x' as field");
        assertTrue(graph.hasNode("field:com.example.Point.y"), "Should have record component 'y' as field");

        CodeNode fieldX = graph.getNode("field:com.example.Point.x");
        assertEquals(NodeType.FIELD, fieldX.type(), "Record component should be FIELD type");
        assertEquals(true, fieldX.properties().get("isRecordComponent"), "Should be marked as record component");

        // CONTAINS relationships from record to components
        boolean containsX = graph.getRelationships().stream()
                .anyMatch(r -> r.type() == RelationshipType.CONTAINS
                        && r.sourceId().equals("type:com.example.Point")
                        && r.targetId().equals("field:com.example.Point.x"));
        assertTrue(containsX, "Record should CONTAIN field x");
    }

    @Test
    void detectsReturnsAndParameterTypes() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);

        Files.writeString(pkg.resolve("Calculator.java"), """
                package com.example;

                public class Calculator {
                    public int add(int a, int b) {
                        return a + b;
                    }
                }
                """);

        JavaSourceParser parser = new JavaSourceParser(tempDir);
        CodeGraph graph = parser.parseDirectory(tempDir);

        // Check that parameters exist
        assertTrue(graph.hasNode("method:com.example.Calculator.add(int,int).param:a"),
                "Should have parameter 'a'");
        assertTrue(graph.hasNode("method:com.example.Calculator.add(int,int).param:b"),
                "Should have parameter 'b'");

        // Check HAS_PARAMETER relationships
        long paramRelCount = graph.getRelationships().stream()
                .filter(r -> r.type() == RelationshipType.HAS_PARAMETER
                        && r.sourceId().contains("add"))
                .count();
        assertEquals(2, paramRelCount, "add method should have 2 HAS_PARAMETER relationships");

        // Check RETURNS relationship
        boolean hasReturns = graph.getRelationships().stream()
                .anyMatch(r -> r.type() == RelationshipType.RETURNS
                        && r.sourceId().contains("add"));
        assertTrue(hasReturns, "add method should have RETURNS relationship");
    }

    @Test
    void exportsToDotFormat() throws IOException {
        CodeGraph graph = new CodeGraph();
        graph.addNode(new CodeNode("type:com.A", NodeType.CLASS, "A", "com.A"));
        graph.addNode(new CodeNode("type:com.B", NodeType.CLASS, "B", "com.B"));
        graph.addRelationship(new CodeRelationship("type:com.A", "type:com.B", RelationshipType.EXTENDS));

        StringWriter writer = new StringWriter();
        new DotExporter().export(graph, writer);
        String dot = writer.toString();

        assertTrue(dot.startsWith("digraph code_mem_graph {"), "Should start with digraph");
        assertTrue(dot.contains("type:com.A"), "Should contain node A");
        assertTrue(dot.contains("type:com.B"), "Should contain node B");
        assertTrue(dot.contains("EXTENDS"), "Should contain EXTENDS edge");
    }

    @Test
    void exportsToGraphMlFormat() throws IOException {
        CodeGraph graph = new CodeGraph();
        graph.addNode(new CodeNode("type:com.A", NodeType.CLASS, "A", "com.A"));
        graph.addRelationship(new CodeRelationship("type:com.A", "type:com.B", RelationshipType.IMPLEMENTS));

        StringWriter writer = new StringWriter();
        new GraphMlExporter().export(graph, writer);
        String xml = writer.toString();

        assertTrue(xml.contains("<?xml"), "Should be XML");
        assertTrue(xml.contains("<graphml"), "Should contain graphml root");
        assertTrue(xml.contains("type:com.A"), "Should contain node A");
        assertTrue(xml.contains("IMPLEMENTS"), "Should contain IMPLEMENTS edge type");
    }

    @Test
    void exportsToJsonFormat() throws IOException {
        CodeGraph graph = new CodeGraph();
        graph.addNode(new CodeNode("type:com.A", NodeType.CLASS, "A", "com.A"));
        graph.addRelationship(new CodeRelationship("type:com.A", "type:com.B", RelationshipType.CALLS));

        StringWriter writer = new StringWriter();
        new JsonExporter().export(graph, writer);
        String json = writer.toString();

        assertTrue(json.contains("\"nodes\""), "Should contain nodes array");
        assertTrue(json.contains("\"relationships\""), "Should contain relationships array");
        assertTrue(json.contains("\"type:com.A\""), "Should contain node ID");
        assertTrue(json.contains("\"CALLS\""), "Should contain relationship type");
    }

    @Test
    void incrementalParsingSkipsUnchangedFiles() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);

        Files.writeString(pkg.resolve("Foo.java"), """
                package com.example;
                public class Foo {
                    public void bar() {}
                }
                """);

        Path cacheFile = tempDir.resolve(".code-mem-graph-hashes");

        // First run: all files parsed, cache created
        FileHashCache cache1 = new FileHashCache(cacheFile);
        JavaSourceParser parser1 = new JavaSourceParser(tempDir, List.of(), cache1);
        CodeGraph graph1 = parser1.parseDirectory(tempDir);
        cache1.save();
        assertTrue(graph1.hasNode("type:com.example.Foo"), "First run should parse Foo");

        // Second run: no changes, file should be skipped
        FileHashCache cache2 = new FileHashCache(cacheFile);
        JavaSourceParser parser2 = new JavaSourceParser(tempDir, List.of(), cache2);
        CodeGraph graph2 = parser2.parseDirectory(tempDir);
        assertEquals(0, graph2.nodeCount(), "Second run should skip unchanged file, resulting in empty graph");

        // Third run: modify file, should be re-parsed
        Files.writeString(pkg.resolve("Foo.java"), """
                package com.example;
                public class Foo {
                    public void bar() {}
                    public void baz() {}
                }
                """);

        FileHashCache cache3 = new FileHashCache(cacheFile);
        JavaSourceParser parser3 = new JavaSourceParser(tempDir, List.of(), cache3);
        CodeGraph graph3 = parser3.parseDirectory(tempDir);
        assertTrue(graph3.hasNode("type:com.example.Foo"), "Modified file should be re-parsed");
        assertTrue(graph3.hasNode("method:com.example.Foo.baz()"), "New method should be detected");
    }

    @Test
    void cpgParserExtractsStatementsAndCfg() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);
        Files.writeString(pkg.resolve("Calculator.java"), """
                package com.example;

                public class Calculator {
                    public int compute(int x) {
                        int result = 0;
                        if (x > 0) {
                            result = x * 2;
                        } else {
                            result = -x;
                        }
                        return result;
                    }
                }
                """);

        var parser = new code.graph.parser.CpgParser(tempDir, List.of());
        CodeGraph graph = parser.parseDirectory(tempDir);

        // Structural nodes should still be present
        assertTrue(graph.hasNode("type:com.example.Calculator"));
        assertTrue(graph.hasNode("method:com.example.Calculator.compute(int)"));

        // FILE node should exist
        boolean hasFile = graph.getNodes().values().stream()
                .anyMatch(n -> n.type() == NodeType.FILE && n.name().equals("Calculator.java"));
        assertTrue(hasFile, "Should have FILE node for Calculator.java");

        // SOURCE_FILE edge should exist
        boolean hasSourceFile = graph.getRelationships().stream()
                .anyMatch(r -> r.type() == RelationshipType.SOURCE_FILE
                        && r.sourceId().contains("Calculator"));
        assertTrue(hasSourceFile, "Should have SOURCE_FILE relationship");

        // STATEMENT nodes should exist
        long stmtCount = graph.getNodes().values().stream()
                .filter(n -> n.type() == NodeType.STATEMENT).count();
        assertTrue(stmtCount >= 3, "Should have at least 3 statements (variable decl, if, return)");

        // AST_CHILD edges from method to statements
        boolean hasAstChild = graph.getRelationships().stream()
                .anyMatch(r -> r.type() == RelationshipType.AST_CHILD
                        && r.sourceId().contains("compute"));
        assertTrue(hasAstChild, "Should have AST_CHILD from method to statements");

        // CFG_NEXT edges should exist
        long cfgCount = graph.getRelationships().stream()
                .filter(r -> r.type() == RelationshipType.CFG_NEXT).count();
        assertTrue(cfgCount >= 3, "Should have at least 3 CFG edges");

        // DATA_FLOW edges should exist (result is defined then used)
        boolean hasDataFlow = graph.getRelationships().stream()
                .anyMatch(r -> r.type() == RelationshipType.DATA_FLOW);
        assertTrue(hasDataFlow, "Should have DATA_FLOW edges for variable 'result'");
    }

    @Test
    void cpgParserHandlesLoops() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);
        Files.writeString(pkg.resolve("Looper.java"), """
                package com.example;

                public class Looper {
                    public int sum(int n) {
                        int total = 0;
                        for (int i = 0; i < n; i++) {
                            total = total + i;
                        }
                        return total;
                    }
                }
                """);

        var parser = new code.graph.parser.CpgParser(tempDir, List.of());
        CodeGraph graph = parser.parseDirectory(tempDir);

        // Should have statement nodes
        long stmtCount = graph.getNodes().values().stream()
                .filter(n -> n.type() == NodeType.STATEMENT).count();
        assertTrue(stmtCount >= 3, "Should have statements for variable decl, for, return");

        // Should have a back-edge (CFG_NEXT with backEdge property)
        boolean hasBackEdge = graph.getRelationships().stream()
                .anyMatch(r -> r.type() == RelationshipType.CFG_NEXT
                        && Boolean.TRUE.equals(r.properties().get("backEdge")));
        assertTrue(hasBackEdge, "Should have a back-edge from loop body to loop head");
    }
}

