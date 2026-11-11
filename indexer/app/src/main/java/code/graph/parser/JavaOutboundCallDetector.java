package code.graph.parser;

import code.graph.model.CodeGraph;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.*;
import com.github.javaparser.ast.expr.*;
import com.github.javaparser.ast.type.ClassOrInterfaceType;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Detects outbound HTTP calls in Java source: RestTemplate, WebClient, RestClient, OpenFeign,
 * java.net.http, OkHttp, Apache HttpClient and Retrofit. Purely syntactic (no classpath needed);
 * client types are matched on the declared type name of the receiver.
 */
final class JavaOutboundCallDetector {

    private static final Map<String, String> REST_TEMPLATE_VERBS = Map.ofEntries(
            Map.entry("getForObject", "GET"), Map.entry("getForEntity", "GET"),
            Map.entry("postForObject", "POST"), Map.entry("postForEntity", "POST"), Map.entry("postForLocation", "POST"),
            Map.entry("put", "PUT"), Map.entry("delete", "DELETE"), Map.entry("patchForObject", "PATCH"),
            Map.entry("headForHeaders", "HEAD"), Map.entry("optionsForAllow", "OPTIONS"),
            Map.entry("exchange", "?"), Map.entry("execute", "?"));
    private static final Set<String> REST_TEMPLATE_TYPES = Set.of("RestTemplate", "TestRestTemplate", "RestOperations");
    private static final Set<String> FLUENT_VERB_CALLS = Set.of("get", "post", "put", "delete", "patch", "head", "options");
    private static final Map<String, String> APACHE_REQUESTS = Map.of(
            "HttpGet", "GET", "HttpPost", "POST", "HttpPut", "PUT", "HttpDelete", "DELETE",
            "HttpPatch", "PATCH", "HttpHead", "HEAD", "HttpOptions", "OPTIONS");
    private static final Map<String, String> SPRING_MAPPINGS = Map.of(
            "GetMapping", "GET", "PostMapping", "POST", "PutMapping", "PUT",
            "DeleteMapping", "DELETE", "PatchMapping", "PATCH");
    private static final Map<String, String> RETROFIT_ANNOTATIONS = Map.of(
            "GET", "GET", "POST", "POST", "PUT", "PUT", "DELETE", "DELETE",
            "PATCH", "PATCH", "HEAD", "HEAD", "OPTIONS", "OPTIONS");
    private static final Pattern HTTP_METHOD_REF = Pattern.compile("HttpMethod\\.(\\w+)");
    private static final Pattern FORMAT_SPECIFIER = Pattern.compile("%[-#+ 0,(]*\\d*(?:\\.\\d+)?[a-zA-Z]");
    private static final int MAX_DEPTH = 5;

    private final CodeGraph graph;
    private final ExternalApiSupport support;
    private final String language;

    JavaOutboundCallDetector(CodeGraph graph, ExternalApiSupport support, String language) {
        this.graph = graph;
        this.support = support;
        this.language = language;
    }

    // ---------------------------------------------------------------------------------------------
    // Entry points
    // ---------------------------------------------------------------------------------------------

    void detect(MethodCallExpr call, String callerId, int line) {
        try {
            String name = call.getNameAsString();
            if (REST_TEMPLATE_VERBS.containsKey(name)) detectRestTemplate(call, callerId, line);
            else if (name.equals("uri")) detectFluentClient(call, callerId, line);
            else if (name.equals("newBuilder")) detectJdkHttpRequest(call, callerId, line);
        } catch (RuntimeException ignored) {
            // Detection is best-effort and must never break indexing.
        }
    }

    void detect(ObjectCreationExpr creation, String callerId, int line) {
        try {
            String type = creation.getType().getNameWithScope();
            String simple = creation.getType().getNameAsString();
            if (APACHE_REQUESTS.containsKey(simple) && !creation.getArguments().isEmpty()) {
                String url = resolveUrl(creation.getArgument(0), creation, 0);
                emit(callerId, APACHE_REQUESTS.get(simple), url, "Apache HttpClient", line, "apache-httpclient");
            } else if (type.equals("Request.Builder") || type.endsWith(".Request.Builder")) {
                detectOkHttp(creation, callerId, line);
            }
        } catch (RuntimeException ignored) {
        }
    }

