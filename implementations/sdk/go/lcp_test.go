package lcp

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSharedHMACVector(t *testing.T) {
	body := []byte(`{"hello":"world"}`)
	timestamp := "2026-08-15T10:20:00Z"
	key := "sdk-vector-001"
	signature := SignHMAC("sdk-shared-secret", timestamp, key, body)
	want := "50f90d29b46e92f257eae62c94a3d985bf9a925da03fee82e33c800fe54e7259"
	if signature != want {
		t.Fatalf("signature = %s, want %s", signature, want)
	}
	if err := VerifyHMAC("sdk-shared-secret", signature, timestamp, key, body, 5*time.Minute, time.Date(2026, 8, 15, 10, 20, 1, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
}

func TestFullSchemaValidation(t *testing.T) {
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
			raw, readErr := os.ReadFile(filepath.Join(root, directory, entry.Name()))
			if readErr != nil {
				t.Fatal(readErr)
			}
			schemas[directory+"/"+entry.Name()] = raw
		}
	}
	raw, err := os.ReadFile(filepath.Join(root, "examples", "lead.json"))
	if err != nil {
		t.Fatal(err)
	}
	var envelope map[string]any
	if err := json.Unmarshal(raw, &envelope); err != nil {
		t.Fatal(err)
	}
	if err := ValidateEnvelopeBundle(schemas, raw); err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(envelope["lcp"].(map[string]any)["payload"])
	if err := ValidateSchemaBundle("schemas/lead.json", schemas, payload); err != nil {
		t.Fatal(err)
	}
}

func TestVerifyHTTPHeadersRequiresRawAuthentication(t *testing.T) {
	headers := http.Header{}
	headers.Set("X-LCP-Signature", SignHMAC("secret", "2026-08-15T10:20:00Z", "key", []byte(`{"hello":"world"}`)))
	headers.Set("X-LCP-Timestamp", "2026-08-15T10:20:00Z")
	headers.Set("X-LCP-Idempotency-Key", "key")
	if err := VerifyHTTPHeaders("secret", headers, []byte(`{"hello":"world"}`), 5*time.Minute, time.Date(2026, 8, 15, 10, 20, 1, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
	if err := VerifyHTTPHeaders("secret", headers, []byte(`{"hello":"tampered"}`), 5*time.Minute, time.Date(2026, 8, 15, 10, 20, 1, 0, time.UTC)); err == nil {
		t.Fatal("tampered body was accepted")
	}
}
