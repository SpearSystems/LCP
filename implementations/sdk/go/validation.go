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
	if err := ValidateSchemaBundle("schemas/"+wire.LCP.Message.Type+".json", schemas, wire.LCP.Payload); err != nil {
		return err
	}
	return validateEnvelopeVerticalPolicy(schemas, wire.LCP.Message.Type, wire.LCP.Payload)
}

func validateEnvelopeVerticalPolicy(schemas map[string][]byte, messageType string, raw json.RawMessage) error {
	if messageType != "lead" && messageType != "call" && messageType != "post" && messageType != "ping" {
		return nil
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return fmt.Errorf("decode %s payload: %w", messageType, err)
	}
	attributes, ok := payload["attributes"].(map[string]any)
	if !ok {
		return nil
	}
	vertical, _ := attributes["vertical"].(string)
	if messageType == "ping" {
		vertical, _ = payload["vertical"].(string)
	}
	if vertical == "" {
		return nil
	}
	schemaName := "verticals/" + vertical + ".json"
	schemaRaw, ok := schemas[schemaName]
	if !ok {
		return fmt.Errorf("vertical schema %q not found", vertical)
	}
	var schema map[string]any
	if err := json.Unmarshal(schemaRaw, &schema); err != nil {
		return fmt.Errorf("parse vertical schema %q: %w", vertical, err)
	}
	verticalAttributes := make(map[string]any, len(attributes)+2)
	for key, value := range attributes {
		verticalAttributes[key] = value
	}
	if messageType == "ping" {
		if _, exists := verticalAttributes["vertical"]; !exists {
			verticalAttributes["vertical"] = vertical
		}
		if _, exists := verticalAttributes["schema_version"]; !exists {
			if properties, ok := schema["properties"].(map[string]any); ok {
				if version, ok := properties["schema_version"].(map[string]any); ok {
					verticalAttributes["schema_version"] = version["const"]
				}
			}
		}
	}
	encoded, err := json.Marshal(verticalAttributes)
	if err != nil {
		return fmt.Errorf("encode vertical attributes: %w", err)
	}
	if err := ValidateSchemaBundle(schemaName, schemas, encoded); err != nil {
		return err
	}
	if messageType == "ping" {
		if err := validatePingSafeFields(attributes, schema, "attributes"); err != nil {
			return err
		}
	}
	return nil
}

func validatePingSafeFields(value map[string]any, schema map[string]any, path string) error {
	properties, _ := schema["properties"].(map[string]any)
	for field, child := range value {
		if path == "attributes" && (field == "vertical" || field == "schema_version") {
			continue
		}
		definition, ok := properties[field].(map[string]any)
		if !ok || definition["ping_safe"] != true {
			return fmt.Errorf("%s.%s is not tagged ping_safe: true", path, field)
		}
		if childProperties, ok := definition["properties"].(map[string]any); ok {
			if childMap, ok := child.(map[string]any); ok {
				if err := validatePingSafeFields(childMap, map[string]any{"properties": childProperties}, path+"."+field); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

// ValidateOffer is a convenience wrapper for buyer offer documents.
func ValidateOffer(schemas map[string][]byte, offer []byte) error {
	return ValidateSchemaBundle("schemas/offer.json", schemas, offer)
}

// ValidateVertical validates a vertical-specific attributes object.
func ValidateVertical(vertical string, schemas map[string][]byte, attributes []byte) error {
	return ValidateSchemaBundle("verticals/"+vertical+".json", schemas, attributes)
}