    /** Declarative clients: OpenFeign interfaces and Retrofit service interfaces. */
    void detectDeclarative(MethodDeclaration method, String methodId, int line) {
        try {
            Optional<TypeDeclaration> owner = method.findAncestor(TypeDeclaration.class);
            if (owner.isEmpty()) return;
            Optional<AnnotationExpr> feign = findAnnotation(owner.get().getAnnotations(), "FeignClient");
            if (feign.isPresent()) {
                detectFeignMethod(method, methodId, line, owner.get(), feign.get());
                return;
            }
            for (var e : RETROFIT_ANNOTATIONS.entrySet()) {
                Optional<AnnotationExpr> ann = findAnnotation(method.getAnnotations(), e.getKey());
                if (ann.isPresent() && owner.get() instanceof ClassOrInterfaceDeclaration cid && cid.isInterface()) {
                    String path = annotationString(ann.get(), method, "value");
                    emit(methodId, e.getValue(), path == null ? "" : path, "Retrofit", line, "retrofit-annotation");
                    return;
                }
            }
        } catch (RuntimeException ignored) {
        }
    }

    // ---------------------------------------------------------------------------------------------
    // Client-specific detection
    // ---------------------------------------------------------------------------------------------

    private void detectRestTemplate(MethodCallExpr call, String callerId, int line) {
        if (call.getScope().isEmpty() || call.getArguments().isEmpty()) return;
        String type = receiverType(call.getScope().get(), call);
        if (type == null || REST_TEMPLATE_TYPES.stream().noneMatch(type::endsWith)) return;

        String verb = REST_TEMPLATE_VERBS.get(call.getNameAsString());
        if (verb.equals("?")) verb = verbFromArguments(call);
        String url = resolveUrl(call.getArgument(0), call, 0);
        emit(callerId, verb, url, "RestTemplate", line, "rest-template-call");
    }

    private String verbFromArguments(MethodCallExpr call) {
        for (Expression arg : call.getArguments()) {
            Matcher m = HTTP_METHOD_REF.matcher(arg.toString());
            if (m.find() && ExternalApiSupport.HTTP_VERBS.contains(m.group(1).toUpperCase(Locale.ROOT))) {
                return m.group(1).toUpperCase(Locale.ROOT);
            }
            if (arg instanceof NameExpr n && ExternalApiSupport.HTTP_VERBS.contains(n.getNameAsString())) {
                return n.getNameAsString();
            }
        }
        return "UNKNOWN";
    }

    /** WebClient / RestClient: client.get().uri("...") and client.method(HttpMethod.X).uri("..."). */
    private void detectFluentClient(MethodCallExpr uriCall, String callerId, int line) {
        List<MethodCallExpr> inward = inwardChain(uriCall);
        Expression root = chainRoot(uriCall);
        String type = root == null ? null : receiverType(root, uriCall);
        if (type == null) return;
        String client = type.contains("WebClient") ? "WebClient" : type.contains("RestClient") ? "RestClient" : null;
        if (client == null) return;

        String verb = "UNKNOWN";
        for (MethodCallExpr c : inward) {
            String n = c.getNameAsString();
            if (FLUENT_VERB_CALLS.contains(n) && c != uriCall) verb = n.toUpperCase(Locale.ROOT);
            else if (n.equals("method") && !c.getArguments().isEmpty()) {
                Matcher m = HTTP_METHOD_REF.matcher(c.getArgument(0).toString());
                if (m.find()) verb = m.group(1).toUpperCase(Locale.ROOT);
            }
        }
        if (uriCall.getArguments().isEmpty()) return;
        String path = resolveUriArgument(uriCall.getArgument(0), uriCall);
        if (!ExternalApiSupport.isAbsolute(path)) {
            String base = findBaseUrl(uriCall);
            if (base != null) path = ExternalApiSupport.joinUrl(base, path);
        }
        emit(callerId, verb, path, client, line, client.equals("WebClient") ? "webclient-chain" : "restclient-chain");
    }

