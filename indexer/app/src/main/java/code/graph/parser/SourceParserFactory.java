package code.graph.parser;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.stream.Stream;

/**
 * Factory for creating the appropriate SourceParser based on language detection.
 * Implements the Strategy pattern for multi-language support.
 */
public class SourceParserFactory {

    /**
     * Supported programming languages.
     */
    public enum Language {
        JAVA("Java", Set.of(".java")),
        JAVASCRIPT("JavaScript/React", Set.of(".js", ".jsx", ".ts", ".tsx", ".mjs")),
        PYTHON("Python", Set.of(".py", ".pyw")),
        KOTLIN("Kotlin", Set.of(".kt", ".kts"));

        private final String displayName;
        private final Set<String> extensions;

        Language(String displayName, Set<String> extensions) {
            this.displayName = displayName;
            this.extensions = extensions;
        }

        public String getDisplayName() {
            return displayName;
        }

        public Set<String> getExtensions() {
            return extensions;
        }
    }

    /**
     * Detect the primary programming language used in the given directory.
     * 
     * @param rootDir the root directory to scan
     * @return the detected language, or empty if no supported language is found
     */
    public static Optional<Language> detectLanguage(Path rootDir) throws IOException {
        Map<Language, Integer> counts = new EnumMap<>(Language.class);
        
        // Count files by language
        try (Stream<Path> walk = Files.walk(rootDir)) {
            walk.filter(Files::isRegularFile)
                .filter(p -> !isIgnoredPath(p, rootDir))
                .forEach(path -> {
                    String fileName = path.getFileName().toString();
                    for (Language lang : Language.values()) {
                        for (String ext : lang.getExtensions()) {
                            if (fileName.endsWith(ext)) {
                                counts.merge(lang, 1, Integer::sum);
                                break;
                            }
                        }
                    }
                });
        }

        if (counts.isEmpty()) {
            return Optional.empty();
        }

        // Return the language with the most files
        return counts.entrySet().stream()
                .max(Map.Entry.comparingByValue())
                .map(Map.Entry::getKey);
    }

    /**
     * Get all languages detected in the given directory.
     * 
     * @param rootDir the root directory to scan
     * @return set of detected languages with file counts
     */
    public static Map<Language, Integer> detectAllLanguages(Path rootDir) throws IOException {
        Map<Language, Integer> counts = new EnumMap<>(Language.class);
        
        try (Stream<Path> walk = Files.walk(rootDir)) {
            walk.filter(Files::isRegularFile)
                .filter(p -> !isIgnoredPath(p, rootDir))
                .forEach(path -> {
                    String fileName = path.getFileName().toString();
                    for (Language lang : Language.values()) {
                        for (String ext : lang.getExtensions()) {
                            if (fileName.endsWith(ext)) {
                                counts.merge(lang, 1, Integer::sum);
                                break;
                            }
                        }
                    }
                });
        }

        return counts;
    }

    /**
     * Create a parser for the specified language.
     * 
     * @param language the target language
     * @param sourceRoot the source root directory
     * @param classpathEntries additional classpath entries (for Java)
     * @param hashCache file hash cache for incremental parsing (for Java)
     * @param workspaceRoot the workspace root (for finding parser scripts)
     * @return the appropriate SourceParser implementation
     */
    public static SourceParser createParser(
            Language language,
            Path sourceRoot,
            List<Path> classpathEntries,
            FileHashCache hashCache,
            Path workspaceRoot) {
        
        return switch (language) {
            case JAVA -> new JavaSourceParser(sourceRoot, classpathEntries, hashCache);
            case JAVASCRIPT -> new JavaScriptSourceParser(workspaceRoot);
            case PYTHON -> new PythonSourceParser(workspaceRoot);
            case KOTLIN -> new KotlinSourceParser(sourceRoot);
        };
    }

    /**
     * Create a parser by auto-detecting the language.
     * 
     * @param rootDir the root directory to parse
     * @param classpathEntries additional classpath entries (for Java)
     * @param hashCache file hash cache for incremental parsing (for Java)
     * @param workspaceRoot the workspace root (for finding parser scripts)
     * @return the appropriate SourceParser, or empty if no supported language detected
     */
    public static Optional<SourceParser> createParserAutoDetect(
            Path rootDir,
            List<Path> classpathEntries,
            FileHashCache hashCache,
            Path workspaceRoot) throws IOException {
        
        Optional<Language> detected = detectLanguage(rootDir);
        if (detected.isEmpty()) {
            return Optional.empty();
        }

        Language lang = detected.get();
        System.out.printf("Detected language: %s%n", lang.getDisplayName());
        
        return Optional.of(createParser(lang, rootDir, classpathEntries, hashCache, workspaceRoot));
    }

    /**
     * Parse a string language name to Language enum.
     */
    public static Optional<Language> parseLanguage(String name) {
        if (name == null || name.isBlank()) {
            return Optional.empty();
        }
        
        String normalized = name.toLowerCase().trim();
        
        return switch (normalized) {
            case "java" -> Optional.of(Language.JAVA);
            case "javascript", "js", "react", "typescript", "ts", "jsx", "tsx" -> 
                Optional.of(Language.JAVASCRIPT);
            case "python", "py" -> Optional.of(Language.PYTHON);
            case "kotlin", "kt", "kts" -> Optional.of(Language.KOTLIN);
            default -> Optional.empty();
        };
    }

    /** Directories to always skip at any depth. */
    private static final Set<String> ALWAYS_IGNORED = Set.of(
            "node_modules", ".git", "__pycache__", "venv", ".venv", ".gradle");

    /** Directories to skip only when they appear directly under the repo root. */
    private static final Set<String> ROOT_ONLY_IGNORED = Set.of("build", "dist", "target");

    private static boolean isIgnoredPath(Path path, Path rootDir) {
        Path relative = rootDir.relativize(path);
        for (int i = 0; i < relative.getNameCount(); i++) {
            String part = relative.getName(i).toString();
            if (ALWAYS_IGNORED.contains(part)) {
                return true;
            }
            if (i == 0 && ROOT_ONLY_IGNORED.contains(part)) {
                return true;
            }
        }
        return false;
    }
}
