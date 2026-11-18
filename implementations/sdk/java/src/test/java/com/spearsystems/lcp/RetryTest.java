package com.spearsystems.lcp;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;

import java.net.InetSocketAddress;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

/**
 * Retry parity with the Python SDK: 429/500/502/503/504 retry up to
 * {@code maxRetries} (default 2), a delta-seconds {@code Retry-After} sets the
 * wait, other statuses fail at once, a batch is never retried, and the timeout
 * bounds each attempt.
 */
final class RetryTest {
    private final AtomicInteger hits = new AtomicInteger();
    private HttpServer server;

    private String serve(List<Integer> statuses, long delayMillis) throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            int status = statuses.get(Math.min(hits.getAndIncrement(), statuses.size() - 1));
            try { Thread.sleep(delayMillis); } catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
            var body = (status == 200 ? "{\"results\":[],\"ok\":true}" : "{\"errors\":[{\"code\":\"LCP-011\"}]}").getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.getResponseHeaders().add("Retry-After", "0");
            exchange.sendResponseHeaders(status, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.start();
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @AfterEach
    void stop() { if (server != null) server.stop(0); }

    @Test
    void retriesRetryableStatusesHonouringRetryAfter() throws Exception {
        var client = new LcpSdk.Client(serve(List.of(429, 503, 200), 0), null, null, null);
        long started = System.nanoTime();
        assertEquals("{\"results\":[],\"ok\":true}", client.getCapabilities());
        assertEquals(3, hits.get());
        assertTrue(Duration.ofNanos(System.nanoTime() - started).toMillis() < 900, "Retry-After: 0 must replace the 1 s / 2 s backoff");
    }

    @Test
    void retriesStopAtMaxRetries() throws Exception {
        var client = new LcpSdk.Client(serve(List.of(503), 0), null, null, null, Duration.ofSeconds(30), 1);
        var error = assertThrows(IllegalStateException.class, client::getCapabilities);
        assertTrue(error.getMessage().startsWith("LCP HTTP 503"));
        assertEquals(2, hits.get());
    }

    @Test
    void nonRetryableStatusIsSentOnce() throws Exception {
        var client = new LcpSdk.Client(serve(List.of(400), 0), null, null, null);
        assertThrows(IllegalStateException.class, client::getCapabilities);
        assertEquals(1, hits.get());
    }

    @Test
    void batchIsNeverRetried() throws Exception {
        var client = new LcpSdk.Client(serve(List.of(503), 0), null, null, null);
        assertThrows(IllegalStateException.class, () -> client.submitLeadsBatch(List.of(new LcpSdk.Client.BatchItem("a", "k", "{}"))));
        assertEquals(1, hits.get());
    }

    @Test
    void timeoutBoundsEachAttempt() throws Exception {
        var client = new LcpSdk.Client(serve(List.of(200), 2_000), null, null, null, Duration.ofMillis(200), 0);
        assertThrows(HttpTimeoutException.class, client::getCapabilities);
        assertEquals(1, hits.get());
    }

    @Test
    void retryDelayPolicy() {
        assertEquals(0, LcpSdk.Client.retryDelayMillis("0", 1));
        assertEquals(7_000, LcpSdk.Client.retryDelayMillis("7", 0));
        assertEquals(1_000, LcpSdk.Client.retryDelayMillis(null, 0));
        assertEquals(2_000, LcpSdk.Client.retryDelayMillis("Wed, 21 Oct 2026 07:28:00 GMT", 1));
        assertEquals(4_000, LcpSdk.Client.retryDelayMillis("1.5", 2));
        assertEquals(1_000, LcpSdk.Client.retryDelayMillis("99999999999999999999", 0));
    }
}
