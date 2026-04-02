package code.graph.parser;

import code.graph.model.*;
import org.jetbrains.kotlin.cli.common.CLIConfigurationKeys;
import org.jetbrains.kotlin.cli.common.messages.MessageCollector;
import org.jetbrains.kotlin.cli.jvm.compiler.EnvironmentConfigFiles;
import org.jetbrains.kotlin.cli.jvm.compiler.KotlinCoreEnvironment;
import org.jetbrains.kotlin.com.intellij.openapi.Disposable;
import org.jetbrains.kotlin.com.intellij.openapi.util.Disposer;
import org.jetbrains.kotlin.com.intellij.psi.PsiElement;
import org.jetbrains.kotlin.com.intellij.psi.PsiFile;
import org.jetbrains.kotlin.config.CompilerConfiguration;
import org.jetbrains.kotlin.lexer.KtTokens;
import org.jetbrains.kotlin.psi.*;

import java.io.IOException;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.*;

/**
 * Parses Kotlin source files using the Kotlin compiler's PSI (Program Structure Interface).
 * Extracts AST and Code Property Graph information.
 */
public class KotlinSourceParser implements SourceParser {

    private static final Set<String> EXTENSIONS = Set.of(".kt", ".kts");

    private final Path sourceRoot;
    private final Set<String> internalTypes = new HashSet<>();
    private final Set<String> internalMethods = new HashSet<>();

    // CPG counters
    private int cpgStmtCount = 0;
    private int cpgCfgEdgeCount = 0;
    private int cpgDataFlowCount = 0;  // Track methods for call resolution

    public KotlinSourceParser(Path sourceRoot) {
        this.sourceRoot = sourceRoot;
    }

    @Override
    public CodeGraph parseDirectory(Path root) throws IOException {
        CodeGraph graph = new CodeGraph();
        List<Path> kotlinFiles = collectKotlinFiles(root);

        System.out.printf("Found %d Kotlin files to parse%n", kotlinFiles.size());

        if (kotlinFiles.isEmpty()) {
            return graph;
        }

        // Create Kotlin compiler environment
        Disposable disposable = Disposer.newDisposable();
        try {
            CompilerConfiguration config = new CompilerConfiguration();
            config.put(CLIConfigurationKeys.MESSAGE_COLLECTOR_KEY, MessageCollector.Companion.getNONE());

            KotlinCoreEnvironment env = KotlinCoreEnvironment.createForProduction(
                    disposable,
                    config,
                    EnvironmentConfigFiles.JVM_CONFIG_FILES
            );

            KtPsiFactory psiFactory = new KtPsiFactory(env.getProject());

            // First pass: collect all internal type and method names
            for (Path file : kotlinFiles) {
                collectInternalSymbols(file, psiFactory);
            }
            
            System.out.printf("Found %d internal types and %d internal methods%n", 
                    internalTypes.size(), internalMethods.size());

            // Second pass: parse all files
            int resolvedCalls = 0;
            int unresolvedCalls = 0;

            for (Path file : kotlinFiles) {
                try {
                    String source = Files.readString(file);
                    KtFile ktFile = psiFactory.createFile(file.getFileName().toString(), source);

                    String relativePath = root.relativize(file).toString();
                    String fileId = "file:" + relativePath;

                    // Create FILE node
                    CodeNode fileNode = new CodeNode(fileId, NodeType.FILE, file.getFileName().toString(), relativePath)
                            .withProperty("path", relativePath);
                    graph.addNode(fileNode);

                    // Parse the file contents
                    ParseContext ctx = new ParseContext(graph, fileId, relativePath, file);
                    ctx.source = source;
                    parseKtFile(ktFile, ctx);

                    resolvedCalls += ctx.resolvedCalls;
                    unresolvedCalls += ctx.unresolvedCalls;

                } catch (Exception e) {
                    System.err.printf("Error parsing %s: %s%n", file, e.getMessage());
                }
            }

            System.out.printf("Method calls: %d resolved, %d best-effort (unresolved)%n",
                    resolvedCalls, unresolvedCalls);

            System.out.printf("CPG: %d statement nodes, %d CFG edges, %d data-flow edges%n",
                    cpgStmtCount, cpgCfgEdgeCount, cpgDataFlowCount);

        } finally {
            Disposer.dispose(disposable);
        }

        return graph;
    }

    private void collectInternalSymbols(Path file, KtPsiFactory psiFactory) {
        try {
            String source = Files.readString(file);
            KtFile ktFile = psiFactory.createFile(file.getFileName().toString(), source);

            String packageName = ktFile.getPackageFqName().asString();

            for (KtDeclaration decl : ktFile.getDeclarations()) {
                collectDeclarationSymbols(decl, packageName);
            }
        } catch (Exception ignored) {
        }
    }

