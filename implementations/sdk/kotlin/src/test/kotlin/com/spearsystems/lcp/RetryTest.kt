package com.spearsystems.lcp

import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.net.http.HttpTimeoutException
import java.time.Duration
import java.util.concurrent.atomic.AtomicInteger
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * Retry parity with the Python SDK: 429/500/502/503/504 retry up to
 * `maxRetries` (default 2), a delta-seconds `Retry-After` sets the wait, other
 * statuses fail at once, a batch is never retried, and the timeout bounds each
 * attempt.
 */
class RetryTest {
    private val hits = AtomicInteger()
    private var server: HttpServer? = null

    private fun serve(statuses: List<Int>, delayMillis: Long = 0): String {
        val created = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        created.createContext("/") { exchange ->
            val status = statuses[minOf(hits.getAndIncrement(), statuses.size - 1)]
            Thread.sleep(delayMillis)
            val body = (if (status == 200) "{\"results\":[],\"ok\":true}" else "{\"errors\":[{\"code\":\"LCP-011\"}]}").toByteArray()
            exchange.responseHeaders.add("Content-Type", "application/json")
            exchange.responseHeaders.add("Retry-After", "0")
            exchange.sendResponseHeaders(status, body.size.toLong())
            exchange.responseBody.write(body)
            exchange.close()
        }
        created.start()
        server = created
        return "http://127.0.0.1:${created.address.port}"
    }

    @AfterTest
    fun stop() { server?.stop(0) }

    @Test
    fun retriesRetryableStatusesHonouringRetryAfter() {
        val client = LcpSdk.Client(serve(listOf(429, 503, 200)))
        val started = System.nanoTime()
        assertEquals("{\"results\":[],\"ok\":true}", client.getCapabilities())
        assertEquals(3, hits.get())
        assertTrue(Duration.ofNanos(System.nanoTime() - started).toMillis() < 900, "Retry-After: 0 must replace the 1 s / 2 s backoff")
    }

    @Test
    fun retriesStopAtMaxRetries() {
        val client = LcpSdk.Client(serve(listOf(503)), maxRetries = 1)
        val error = assertFailsWith<IllegalArgumentException> { client.getCapabilities() }
        assertTrue(error.message!!.startsWith("LCP HTTP 503"))
        assertEquals(2, hits.get())
    }

    @Test
    fun nonRetryableStatusIsSentOnce() {
        val client = LcpSdk.Client(serve(listOf(400)))
        assertFailsWith<IllegalArgumentException> { client.getCapabilities() }
        assertEquals(1, hits.get())
    }

    @Test
    fun batchIsNeverRetried() {
        val client = LcpSdk.Client(serve(listOf(503)))
        assertFailsWith<IllegalArgumentException> { client.submitLeadsBatch(listOf(BatchItem("a", "k", "{}"))) }
        assertEquals(1, hits.get())
    }

    @Test
    fun timeoutBoundsEachAttempt() {
        val client = LcpSdk.Client(serve(listOf(200), delayMillis = 2_000), timeout = Duration.ofMillis(200), maxRetries = 0)
        assertFailsWith<HttpTimeoutException> { client.getCapabilities() }
        assertEquals(1, hits.get())
    }

    @Test
    fun retryDelayPolicy() {
        assertEquals(0, LcpSdk.Client.retryDelayMillis("0", 1))
        assertEquals(7_000, LcpSdk.Client.retryDelayMillis("7", 0))
        assertEquals(1_000, LcpSdk.Client.retryDelayMillis(null, 0))
        assertEquals(2_000, LcpSdk.Client.retryDelayMillis("Wed, 21 Oct 2026 07:28:00 GMT", 1))
        assertEquals(4_000, LcpSdk.Client.retryDelayMillis("1.5", 2))
        assertEquals(1_000, LcpSdk.Client.retryDelayMillis("99999999999999999999", 0))
    }
}