    /** java.net.http: HttpRequest.newBuilder().uri(URI.create("...")).GET()... */
    private void detectJdkHttpRequest(MethodCallExpr newBuilder, String callerId, int line) {
        if (!(newBuilder.getScope().orElse(null) instanceof NameExpr scope) || !scope.getNameAsString().equals("HttpRequest")) {
            return;
        }
        String url = null;
        if (!newBuilder.getArguments().isEmpty()) url = resolveUrl(newBuilder.getArgument(0), newBuilder, 0);
        String verb = "GET";
        for (MethodCallExpr c : outwardChain(newBuilder)) {
            String n = c.getNameAsString();
            if (n.equals("uri") && !c.getArguments().isEmpty()) url = resolveUrl(c.getArgument(0), c, 0);
            else if (ExternalApiSupport.HTTP_VERBS.contains(n)) verb = n;
            else if (n.equals("method") && !c.getArguments().isEmpty()) verb = literalOr(c.getArgument(0), verb);
        }
        if (url != null) emit(callerId, verb, url, "java.net.http", line, "java-http-client");
    }

    /** OkHttp: new Request.Builder().url("...").post(body).build() */
    private void detectOkHttp(ObjectCreationExpr creation, String callerId, int line) {
        String url = null;
        String verb = "GET";
        if (!(creation.getParentNode().orElse(null) instanceof MethodCallExpr first)) return;
        for (MethodCallExpr c : outwardChain(first)) {
            String n = c.getNameAsString();
            if (n.equals("url") && !c.getArguments().isEmpty()) url = resolveUrl(c.getArgument(0), c, 0);
            else if (FLUENT_VERB_CALLS.contains(n)) verb = n.toUpperCase(Locale.ROOT);
            else if (n.equals("method") && !c.getArguments().isEmpty()) verb = literalOr(c.getArgument(0), verb);
        }
        if (url != null) emit(callerId, verb, url, "OkHttp", line, "okhttp-builder");
    }

    private void detectFeignMethod(MethodDeclaration method, String methodId, int line,
                                   TypeDeclaration<?> owner, AnnotationExpr feign) {
        String verb = null;
        String path = null;
        for (var e : SPRING_MAPPINGS.entrySet()) {
            Optional<AnnotationExpr> ann = findAnnotation(method.getAnnotations(), e.getKey());
            if (ann.isPresent()) {
                verb = e.getValue();
                path = annotationString(ann.get(), method, "value", "path");
            }
        }
        if (verb == null) {
            Optional<AnnotationExpr> rm = findAnnotation(method.getAnnotations(), "RequestMapping");
            if (rm.isEmpty()) return;
            path = annotationString(rm.get(), method, "value", "path");
            String methodAttr = annotationRaw(rm.get(), "method");
            Matcher m = Pattern.compile("(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)").matcher(methodAttr == null ? "" : methodAttr);
            verb = m.find() ? m.group(1) : "UNKNOWN";
        }

        String url = annotationString(feign, owner, "url");
        double confidence = 0.95;
        if (url == null || url.isBlank()) {
            String serviceName = annotationString(feign, owner, "name", "value");
            url = serviceName == null || serviceName.isBlank() ? "" : "http://" + serviceName;
            confidence = 0.8;
        }
        String prefix = annotationString(feign, owner, "path");
        String classMapping = findAnnotation(owner.getAnnotations(), "RequestMapping")
                .map(a -> annotationString(a, owner, "value", "path")).orElse(null);
        String full = url;
        if (prefix != null) full = ExternalApiSupport.joinUrl(full, prefix);
        if (classMapping != null) full = ExternalApiSupport.joinUrl(full, classMapping);
        if (path != null) full = ExternalApiSupport.joinUrl(full, path);
        support.addCall(graph, methodId, verb, full, "OpenFeign", line, confidence, "feign-annotation", language);
    }

    // ---------------------------------------------------------------------------------------------
    // URL / expression resolution
    // ---------------------------------------------------------------------------------------------

