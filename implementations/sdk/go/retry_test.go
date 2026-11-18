package lcp

import (
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

// Retry parity with the Python SDK: 429/500/502/503/504 retry up to
// MaxRetries (default 2), a delta-seconds Retry-After sets the wait, other
// statuses fail at once, and a batch is never retried.
func retryServer(t *testing.T, statuses ...int) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	hits := &atomic.Int32{}
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		index := int(hits.Add(1)) - 1
		status := statuses[min(index, len(statuses)-1)]
		writer.Header().Set("Content-Type", "application/json")
		writer.Header().Set("Retry-After", "0")
		writer.WriteHeader(status)
		_, _ = writer.Write([]byte(`{"ok":true}`))
	}))
	t.Cleanup(server.Close)
	return server, hits
}

func TestRetryHonoursRetryAfter(t *testing.T) {
	server, hits := retryServer(t, 429, 503, 200)
	started := time.Now()
	if _, err := NewClient(server.URL).GetCapabilities(); err != nil {
		t.Fatal(err)
	}
	if hits.Load() != 3 {
		t.Fatalf("hits = %d, want 3", hits.Load())
	}
	if elapsed := time.Since(started); elapsed > 900*time.Millisecond {
		t.Fatalf("Retry-After: 0 must replace the 1 s / 2 s backoff, took %s", elapsed)
	}
}

func TestRetryStopsAtMaxRetriesAndSkipsNonRetryable(t *testing.T) {
	server, hits := retryServer(t, 503)
	client := NewClient(server.URL)
	client.MaxRetries = 1
	if _, err := client.GetCapabilities(); err == nil || err.(*HTTPError).Status != 503 {
		t.Fatalf("err = %v, want HTTP 503", err)
	}
	if hits.Load() != 2 {
		t.Fatalf("hits = %d, want 2", hits.Load())
	}

	server, hits = retryServer(t, 400)
	if _, err := NewClient(server.URL).GetCapabilities(); err == nil {
		t.Fatal("400 was accepted")
	}
	if hits.Load() != 1 {
		t.Fatalf("hits = %d, want 1 for a non-retryable status", hits.Load())
	}

	server, hits = retryServer(t, 503)
	if _, err := NewClient(server.URL).SubmitLeadsBatch([]BatchItem{{ItemID: "a", IdempotencyKey: "k", Payload: map[string]any{}}}, ""); err == nil {
		t.Fatal("503 batch was accepted")
	}
	if hits.Load() != 1 {
		t.Fatalf("hits = %d, want 1: a batch is never retried as a whole", hits.Load())
	}
}

func TestRetryDelayPolicy(t *testing.T) {
	for _, tc := range []struct {
		retryAfter string
		attempt    int
		want       time.Duration
	}{
		{"0", 1, 0},
		{"7", 0, 7 * time.Second},
		{"", 0, time.Second},
		{"Wed, 21 Oct 2026 07:28:00 GMT", 1, 2 * time.Second},
		{"1.5", 2, 4 * time.Second},
		{"99999999999999999999", 0, time.Second},
	} {
		if got := retryDelay(tc.retryAfter, tc.attempt); got != tc.want {
			t.Errorf("retryDelay(%q, %d) = %s, want %s", tc.retryAfter, tc.attempt, got, tc.want)
		}
	}
}
