package lcp

import (
	"encoding/json"
	"fmt"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

// ValidateSchemaBundle validates a JSON document against a named schema and
// resolves every $ref from the supplied canonical schema bundle.
// ValidateSchemaBundle validates any canonical schema in a complete LCP schema bundle.
func ValidateSchemaBundle(schemaName string, schemas map[string][]byte, document []byte) error {
	compiler := jsonschema.NewCompiler()
	for name, raw := range schemas {
		var metadata struct {
			ID string `json:"$id"`
		}
		if err := json.Unmarshal(raw, &metadata); err != nil {
			return fmt.Errorf("parse schema %s: %w", name, err)
		}
		resourceName := metadata.ID
		if resourceName == "" {
			resourceName = name
		}

		var document any
		if err := json.Unmarshal(raw, &document); err != nil {
			return fmt.Errorf("parse schema %s: %w", name, err)
		}
		if err := compiler.AddResource(resourceName, document); err != nil {
			return fmt.Errorf("register schema %s: %w", name, err)
		}
	}
	schemaID := schemaName
	if raw, ok := schemas[schemaName]; ok {
		var metadata struct {
			ID string `json:"$id"`
		}
		if err := json.Unmarshal(raw, &metadata); err == nil && metadata.ID != "" {
			schemaID = metadata.ID
		}
	}
	compiled, err := compiler.Compile(schemaID)
	if err != nil {
		return fmt.Errorf("compile schema %s: %w", schemaName, err)
	}
	var value any
	if err := json.Unmarshal(document, &value); err != nil {
		return fmt.Errorf("decode document: %w", err)
	}
	if err := compiled.Validate(value); err != nil {
		return fmt.Errorf("LCP schema validation failed for %s: %w", schemaName, err)
	}
	return nil
}

// ValidateEnvelopeBundle validates the envelope and then dispatches its payload
// through the message-type schema named by lcp.message.type.
func ValidateEnvelopeBundle(schemas map[string][]byte, envelope []byte) error {
	if err := ValidateSchemaBundle("schemas/envelope.json", schemas, envelope); err != nil {
		return err
	}
	var wire struct {
		LCP struct {
			Message struct {
				Type string `json:"type"`
			} `json:"message"`
			Payload json.RawMessage `json:"payload"`
		} `json:"lcp"`
	}
	if err := json.Unmarshal(envelope, &wire); err != nil {
		return fmt.Errorf("decode envelope: %w", err)
	}
	return ValidateSchemaBundle("schemas/"+wire.LCP.Message.Type+".json", schemas, wire.LCP.Payload)
}

// ValidateOffer is a convenience wrapper for buyer offer documents.
func ValidateOffer(schemas map[string][]byte, offer []byte) error {
	return ValidateSchemaBundle("schemas/offer.json", schemas, offer)
}

// ValidateVertical validates a vertical-specific attributes object.
func ValidateVertical(vertical string, schemas map[string][]byte, attributes []byte) error {
	return ValidateSchemaBundle("verticals/"+vertical+".json", schemas, attributes)
}