    private String resolveUriArgument(Expression arg, Node ctx) {
        if (arg instanceof LambdaExpr lambda) {
            // uriBuilder -> uriBuilder.path("/x/{id}").queryParam(...).build(id)
            StringBuilder sb = new StringBuilder();
            for (MethodCallExpr c : lambda.findAll(MethodCallExpr.class)) {
                if (c.getNameAsString().equals("path") && !c.getArguments().isEmpty()) {
                    sb.insert(0, resolveUrl(c.getArgument(0), ctx, 0));
                }
            }
            return sb.length() == 0 ? "{dynamic}" : sb.toString();
        }
        return resolveUrl(arg, ctx, 0);
    }

    private String findBaseUrl(MethodCallExpr from) {
        Optional<TypeDeclaration> owner = from.findAncestor(TypeDeclaration.class);
        List<Node> scopes = new ArrayList<>();
        owner.ifPresent(scopes::add);
        from.findCompilationUnit().ifPresent(scopes::add);
        for (Node scope : scopes) {
            for (MethodCallExpr c : scope.findAll(MethodCallExpr.class)) {
                String n = c.getNameAsString();
                boolean isBase = (n.equals("baseUrl") || n.equals("rootUri"))
                        || (n.equals("create") && c.getScope().filter(s -> s instanceof NameExpr ne
                        && (ne.getNameAsString().equals("WebClient") || ne.getNameAsString().equals("RestClient"))).isPresent());
                if (isBase && c.getArguments().size() == 1) {
                    String base = resolveUrl(c.getArgument(0), c, 0);
                    if (base != null && !base.isBlank()) return base;
                }
            }
        }
        return null;
    }

    String resolveUrl(Expression expr, Node ctx, int depth) {
        if (expr == null) return "{dynamic}";
        if (depth > MAX_DEPTH) return "{" + shorten(expr) + "}";
        if (expr instanceof StringLiteralExpr s) return s.asString();
        if (expr instanceof TextBlockLiteralExpr t) return t.asString().trim();
        if (expr instanceof EnclosedExpr e) return resolveUrl(e.getInner(), ctx, depth + 1);
        if (expr instanceof BinaryExpr b && b.getOperator() == BinaryExpr.Operator.PLUS) {
            return resolveUrl(b.getLeft(), ctx, depth + 1) + resolveUrl(b.getRight(), ctx, depth + 1);
        }
        if (expr instanceof NameExpr n) return resolveVariable(n.getNameAsString(), ctx, depth);
        if (expr instanceof FieldAccessExpr f) {
            if (f.getScope() instanceof ThisExpr) return resolveVariable(f.getNameAsString(), ctx, depth);
            return "{" + f.getNameAsString() + "}";
        }
        if (expr instanceof ObjectCreationExpr o && !o.getArguments().isEmpty()
                && (o.getType().getNameAsString().equals("URI") || o.getType().getNameAsString().equals("URL"))) {
            return resolveUrl(o.getArgument(0), ctx, depth + 1);
        }
        if (expr instanceof MethodCallExpr m) return resolveCallExpression(m, ctx, depth);
        return "{" + shorten(expr) + "}";
    }

    private String resolveCallExpression(MethodCallExpr m, Node ctx, int depth) {
        String name = m.getNameAsString();
        List<Expression> args = m.getArguments();
        String scopeText = m.getScope().map(Expression::toString).orElse("");

        if (name.equals("format") && scopeText.equals("String") && !args.isEmpty()) {
            return stripFormatSpecifiers(resolveUrl(args.get(0), ctx, depth + 1));
        }
        if (name.equals("formatted") && m.getScope().isPresent()) {
            return stripFormatSpecifiers(resolveUrl(m.getScope().get(), ctx, depth + 1));
        }
        if ((name.equals("create") && scopeText.equals("URI")) && args.size() == 1) {
            return resolveUrl(args.get(0), ctx, depth + 1);
        }
        if (name.equals("getProperty") && !args.isEmpty() && args.get(0) instanceof StringLiteralExpr key) {
            String def = args.size() > 1 && args.get(1) instanceof StringLiteralExpr d ? ":" + d.asString() : "";
            return "${" + key.asString() + def + "}";
        }
        if (name.equals("toString") || name.equals("toUriString") || name.equals("toUri") || name.equals("build")
                || name.equals("encode") || name.equals("expand") || name.equals("buildAndExpand")
                || name.equals("trim") || name.equals("strip")) {
            if (m.getScope().isPresent()) return resolveUrl(m.getScope().get(), ctx, depth + 1);
        }
        if (name.equals("path") && m.getScope().isPresent() && !args.isEmpty()) {
            return resolveUrl(m.getScope().get(), ctx, depth + 1) + resolveUrl(args.get(0), ctx, depth + 1);
        }
        if ((name.equals("queryParam") || name.equals("queryParams") || name.equals("replaceQueryParam"))
                && m.getScope().isPresent()) {
            return resolveUrl(m.getScope().get(), ctx, depth + 1);
        }
        if ((name.equals("fromHttpUrl") || name.equals("fromUriString") || name.equals("fromUri")
                || name.equals("fromPath")) && !args.isEmpty()) {
            return resolveUrl(args.get(0), ctx, depth + 1);
        }
        return "{" + name + "()}";
    }

