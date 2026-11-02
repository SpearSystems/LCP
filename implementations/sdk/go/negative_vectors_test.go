// Negative security vector runner (test-vectors/sdk/negative/, README.md format).
//
// Mirrors the Python reference runner: load every *.json suite, lint the shared
// format enough to trust it (unique ids, known checks, gap rules), then execute
// each applicable vector through the real Go SDK primitives — VerifyHMAC with
// the verifier clock pinned to the vector's `now` (raw bytes, invariant 2) and
// ValidateEnvelopeBundle / ValidateOffer for schema checks. The Go primitives
// surface plain errors, not structured LCP codes, so per README we assert the
// reject/accept outcome only. Unknown check ⇒ fail, never skip.
// sdk_exercisable=false vectors print their gap_note to stdout so the gap shows
// in `go test ./...` job output even without -v (bare passing-test t.Log output
// is hidden).
package lcp

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"testing"
	"time"
)

type negExpect struct {
	Outcome   string  `json:"outcome"`
	ErrorCode *string `json:"error_code"`
	ErrorName *string `json:"error_name"`
}

type negVector struct {
	ID             string         `json:"id"`
	Invariant      *int           `json:"invariant"`
	SpecRef        string         `json:"spec_ref"`
	Description    string         `json:"description"`
	AppliesTo      []string       `json:"applies_to"`
	Check          string         `json:"check"`
	Inputs         map[string]any `json:"inputs"`
	Expect         negExpect      `json:"expect"`
	SDKExercisable *bool          `json:"sdk_exercisable"`
	GapNote        string         `json:"gap_note"`
}

type negSuite struct {
	Suite       string      `json:"suite"`
	Version     int         `json:"version"`
	Group       string      `json:"group"`
	Description string      `json:"description"`
	Vectors     []negVector `json:"vectors"`
	file        string
}

var negCheckCodes = map[string]map[string]bool{
	"verify_hmac":            {"LCP-012": true},
	"verify_raw_body":        {"LCP-012": true},
	"envelope_validate":      {"LCP-006": true, "LCP-008": true, "LCP-100": true},
	"offer_validate":         {"LCP-100": true},
	"production_mode_verify": {"LCP-013": true},
}

func loadNegativeSuites(t *testing.T) []negSuite {
	t.Helper()
	dir := filepath.Join("..", "..", "..", "test-vectors", "sdk", "negative")
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, entry := range entries {
		if filepath.Ext(entry.Name()) == ".json" {
			names = append(names, entry.Name())
		}
	}
	sort.Strings(names)
	if len(names) == 0 {
		t.Fatalf("no vector files found under %s", dir)
	}
	var suites []negSuite
	for _, name := range names {
		raw, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatal(err)
		}
		var suite negSuite
		if err := json.Unmarshal(raw, &suite); err != nil {
			t.Fatalf("%s: invalid JSON: %v", name, err)
		}
		suite.file = name
		suites = append(suites, suite)
	}
	return suites
}

// lintNegativeSuites mirrors the Python format lint's load-bearing rules.
func lintNegativeSuites(t *testing.T, suites []negSuite) {
	t.Helper()
	var problems []string
	seen := map[string]bool{}
	for _, suite := range suites {
		if suite.Suite != "lcp-sdk-negative" {
			problems = append(problems, suite.file+": suite must be 'lcp-sdk-negative'")
		}
		if suite.Version != 1 {
			problems = append(problems, fmt.Sprintf("%s: unsupported suite version %d", suite.file, suite.Version))
		}
		if suite.Group == "" || suite.Description == "" {
			problems = append(problems, suite.file+": missing group/description")
		}
		if len(suite.Vectors) == 0 {
			problems = append(problems, suite.file+": empty vectors array")
		}
		for _, vector := range suite.Vectors {
			where := suite.file + ":" + vector.ID
			if vector.ID == "" || vector.SpecRef == "" || len(vector.AppliesTo) == 0 || vector.Inputs == nil ||
				vector.SDKExercisable == nil {
				problems = append(problems, where+": missing required field(s)")
			}
			if vector.Invariant != nil && (*vector.Invariant < 1 || *vector.Invariant > 8) {
				problems = append(problems, where+": invariant must be 1-8 or null")
			}
			if seen[vector.ID] {
				problems = append(problems, where+": duplicate vector id")
			}
			seen[vector.ID] = true
			codes, known := negCheckCodes[vector.Check]
			if !known {
				problems = append(problems, fmt.Sprintf("%s: unknown check %q", where, vector.Check))
				continue
			}
			switch vector.Expect.Outcome {
			case "reject":
				if vector.Expect.ErrorCode == nil || !codes[*vector.Expect.ErrorCode] {
					problems = append(problems, fmt.Sprintf("%s: reject expects error_code in %v, got %v", where, codes, vector.Expect.ErrorCode))
				}
				if vector.Expect.ErrorName == nil || *vector.Expect.ErrorName == "" {
					problems = append(problems, where+": reject requires error_name")
				}
			case "accept":
				if vector.Expect.ErrorCode != nil || vector.Expect.ErrorName != nil {
					problems = append(problems, where+": accept vectors must not declare an error")
				}
			default:
				problems = append(problems, where+": expect.outcome must be reject|accept")
			}
			if vector.SDKExercisable != nil && !*vector.SDKExercisable && len(vector.GapNote) < 40 {
				problems = append(problems, where+": sdk_exercisable=false requires a substantive gap_note")
			}
		}
	}
	if len(problems) > 0 {
		t.Fatalf("negative vector format lint failed:\n%s", join(problems))
	}
}