    private void collectDeclarationSymbols(KtDeclaration decl, String packageName) {
        if (decl instanceof KtClassOrObject classOrObject) {
            String typeName = packageName.isEmpty()
                    ? classOrObject.getName()
                    : packageName + "." + classOrObject.getName();
            if (typeName != null) {
                internalTypes.add(typeName);
                
                // Collect methods from the class (simple names only for matching)
                KtClassBody body = classOrObject.getBody();
                if (body != null) {
                    for (KtDeclaration member : body.getDeclarations()) {
                        if (member instanceof KtNamedFunction func) {
                            String methodName = func.getName();
                            if (methodName != null) {
                                internalMethods.add(methodName);
                            }
                        }
                    }
                }
            }
        } else if (decl instanceof KtNamedFunction func) {
            // Top-level function - store simple name
            String funcName = func.getName();
            if (funcName != null) {
                internalMethods.add(funcName);
            }
        }
    }

    private void parseKtFile(KtFile ktFile, ParseContext ctx) {
        String packageName = ktFile.getPackageFqName().asString();

        // Create package node if exists
        String currentContainerId = ctx.fileId;
        if (!packageName.isEmpty()) {
            String packageId = "package:" + packageName;
            if (!ctx.graph.hasNode(packageId)) {
                CodeNode packageNode = new CodeNode(packageId, NodeType.PACKAGE, packageName, packageName);
                ctx.graph.addNode(packageNode);
            }
            currentContainerId = packageId;
        }

        // Process imports
        for (KtImportDirective importDirective : ktFile.getImportDirectives()) {
            String importedFqn = importDirective.getImportedFqName() != null
                    ? importDirective.getImportedFqName().asString()
                    : null;
            if (importedFqn != null) {
                // Track imports for relationship resolution
                ctx.imports.add(importedFqn);
            }
        }

        // Process top-level declarations
        for (KtDeclaration decl : ktFile.getDeclarations()) {
            parseDeclaration(decl, currentContainerId, packageName, ctx);
        }
    }

    private void parseDeclaration(KtDeclaration decl, String containerId, String packageName, ParseContext ctx) {
        if (decl instanceof KtClass ktClass) {
            parseClass(ktClass, containerId, packageName, ctx);
        } else if (decl instanceof KtObjectDeclaration ktObject) {
            parseObject(ktObject, containerId, packageName, ctx);
        } else if (decl instanceof KtNamedFunction function) {
            parseFunction(function, containerId, packageName, ctx, false);
        } else if (decl instanceof KtProperty property) {
            parseProperty(property, containerId, packageName, ctx);
        } else if (decl instanceof KtTypeAlias typeAlias) {
            parseTypeAlias(typeAlias, containerId, packageName, ctx);
        }
    }

    private void parseClass(KtClass ktClass, String containerId, String packageName, ParseContext ctx) {
        String className = ktClass.getName();
        if (className == null) return;

        String qualifiedName = packageName.isEmpty() ? className : packageName + "." + className;
        String classId = "class:" + qualifiedName;

        // Determine node type
        NodeType nodeType = determineClassNodeType(ktClass);

        CodeNode classNode = new CodeNode(classId, nodeType, className, qualifiedName)
                .withProperty("visibility", getVisibility(ktClass))
                .withProperty("isAbstract", ktClass.hasModifier(KtTokens.ABSTRACT_KEYWORD))
                .withProperty("isOpen", ktClass.hasModifier(KtTokens.OPEN_KEYWORD))
                .withProperty("isInner", ktClass.hasModifier(KtTokens.INNER_KEYWORD));

        if (ktClass.isData()) {
            classNode = classNode.withProperty("isData", true);
        }
        if (ktClass.isSealed()) {
            classNode = classNode.withProperty("isSealed", true);
        }

        ctx.graph.addNode(classNode);

        // CONTAINS relationship
        ctx.graph.addRelationship(new CodeRelationship(containerId, classId, RelationshipType.CONTAINS));

        // SOURCE_FILE relationship
        ctx.graph.addRelationship(new CodeRelationship(classId, ctx.fileId, RelationshipType.SOURCE_FILE));

        // Handle supertype relationships
        for (KtSuperTypeListEntry superType : ktClass.getSuperTypeListEntries()) {
            KtTypeReference typeRef = superType.getTypeReference();
            if (typeRef != null) {
                String superName = typeRef.getText();
                String resolvedSuper = resolveTypeName(superName, packageName, ctx);
                String superTypeId = "class:" + resolvedSuper;

                // Determine if EXTENDS or IMPLEMENTS (for interfaces)
                RelationshipType relType = RelationshipType.EXTENDS;
                if (superType instanceof KtDelegatedSuperTypeEntry) {
                    // "by" delegation
                    relType = RelationshipType.DELEGATES_TO;
                }

                ctx.graph.addRelationship(new CodeRelationship(classId, superTypeId, relType));

                // If sealed, add SEALED_SUBTYPE relationship
                // (This would need more sophisticated analysis in practice)
            }
        }

        // Process class body
        KtClassBody body = ktClass.getBody();
        if (body != null) {
            parseClassBody(body, classId, qualifiedName, ctx);
        }

        // Handle primary constructor parameters as properties
        KtPrimaryConstructor primaryCtor = ktClass.getPrimaryConstructor();
        if (primaryCtor != null) {
            parsePrimaryConstructor(primaryCtor, classId, qualifiedName, ctx);
        }
    }