    private String resolveVariable(String name, Node ctx, int depth) {
        Optional<CallableDeclaration> callable = ctx instanceof CallableDeclaration<?> c
                ? Optional.of((CallableDeclaration) c) : ctx.findAncestor(CallableDeclaration.class);

        if (callable.isPresent()) {
            for (Object p : callable.get().getParameters()) {
                Parameter param = (Parameter) p;
                if (param.getNameAsString().equals(name)) {
                    String v = annotationString(findAnnotation(param.getAnnotations(), "Value").orElse(null), param, "value");
                    return v != null ? v : "{" + name + "}";
                }
            }
            for (VariableDeclarator v : ((Node) callable.get()).findAll(VariableDeclarator.class)) {
                if (v.getNameAsString().equals(name) && v.getInitializer().isPresent()
                        && !(v.getParentNode().orElse(null) instanceof FieldDeclaration)) {
                    return resolveUrl(v.getInitializer().get(), ctx, depth + 1);
                }
            }
        }

        Optional<TypeDeclaration> type = ctx.findAncestor(TypeDeclaration.class);
        while (type.isPresent()) {
            for (FieldDeclaration f : ((TypeDeclaration<?>) type.get()).getFields()) {
                for (VariableDeclarator v : f.getVariables()) {
                    if (!v.getNameAsString().equals(name)) continue;
                    String valueAnn = annotationString(findAnnotation(f.getAnnotations(), "Value").orElse(null), f, "value");
                    if (valueAnn != null) return valueAnn;
                    if (v.getInitializer().isPresent()) return resolveUrl(v.getInitializer().get(), f, depth + 1);
                    return "{" + name + "}";
                }
            }
            type = type.get().findAncestor(TypeDeclaration.class);
        }
        return "{" + name + "}";
    }

    private static String stripFormatSpecifiers(String s) {
        return FORMAT_SPECIFIER.matcher(s).replaceAll("{}");
    }

    private static String shorten(Expression e) {
        String s = e.toString().replaceAll("\\s+", " ");
        return s.length() > 30 ? s.substring(0, 30) + "..." : s;
    }

    private static String literalOr(Expression e, String fallback) {
        return e instanceof StringLiteralExpr s ? s.asString().toUpperCase(Locale.ROOT) : fallback;
    }

    // ---------------------------------------------------------------------------------------------
    // Annotation + chain + type helpers
    // ---------------------------------------------------------------------------------------------

    private static Optional<AnnotationExpr> findAnnotation(List<AnnotationExpr> annotations, String name) {
        return annotations.stream().filter(a -> a.getNameAsString().equals(name)
                || a.getNameAsString().endsWith("." + name)).findFirst();
    }

    private static String annotationRaw(AnnotationExpr ann, String... attrs) {
        if (ann == null) return null;
        if (ann instanceof SingleMemberAnnotationExpr s) {
            for (String a : attrs) if (a.equals("value")) return s.getMemberValue().toString();
        } else if (ann instanceof NormalAnnotationExpr n) {
            for (String a : attrs) {
                for (MemberValuePair p : n.getPairs()) {
                    if (p.getNameAsString().equals(a)) return p.getValue().toString();
                }
            }
        }
        return null;
    }

