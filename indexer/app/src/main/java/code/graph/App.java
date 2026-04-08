/*
 * code-mem-graph: CLI tool to extract Java code knowledge graphs.
 */
package code.graph;

import code.graph.config.AppConfig;
import code.graph.model.CodeGraph;
import code.graph.parser.CpgParser;
import code.graph.parser.FileHashCache;
import code.graph.parser.SourceParser;
import code.graph.parser.SourceParserFactory;
import code.graph.parser.SourceParserFactory.Language;
import code.graph.store.GraphStore;
import code.graph.store.KuzuGraphStore;
import code.graph.store.Neo4jGraphStore;
import picocli.CommandLine;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import picocli.CommandLine.Parameters;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.Callable;

@Command(
        name = "code-mem-graph",
        mixinStandardHelpOptions = true,
        version = "code-mem-graph 0.1.0",
        description = "Parses source code and builds a Code Property Graph in Neo4j or KuzuDB. Auto-detects language (Java, JavaScript, Python, Kotlin).",
        subcommands = {ExportCommand.class}
)
public class App implements Callable<Integer> {

    @Parameters(index = "0", arity = "0..1", description = "Path to project root directory.")
    private Path sourcePath;

    @Option(names = {"-c", "--config"}, description = "Path to config file (default: ./application.yaml)")
    private Path configPath;

    @Option(names = {"-b", "--backend"}, description = "Graph backend: neo4j or kuzu")
    private String backend;

    @Option(names = {"--neo4j-uri"}, description = "Neo4j connection URI")
    private String neo4jUri;

    @Option(names = {"--neo4j-user"}, description = "Neo4j username")
    private String neo4jUser;

    @Option(names = {"--neo4j-password"}, description = "Neo4j password")
    private String neo4jPassword;

    @Option(names = {"--neo4j-database"}, description = "Neo4j database name")
    private String neo4jDatabase;

    @Option(names = {"--db-path"}, description = "KuzuDB database directory")
    private String kuzuDbPath;

    @Option(names = {"--classpath", "--cp"}, description = "Additional classpath entries (JARs or directories of JARs). Comma-separated.")
    private String classpath;

    @Option(names = {"--incremental"}, description = "Only re-parse files that have changed since the last run.")
    private boolean incremental;

    @Option(names = {"-l", "--language"}, description = "Source language: java, javascript, python, kotlin, jvm (default: auto-detect)")
    private String language;