    private void parseObject(KtObjectDeclaration ktObject, String containerId, String packageName, ParseContext ctx) {
        String objectName = ktObject.getName();
        if (objectName == null) objectName = "<anonymous>";

        String qualifiedName = packageName.isEmpty() ? objectName : packageName + "." + objectName;
        String objectId = "object:" + qualifiedName;

        NodeType nodeType = ktObject.isCompanion() ? NodeType.COMPANION_OBJECT : NodeType.OBJECT_DECL;

        CodeNode objectNode = new CodeNode(objectId, nodeType, objectName, qualifiedName);
        ctx.graph.addNode(objectNode);

        // CONTAINS relationship
        ctx.graph.addRelationship(new CodeRelationship(containerId, objectId, RelationshipType.CONTAINS));

        // SOURCE_FILE relationship
        ctx.graph.addRelationship(new CodeRelationship(objectId, ctx.fileId, RelationshipType.SOURCE_FILE));

        // COMPANION_OF relationship if it's a companion object
        if (ktObject.isCompanion()) {
            ctx.graph.addRelationship(new CodeRelationship(objectId, containerId, RelationshipType.COMPANION_OF));
        }

        // Process object body
        KtClassBody body = ktObject.getBody();
        if (body != null) {
            parseClassBody(body, objectId, qualifiedName, ctx);
        }
    }

    private void parseClassBody(KtClassBody body, String containerId, String containerQualifiedName, ParseContext ctx) {
        for (KtDeclaration decl : body.getDeclarations()) {
            if (decl instanceof KtNamedFunction function) {
                parseFunction(function, containerId, containerQualifiedName, ctx, true);
            } else if (decl instanceof KtProperty property) {
                parseProperty(property, containerId, containerQualifiedName, ctx);
            } else if (decl instanceof KtClass nestedClass) {
                parseClass(nestedClass, containerId, containerQualifiedName, ctx);
            } else if (decl instanceof KtObjectDeclaration nestedObject) {
                parseObject(nestedObject, containerId, containerQualifiedName, ctx);
            } else if (decl instanceof KtClassInitializer initBlock) {
                parseInitBlock(initBlock, containerId, containerQualifiedName, ctx);
            }
        }
    }

    private void parsePrimaryConstructor(KtPrimaryConstructor ctor, String classId, String classQualifiedName, ParseContext ctx) {
        String ctorId = classId + ".<init>";
        CodeNode ctorNode = new CodeNode(ctorId, NodeType.CONSTRUCTOR, "<init>", classQualifiedName + ".<init>")
                .withProperty("visibility", getVisibility(ctor));
        ctx.graph.addNode(ctorNode);
        ctx.graph.addRelationship(new CodeRelationship(classId, ctorId, RelationshipType.CONTAINS));

        // Parse val/var parameters as properties
        for (KtParameter param : ctor.getValueParameters()) {
            if (param.hasValOrVar()) {
                String propName = param.getName();
                if (propName != null) {
                    String propId = classId + "." + propName;
                    String propQualifiedName = classQualifiedName + "." + propName;

                    // Check if it's val or var by examining the keyword token
                    var valOrVar = param.getValOrVarKeyword();
                    boolean isVal = valOrVar != null && "val".equals(valOrVar.getText());
                    boolean isVar = valOrVar != null && "var".equals(valOrVar.getText());

                    CodeNode propNode = new CodeNode(propId, NodeType.PROPERTY, propName, propQualifiedName)
                            .withProperty("isVal", isVal)
                            .withProperty("isVar", isVar)
                            .withProperty("visibility", getVisibility(param));

                    KtTypeReference typeRef = param.getTypeReference();
                    if (typeRef != null) {
                        propNode = propNode.withProperty("type", typeRef.getText());
                    }

                    ctx.graph.addNode(propNode);
                    ctx.graph.addRelationship(new CodeRelationship(classId, propId, RelationshipType.CONTAINS));
                }
            }

            // Add parameter to constructor
            String paramName = param.getName();
            if (paramName != null) {
                String paramId = ctorId + ".param:" + paramName;
                CodeNode paramNode = new CodeNode(paramId, NodeType.PARAMETER, paramName, paramName);

                KtTypeReference typeRef = param.getTypeReference();
                if (typeRef != null) {
                    paramNode = paramNode.withProperty("type", typeRef.getText());
                }

                ctx.graph.addNode(paramNode);
                ctx.graph.addRelationship(new CodeRelationship(ctorId, paramId, RelationshipType.HAS_PARAMETER));
            }
        }
    }

