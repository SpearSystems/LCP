// Command interop_probe emits the Go SDK's row for
// tools/sdk_interop_matrix.py (LEP-0002 rollout step 1).
//
// It signs the fixed hmac.json vector message through the SDK's own
// CanonicalSigningBytes + SignHMAC, then formats the SDK's timestamp
// generation expression (lcp.go: time.Now().UTC().Format(time.RFC3339)) at a
// frozen instant with non-zero nanoseconds. Go's Format appends the minimal
// fractional digits when the layout carries none, so this reproduces exactly
// what a request signed at a sub-second clock tick emits — the LEP-0002
// evidence. The probe never asserts; the matrix owns the checks.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	lcp "github.com/SpearSystems/LCP/implementations/sdk/go"
)

type vector struct {
	ID             string `json:"id"`
	Secret         string `json:"secret"`
	Timestamp      string `json:"timestamp"`
	IdempotencyKey string `json:"idempotency_key"`
	BodyUTF8       string `json:"body_utf8"`
}

func vectorPath() string {
	args := os.Args
	for i := 0; i+1 < len(args); i++ {
		if args[i] == "--vector" {
			return args[i+1]
		}
	}
	if fromEnv := os.Getenv("LCP_VECTOR"); fromEnv != "" {
		return fromEnv
	}
	directory, _ := os.Getwd()
	for {
		candidate := filepath.Join(directory, "test-vectors", "sdk", "hmac.json")
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
		parent := filepath.Dir(directory)
		if parent == directory {
			fmt.Fprintln(os.Stderr, "hmac.json vector not found (pass --vector)")
			os.Exit(2)
		}
		directory = parent
	}
}

func main() {
	raw, err := os.ReadFile(vectorPath())
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	var message vector
	if err := json.Unmarshal(raw, &message); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	body := []byte(message.BodyUTF8)
	canonical := lcp.CanonicalSigningBytes(message.Timestamp, message.IdempotencyKey, body)
	digest := sha256.Sum256(canonical)
	// Frozen instant 2026-08-15T10:20:00.123456789Z through the SDK's own
	// generation expression (lcp.go:164); Go offers no clock seam to freeze
	// time.Now itself, so the format call is mirrored exactly.
	emitted := time.Date(2026, 8, 15, 10, 20, 0, 123456789, time.UTC).Format(time.RFC3339)
	result := struct {
		SDK              string `json:"sdk"`
		CanonicalSHA256  string `json:"canonical_sha256"`
		SignatureHex     string `json:"signature_hex"`
		EmittedTimestamp string `json:"emitted_timestamp"`
	}{
		SDK:              "go",
		CanonicalSHA256:  hex.EncodeToString(digest[:]),
		SignatureHex:     lcp.SignHMAC(message.Secret, message.Timestamp, message.IdempotencyKey, body),
		EmittedTimestamp: emitted,
	}
	payload, _ := json.Marshal(result)
	fmt.Println("LCP_INTEROP", string(payload))
	if out := os.Getenv("LCP_INTEROP_OUT"); out != "" {
		os.WriteFile(out, append(payload, '\n'), 0o644)
	}
}