    @Override
    public Integer call() throws Exception {
        if (sourcePath == null) {
            System.err.println("Error: Missing required argument: <sourcePath>");
            return 1;
        }

        // Load config file, then let CLI flags override
        AppConfig config = AppConfig.load(configPath);
        if (backend == null) backend = config.getBackend();
        if (neo4jUri == null) neo4jUri = config.getNeo4jUri();
        if (neo4jUser == null) neo4jUser = config.getNeo4jUsername();
        if (neo4jPassword == null) neo4jPassword = config.getNeo4jPassword();
        if (neo4jDatabase == null) neo4jDatabase = config.getNeo4jDatabase();
        if (kuzuDbPath == null) {
            kuzuDbPath = config.getKuzuDbPath();
        }

        // Validate source path
        if (!Files.isDirectory(sourcePath)) {
            System.err.println("Error: Not a directory: " + sourcePath);
            return 1;
        }

        // Derive database name from project directory and always use <db-path>/<project-name>-db
        String projectName = sourcePath.toAbsolutePath().normalize().getFileName().toString();
        if (neo4jDatabase == null || neo4jDatabase.equals(config.getNeo4jDatabase())) {
            if ("neo4j".equals(config.getNeo4jDatabase())) {
                neo4jDatabase = projectName;
            }
        }
        java.nio.file.Path dbDir;
        if (kuzuDbPath == null || kuzuDbPath.isEmpty()) {
            dbDir = java.nio.file.Path.of("kuzu_db");
        } else {
            dbDir = java.nio.file.Path.of(kuzuDbPath);
        }
        if (!java.nio.file.Files.exists(dbDir)) {
            java.nio.file.Files.createDirectories(dbDir);
        }
        kuzuDbPath = dbDir.resolve(projectName + "-db").toString();

        // Detect modules (or single project) and build combined classpath
        List<Path> modules = detectModules(sourcePath);
        List<Path> classpathEntries = buildClasspath(sourcePath);

        // Detect language (CLI override or auto-detect)
        List<Language> parseLanguages = new ArrayList<>();
        Map<Language, Integer> allLangs = SourceParserFactory.detectAllLanguages(sourcePath);
        if (language != null) {
            if ("jvm".equalsIgnoreCase(language.trim())) {
                if (allLangs.containsKey(Language.JAVA)) {
                    parseLanguages.add(Language.JAVA);
                }
                if (allLangs.containsKey(Language.KOTLIN)) {
                    parseLanguages.add(Language.KOTLIN);
                }
                if (parseLanguages.isEmpty()) {
                    System.err.println("Error: No Java or Kotlin sources found in " + sourcePath);
                    return 1;
                }
                System.out.printf("Language family: JVM (%s) (specified via --language)%n",
                        parseLanguages.stream().map(Language::getDisplayName).toList());
            } else {
                Optional<Language> parsed = SourceParserFactory.parseLanguage(language);
                if (parsed.isEmpty()) {
                    System.err.println("Error: Unknown language: " + language + ". Supported: java, javascript, python, kotlin, jvm");
                    return 1;
                }
                parseLanguages.add(parsed.get());
                System.out.printf("Language: %s (specified via --language)%n", parsed.get().getDisplayName());
            }
        } else {
            if (allLangs.isEmpty()) {
                System.err.println("Error: No supported source files found in " + sourcePath);
                return 1;
            }
            if (allLangs.containsKey(Language.JAVA) || allLangs.containsKey(Language.KOTLIN)) {
                if (allLangs.containsKey(Language.JAVA)) {
                    parseLanguages.add(Language.JAVA);
                }
                if (allLangs.containsKey(Language.KOTLIN)) {
                    parseLanguages.add(Language.KOTLIN);
                }
                System.out.printf("Detected JVM languages: %s%n",
                        parseLanguages.stream().map(Language::getDisplayName).toList());
            } else {
                Optional<Language> detected = SourceParserFactory.detectLanguage(sourcePath);
                if (detected.isEmpty()) {
                    System.err.println("Error: No supported source files found in " + sourcePath);
                    return 1;
                }
                parseLanguages.add(detected.get());
                if (allLangs.size() > 1) {
                    System.out.printf("Detected languages: %s (using %s — override with --language)%n",
                            allLangs, detected.get().getDisplayName());
                } else {
                    System.out.printf("Detected language: %s%n", detected.get().getDisplayName());
                }
            }
        }

        // Set up incremental hash cache if requested
        FileHashCache hashCache = null;
        if (incremental) {
            Path cacheFile = sourcePath.resolve(".code-mem-graph-hashes");
            hashCache = new FileHashCache(cacheFile);
            System.out.println("Incremental mode: tracking file hashes in " + cacheFile);
        }

        // Resolve the workspace root (for finding external parser scripts)
        Path workspaceRoot = Path.of(System.getProperty("user.dir"));

        // Parse all modules into a single combined graph
        CodeGraph graph = new CodeGraph();
        for (Path module : modules) {
            Path sourceRoot = detectSourceRoot(module, parseLanguages);
            System.out.println("Parsing " +
                    parseLanguages.stream().map(Language::getDisplayName).toList() +
                    " sources from: " + sourceRoot.toAbsolutePath());

            // For multi-module, also add each module's own classpath
            List<Path> moduleCp = new ArrayList<>(classpathEntries);
            if (!module.equals(sourcePath)) {
                moduleCp.addAll(buildModuleClasspath(module));
            }

            for (Language parseLanguage : parseLanguages) {
                CodeGraph moduleGraph;
                if (parseLanguage == Language.JAVA) {
                    CpgParser cpgParser = new CpgParser(sourceRoot, moduleCp, hashCache);
                    moduleGraph = cpgParser.parseDirectory(sourceRoot);
                } else {
                    SourceParser parser = SourceParserFactory.createParser(parseLanguage, sourceRoot, moduleCp, hashCache, workspaceRoot);
                    moduleGraph = parser.parseDirectory(sourceRoot);
                }
                graph.merge(moduleGraph);
            }
        }

        // Save hash cache after successful parse
        if (hashCache != null) {
            hashCache.save();
        }

        System.out.printf("Extracted %d nodes and %d relationships%n",
                graph.nodeCount(), graph.relationshipCount());

        // Store in graph database
        try (GraphStore store = createStore()) {
            store.initSchema();

            if (!incremental) {
                System.out.println("Clearing existing graph data (use --incremental to keep)...");
                store.clear();
            }

            System.out.println("Saving graph to " + backend + "...");
            store.save(graph);
            System.out.println(store.summary());
            System.out.println("Done.");
        }

        return 0;
    }