    private void parseFunction(KtNamedFunction function, String containerId, String containerQualifiedName,
                               ParseContext ctx, boolean isMethod) {
        String funcName = function.getName();
        if (funcName == null) return;

        // Check for extension function
        KtTypeReference receiverType = function.getReceiverTypeReference();
        boolean isExtension = receiverType != null;

        // Check for suspend function
        boolean isSuspend = function.hasModifier(KtTokens.SUSPEND_KEYWORD);

        String qualifiedName = containerQualifiedName + "." + funcName;
        String funcId = (isMethod ? "method:" : "function:") + qualifiedName;

        // Determine node type
        NodeType nodeType;
        if (isExtension) {
            nodeType = NodeType.EXTENSION_FUNCTION;
        } else if (isSuspend) {
            nodeType = NodeType.SUSPEND_FUNCTION;
        } else if (isMethod) {
            nodeType = NodeType.METHOD;
        } else {
            nodeType = NodeType.FUNCTION;
        }

        CodeNode funcNode = new CodeNode(funcId, nodeType, funcName, qualifiedName)
                .withProperty("visibility", getVisibility(function))
                .withProperty("isInline", function.hasModifier(KtTokens.INLINE_KEYWORD))
                .withProperty("isOperator", function.hasModifier(KtTokens.OPERATOR_KEYWORD))
                .withProperty("isInfix", function.hasModifier(KtTokens.INFIX_KEYWORD))
                .withProperty("isTailrec", function.hasModifier(KtTokens.TAILREC_KEYWORD))
                .withProperty("isSuspend", isSuspend)
                .withProperty("isExtension", isExtension);

        // Return type
        KtTypeReference returnType = function.getTypeReference();
        if (returnType != null) {
            funcNode = funcNode.withProperty("returnType", returnType.getText());
        }

        ctx.graph.addNode(funcNode);

        // CONTAINS relationship
        ctx.graph.addRelationship(new CodeRelationship(containerId, funcId, RelationshipType.CONTAINS));

        // EXTENSION_OF relationship for extension functions
        if (isExtension) {
            String receiverTypeName = receiverType.getText();
            String resolvedReceiver = resolveTypeName(receiverTypeName, containerQualifiedName, ctx);
            String receiverTypeId = "class:" + resolvedReceiver;
            ctx.graph.addRelationship(new CodeRelationship(funcId, receiverTypeId, RelationshipType.EXTENSION_OF));
        }

        // Parse parameters
        for (KtParameter param : function.getValueParameters()) {
            String paramName = param.getName();
            if (paramName != null) {
                String paramId = funcId + ".param:" + paramName;
                CodeNode paramNode = new CodeNode(paramId, NodeType.PARAMETER, paramName, paramName);

                KtTypeReference typeRef = param.getTypeReference();
                if (typeRef != null) {
                    paramNode = paramNode.withProperty("type", typeRef.getText());
                }

                ctx.graph.addNode(paramNode);
                ctx.graph.addRelationship(new CodeRelationship(funcId, paramId, RelationshipType.HAS_PARAMETER));
            }
        }

        // Parse function body for call graph
        KtExpression bodyExpr = function.getBodyExpression();
        if (bodyExpr != null) {
            parseExpressionsForCalls(bodyExpr, funcId, containerQualifiedName, ctx);

            // CPG: process function body for statement-level edges
            processFunctionBodyForCpg(bodyExpr, funcId, qualifiedName, ctx);
        }
    }

    private void parseProperty(KtProperty property, String containerId, String containerQualifiedName, ParseContext ctx) {
        String propName = property.getName();
        if (propName == null) return;

        String qualifiedName = containerQualifiedName + "." + propName;
        String propId = "property:" + qualifiedName;

        CodeNode propNode = new CodeNode(propId, NodeType.PROPERTY, propName, qualifiedName)
                .withProperty("isVal", property.isVar() ? false : true)
                .withProperty("isVar", property.isVar())
                .withProperty("isLateinit", property.hasModifier(KtTokens.LATEINIT_KEYWORD))
                .withProperty("isConst", property.hasModifier(KtTokens.CONST_KEYWORD))
                .withProperty("visibility", getVisibility(property));

        KtTypeReference typeRef = property.getTypeReference();
        if (typeRef != null) {
            propNode = propNode.withProperty("type", typeRef.getText());
        }

        ctx.graph.addNode(propNode);
        ctx.graph.addRelationship(new CodeRelationship(containerId, propId, RelationshipType.CONTAINS));

        // Check for delegation
        KtPropertyDelegate delegate = property.getDelegate();
        if (delegate != null) {
            // Property is delegated using "by" keyword
            propNode = propNode.withProperty("isDelegated", true);
        }

        // Parse initializer and accessors for calls
        KtExpression initializer = property.getInitializer();
        if (initializer != null) {
            parseExpressionsForCalls(initializer, propId, containerQualifiedName, ctx);
        }

        KtPropertyAccessor getter = property.getGetter();
        if (getter != null && getter.getBodyExpression() != null) {
            parseExpressionsForCalls(getter.getBodyExpression(), propId, containerQualifiedName, ctx);
        }

        KtPropertyAccessor setter = property.getSetter();
        if (setter != null && setter.getBodyExpression() != null) {
            parseExpressionsForCalls(setter.getBodyExpression(), propId, containerQualifiedName, ctx);
        }
    }

