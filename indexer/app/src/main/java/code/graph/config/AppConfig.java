package code.graph.config;

import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

/**
 * Loads configuration from an application.yaml file.
 */
public class AppConfig {

    private static final String DEFAULT_CONFIG_FILE = "application.yaml";

    private final String backend;
    private final String neo4jUri;
    private final String neo4jUsername;
    private final String neo4jPassword;
    private final String neo4jDatabase;
    private final String kuzuDbPath;

    private AppConfig(String backend, String neo4jUri, String neo4jUsername,
                      String neo4jPassword, String neo4jDatabase, String kuzuDbPath) {
        this.backend = backend;
        this.neo4jUri = neo4jUri;
        this.neo4jUsername = neo4jUsername;
        this.neo4jPassword = neo4jPassword;
        this.neo4jDatabase = neo4jDatabase;
        this.kuzuDbPath = kuzuDbPath;
    }

    /**
     * Load config from the given path. Falls back to ./application.yaml
     * in the current directory, then to built-in defaults.
     */
    @SuppressWarnings("unchecked")
    public static AppConfig load(Path configPath) {
        // Defaults
        String backend = "kuzu";
        String neo4jUri = "bolt://localhost:7687";
        String neo4jUsername = "neo4j";
        String neo4jPassword = "123456789";
        String neo4jDatabase = "neo4j";
        String kuzuDbPath = "./kuzu_db";

        Path resolved = configPath != null ? configPath : Path.of(DEFAULT_CONFIG_FILE);
        if (Files.isRegularFile(resolved)) {
            try (InputStream in = Files.newInputStream(resolved)) {
                Yaml yaml = new Yaml();
                Map<String, Object> root = yaml.load(in);
                if (root != null) {
                    backend = getString(root, "backend", backend);

                    Map<String, Object> neo4j = getMap(root, "neo4j");
                    if (neo4j != null) {
                        neo4jUri = getString(neo4j, "uri", neo4jUri);
                        neo4jUsername = getString(neo4j, "username", neo4jUsername);
                        neo4jPassword = getString(neo4j, "password", neo4jPassword);
                        neo4jDatabase = getString(neo4j, "database", neo4jDatabase);
                    }

                    Map<String, Object> kuzu = getMap(root, "kuzu");
                    if (kuzu != null) {
                        kuzuDbPath = getString(kuzu, "db-path", kuzuDbPath);
                    }
                }
                System.out.println("Loaded config from: " + resolved.toAbsolutePath());
            } catch (IOException e) {
                System.err.println("Warning: Could not read config file " + resolved + ": " + e.getMessage());
            }
        } else if (configPath != null) {
            System.err.println("Warning: Config file not found: " + resolved.toAbsolutePath());
        }

        return new AppConfig(backend, neo4jUri, neo4jUsername, neo4jPassword, neo4jDatabase, kuzuDbPath);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> getMap(Map<String, Object> map, String key) {
        Object value = map.get(key);
        return value instanceof Map ? (Map<String, Object>) value : null;
    }

    private static String getString(Map<String, Object> map, String key, String defaultValue) {
        Object value = map.get(key);
        return value != null ? value.toString() : defaultValue;
    }

    public String getBackend() {
        return backend;
    }

    public String getNeo4jUri() {
        return neo4jUri;
    }

    public String getNeo4jUsername() {
        return neo4jUsername;
    }

    public String getNeo4jPassword() {
        return neo4jPassword;
    }

    public String getNeo4jDatabase() {
        return neo4jDatabase;
    }

    public String getKuzuDbPath() {
        return kuzuDbPath;
    }
}