    private GraphStore createStore() {
        return switch (backend.toLowerCase()) {
            case "neo4j" -> new Neo4jGraphStore(neo4jUri, neo4jUser, neo4jPassword, neo4jDatabase);
            case "kuzu" -> new KuzuGraphStore(kuzuDbPath);
            default -> throw new IllegalArgumentException("Unknown backend: " + backend + ". Use 'neo4j' or 'kuzu'.");
        };
    }

    /**
     * Detect the Java source root within a project directory.
     * Checks common layouts in order: src/main/java, src, then falls back to the project root.
     */
    static Path detectSourceRoot(Path projectRoot, List<Language> languages) {
        boolean hasJava = languages.contains(Language.JAVA);
        boolean hasKotlin = languages.contains(Language.KOTLIN);

        if (hasJava && hasKotlin) {
            Path[] mixedCandidates = {
                    projectRoot.resolve("src/main"),
                    projectRoot.resolve("src"),
            };
            for (Path candidate : mixedCandidates) {
                if (Files.isDirectory(candidate)) {
                    return candidate;
                }
            }
        }

        Path[] candidates = hasKotlin
                ? new Path[]{projectRoot.resolve("src/main/kotlin"), projectRoot.resolve("src/main"), projectRoot.resolve("src")}
                : new Path[]{projectRoot.resolve("src/main/java"), projectRoot.resolve("src/main"), projectRoot.resolve("src")};
        for (Path candidate : candidates) {
            if (Files.isDirectory(candidate)) {
                return candidate;
            }
        }
        return projectRoot;
    }

    /**
     * Detect sub-modules in a multi-module project.
     * Looks for subdirectories with their own build.gradle / pom.xml that have Java sources.
     * Returns the root project itself if no sub-modules are found.
     */
    static List<Path> detectModules(Path projectRoot) {
        List<Path> modules = new ArrayList<>();

        try (var entries = Files.list(projectRoot)) {
            for (Path child : entries.filter(Files::isDirectory).toList()) {
                boolean hasOwnBuild = Files.exists(child.resolve("build.gradle"))
                        || Files.exists(child.resolve("build.gradle.kts"))
                        || Files.exists(child.resolve("pom.xml"));
                boolean hasSrc = Files.isDirectory(child.resolve("src/main/java"))
                        || Files.isDirectory(child.resolve("src/main/kotlin"))
                        || Files.isDirectory(child.resolve("src/main"))
                        || Files.isDirectory(child.resolve("src"));

                if (hasOwnBuild && hasSrc) {
                    modules.add(child);
                }
            }
        } catch (Exception e) {
            // Fall through to single-module
        }

        if (modules.isEmpty()) {
            // Single-module project
            modules.add(projectRoot);
        } else {
            System.out.printf("Detected multi-module project with %d modules: %s%n",
                    modules.size(), modules.stream().map(p -> p.getFileName().toString()).toList());
            // Also include the root if it has its own sources
            if (Files.isDirectory(projectRoot.resolve("src/main/java"))
                    || Files.isDirectory(projectRoot.resolve("src/main/kotlin"))
                    || Files.isDirectory(projectRoot.resolve("src/main"))) {
                modules.addFirst(projectRoot);
            }
        }

        return modules;
    }