    private void parseTypeAlias(KtTypeAlias typeAlias, String containerId, String packageName, ParseContext ctx) {
        String aliasName = typeAlias.getName();
        if (aliasName == null) return;

        String qualifiedName = packageName.isEmpty() ? aliasName : packageName + "." + aliasName;
        String aliasId = "typealias:" + qualifiedName;

        CodeNode aliasNode = new CodeNode(aliasId, NodeType.TYPE_ALIAS, aliasName, qualifiedName)
                .withProperty("visibility", getVisibility(typeAlias));

        KtTypeReference targetType = typeAlias.getTypeReference();
        if (targetType != null) {
            aliasNode = aliasNode.withProperty("aliasedType", targetType.getText());
        }

        ctx.graph.addNode(aliasNode);
        ctx.graph.addRelationship(new CodeRelationship(containerId, aliasId, RelationshipType.CONTAINS));
    }

    private void parseInitBlock(KtClassInitializer initBlock, String classId, String classQualifiedName, ParseContext ctx) {
        String initId = classId + ".<init_block>";

        CodeNode initNode = new CodeNode(initId, NodeType.INIT_BLOCK, "<init>", classQualifiedName + ".<init>");
        ctx.graph.addNode(initNode);
        ctx.graph.addRelationship(new CodeRelationship(classId, initId, RelationshipType.CONTAINS));

        // Parse init block body for calls
        KtExpression body = initBlock.getBody();
        if (body != null) {
            parseExpressionsForCalls(body, initId, classQualifiedName, ctx);

            // CPG: process init block body for statement-level edges
            processFunctionBodyForCpg(body, initId, classQualifiedName + ".<init>", ctx);
        }
    }

    private void parseExpressionsForCalls(PsiElement element, String callerId, String callerPackage, ParseContext ctx) {
        if (element instanceof KtCallExpression callExpr) {
            KtExpression callee = callExpr.getCalleeExpression();
            if (callee != null) {
                String calleeName = callee.getText();

                // Check if this is a known internal method
                if (internalMethods.contains(calleeName)) {
                    // Resolve to qualified name - find matching method
                    String resolvedTarget = resolveCallTarget(calleeName, callerPackage, ctx);
                    String targetId = "method:" + resolvedTarget;
                    ctx.graph.addRelationship(new CodeRelationship(callerId, targetId, RelationshipType.CALLS)
                            .withProperty("resolved", true));
                    ctx.resolvedCalls++;
                } else {
                    // External or unresolved call
                    String targetId = "method:" + calleeName;
                    ctx.graph.addRelationship(new CodeRelationship(callerId, targetId, RelationshipType.CALLS)
                            .withProperty("resolved", false));
                    ctx.unresolvedCalls++;
                }
            }
        } else if (element instanceof KtDotQualifiedExpression dotExpr) {
            // Handle qualified calls like obj.method()
            KtExpression selector = dotExpr.getSelectorExpression();
            if (selector instanceof KtCallExpression callExpr) {
                KtExpression callee = callExpr.getCalleeExpression();
                if (callee != null) {
                    String methodName = callee.getText();
                    KtExpression receiver = dotExpr.getReceiverExpression();
                    String receiverText = receiver.getText();

                    // Check if method is internal
                    boolean isResolved = internalMethods.contains(methodName);
                    String qualified = receiverText + "." + methodName;
                    String targetId = "method:" + qualified;

                    ctx.graph.addRelationship(new CodeRelationship(callerId, targetId, RelationshipType.CALLS)
                            .withProperty("resolved", isResolved));
                    if (isResolved) {
                        ctx.resolvedCalls++;
                    } else {
                        ctx.unresolvedCalls++;
                    }
                }
            }
        } else if (element instanceof KtLambdaExpression lambda) {
            // Parse lambda body
            KtBlockExpression body = lambda.getBodyExpression();
            if (body != null) {
                for (PsiElement child : body.getChildren()) {
                    parseExpressionsForCalls(child, callerId, callerPackage, ctx);
                }
            }
        }

        // Recursively process children
        for (PsiElement child : element.getChildren()) {
            parseExpressionsForCalls(child, callerId, callerPackage, ctx);
        }
    }

    // ---- CPG: Statement-level analysis ----

    private void processFunctionBodyForCpg(KtExpression body, String parentId, String qualifiedName, ParseContext ctx) {
        List<KtExpression> stmts;
        if (body instanceof KtBlockExpression block) {
            stmts = block.getStatements();
        } else {
            // Expression-bodied function: fun foo() = expr
            stmts = List.of(body);
        }

        int[] counter = {0};
        Map<String, String> varDefs = new HashMap<>();
        processKtStatements(stmts, parentId, qualifiedName, counter, varDefs, List.of(parentId), ctx);
    }

