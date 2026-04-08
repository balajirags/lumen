package code.graph;

import code.graph.export.DotExporter;
import code.graph.export.GraphExporter;
import code.graph.export.GraphMlExporter;
import code.graph.export.JsonExporter;
import code.graph.model.CodeGraph;
import code.graph.parser.JavaSourceParser;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import picocli.CommandLine.Parameters;
import picocli.CommandLine.ParentCommand;

import java.io.BufferedWriter;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.Callable;

/**
 * Subcommand: export parsed Java source code to DOT, GraphML, or JSON file.
 */
@Command(
        name = "export",
        description = "Parse Java source code and export the graph to DOT, GraphML, or JSON format."
)
public class ExportCommand implements Callable<Integer> {

    @ParentCommand
    private App parent;

    @Parameters(index = "0", description = "Path to Java project root directory.")
    private Path sourcePath;

    @Option(names = {"-f", "--format"}, description = "Export format: dot, graphml, json (default: json)", defaultValue = "json")
    private String format;

    @Option(names = {"-o", "--output"}, description = "Output file path (default: stdout)")
    private Path outputPath;

    @Option(names = {"--classpath", "--cp"}, description = "Additional classpath entries (JARs or directories). Comma-separated.")
    private String classpath;

    @Override
    public Integer call() throws Exception {
        if (!Files.isDirectory(sourcePath)) {
            System.err.println("Error: Not a directory: " + sourcePath);
            return 1;
        }

        // Detect modules and build classpath (reuse App's logic)
        List<Path> modules = App.detectModules(sourcePath);
        List<Path> classpathEntries = buildExportClasspath();

        // Parse all modules
        CodeGraph graph = new CodeGraph();
        for (Path module : modules) {
            Path sourceRoot = App.detectSourceRoot(module, Collections.singletonList(code.graph.parser.SourceParserFactory.Language.JAVA));
            System.err.println("Parsing Java sources from: " + sourceRoot.toAbsolutePath());

            List<Path> moduleCp = new ArrayList<>(classpathEntries);
            if (!module.equals(sourcePath)) {
                moduleCp.addAll(App.buildModuleClasspath(module));
            }

            JavaSourceParser parser = new JavaSourceParser(sourceRoot, moduleCp);
            CodeGraph moduleGraph = parser.parseDirectory(sourceRoot);
            graph.merge(moduleGraph);
        }

        System.err.printf("Extracted %d nodes and %d relationships%n",
                graph.nodeCount(), graph.relationshipCount());

        // Select exporter
        GraphExporter exporter = switch (format.toLowerCase()) {
            case "dot" -> new DotExporter();
            case "graphml" -> new GraphMlExporter();
            case "json" -> new JsonExporter();
            default -> {
                System.err.println("Unknown format: " + format + ". Use dot, graphml, or json.");
                yield null;
            }
        };

        if (exporter == null) return 1;

        // Write output
        if (outputPath != null) {
            try (Writer writer = Files.newBufferedWriter(outputPath, StandardCharsets.UTF_8)) {
                exporter.export(graph, writer);
            }
            System.err.printf("Exported graph to %s (%s format)%n", outputPath, format);
        } else {
            Writer writer = new BufferedWriter(new OutputStreamWriter(System.out, StandardCharsets.UTF_8));
            exporter.export(graph, writer);
            writer.flush();
        }

        return 0;
    }

    private List<Path> buildExportClasspath() {
        List<Path> entries = new ArrayList<>();
        if (classpath != null && !classpath.isBlank()) {
            for (String part : classpath.split(",")) {
                Path p = Path.of(part.trim());
                if (Files.exists(p)) {
                    entries.add(p);
                }
            }
        }
        // Also try auto-resolution
        entries.addAll(App.autoResolveClasspath(sourcePath));
        return entries;
    }
}
