package code.graph;

import code.graph.model.*;
import code.graph.parser.JavaSourceParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.*;

class ExternalApiDetectionTest {

    @TempDir
    Path tempDir;

    private Set<String> externalApis(CodeGraph graph) {
        return graph.getNodes().values().stream()
                .filter(n -> n.type() == NodeType.EXTERNAL_API)
                .map(CodeNode::name)
                .collect(Collectors.toSet());
    }

    private boolean hasExternalEdge(CodeGraph graph, String callerId, String apiName) {
        return graph.getRelationships().stream().anyMatch(r ->
                r.type() == RelationshipType.CALLS_EXTERNAL
                        && r.sourceId().equals(callerId)
                        && r.targetId().equals("extapi:" + apiName));
    }

    @Test
    void javaRestTemplateWebClientAndFeignWithConfigResolution() throws IOException {
        Path pkg = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkg);
        Path res = tempDir.resolve("src/main/resources");
        Files.createDirectories(res);
        Files.writeString(res.resolve("application.yml"), """
                payments:
                  base-url: https://pay.example.com/v1
                inventory:
                  url: http://inventory.internal:8080
                """);
        Files.writeString(pkg.resolve("PaymentClient.java"), """
                package com.example;

                import org.springframework.beans.factory.annotation.Value;
                import org.springframework.web.client.RestTemplate;

                public class PaymentClient {
                    @Value("${payments.base-url}")
                    private String baseUrl;
                    private final RestTemplate restTemplate;

                    public PaymentClient(RestTemplate restTemplate) { this.restTemplate = restTemplate; }

                    public String charge(String id) {
                        return restTemplate.postForObject(baseUrl + "/charges/" + id, null, String.class);
                    }

                    public String status(String id) {
                        return restTemplate.getForObject("https://status.example.org/api/" + id, String.class);
                    }
                }
                """);
        Files.writeString(pkg.resolve("StockClient.java"), """
                package com.example;

                import org.springframework.web.reactive.function.client.WebClient;

                public class StockClient {
                    private final WebClient webClient = WebClient.create("${inventory.url}");

                    public Object stock(String sku) {
                        return webClient.get().uri("/stock/{sku}", sku).retrieve().bodyToMono(Object.class).block();
                    }
                }
                """);
        Files.writeString(pkg.resolve("TaxApi.java"), """
                package com.example;

                import org.springframework.cloud.openfeign.FeignClient;
                import org.springframework.web.bind.annotation.GetMapping;
                import org.springframework.web.bind.annotation.PathVariable;

                @FeignClient(name = "tax-service", url = "https://tax.example.com", path = "/api")
                public interface TaxApi {
                    @GetMapping("/rates/{country}")
                    Object rate(@PathVariable("country") String country);
                }
                """);

        CodeGraph graph = new JavaSourceParser(tempDir).parseDirectory(tempDir);

        Set<String> apis = externalApis(graph);
        assertTrue(apis.contains("POST https://pay.example.com/v1/charges/{id}"), apis.toString());
        assertTrue(apis.contains("GET https://status.example.org/api/{id}"), apis.toString());
        assertTrue(apis.contains("GET http://inventory.internal:8080/stock/{sku}"), apis.toString());
        assertTrue(apis.contains("GET https://tax.example.com/api/rates/{country}"), apis.toString());

        assertTrue(hasExternalEdge(graph, "method:com.example.PaymentClient.charge(String)",
                "POST https://pay.example.com/v1/charges/{id}"));
        assertTrue(hasExternalEdge(graph, "method:com.example.TaxApi.rate(String)",
                "GET https://tax.example.com/api/rates/{country}"));

        CodeNode api = graph.getNode("extapi:GET https://tax.example.com/api/rates/{country}");
        assertEquals("tax.example.com", api.properties().get("host"));
        assertEquals("OpenFeign", api.properties().get("client"));
    }

    @Test
    void javaIgnoresNonHttpClientsWithSameMethodNames() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);
        Files.writeString(pkg.resolve("Cache.java"), """
                package com.example;

                import java.util.HashMap;
                import java.util.Map;

                public class Cache {
                    private final Map<String, String> store = new HashMap<>();

                    public void evict(String key) {
                        store.put(key, "x");
                        store.remove(key);
                    }
                }
                """);

        CodeGraph graph = new JavaSourceParser(tempDir).parseDirectory(tempDir);

        assertTrue(externalApis(graph).isEmpty());
    }

    @Test
    void javaDetectsJdkHttpClientAndOkHttp() throws IOException {
        Path pkg = tempDir.resolve("com/example");
        Files.createDirectories(pkg);
        Files.writeString(pkg.resolve("Raw.java"), """
                package com.example;

                import java.net.URI;
                import java.net.http.HttpRequest;

                public class Raw {
                    public void jdk() {
                        HttpRequest req = HttpRequest.newBuilder()
                                .uri(URI.create("https://jdk.example.com/ping"))
                                .POST(null)
                                .build();
                    }

                    public void ok() {
                        okhttp3.Request r = new okhttp3.Request.Builder()
                                .url("https://ok.example.com/data")
                                .delete()
                                .build();
                    }
                }
                """);

        CodeGraph graph = new JavaSourceParser(tempDir).parseDirectory(tempDir);

        Set<String> apis = externalApis(graph);
        assertTrue(apis.contains("POST https://jdk.example.com/ping"), apis.toString());
        assertTrue(apis.contains("DELETE https://ok.example.com/data"), apis.toString());
    }

    @Test
    void resolvesConfigWhenParserRootIsSrcMainJava() throws IOException {
        Path src = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(src);
        Path res = tempDir.resolve("src/main/resources");
        Files.createDirectories(res);
        Files.writeString(res.resolve("application.yml"), "payments:\n  base-url: https://pay.example.com/v1\n");
        Files.writeString(src.resolve("Pay.java"), """
                package com.example;

                import org.springframework.beans.factory.annotation.Value;
                import org.springframework.web.client.RestTemplate;

                public class Pay {
                    @Value("${payments.base-url}")
                    private String baseUrl;
                    private final RestTemplate rt = new RestTemplate();

                    public String charge(String id) {
                        return rt.getForObject(baseUrl + "/charges/" + id, String.class);
                    }
                }
                """);

        Path sourceRoot = tempDir.resolve("src/main/java");
        CodeGraph graph = new JavaSourceParser(sourceRoot).parseDirectory(sourceRoot);

        Set<String> apis = externalApis(graph);
        assertTrue(apis.contains("GET https://pay.example.com/v1/charges/{id}"), apis.toString());
    }
}