    private List<String> processKtStatements(List<KtExpression> stmts, String parentId,
                                             String qualifiedName, int[] counter,
                                             Map<String, String> varDefs,
                                             List<String> cfgPredecessors, ParseContext ctx) {
        List<String> currentPreds = new ArrayList<>(cfgPredecessors);

        for (KtExpression stmt : stmts) {
            // Skip empty block wrappers
            if (stmt instanceof KtBlockExpression block) {
                currentPreds = processKtStatements(block.getStatements(), parentId,
                        qualifiedName, counter, varDefs, currentPreds, ctx);
                continue;
            }

            String stmtId = "stmt:" + qualifiedName + ":S" + (counter[0]++);
            String stmtType = classifyKtStatement(stmt);
            String code = stmt.getText();
            if (code.length() > 200) code = code.substring(0, 200) + "...";
            int line = getLineNumber(stmt, ctx.source);
            int endLine = getEndLineNumber(stmt, ctx.source);

            CodeNode stmtNode = new CodeNode(stmtId, NodeType.STATEMENT, stmtType,
                    qualifiedName + ":S" + (counter[0] - 1))
                    .withProperty("statementType", stmtType)
                    .withProperty("code", code)
                    .withProperty("lineNumber", line)
                    .withProperty("endLineNumber", endLine);
            ctx.graph.addNode(stmtNode);
            cpgStmtCount++;

            // AST_CHILD: parent -> this statement
            ctx.graph.addRelationship(new CodeRelationship(parentId, stmtId, RelationshipType.AST_CHILD)
                    .withProperty("ast_order", counter[0] - 1));

            // CFG_NEXT from predecessors -> this statement
            for (String pred : currentPreds) {
                ctx.graph.addRelationship(new CodeRelationship(pred, stmtId, RelationshipType.CFG_NEXT));
                cpgCfgEdgeCount++;
            }

            // Data flow
            processKtDataFlow(stmt, stmtId, varDefs, ctx);

            // Handle compound statements
            if (stmt instanceof KtIfExpression ifExpr) {
                currentPreds = processKtIf(ifExpr, stmtId, qualifiedName, counter, varDefs, ctx);
            } else if (stmt instanceof KtWhenExpression whenExpr) {
                currentPreds = processKtWhen(whenExpr, stmtId, qualifiedName, counter, varDefs, ctx);
            } else if (stmt instanceof KtForExpression forExpr) {
                currentPreds = processKtLoopBody(forExpr.getBody(), stmtId, qualifiedName, counter, varDefs, ctx);
            } else if (stmt instanceof KtWhileExpression whileExpr) {
                currentPreds = processKtLoopBody(whileExpr.getBody(), stmtId, qualifiedName, counter, varDefs, ctx);
            } else if (stmt instanceof KtDoWhileExpression doWhileExpr) {
                currentPreds = processKtLoopBody(doWhileExpr.getBody(), stmtId, qualifiedName, counter, varDefs, ctx);
            } else if (stmt instanceof KtTryExpression tryExpr) {
                currentPreds = processKtTry(tryExpr, stmtId, qualifiedName, counter, varDefs, ctx);
            } else if (stmt instanceof KtReturnExpression || stmt instanceof KtThrowExpression) {
                currentPreds = List.of(); // terminal
            } else if (stmt instanceof KtBreakExpression || stmt instanceof KtContinueExpression) {
                currentPreds = List.of();
            } else {
                currentPreds = List.of(stmtId);
            }
        }

        return currentPreds;
    }

    private List<String> processKtIf(KtIfExpression ifExpr, String stmtId,
                                     String qualifiedName, int[] counter,
                                     Map<String, String> varDefs, ParseContext ctx) {
        List<String> exits = new ArrayList<>();

        // Then branch
        KtExpression thenExpr = ifExpr.getThen();
        if (thenExpr != null) {
            List<KtExpression> thenStmts = unwrapKtBlock(thenExpr);
            exits.addAll(processKtStatements(thenStmts, stmtId, qualifiedName,
                    counter, varDefs, List.of(stmtId), ctx));
        }

        // Else branch
        KtExpression elseExpr = ifExpr.getElse();
        if (elseExpr != null) {
            List<KtExpression> elseStmts = unwrapKtBlock(elseExpr);
            exits.addAll(processKtStatements(elseStmts, stmtId, qualifiedName,
                    counter, varDefs, List.of(stmtId), ctx));
        } else {
            exits.add(stmtId);
        }

        return exits;
    }

    private List<String> processKtWhen(KtWhenExpression whenExpr, String stmtId,
                                       String qualifiedName, int[] counter,
                                       Map<String, String> varDefs, ParseContext ctx) {
        List<String> exits = new ArrayList<>();
        boolean hasElse = false;

        for (KtWhenEntry entry : whenExpr.getEntries()) {
            if (entry.isElse()) hasElse = true;
            KtExpression body = entry.getExpression();
            if (body != null) {
                List<KtExpression> bodyStmts = unwrapKtBlock(body);
                exits.addAll(processKtStatements(bodyStmts, stmtId, qualifiedName,
                        counter, varDefs, List.of(stmtId), ctx));
            }
        }

        if (!hasElse) {
            exits.add(stmtId);
        }

        if (exits.isEmpty()) {
            exits.add(stmtId);
        }

        return exits;
    }