    /**
     * Build classpath entries specific to a sub-module (e.g. its own build/libs).
     */
    static List<Path> buildModuleClasspath(Path moduleRoot) {
        List<Path> entries = new ArrayList<>();
        scanWellKnownDirs(moduleRoot, entries);
        return entries;
    }

    /**
     * Build the classpath for symbol resolution.
     * Detects Gradle or Maven projects and resolves the compile classpath automatically.
     * Falls back to scanning well-known directories. Explicit --classpath entries are always added.
     */
    private List<Path> buildClasspath(Path projectRoot) {
        List<Path> entries = new ArrayList<>();

        // Explicit classpath from CLI
        if (classpath != null && !classpath.isBlank()) {
            for (String part : classpath.split(",")) {
                Path p = Path.of(part.trim());
                if (Files.exists(p)) {
                    entries.add(p);
                } else {
                    System.err.println("Warning: Classpath entry not found: " + p);
                }
            }
        }

        entries.addAll(autoResolveClasspath(projectRoot));

        if (!entries.isEmpty()) {
            System.out.printf("Found %d classpath entries for symbol resolution%n", entries.size());
        }

        return entries;
    }

    /**
     * Auto-resolve classpath from build system (Gradle/Maven) or well-known directories.
     */
    static List<Path> autoResolveClasspath(Path projectRoot) {
        List<Path> entries = new ArrayList<>();

        boolean resolved = false;
        if (isGradleProject(projectRoot)) {
            resolved = resolveGradleClasspath(projectRoot, entries);
        } else if (isMavenProject(projectRoot)) {
            resolved = resolveMavenClasspath(projectRoot, entries);
        }

        if (!resolved) {
            scanWellKnownDirs(projectRoot, entries);
        }

        return entries;
    }

    private static boolean isGradleProject(Path projectRoot) {
        return Files.exists(projectRoot.resolve("build.gradle"))
                || Files.exists(projectRoot.resolve("build.gradle.kts"))
                || Files.exists(projectRoot.resolve("gradlew"));
    }

    private static boolean isMavenProject(Path projectRoot) {
        return Files.exists(projectRoot.resolve("pom.xml"));
    }

    /**
     * Resolve classpath by running Gradle with a temporary init script
     * that prints the compileClasspath configuration.
     */
    private static boolean resolveGradleClasspath(Path projectRoot, List<Path> entries) {
        System.out.println("Detected Gradle project — resolving dependencies...");
        try {
            // Create a temporary init script to print classpath
            Path initScript = Files.createTempFile("code-mem-graph-init", ".gradle");
            Files.writeString(initScript, """
                    allprojects {
                        task codeGraphClasspath {
                            doLast {
                                def cp = configurations.findByName('compileClasspath')
                                if (cp != null && cp.isCanBeResolved()) {
                                    cp.resolve().each { println "CP_ENTRY:" + it }
                                }
                            }
                        }
                    }
                    """);

            // Prefer the project's Gradle wrapper
            String gradleCmd = Files.isExecutable(projectRoot.resolve("gradlew"))
                    ? projectRoot.resolve("gradlew").toAbsolutePath().toString()
                    : "gradle";

            List<String> command = List.of(
                    gradleCmd, "-q", "--init-script", initScript.toAbsolutePath().toString(),
                    "codeGraphClasspath"
            );

            int count = runClasspathCommand(command, projectRoot, entries, "CP_ENTRY:");
            Files.deleteIfExists(initScript);

            if (count > 0) {
                System.out.printf("Resolved %d JARs from Gradle compileClasspath%n", count);
                return true;
            }
        } catch (Exception e) {
            System.err.println("Warning: Could not resolve Gradle classpath: " + e.getMessage());
        }
        return false;
    }