func join(lines []string) string {
	out := ""
	for _, line := range lines {
		out += "  - " + line + "\n"
	}
	return out
}

func negString(inputs map[string]any, key string) string {
	value, _ := inputs[key].(string)
	return value
}

// executeNegativeVector returns outcome ("reject"|"accept"|"gap"|"error") + detail.
func executeNegativeVector(t *testing.T, schemas map[string][]byte, vector negVector) (string, string) {
	t.Helper()
	if vector.SDKExercisable != nil && !*vector.SDKExercisable {
		return "gap", vector.GapNote
	}
	inputs := vector.Inputs
	switch vector.Check {
	case "verify_hmac", "verify_raw_body":
		body := negString(inputs, "body")
		if vector.Check == "verify_raw_body" {
			body = negString(inputs, "verify_body")
		}
		now, err := time.Parse(time.RFC3339, negString(inputs, "now"))
		if err != nil {
			return "error", "unparseable vector clock: " + err.Error()
		}
		window := time.Duration(int64(inputs["replay_window_seconds"].(float64))) * time.Second
		if verifyErr := VerifyHMAC(negString(inputs, "secret"), negString(inputs, "signature"),
			negString(inputs, "timestamp"), negString(inputs, "idempotency_key"), []byte(body), window, now); verifyErr != nil {
			return "reject", verifyErr.Error()
		}
		return "accept", ""
	case "envelope_validate":
		raw, err := json.Marshal(inputs["envelope"])
		if err != nil {
			return "error", err.Error()
		}
		if err := ValidateEnvelopeBundle(schemas, raw); err != nil {
			return "reject", err.Error()
		}
		return "accept", ""
	case "offer_validate":
		raw, err := json.Marshal(inputs["offer"])
		if err != nil {
			return "error", err.Error()
		}
		if err := ValidateOffer(schemas, raw); err != nil {
			return "reject", err.Error()
		}
		return "accept", ""
	default:
		// Exercisable vector whose check this lane has no adapter for: fail loudly.
		return "error", fmt.Sprintf("no runner adapter for check %q", vector.Check)
	}
}

func TestNegativeSecurityVectors(t *testing.T) {
	suites := loadNegativeSuites(t)
	lintNegativeSuites(t, suites)

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

	ran := 0
	gaps := 0
	for _, suite := range suites {
		for _, vector := range suite.Vectors {
			outcome, detail := executeNegativeVector(t, schemas, vector)
			if outcome == "gap" {
				gaps++
				// stdout, not t.Log: visible in `go test ./...` without -v.
				fmt.Printf("GAP (reported, never silently passed): %s: %s\n", vector.ID, detail)
				continue
			}
			ran++
			if outcome == "error" {
				t.Errorf("%s (%s): runner error — %s — %s", vector.ID, vector.Check, detail, vector.Description)
				continue
			}
			if outcome != vector.Expect.Outcome {
				t.Errorf("%s (%s): expected %s, got %s — %s — %s",
					vector.ID, vector.Check, vector.Expect.Outcome, outcome, detail, vector.Description)
			}
		}
	}
	if ran == 0 {
		t.Fatal("no negative vector was actually executed")
	}
	fmt.Printf("Go SDK negative vectors: %d executed, %d gap-reported\n", ran, gaps)
}
