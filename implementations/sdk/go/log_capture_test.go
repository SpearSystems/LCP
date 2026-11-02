// Runtime log-cleanliness tests (SDK-ROADMAP invariant 7, dynamic half).
//
// tools/check_logging_hygiene.py does not scan Go, so this file carries the
// whole invariant for this SDK: the lcp package must never write to
// os.Stdout/os.Stderr — including an application logging the SDK's own error
// strings, which is the realistic leak route (fmt.Println(err) is framework
// default behaviour) — PII from the realistic post fixture: consumer name,
// phone digits, email localpart, or the serialized envelope. Deterministic
// and network-free: fixed fixture timestamps, injected now, no HTTP.
package lcp

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	captureSecret = "log-capture-shared-secret"
)

// captureStreams runs action with os.Stdout/os.Stderr redirected to pipes
// and returns everything written to either.
func captureStreams(t *testing.T, action func()) string {
	t.Helper()
	outR, outW, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe(stdout): %v", err)
	}
	errR, errW, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe(stderr): %v", err)
	}
	originalOut, originalErr := os.Stdout, os.Stderr
	os.Stdout, os.Stderr = outW, errW
	t.Cleanup(func() { os.Stdout, os.Stderr = originalOut, originalErr })

	var collected bytes.Buffer
	var reads sync.WaitGroup
	reads.Add(2)
	go func() { defer reads.Done(); _, _ = io.Copy(&collected, outR) }()
	go func() { defer reads.Done(); _, _ = io.Copy(&collected, errR) }()

	action()

	os.Stdout, os.Stderr = originalOut, originalErr
	_ = outW.Close()
	_ = errW.Close()
	reads.Wait()
	return collected.String()
}

// assertClean fails if the capture contains any consumer identifier or the
// serialized envelope.
func assertClean(t *testing.T, output, label string, forbidden []string) {
	t.Helper()
	for _, marker := range forbidden {
		if strings.Contains(output, marker) {
			t.Fatalf("%s: forbidden PII fragment %q appeared in captured output: %s", label, marker, output)
		}
	}
}

func postFixture(t *testing.T) (raw []byte, envelope map[string]any) {
	t.Helper()
	root := filepath.Join("..", "..", "..")
	raw, err := os.ReadFile(filepath.Join(root, "examples", "post.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		t.Fatal(err)
	}
	return raw, envelope
}

// forbiddenFragments builds the marker list from the fixture itself: the
// consumer identifiers, both serializations of the envelope, and the raw file
// bytes.
func forbiddenFragments(t *testing.T, raw []byte, envelope map[string]any) []string {
	t.Helper()
	remarshaled, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}
	return []string{
		"Jane", "Smith", "Jane Smith",
		"+61412345678", "412345678", "jane.smith", "Sydney",
		string(raw), string(remarshaled),
	}
}

func TestHappyPathSignAndVerifyIsSilent(t *testing.T) {
	raw, envelope := postFixture(t)
	message := envelope["lcp"].(map[string]any)["message"].(map[string]any)
	timestamp, _ := message["timestamp"].(string)
	idempotencyKey, _ := message["idempotency_key"].(string)

	output := captureStreams(t, func() {
		signature := SignHMAC(captureSecret, timestamp, idempotencyKey, raw)
		now := time.Date(2026, 8, 15, 10, 20, 40, 0, time.UTC)
		if err := VerifyHMAC(captureSecret, signature, timestamp, idempotencyKey, raw, 5*time.Minute, now); err != nil {
			t.Errorf("sign/verify round trip failed: %v", err)
		}
	})
	if output != "" {
		t.Fatalf("SDK wrote %d bytes to stdout/stderr on the sign+verify happy path: %q", len(output), output)
	}
}

func TestMalformedEnvelopeErrorPathStaysClean(t *testing.T) {
	raw, envelope := postFixture(t)
	forbidden := forbiddenFragments(t, raw, envelope)

	root := filepath.Join("..", "..", "..")
	schemas := map[string][]byte{}
	for _, directory := range []string{"schemas", "verticals"} {
		entries, err := os.ReadDir(filepath.Join(root, directory))
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			if filepath.Ext(entry.Name()) != ".json" {
				continue
			}
			content, readErr := os.ReadFile(filepath.Join(root, directory, entry.Name()))
			if readErr != nil {
				t.Fatal(readErr)
			}
			schemas[directory+"/"+entry.Name()] = content
		}
	}

	broken := deepCopyMap(t, envelope)
	delete(broken["lcp"].(map[string]any)["payload"].(map[string]any), "delivered_at")
	brokenBody, err := json.Marshal(broken)
	if err != nil {
		t.Fatal(err)
	}

	output := captureStreams(t, func() {
		validationErr := ValidateEnvelopeBundle(schemas, brokenBody)
		if validationErr == nil {
			t.Error("malformed post envelope must fail schema validation")
			return
		}
		if !strings.Contains(validationErr.Error(), "delivered_at") {
			t.Errorf("validation error must name the missing property, got: %v", validationErr)
		}
		// Simulate the framework default: the application logs the SDK error.
		fmt.Fprintf(os.Stderr, "validation failed: %v\n", validationErr)
	})
	assertClean(t, output, "malformed-envelope path", forbidden)
}

func TestWebhookVerifyFailurePathsStayClean(t *testing.T) {
	raw, envelope := postFixture(t)
	forbidden := forbiddenFragments(t, raw, envelope)
	message := envelope["lcp"].(map[string]any)["message"].(map[string]any)
	idempotencyKey, _ := message["idempotency_key"].(string)
	now := time.Date(2026, 8, 15, 10, 20, 40, 0, time.UTC)

	output := captureStreams(t, func() {
		// (a) Tampered signature over the PII body → rejected.
		tampered := http.Header{}
		tampered.Set("X-LCP-Signature", strings.Repeat("0", 64))
		tampered.Set("X-LCP-Timestamp", now.UTC().Format(time.RFC3339))
		tampered.Set("X-LCP-Idempotency-Key", idempotencyKey)
		tamperedErr := VerifyHTTPHeaders(captureSecret, tampered, raw, 5*time.Minute, now)
		if tamperedErr == nil || !strings.Contains(tamperedErr.Error(), "invalid X-LCP-Signature") {
			t.Errorf("tampered webhook signature must be rejected, got %v", tamperedErr)
			return
		}
		// (b) Missing headers → rejected.
		missing := http.Header{}
		missing.Set("X-LCP-Timestamp", now.UTC().Format(time.RFC3339))
		missingErr := VerifyHTTPHeaders(captureSecret, missing, raw, 5*time.Minute, now)
		if missingErr == nil || !strings.Contains(missingErr.Error(), "missing required LCP authentication headers") {
			t.Errorf("webhook without headers must be rejected, got %v", missingErr)
			return
		}
		// Simulate the framework default: the application logs SDK errors.
		fmt.Fprintf(os.Stderr, "webhook rejected: %v; %v\n", tamperedErr, missingErr)
	})
	assertClean(t, output, "webhook-failure path", forbidden)
}

func deepCopyMap(t *testing.T, value map[string]any) map[string]any {
	t.Helper()
	body, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var copy map[string]any
	if err := json.Unmarshal(body, &copy); err != nil {
		t.Fatal(err)
	}
	return copy
}
