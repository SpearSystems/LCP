package com.spearsystems.lcp;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.sun.net.httpserver.HttpServer;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.IntStream;

import org.junit.jupiter.api.Test;

import tools.jackson.databind.ObjectMapper;

/**
 * LEP-0001 W2-01: batch submission sends the per-item wire shape, treats HTTP
 * 207 as a successful response, and returns receipts in input order. A batch
 * above the 100-item cap is refused before any request leaves the client.
 */
final class BatchSubmissionTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Test
    void batchTreats207AsSuccessAndOrdersReceipts() throws Exception {
        var received = new AtomicReference<String>();
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v1/lcp/leads/batch", exchange -> {
            received.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            var body = ("{\"request_id\":\"req-batch-1\",\"summary\":{\"total\":2,\"accepted\":1,"
                    + "\"duplicate\":0,\"rejected\":1,\"error\":0},\"results\":["
                    + "{\"item_id\":\"item_b\",\"status\":\"accepted\",\"lead_id\":\"lead_b\"},"
                    + "{\"item_id\":\"item_a\",\"status\":\"rejected\",\"error\":{\"code\":\"LCP-100\",\"message\":\"invalid lead\"}}]}")
                    .getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(207, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.start();
        try {
            var client = new LcpSdk.Client("http://127.0.0.1:" + server.getAddress().getPort(), null, null, null);
            var results = client.submitLeadsBatch(List.of(
                    new LcpSdk.Client.BatchItem("item_a", "batch-a", "{\"lead_id\":\"lead_a\"}"),
                    new LcpSdk.Client.BatchItem("item_b", "batch-b", "{\"lead_id\":\"lead_b\"}")), "req-batch-1");
            assertEquals(List.of("item_a", "item_b"), results.stream().map(node -> node.path("item_id").asText()).toList());
            assertEquals("rejected", results.get(0).path("status").asText());
            assertEquals("LCP-100", results.get(0).path("error").path("code").asText());
            assertEquals("accepted", results.get(1).path("status").asText());
            var sent = MAPPER.readTree(received.get());
            assertEquals("req-batch-1", sent.path("request_id").asText());
            assertEquals("item_a", sent.path("items").get(0).path("item_id").asText());
            assertEquals("lead_a", sent.path("items").get(0).path("payload").path("lead_id").asText());
        } finally {
            server.stop(0);
        }
    }

    @Test
    void batchRejectsOverCapBeforeSending() {
        var client = new LcpSdk.Client("http://127.0.0.1:1", null, null, null);
        var items = IntStream.range(0, 101)
                .mapToObj(index -> new LcpSdk.Client.BatchItem("item_" + index, "key_" + index, "{}"))
                .toList();
        assertThrows(IllegalArgumentException.class, () -> client.submitLeadsBatch(items, "req-batch-1"));
    }
}