    private List<String> processKtLoopBody(KtExpression body, String loopStmtId,
                                           String qualifiedName, int[] counter,
                                           Map<String, String> varDefs, ParseContext ctx) {
        if (body == null) return List.of(loopStmtId);

        List<KtExpression> bodyStmts = unwrapKtBlock(body);
        List<String> bodyExits = processKtStatements(bodyStmts, loopStmtId, qualifiedName,
                counter, varDefs, List.of(loopStmtId), ctx);

        // Back edge: body exits -> loop head
        for (String exit : bodyExits) {
            ctx.graph.addRelationship(new CodeRelationship(exit, loopStmtId, RelationshipType.CFG_NEXT)
                    .withProperty("backEdge", true));
            cpgCfgEdgeCount++;
        }

        return List.of(loopStmtId);
    }

    private List<String> processKtTry(KtTryExpression tryExpr, String stmtId,
                                      String qualifiedName, int[] counter,
                                      Map<String, String> varDefs, ParseContext ctx) {
        List<String> exits = new ArrayList<>();

        // Try block
        KtBlockExpression tryBlock = tryExpr.getTryBlock();
        if (tryBlock != null) {
            exits.addAll(processKtStatements(tryBlock.getStatements(), stmtId, qualifiedName,
                    counter, varDefs, List.of(stmtId), ctx));
        }

        // Catch clauses
        for (KtCatchClause cc : tryExpr.getCatchClauses()) {
            KtExpression catchBody = cc.getCatchBody();
            if (catchBody != null) {
                List<KtExpression> catchStmts = unwrapKtBlock(catchBody);
                exits.addAll(processKtStatements(catchStmts, stmtId, qualifiedName,
                        counter, varDefs, List.of(stmtId), ctx));
            }
        }

        // Finally block
        KtFinallySection finallySection = tryExpr.getFinallyBlock();
        if (finallySection != null) {
            KtBlockExpression finallyBody = finallySection.getFinalExpression();
            if (finallyBody != null) {
                List<String> finallyExits = processKtStatements(finallyBody.getStatements(), stmtId,
                        qualifiedName, counter, varDefs, List.of(stmtId), ctx);
                exits.clear();
                exits.addAll(finallyExits);
            }
        }

        return exits;
    }

    // -- Data flow tracking --

    private void processKtDataFlow(KtExpression stmt, String stmtId,
                                   Map<String, String> varDefs, ParseContext ctx) {
        // Collect reads: all name references
        Set<String> reads = new LinkedHashSet<>();
        collectNameReferences(stmt, reads);

        // DATA_FLOW from previous definitions to current uses
        for (String varName : reads) {
            String defStmtId = varDefs.get(varName);
            if (defStmtId != null) {
                ctx.graph.addRelationship(new CodeRelationship(defStmtId, stmtId, RelationshipType.DATA_FLOW)
                        .withProperty("variable", varName));
                cpgDataFlowCount++;
            }
        }

        // Track writes: property declarations
        if (stmt instanceof KtProperty prop) {
            String name = prop.getName();
            if (name != null) varDefs.put(name, stmtId);
        }
        // Track writes: assignments
        collectAssignments(stmt, stmtId, varDefs);
    }

    private void collectNameReferences(PsiElement element, Set<String> names) {
        if (element instanceof KtNameReferenceExpression ref) {
            names.add(ref.getReferencedName());
        }
        for (PsiElement child : element.getChildren()) {
            collectNameReferences(child, names);
        }
    }

    private void collectAssignments(PsiElement element, String stmtId, Map<String, String> varDefs) {
        if (element instanceof KtBinaryExpression binExpr) {
            String op = binExpr.getOperationReference().getText();
            if ("=".equals(op) || "+=".equals(op) || "-=".equals(op) ||
                    "*=".equals(op) || "/=".equals(op) || "%=".equals(op)) {
                KtExpression left = binExpr.getLeft();
                if (left instanceof KtNameReferenceExpression ref) {
                    varDefs.put(ref.getReferencedName(), stmtId);
                }
                return;
            }
        }
        for (PsiElement child : element.getChildren()) {
            collectAssignments(child, stmtId, varDefs);
        }
    }