    /**
     * Resolve classpath by running Maven's dependency:build-classpath goal.
     */
    private static boolean resolveMavenClasspath(Path projectRoot, List<Path> entries) {
        System.out.println("Detected Maven project — resolving dependencies...");
        try {
            // Prefer the project's Maven wrapper
            String mvnCmd = Files.isExecutable(projectRoot.resolve("mvnw"))
                    ? projectRoot.resolve("mvnw").toAbsolutePath().toString()
                    : "mvn";

            List<String> command = List.of(
                    mvnCmd, "-q", "dependency:build-classpath",
                    "-Dmdep.outputFile=/dev/stdout", "-Dmdep.pathSeparator=\n"
            );

            int count = runClasspathCommand(command, projectRoot, entries, null);
            if (count > 0) {
                System.out.printf("Resolved %d JARs from Maven classpath%n", count);
                return true;
            }
        } catch (Exception e) {
            System.err.println("Warning: Could not resolve Maven classpath: " + e.getMessage());
        }
        return false;
    }

    /**
     * Run an external command and collect JAR paths from its stdout.
     * If linePrefix is non-null, only lines starting with that prefix are used (prefix stripped).
     * Otherwise every non-blank line is treated as a potential JAR path.
     */
    private static int runClasspathCommand(List<String> command, Path workDir,
                                    List<Path> entries, String linePrefix) throws Exception {
        ProcessBuilder pb = new ProcessBuilder(command)
                .directory(workDir.toFile())
                .redirectErrorStream(false);
        Process process = pb.start();

        int count = 0;
        try (var reader = new BufferedReader(new InputStreamReader(process.getInputStream()))) {
            String line;
            while ((line = reader.readLine()) != null) {
                String path;
                if (linePrefix != null) {
                    if (!line.startsWith(linePrefix)) continue;
                    path = line.substring(linePrefix.length()).trim();
                } else {
                    path = line.trim();
                }
                if (path.isEmpty()) continue;
                Path p = Path.of(path);
                if (Files.exists(p) && p.toString().endsWith(".jar")) {
                    entries.add(p);
                    count++;
                }
            }
        }

        // Drain stderr so the process doesn't block
        try (var errReader = new BufferedReader(new InputStreamReader(process.getErrorStream()))) {
            while (errReader.readLine() != null) { /* discard */ }
        }

        int exitCode = process.waitFor();
        if (exitCode != 0 && count == 0) {
            System.err.println("Warning: Build tool exited with code " + exitCode);
        }
        return count;
    }

    /**
     * Fallback: scan well-known build output directories for JARs.
     */
    private static void scanWellKnownDirs(Path projectRoot, List<Path> entries) {
        Path[] candidates = {
                projectRoot.resolve("build/libs"),
                projectRoot.resolve("build/dependencies"),
                projectRoot.resolve("target/dependency"),
        };
        for (Path dir : candidates) {
            if (Files.isDirectory(dir)) {
                entries.add(dir);
            }
        }

        // Try Gradle's classpath file if the project has been built
        Path gradleClasspath = projectRoot.resolve("build/classpath.txt");
        if (Files.isRegularFile(gradleClasspath)) {
            try {
                String cp = Files.readString(gradleClasspath).trim();
                for (String part : cp.split(":")) {
                    Path p = Path.of(part.trim());
                    if (Files.exists(p) && p.toString().endsWith(".jar")) {
                        entries.add(p);
                    }
                }
            } catch (Exception e) {
                System.err.println("Warning: Could not read classpath file: " + e.getMessage());
            }
        }
    }

    public static void main(String[] args) {
        int exitCode = new CommandLine(new App()).execute(args);
        System.exit(exitCode);
    }
}
