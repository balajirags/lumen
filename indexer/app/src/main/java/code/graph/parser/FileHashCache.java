package code.graph.parser;

import java.io.IOException;
import java.nio.file.*;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;

/**
 * Tracks file hashes to support incremental parsing.
 * Stores SHA-256 hashes in a simple properties file (.code-mem-graph-hashes).
 */
public class FileHashCache {

    private final Path cacheFile;
    private final Map<String, String> previousHashes = new LinkedHashMap<>();
    private final Map<String, String> currentHashes = new LinkedHashMap<>();

    public FileHashCache(Path cacheFile) {
        this.cacheFile = cacheFile;
        load();
    }

    /**
     * Check if a file has changed since the last run.
     * Also records the current hash for saving later.
     */
    public boolean hasChanged(Path file) {
        try {
            String currentHash = hashFile(file);
            String relativePath = file.toAbsolutePath().normalize().toString();
            currentHashes.put(relativePath, currentHash);

            String previousHash = previousHashes.get(relativePath);
            return !currentHash.equals(previousHash);
        } catch (IOException e) {
            // If we can't hash it, assume it changed
            return true;
        }
    }

    /**
     * Get the number of unchanged files (skipped).
     */
    public int skippedCount(int totalFiles) {
        return totalFiles - changedCount();
    }

    private int changedCount() {
        int changed = 0;
        for (Map.Entry<String, String> entry : currentHashes.entrySet()) {
            String prev = previousHashes.get(entry.getKey());
            if (!entry.getValue().equals(prev)) {
                changed++;
            }
        }
        return changed;
    }

    /**
     * Save the current hashes to the cache file.
     */
    public void save() {
        try {
            StringBuilder sb = new StringBuilder();
            sb.append("# code-mem-graph file hash cache — do not edit\n");
            for (Map.Entry<String, String> entry : currentHashes.entrySet()) {
                sb.append(entry.getValue()).append("  ").append(entry.getKey()).append("\n");
            }
            Files.writeString(cacheFile, sb.toString());
        } catch (IOException e) {
            System.err.println("Warning: Could not save hash cache: " + e.getMessage());
        }
    }

    private void load() {
        if (!Files.isRegularFile(cacheFile)) return;
        try {
            for (String line : Files.readAllLines(cacheFile)) {
                if (line.startsWith("#") || line.isBlank()) continue;
                int sep = line.indexOf("  ");
                if (sep > 0) {
                    String hash = line.substring(0, sep);
                    String path = line.substring(sep + 2);
                    previousHashes.put(path, hash);
                }
            }
        } catch (IOException e) {
            // Start fresh if we can't read the cache
        }
    }

    private static String hashFile(Path file) throws IOException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = Files.readAllBytes(file);
            byte[] hash = digest.digest(bytes);
            return HexFormat.of().formatHex(hash);
        } catch (NoSuchAlgorithmException e) {
            throw new RuntimeException("SHA-256 not available", e);
        }
    }
}
