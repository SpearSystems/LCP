package lcp

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

// Slot fields introduced at v1.1 (LEP-0003) that require the receiving
// endpoint to advertise v1.1 support before they may be sent. The ping/bid/
// post payload objects are closed (additionalProperties: false), so a v1.0
// schema-validating receiver REJECTS these fields rather than ignoring them;
// SPEC.md §8 makes the advertisement a parse rule, never a string literal or
// prefix match.
var SlotFields = []string{"slots_available", "slots_claimed", "contacted_hashes"}

const minSlotMinor = 1

var fullSemverElement = regexp.MustCompile(`^\d+\.\d+\.\d+$`)

// ReceiverAdvertisesSlotFields implements the SPEC.md §8 version-string
// membership rule: the gate opens only when the advertised set contains a
// full major.minor.patch element with major == 1 and minor >= 1. Malformed or
// partial elements ("1.1", "v1.1.0", "1.1.0.1") never open the gate.
func ReceiverAdvertisesSlotFields(advertisedLcpVersions []string) bool {
	for _, element := range advertisedLcpVersions {
		if !fullSemverElement.MatchString(element) {
			continue
		}
		parts := strings.Split(element, ".")
		if parts[0] != "1" {
			continue
		}
		var minor int
		if _, err := fmt.Sscanf(parts[1], "%d", &minor); err != nil {
			continue
		}
		if minor >= minSlotMinor {
			return true
		}
	}
	return false
}

// SlotCapabilityErrors returns one error per v1.1 slot field carried by the
// envelope when the receiver's advertisement does not support them. An empty
// result is capability-compatibility, not a schema verdict.
func SlotCapabilityErrors(envelope []byte, advertisedLcpVersions []string) ([]string, error) {
	var wire struct {
		LCP struct {
			Payload json.RawMessage `json:"payload"`
		} `json:"lcp"`
	}
	if err := json.Unmarshal(envelope, &wire); err != nil {
		return nil, fmt.Errorf("decode envelope: %w", err)
	}
	payload, err := decodePayload(wire.LCP.Payload)
	if err != nil {
		return nil, err
	}
	present := slotFieldsIn(payload)
	if len(present) == 0 {
		return nil, nil
	}
	if ReceiverAdvertisesSlotFields(advertisedLcpVersions) {
		return nil, nil
	}
	problems := make([]string, 0, len(present))
	for _, field := range present {
		problems = append(problems, fmt.Sprintf(
			"%s requires the receiver to advertise a full-semver LCP version with major 1 and minor >= %d (SPEC.md §8 capability gate)",
			field, minSlotMinor,
		))
	}
	return problems, nil
}

func decodePayload(raw json.RawMessage) (map[string]any, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, fmt.Errorf("decode payload: %w", err)
	}
	return payload, nil
}

func slotFieldsIn(payload map[string]any) []string {
	if payload == nil {
		return nil
	}
	var present []string
	for _, field := range SlotFields {
		if _, ok := payload[field]; ok {
			present = append(present, field)
		}
	}
	if event, _ := payload["event"].(string); event == "CONTACTED" {
		if details, ok := payload["details"].(map[string]any); ok {
			if _, exists := details["contacted_hashes"]; exists {
				present = append(present, "details.contacted_hashes")
			}
		}
	}
	return present
}
