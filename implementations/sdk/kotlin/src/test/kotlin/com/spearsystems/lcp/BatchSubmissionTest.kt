package com.spearsystems.lcp

import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.util.concurrent.atomic.AtomicReference
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import tools.jackson.databind.ObjectMapper

/**
 * LEP-0001 W2-01: batch submission sends the per-item wire shape, treats HTTP
 * 207 as a successful response, and returns receipts in input order. A batch
 * above the 100-item cap is refused before any request leaves the client.
 */
class BatchSubmissionTest {
    private val mapper = ObjectMapper()

    @Test
    fun batchTreats207AsSuccessAndOrdersReceipts() {
        val received = AtomicReference<String>()
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/v1/lcp/leads/batch") { exchange ->
            received.set(exchange.requestBody.readAllBytes().toString(Charsets.UTF_8))
            val body = "{\"request_id\":\"req-batch-1\",\"summary\":{\"total\":2,\"accepted\":1," +
                "\"duplicate\":0,\"rejected\":1,\"error\":0},\"results\":[" +
                "{\"item_id\":\"item_b\",\"status\":\"accepted\",\"lead_id\":\"lead_b\"}," +
                "{\"item_id\":\"item_a\",\"status\":\"rejected\",\"error\":{\"code\":\"LCP-100\",\"message\":\"invalid lead\"}}]}"
            val bytes = body.toByteArray()
            exchange.responseHeaders.add("Content-Type", "application/json")
            exchange.sendResponseHeaders(207, bytes.size.toLong())
            exchange.responseBody.write(bytes)
            exchange.close()
        }
        server.start()
        try {
            val client = LcpSdk.Client("http://127.0.0.1:${server.address.port}")
            val results = client.submitLeadsBatch(
                listOf(
                    BatchItem("item_a", "batch-a", "{\"lead_id\":\"lead_a\"}"),
                    BatchItem("item_b", "batch-b", "{\"lead_id\":\"lead_b\"}"),
                ),
                "req-batch-1",
            )
            assertEquals(listOf("item_a", "item_b"), results.map { it.path("item_id").asText() })
            assertEquals("rejected", results[0].path("status").asText())
            assertEquals("LCP-100", results[0].path("error").path("code").asText())
            assertEquals("accepted", results[1].path("status").asText())
            val sent = mapper.readTree(received.get())
            assertEquals("req-batch-1", sent.path("request_id").asText())
            assertEquals("item_a", sent.path("items").get(0).path("item_id").asText())
            assertEquals("lead_a", sent.path("items").get(0).path("payload").path("lead_id").asText())
        } finally {
            server.stop(0)
        }
    }

    @Test
    fun batchRejectsOverCapBeforeSending() {
        val client = LcpSdk.Client("http://127.0.0.1:1")
        val items = (0 until 101).map { BatchItem("item_$it", "key_$it", "{}") }
        assertFailsWith<IllegalArgumentException> { client.submitLeadsBatch(items, "req-batch-1") }
    }
}
