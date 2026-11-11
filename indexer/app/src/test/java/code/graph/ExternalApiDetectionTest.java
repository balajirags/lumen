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

    private Object edgeProp(CodeGraph graph, String apiName, String key) {
        return graph.getRelationships().stream()
                .filter(r -> r.type() == RelationshipType.CALLS_EXTERNAL && r.targetId().equals("extapi:" + apiName))
                .map(r -> r.properties().get(key))
                .filter(java.util.Objects::nonNull)
                .findFirst().orElse(null);
    }

    @Test
    void javaCapturesRequestAndResponseShapes() throws IOException {
        Path pkg = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkg);
        Files.writeString(pkg.resolve("Clients.java"), """
                package com.example;

                import java.util.List;
                import org.springframework.core.ParameterizedTypeReference;
                import org.springframework.http.ResponseEntity;
                import org.springframework.web.client.RestClient;
                import org.springframework.web.client.RestTemplate;
                import org.springframework.web.reactive.function.client.WebClient;

                public class Clients {
                    private final RestTemplate rt = new RestTemplate();
                    private final WebClient web = WebClient.create("http://customers");

                    public Owner create(OwnerRequest req) {
                        return rt.postForObject("http://owners/api/owners", req, Owner.class);
                    }

                    public Owner owner(int id) {
                        return web.get().uri("/owners/{id}", id).header("X-Trace", "1").retrieve().bodyToMono(Owner.class).block();
                    }

}
                """);
        Files.writeString(pkg.resolve("AiClient.java"), """
                package com.example;

                import java.util.List;
                import org.springframework.core.ParameterizedTypeReference;
                import org.springframework.web.client.RestClient;

                public class AiClient {
                    private final RestClient rest = RestClient.create("http://ai");

                    public List<Vet> vets() {
                        return rest.get().uri(b -> b.path("/vets").queryParam("active", true).build())
                                .retrieve().body(new ParameterizedTypeReference<>() {});
                    }

                    public void save(PetRequest pet) {
                        rest.post().uri("/pets").contentType(null).body(pet).retrieve().toBodilessEntity();
                    }
                }
                """);
        Files.writeString(pkg.resolve("TaxApi.java"), """
                package com.example;

                import org.springframework.cloud.openfeign.FeignClient;
                import org.springframework.http.ResponseEntity;
                import org.springframework.web.bind.annotation.*;

                @FeignClient(name = "tax", url = "http://tax")
                public interface TaxApi {
                    @PostMapping("/rates/{country}")
                    ResponseEntity<Rate> rate(@PathVariable("country") String c, @RequestParam("year") int y,
                                              @RequestHeader("X-Key") String k, @RequestBody RateRequest body);
                }
                """);

        CodeGraph graph = new JavaSourceParser(tempDir).parseDirectory(tempDir);

        assertEquals("OwnerRequest", edgeProp(graph, "POST http://owners/api/owners", "requestType"));
        assertEquals("Owner", edgeProp(graph, "POST http://owners/api/owners", "responseType"));
        assertEquals("Owner", edgeProp(graph, "GET http://customers/owners/{id}", "responseType"));
        assertEquals("X-Trace", edgeProp(graph, "GET http://customers/owners/{id}", "requestHeaders"));
        assertEquals("List<Vet>", edgeProp(graph, "GET http://ai/vets", "responseType"));
        assertEquals("query:active", edgeProp(graph, "GET http://ai/vets", "params"));
        assertEquals("PetRequest", edgeProp(graph, "POST http://ai/pets", "requestType"));
        assertNull(edgeProp(graph, "POST http://ai/pets", "responseType"));
        assertEquals("Rate", edgeProp(graph, "POST http://tax/rates/{country}", "responseType"));
        assertEquals("RateRequest", edgeProp(graph, "POST http://tax/rates/{country}", "requestType"));
        assertEquals("path:country,query:year", edgeProp(graph, "POST http://tax/rates/{country}", "params"));
        assertEquals("X-Key", edgeProp(graph, "POST http://tax/rates/{country}", "requestHeaders"));
    }
}
