package lcp

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func loadLep0003Schemas(t *testing.T) map[string][]byte {
	t.Helper()
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
	return schemas
}

func readLep0003File(t *testing.T, segments ...string) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(append([]string{"..", "..", ".."}, segments...)...))
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

// TestContactHashVectors executes every pinned preimage→digest row from
// test-vectors/sdk/contact-hash.json (#153 T9 run). A row that cannot be
// reproduced is an interop failure, reported with the exact preimage and the
// observed digest.
func TestContactHashVectors(t *testing.T) {
	var suite struct {
		Vectors []struct {
			Name     string `json:"name"`
			Secret   string `json:"secret"`
			Domain   string `json:"domain"`
			Preimage string `json:"preimage"`
			Digest   string `json:"digest"`
		} `json:"vectors"`
	}
	if err := json.Unmarshal(readLep0003File(t, "test-vectors", "sdk", "contact-hash.json"), &suite); err != nil {
		t.Fatal(err)
	}
	if len(suite.Vectors) == 0 {
		t.Fatal("contact-hash.json exposed no vectors")
	}
	var failures []string
	for _, vector := range suite.Vectors {
		observed, err := ContactHash(vector.Secret, vector.Preimage, vector.Domain)
		if err != nil {
			failures = append(failures, vector.Name+": "+err.Error())
			continue
		}
		if observed != vector.Digest {
			failures = append(failures, vector.Name+" ("+vector.Domain+"): preimage "+
				quote(vector.Preimage)+" pinned "+vector.Digest+" but Go SDK produced "+observed)
		}
	}
	if len(failures) > 0 {
		t.Fatalf("contact-hash vector reproduction failed:\n%s", strings.Join(failures, "\n"))
	}
}

func TestContactHashPreimageRules(t *testing.T) {
	// SPEC §9: phone is the verbatim wire bytes — formatting noise is a different digest.
	if got, _ := ContactHash("pair-secret-for-vectors-0123456789", "+1 (512) 555-0147", "phone"); got ==
		"c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12" {
		t.Fatal("phone preimage must stay verbatim; separators must not be folded away")
	}
	const pinnedEmail = "d28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4"
	if got, _ := ContactHash("pair-secret-for-vectors-0123456789", "  Jordan@Example.COM  ", "email"); got != pinnedEmail {
		t.Fatalf("email canonical must trim whitespace and lowercase the domain only, got %s", got)
	}
	if got, _ := ContactHash("pair-secret-for-vectors-0123456789", "jordan@example.com", "email"); got == pinnedEmail {
		t.Fatal("local-part bytes are verbatim: casefolded local must differ")
	}
	if got, _ := ContactHash("pair-secret-for-vectors-0123456789", "Jordan+crm@example.com", "email"); got == pinnedEmail {
		t.Fatal("plus-addresses are distinct under the §9 canonical")
	}
}

func TestReceiverAdvertisesSlotFields(t *testing.T) {
	cases := []struct {
		advertised []string
		want       bool
	}{
		{[]string{"1.0.0"}, false},
		{[]string{"1.1.0"}, true},
		{[]string{"1.0.0", "1.1.0"}, true},
		{[]string{"1.10.0"}, true}, // parsed minor 10 >= 1
		{[]string{"2.0.0"}, false}, // major must be 1
		{[]string{"1.1"}, false},   // literal never opens the gate
		{[]string{"v1.1.0", "1.1.0.1", ""}, false},
	}
	for _, testCase := range cases {
		if got := ReceiverAdvertisesSlotFields(testCase.advertised); got != testCase.want {
			t.Errorf("ReceiverAdvertisesSlotFields(%v) = %v, want %v", testCase.advertised, got, testCase.want)
		}
	}
}

func TestSlotRoundTripAndCapabilityGate(t *testing.T) {
	schemas := loadLep0003Schemas(t)
	ping := readLep0003File(t, "examples", "ping-slots.json")
	bid := readLep0003File(t, "examples", "bid-slots.json")
	post := readLep0003File(t, "examples", "post-contacted.json")
	event := readLep0003File(t, "examples", "event-contacted.json")

	// Slot-bearing messages validate through the SDK schema path (v1.1 bundle).
	for name, envelope := range map[string][]byte{"ping": ping, "bid": bid, "post": post, "event": event} {
		if err := ValidateEnvelopeBundle(schemas, envelope); err != nil {
			t.Fatalf("slot-bearing %s failed validation: %v", name, err)
		}
	}

	// The capability gate rejects them toward a receiver advertising v1.0 only…
	for name, envelope := range map[string][]byte{"ping": ping, "bid": bid, "post": post, "event": event} {
		err := ValidateEnvelopeBundle(schemas, envelope, []string{"1.0.0"})
		if err == nil || !strings.Contains(err.Error(), "capability gate") {
			t.Fatalf("slot-bearing %s must be rejected for a v1.0-only receiver, got %v", name, err)
		}
	}
	// …and accepts them toward a v1.1 advertisement.
	for name, envelope := range map[string][]byte{"ping": ping, "bid": bid, "post": post, "event": event} {
		if err := ValidateEnvelopeBundle(schemas, envelope, []string{"1.0.0", "1.1.0"}); err != nil {
			t.Fatalf("slot-bearing %s must validate for a v1.1 receiver: %v", name, err)
		}
	}
}

func quote(value string) string { return "\"" + value + "\"" }
