package lcp

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
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

func TestSharedValidationCorpus(t *testing.T) {
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
	corpusRaw, err := os.ReadFile(filepath.Join(root, "test-vectors", "sdk", "validation-corpus.json"))
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Fixtures []struct {
			ID       string         `json:"id"`
			Rule     string         `json:"rule"`
			Expect   string         `json:"expect"`
			Envelope map[string]any `json:"envelope"`
			Offer    map[string]any `json:"offer"`
		} `json:"fixtures"`
	}
	if err := json.Unmarshal(corpusRaw, &corpus); err != nil {
		t.Fatal(err)
	}
	for _, fixture := range corpus.Fixtures {
		isOffer := fixture.Envelope == nil
		document := fixture.Envelope
		if isOffer {
			document = fixture.Offer
		}
		raw, err := json.Marshal(document)
		if err != nil {
			t.Fatal(err)
		}
		var validateErr error
		if isOffer {
			validateErr = ValidateOffer(schemas, raw)
		} else {
			validateErr = ValidateEnvelopeBundle(schemas, raw)
		}
		passed := validateErr == nil
		expected := fixture.Expect == "pass"
		if passed != expected {
			t.Errorf("fixture %s (%s): expected %s, got %v", fixture.ID, fixture.Rule, fixture.Expect, validateErr)
		}
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

func TestSubmitLeadsBatchOrdersReceiptsAndTreats207AsSuccess(t *testing.T) {
	received := make(chan map[string]any, 1)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/v1/lcp/leads/batch" {
			t.Errorf("path = %s", request.URL.Path)
		}
		var body map[string]any
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		received <- body
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusMultiStatus)
		_, _ = writer.Write([]byte(`{"request_id":"req-batch-1","summary":{"total":2,"accepted":1,"duplicate":0,"rejected":1,"error":0},"results":[{"item_id":"item_b","status":"accepted","lead_id":"lead_b"},{"item_id":"item_a","status":"rejected","error":{"code":"LCP-100","message":"invalid lead"}}]}`))
	}))
	defer server.Close()

	client := NewClient(server.URL)
	client.MaxRetries = 0
	items := []BatchItem{
		{ItemID: "item_a", IdempotencyKey: "batch-a", Payload: map[string]any{"lead_id": "lead_a"}},
		{ItemID: "item_b", IdempotencyKey: "batch-b", Payload: map[string]any{"lead_id": "lead_b"}},
	}
	results, err := client.SubmitLeadsBatch(items, "req-batch-1")
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 2 || results[0]["item_id"] != "item_a" || results[1]["item_id"] != "item_b" {
		t.Fatalf("results = %#v", results)
	}
	if results[0]["status"] != "rejected" {
		t.Fatalf("status = %v", results[0]["status"])
	}
	errorObject, ok := results[0]["error"].(map[string]any)
	if !ok || errorObject["code"] != "LCP-100" {
		t.Fatalf("error = %#v", results[0]["error"])
	}
	body := <-received
	if body["request_id"] != "req-batch-1" {
		t.Fatalf("request_id = %v", body["request_id"])
	}
	sentItems, ok := body["items"].([]any)
	if !ok || len(sentItems) != 2 {
		t.Fatalf("items = %#v", body["items"])
	}
	first := sentItems[0].(map[string]any)
	if first["item_id"] != "item_a" || first["payload"].(map[string]any)["lead_id"] != "lead_a" {
		t.Fatalf("first item = %#v", first)
	}
}

func TestSubmitLeadsBatchRejectsOverCapBeforeSending(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		requests++
		writer.WriteHeader(http.StatusMultiStatus)
		_, _ = writer.Write([]byte(`{"results":[]}`))
	}))
	defer server.Close()

	items := make([]BatchItem, 101)
	for index := range items {
		items[index] = BatchItem{ItemID: "item", IdempotencyKey: "key", Payload: map[string]any{}}
	}
	if _, err := NewClient(server.URL).SubmitLeadsBatch(items, "req"); err == nil {
		t.Fatal("expected a cap error")
	}
	if requests != 0 {
		t.Fatalf("requests = %d, want 0", requests)
	}
}