    private String annotationString(AnnotationExpr ann, Node ctx, String... attrs) {
        if (ann == null) return null;
        Expression value = null;
        if (ann instanceof SingleMemberAnnotationExpr s) {
            for (String a : attrs) if (a.equals("value")) value = s.getMemberValue();
        } else if (ann instanceof NormalAnnotationExpr n) {
            outer:
            for (String a : attrs) {
                for (MemberValuePair p : n.getPairs()) {
                    if (p.getNameAsString().equals(a)) { value = p.getValue(); break outer; }
                }
            }
        }
        if (value == null) return null;
        if (value instanceof ArrayInitializerExpr arr) {
            if (arr.getValues().isEmpty()) return null;
            value = arr.getValues().get(0);
        }
        return resolveUrl(value, ctx, 0);
    }

    private static List<MethodCallExpr> inwardChain(MethodCallExpr call) {
        List<MethodCallExpr> chain = new ArrayList<>();
        Expression cur = call;
        while (cur instanceof MethodCallExpr m) {
            chain.add(m);
            cur = m.getScope().orElse(null);
        }
        Collections.reverse(chain);
        return chain;
    }

    private static Expression chainRoot(MethodCallExpr call) {
        Expression cur = call;
        while (cur instanceof MethodCallExpr m) {
            if (m.getScope().isEmpty()) return null;
            cur = m.getScope().get();
        }
        return cur;
    }

    /** The call itself plus every call that is chained after it (x.a().b().c() from a() -> [a, b, c]). */
    private static List<MethodCallExpr> outwardChain(MethodCallExpr start) {
        List<MethodCallExpr> chain = new ArrayList<>();
        chain.add(start);
        Node cur = start;
        while (cur.getParentNode().orElse(null) instanceof MethodCallExpr parent
                && parent.getScope().isPresent() && parent.getScope().get() == cur) {
            chain.add(parent);
            cur = parent;
        }
        return chain;
    }

    /** Declared type (simple/with-scope name, generics stripped) of a receiver expression, or null. */
    private String receiverType(Expression expr, Node ctx) {
        if (expr instanceof MethodCallExpr m) {
            Expression root = chainRoot(m);
            return root == null ? null : receiverType(root, ctx);
        }
        String name = null;
        if (expr instanceof NameExpr n) name = n.getNameAsString();
        else if (expr instanceof FieldAccessExpr f && f.getScope() instanceof ThisExpr) name = f.getNameAsString();
        if (name == null) return null;
        if (Character.isUpperCase(name.charAt(0))) return name; // static access: WebClient.create(...)

        Optional<CallableDeclaration> callable = ctx.findAncestor(CallableDeclaration.class);
        if (callable.isPresent()) {
            for (Object p : callable.get().getParameters()) {
                Parameter param = (Parameter) p;
                if (param.getNameAsString().equals(name)) return typeName(param.getType());
            }
            for (VariableDeclarator v : ((Node) callable.get()).findAll(VariableDeclarator.class)) {
                if (v.getNameAsString().equals(name)) return typeName(v.getType());
            }
        }
        Optional<TypeDeclaration> type = ctx.findAncestor(TypeDeclaration.class);
        while (type.isPresent()) {
            for (FieldDeclaration f : ((TypeDeclaration<?>) type.get()).getFields()) {
                for (VariableDeclarator v : f.getVariables()) {
                    if (v.getNameAsString().equals(name)) return typeName(v.getType());
                }
            }
            type = type.get().findAncestor(TypeDeclaration.class);
        }
        return null;
    }

    private static String typeName(com.github.javaparser.ast.type.Type t) {
        return t instanceof ClassOrInterfaceType c ? c.getNameWithScope() : t.asString();
    }

    private void emit(String callerId, String verb, String url, String client, int line, String reason) {
        double confidence = url != null && (url.contains("{") ) ? 0.7 : 0.9;
        support.addCall(graph, callerId, verb, url, client, line, confidence, reason, language);
    }
}