    private static String classifyKtStatement(KtExpression stmt) {
        if (stmt instanceof KtIfExpression) return "IF";
        if (stmt instanceof KtWhenExpression) return "WHEN";
        if (stmt instanceof KtForExpression) return "FOR";
        if (stmt instanceof KtWhileExpression) return "WHILE";
        if (stmt instanceof KtDoWhileExpression) return "DO_WHILE";
        if (stmt instanceof KtReturnExpression) return "RETURN";
        if (stmt instanceof KtThrowExpression) return "THROW";
        if (stmt instanceof KtBreakExpression) return "BREAK";
        if (stmt instanceof KtContinueExpression) return "CONTINUE";
        if (stmt instanceof KtTryExpression) return "TRY";
        if (stmt instanceof KtProperty) return "VAR_DECL";
        return "EXPRESSION";
    }

    private static List<KtExpression> unwrapKtBlock(KtExpression expr) {
        if (expr instanceof KtBlockExpression block) {
            return block.getStatements();
        }
        return List.of(expr);
    }

    private static int getLineNumber(PsiElement element, String source) {
        int offset = element.getTextOffset();
        int line = 1;
        for (int i = 0; i < offset && i < source.length(); i++) {
            if (source.charAt(i) == '\n') line++;
        }
        return line;
    }

    private static int getEndLineNumber(PsiElement element, String source) {
        int offset = element.getTextOffset() + element.getTextLength();
        int line = 1;
        for (int i = 0; i < offset && i < source.length(); i++) {
            if (source.charAt(i) == '\n') line++;
        }
        return line;
    }

    private NodeType determineClassNodeType(KtClass ktClass) {
        if (ktClass.isData()) {
            return NodeType.DATA_CLASS;
        } else if (ktClass.isSealed()) {
            if (ktClass.isInterface()) {
                return NodeType.SEALED_INTERFACE;
            }
            return NodeType.SEALED_CLASS;
        } else if (ktClass.isInterface()) {
            return NodeType.INTERFACE;
        } else if (ktClass.isEnum()) {
            return NodeType.ENUM;
        } else if (ktClass.isAnnotation()) {
            return NodeType.ANNOTATION_TYPE;
        }
        return NodeType.CLASS;
    }

    private String getVisibility(KtModifierListOwner element) {
        if (element.hasModifier(KtTokens.PRIVATE_KEYWORD)) return "private";
        if (element.hasModifier(KtTokens.PROTECTED_KEYWORD)) return "protected";
        if (element.hasModifier(KtTokens.INTERNAL_KEYWORD)) return "internal";
        return "public"; // Kotlin default is public
    }

    private String resolveTypeName(String simpleName, String currentPackage, ParseContext ctx) {
        // Check imports first
        for (String importedFqn : ctx.imports) {
            if (importedFqn.endsWith("." + simpleName) || importedFqn.equals(simpleName)) {
                return importedFqn;
            }
        }

        // Check internal types
        for (String internalType : internalTypes) {
            if (internalType.endsWith("." + simpleName)) {
                return internalType;
            }
        }

        // Assume same package
        if (!currentPackage.isEmpty()) {
            return currentPackage + "." + simpleName;
        }

        return simpleName;
    }

    private String resolveCallTarget(String calleeName, String currentPackage, ParseContext ctx) {
        // Check if method is internal and try to resolve its qualified name
        if (internalMethods.contains(calleeName)) {
            // Look for a matching internal type that could contain this method
            // First try current package
            if (!currentPackage.isEmpty()) {
                return currentPackage + "." + calleeName;
            }
        }
        return calleeName;
    }

    private List<Path> collectKotlinFiles(Path root) throws IOException {
        List<Path> files = new ArrayList<>();
        Files.walkFileTree(root, new SimpleFileVisitor<>() {
            @Override
            public FileVisitResult visitFile(Path file, BasicFileAttributes attrs) {
                String fileName = file.getFileName().toString();
                if (fileName.endsWith(".kt") || fileName.endsWith(".kts")) {
                    // Skip generated files
                    String pathStr = file.toString();
                    if (!pathStr.contains("/build/") && !pathStr.contains("/.gradle/")) {
                        files.add(file);
                    }
                }
                return FileVisitResult.CONTINUE;
            }

            @Override
            public FileVisitResult preVisitDirectory(Path dir, BasicFileAttributes attrs) {
                String dirName = dir.getFileName().toString();
                if (dirName.equals(".git") || dirName.equals("build") || dirName.equals(".gradle")) {
                    return FileVisitResult.SKIP_SUBTREE;
                }
                return FileVisitResult.CONTINUE;
            }
        });
        return files;
    }

    @Override
    public Set<String> getSupportedExtensions() {
        return EXTENSIONS;
    }

    @Override
    public String getLanguageName() {
        return "Kotlin";
    }

    /**
     * Internal context for parsing.
     */
    private static class ParseContext {
        final CodeGraph graph;
        final String fileId;
        final String relativePath;
        final Path filePath;
        final Set<String> imports = new HashSet<>();
        String source;
        int resolvedCalls = 0;
        int unresolvedCalls = 0;
        int skippedFiles = 0;

        ParseContext(CodeGraph graph, String fileId, String relativePath, Path filePath) {
            this.graph = graph;
            this.fileId = fileId;
            this.relativePath = relativePath;
            this.filePath = filePath;
        }
    }
}
