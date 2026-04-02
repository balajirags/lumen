package code.graph.parser;

import code.graph.model.*;
import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.*;
import com.github.javaparser.ast.expr.AssignExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.VariableDeclarationExpr;
import com.github.javaparser.ast.stmt.*;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;

import java.io.IOException;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Builds a Code Property Graph by combining the structural knowledge graph
 * (from JavaSourceParser) with statement-level AST, CFG, and data-flow edges.
 */
public class CpgParser {

    private final JavaSourceParser structuralParser;
    private final JavaParser cpgJavaParser;
    private final FileHashCache hashCache;

    public CpgParser(Path sourceRoot, List<Path> classpathEntries) {
        this(sourceRoot, classpathEntries, null);
    }

    public CpgParser(Path sourceRoot, List<Path> classpathEntries, FileHashCache hashCache) {
        this.structuralParser = new JavaSourceParser(sourceRoot, classpathEntries, hashCache);
        this.hashCache = hashCache;

        // Lightweight parser for the CPG pass (symbol resolution not needed)
        ParserConfiguration config = new ParserConfiguration()
                .setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_21);
        this.cpgJavaParser = new JavaParser(config);
    }

    public CodeGraph parseDirectory(Path root) throws IOException {
        // Pass 1: structural graph (packages, types, methods, calls, etc.)
        CodeGraph graph = structuralParser.parseDirectory(root);

        System.out.println("Enhancing with Code Property Graph edges...");

        // Pass 2: CPG edges (files, statements, CFG, data flow)
        List<Path> javaFiles = collectJavaFiles(root);
        int stmtCount = 0, cfgEdges = 0, dataFlowEdges = 0, skipped = 0;

        for (Path file : javaFiles) {
            if (hashCache != null && !hashCache.hasChanged(file)) {
                skipped++;
                continue;
            }
            int[] counts = addCpgEdges(file, graph);
            stmtCount += counts[0];
            cfgEdges += counts[1];
            dataFlowEdges += counts[2];
        }

        if (skipped > 0) {
            System.out.printf("CPG pass: skipped %d unchanged files%n", skipped);
        }
        System.out.printf("CPG: %d statement nodes, %d CFG edges, %d data-flow edges%n",
                stmtCount, cfgEdges, dataFlowEdges);

        return graph;
    }

    /**
     * Returns int[]{statements, cfgEdges, dataFlowEdges}.
     */
    private int[] addCpgEdges(Path file, CodeGraph graph) {
        try {
            ParseResult<CompilationUnit> result = cpgJavaParser.parse(file);
            if (result.isSuccessful() && result.getResult().isPresent()) {
                CpgVisitor visitor = new CpgVisitor(graph, file);
                result.getResult().get().accept(visitor, null);
                return new int[]{visitor.stmtCount, visitor.cfgEdgeCount, visitor.dataFlowCount};
            }
        } catch (IOException e) {
            System.err.printf("CPG: Error reading %s: %s%n", file, e.getMessage());
        }
        return new int[]{0, 0, 0};
    }

    private List<Path> collectJavaFiles(Path root) throws IOException {
        List<Path> files = new ArrayList<>();
        Files.walkFileTree(root, new SimpleFileVisitor<>() {
            @Override
            public FileVisitResult visitFile(Path file, BasicFileAttributes attrs) {
                if (file.toString().endsWith(".java")) {
                    files.add(file);
                }
                return FileVisitResult.CONTINUE;
            }
        });
        return files;
    }

    // ---- CPG Visitor ----

    private static class CpgVisitor extends VoidVisitorAdapter<Void> {

        private final CodeGraph graph;
        private final Path filePath;
        private String fileNodeId;
        int stmtCount = 0;
        int cfgEdgeCount = 0;
        int dataFlowCount = 0;

        CpgVisitor(CodeGraph graph, Path filePath) {
            this.graph = graph;
            this.filePath = filePath;
        }

        // -- FILE node & SOURCE_FILE edges --

        @Override
        public void visit(CompilationUnit cu, Void arg) {
            String fileName = filePath.getFileName().toString();
            fileNodeId = "file:" + filePath.toAbsolutePath().normalize();
            graph.addNode(new CodeNode(fileNodeId, NodeType.FILE, fileName, filePath.toString()));
            super.visit(cu, arg);
        }

        @Override
        public void visit(ClassOrInterfaceDeclaration decl, Void arg) {
            linkTypeToFile(decl);
            super.visit(decl, arg);
        }

        @Override
        public void visit(EnumDeclaration decl, Void arg) {
            linkTypeToFile(decl);
            super.visit(decl, arg);
        }

        @Override
        public void visit(RecordDeclaration decl, Void arg) {
            linkTypeToFile(decl);
            super.visit(decl, arg);
        }

        private void linkTypeToFile(TypeDeclaration<?> decl) {
            try {
                String qualifiedName = decl.getFullyQualifiedName().orElse(decl.getNameAsString());
                String typeId = "type:" + qualifiedName;
                if (graph.hasNode(typeId) && fileNodeId != null) {
                    graph.addRelationship(new CodeRelationship(typeId, fileNodeId, RelationshipType.SOURCE_FILE));
                }
            } catch (Exception ignored) {
            }
        }

        // -- Method / Constructor body analysis --

        @Override
        public void visit(MethodDeclaration decl, Void arg) {
            decl.getBody().ifPresent(body -> processMethodBody(body, decl));
            // Don't call super — we handle the body ourselves
        }

        @Override
        public void visit(ConstructorDeclaration decl, Void arg) {
            processMethodBody(decl.getBody(), decl);
        }

        private void processMethodBody(BlockStmt body, CallableDeclaration<?> decl) {
            String containingType = findContainingType(decl);
            String sig;
            String methodId;

            if (decl instanceof MethodDeclaration md) {
                sig = md.getNameAsString() + "(" +
                        md.getParameters().stream().map(p -> p.getTypeAsString())
                                .collect(Collectors.joining(",")) + ")";
                methodId = "method:" + containingType + "." + sig;
            } else if (decl instanceof ConstructorDeclaration cd) {
                sig = cd.getNameAsString() + "(" +
                        cd.getParameters().stream().map(p -> p.getTypeAsString())
                                .collect(Collectors.joining(",")) + ")";
                methodId = "ctor:" + containingType + "." + sig;
            } else {
                return;
            }

            int[] counter = {0};
            Map<String, String> varDefs = new HashMap<>();
            processStatements(body.getStatements(), methodId, containingType, sig,
                    counter, varDefs, List.of(methodId));
        }

        /**
         * Process a list of statements: create STATEMENT nodes, AST_CHILD, CFG_NEXT, and DATA_FLOW edges.
         *
         * @return the exit statement IDs (nodes that flow to whatever comes next)
         */
        private List<String> processStatements(List<Statement> stmts, String parentId,
                                               String containingType, String sig, int[] counter,
                                               Map<String, String> varDefs, List<String> cfgPredecessors) {

            List<String> currentPreds = new ArrayList<>(cfgPredecessors);

            for (Statement stmt : stmts) {
                // Skip empty block wrappers
                if (stmt instanceof BlockStmt block) {
                    currentPreds = processStatements(block.getStatements(), parentId,
                            containingType, sig, counter, varDefs, currentPreds);
                    continue;
                }

                String stmtId = "stmt:" + containingType + "." + sig + ":S" + (counter[0]++);
                String stmtType = classifyStatement(stmt);
                String code = stmt.toString();
                if (code.length() > 200) code = code.substring(0, 200) + "...";
                int line = stmt.getBegin().map(p -> p.line).orElse(-1);

                CodeNode stmtNode = new CodeNode(stmtId, NodeType.STATEMENT, stmtType,
                        containingType + "." + sig + ":S" + (counter[0] - 1))
                        .withProperty("statementType", stmtType)
                        .withProperty("code", code)
                        .withProperty("lineNumber", line);
                stmt.getEnd().ifPresent(pos -> stmtNode.withProperty("endLineNumber", pos.line));
                graph.addNode(stmtNode);
                stmtCount++;

                // AST_CHILD: parent → this statement
                graph.addRelationship(new CodeRelationship(parentId, stmtId, RelationshipType.AST_CHILD)
                        .withProperty("ast_order", counter[0] - 1));

                // CFG_NEXT from predecessors → this statement
                for (String pred : currentPreds) {
                    graph.addRelationship(new CodeRelationship(pred, stmtId, RelationshipType.CFG_NEXT));
                    cfgEdgeCount++;
                }

                // Data flow
                processDataFlow(stmt, stmtId, varDefs);

                // Handle compound statements — each returns the new exit points for CFG
                if (stmt instanceof IfStmt ifStmt) {
                    currentPreds = processIfStmt(ifStmt, stmtId, containingType, sig, counter, varDefs);
                } else if (stmt instanceof ForStmt forStmt) {
                    currentPreds = processLoopBody(forStmt.getBody(), stmtId, containingType, sig, counter, varDefs);
                } else if (stmt instanceof ForEachStmt forEachStmt) {
                    currentPreds = processLoopBody(forEachStmt.getBody(), stmtId, containingType, sig, counter, varDefs);
                } else if (stmt instanceof WhileStmt whileStmt) {
                    currentPreds = processLoopBody(whileStmt.getBody(), stmtId, containingType, sig, counter, varDefs);
                } else if (stmt instanceof DoStmt doStmt) {
                    currentPreds = processLoopBody(doStmt.getBody(), stmtId, containingType, sig, counter, varDefs);
                } else if (stmt instanceof TryStmt tryStmt) {
                    currentPreds = processTryStmt(tryStmt, stmtId, containingType, sig, counter, varDefs);
                } else if (stmt instanceof SwitchStmt switchStmt) {
                    currentPreds = processSwitchStmt(switchStmt, stmtId, containingType, sig, counter, varDefs);
                } else if (stmt instanceof ReturnStmt || stmt instanceof ThrowStmt) {
                    currentPreds = List.of(); // terminal — no outgoing CFG
                } else if (stmt instanceof BreakStmt || stmt instanceof ContinueStmt) {
                    currentPreds = List.of();
                } else {
                    currentPreds = List.of(stmtId);
                }
            }

            return currentPreds;
        }

        private List<String> processIfStmt(IfStmt ifStmt, String stmtId,
                                           String containingType, String sig,
                                           int[] counter, Map<String, String> varDefs) {
            List<String> exits = new ArrayList<>();

            // Then branch
            List<Statement> thenStmts = unwrapBlock(ifStmt.getThenStmt());
            exits.addAll(processStatements(thenStmts, stmtId, containingType, sig,
                    counter, varDefs, List.of(stmtId)));

            // Else branch
            if (ifStmt.getElseStmt().isPresent()) {
                List<Statement> elseStmts = unwrapBlock(ifStmt.getElseStmt().get());
                exits.addAll(processStatements(elseStmts, stmtId, containingType, sig,
                        counter, varDefs, List.of(stmtId)));
            } else {
                // No else → the IF node itself is also an exit
                exits.add(stmtId);
            }

            return exits;
        }

        private List<String> processLoopBody(Statement body, String loopStmtId,
                                             String containingType, String sig,
                                             int[] counter, Map<String, String> varDefs) {
            List<Statement> bodyStmts = unwrapBlock(body);
            List<String> bodyExits = processStatements(bodyStmts, loopStmtId, containingType, sig,
                    counter, varDefs, List.of(loopStmtId));

            // Back edge: body exits → loop head
            for (String exit : bodyExits) {
                graph.addRelationship(new CodeRelationship(exit, loopStmtId, RelationshipType.CFG_NEXT)
                        .withProperty("backEdge", true));
                cfgEdgeCount++;
            }

            // Loop can be exited or skipped → loop head is an exit
            return List.of(loopStmtId);
        }

        private List<String> processTryStmt(TryStmt tryStmt, String stmtId,
                                            String containingType, String sig,
                                            int[] counter, Map<String, String> varDefs) {
            List<String> exits = new ArrayList<>();

            // Try block
            List<Statement> tryBody = tryStmt.getTryBlock().getStatements();
            exits.addAll(processStatements(tryBody, stmtId, containingType, sig,
                    counter, varDefs, List.of(stmtId)));

            // Catch clauses
            for (CatchClause cc : tryStmt.getCatchClauses()) {
                List<Statement> catchBody = cc.getBody().getStatements();
                exits.addAll(processStatements(catchBody, stmtId, containingType, sig,
                        counter, varDefs, List.of(stmtId)));
            }

            // Finally block
            tryStmt.getFinallyBlock().ifPresent(fb -> {
                List<String> finallyExits = processStatements(fb.getStatements(), stmtId,
                        containingType, sig, counter, varDefs, List.of(stmtId));
                // Finally always executes — its exits become the true exits
                exits.clear();
                exits.addAll(finallyExits);
            });

            return exits;
        }

        private List<String> processSwitchStmt(SwitchStmt switchStmt, String stmtId,
                                               String containingType, String sig,
                                               int[] counter, Map<String, String> varDefs) {
            List<String> exits = new ArrayList<>();
            for (SwitchEntry entry : switchStmt.getEntries()) {
                List<String> entryExits = processStatements(entry.getStatements(), stmtId,
                        containingType, sig, counter, varDefs, List.of(stmtId));
                exits.addAll(entryExits);
            }
            if (exits.isEmpty()) {
                exits.add(stmtId);
            }
            return exits;
        }

        // -- Data flow tracking --

        private void processDataFlow(Statement stmt, String stmtId, Map<String, String> varDefs) {
            // Find variable uses (reads)
            Set<String> uses = new LinkedHashSet<>();
            stmt.findAll(NameExpr.class).forEach(ne -> uses.add(ne.getNameAsString()));

            // Create DATA_FLOW edges from last definition to this use
            for (String varName : uses) {
                String defStmtId = varDefs.get(varName);
                if (defStmtId != null) {
                    graph.addRelationship(new CodeRelationship(defStmtId, stmtId, RelationshipType.DATA_FLOW)
                            .withProperty("variable", varName));
                    dataFlowCount++;
                }
            }

            // Track variable definitions (writes)
            stmt.findAll(VariableDeclarationExpr.class).forEach(vde ->
                    vde.getVariables().forEach(v -> varDefs.put(v.getNameAsString(), stmtId)));
            stmt.findAll(AssignExpr.class).forEach(ae -> {
                if (ae.getTarget() instanceof NameExpr ne) {
                    varDefs.put(ne.getNameAsString(), stmtId);
                }
            });
        }

        // -- Helpers --

        private static List<Statement> unwrapBlock(Statement stmt) {
            if (stmt instanceof BlockStmt block) {
                return block.getStatements();
            }
            return List.of(stmt);
        }

        private static String classifyStatement(Statement stmt) {
            return switch (stmt) {
                case IfStmt s -> "IF";
                case ForStmt s -> "FOR";
                case ForEachStmt s -> "FOREACH";
                case WhileStmt s -> "WHILE";
                case DoStmt s -> "DO_WHILE";
                case ReturnStmt s -> "RETURN";
                case ThrowStmt s -> "THROW";
                case TryStmt s -> "TRY";
                case SwitchStmt s -> "SWITCH";
                case BreakStmt s -> "BREAK";
                case ContinueStmt s -> "CONTINUE";
                case ExpressionStmt s -> "EXPRESSION";
                case ExplicitConstructorInvocationStmt s -> "CONSTRUCTOR_INVOCATION";
                case AssertStmt s -> "ASSERT";
                case SynchronizedStmt s -> "SYNCHRONIZED";
                case LabeledStmt s -> "LABELED";
                default -> "OTHER";
            };
        }

        private static String findContainingType(CallableDeclaration<?> decl) {
            return decl.findAncestor(TypeDeclaration.class)
                    .map(td -> {
                        try {
                            return ((TypeDeclaration<?>) td).getFullyQualifiedName()
                                    .orElse(((TypeDeclaration<?>) td).getNameAsString());
                        } catch (Exception e) {
                            return ((TypeDeclaration<?>) td).getNameAsString();
                        }
                    })
                    .orElse("unknown");
        }
    }
}
