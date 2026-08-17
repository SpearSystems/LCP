use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::path::Path;

#[derive(Debug)]
pub struct SchemaValidationError(pub String);
impl std::fmt::Display for SchemaValidationError { fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { f.write_str(&self.0) } }
impl std::error::Error for SchemaValidationError {}

/// Validates every canonical LCP schema without fetching references over the network.
/// The core definitions are inlined into the validator input, so package users can
/// safely validate an SDK-bundled schema set offline.
pub struct SchemaValidator { schemas: HashMap<String, Value> }
impl SchemaValidator {
    pub fn new(schemas: HashMap<String, Value>) -> Self { Self { schemas } }

    pub fn from_directory(root: impl AsRef<Path>) -> Result<Self, SchemaValidationError> {
        let root = root.as_ref();
        let mut schemas = HashMap::new();
        Self::load_directory(root, &mut schemas)?;
        if root.file_name().and_then(|value| value.to_str()) == Some("schemas") {
            if let Some(parent) = root.parent() {
                let vertical_root = parent.join("verticals");
                if vertical_root.is_dir() {
                    let mut verticals = HashMap::new();
                    Self::load_directory(&vertical_root, &mut verticals)?;
                    for (name, value) in verticals {
                        schemas.insert(format!("verticals/{name}"), value);
                    }
                }
            }
        }
        Ok(Self::new(schemas))
    }

    fn load_directory(root: &Path, schemas: &mut HashMap<String, Value>) -> Result<(), SchemaValidationError> {
        for entry in fs::read_dir(root).map_err(|e| SchemaValidationError(e.to_string()))? {
            let path = entry.map_err(|e| SchemaValidationError(e.to_string()))?.path();
            if path.is_dir() { Self::load_directory(&path, schemas)?; continue; }
            if path.extension().and_then(|v| v.to_str()) != Some("json") { continue; }
            let relative = path.strip_prefix(root).unwrap_or(&path).to_string_lossy().replace('\\', "/");
            let value = serde_json::from_str(&fs::read_to_string(&path).map_err(|e| SchemaValidationError(e.to_string()))?).map_err(|e| SchemaValidationError(e.to_string()))?;
            schemas.insert(relative, value);
        }
        Ok(())
    }

    pub fn validate(&self, schema_name: &str, document: &Value) -> Result<(), SchemaValidationError> {
        let key = self.find_key(schema_name).ok_or_else(|| SchemaValidationError(format!("unknown LCP schema: {schema_name}")))?;
        let mut schema = self.schemas[key].clone();
        let core = self.schemas.get("core.json").or_else(|| self.schemas.get("schemas/core.json"));
        inline_core_refs(&mut schema, core);
        let validator = jsonschema::draft202012::options().should_validate_formats(true).build(&schema).map_err(|e| SchemaValidationError(e.to_string()))?;
        validator.validate(document).map_err(|e| SchemaValidationError(format!("LCP schema validation failed for {schema_name}: {e}")))
    }

    pub fn validate_envelope(&self, envelope: &Value) -> Result<(), SchemaValidationError> {
        self.validate("schemas/envelope.json", envelope)?;
        let message_type = envelope.pointer("/lcp/message/type").and_then(Value::as_str).ok_or_else(|| SchemaValidationError("missing lcp.message.type".into()))?;
        let payload = envelope.pointer("/lcp/payload").ok_or_else(|| SchemaValidationError("missing lcp.payload".into()))?;
        self.validate(&format!("schemas/{message_type}.json"), payload)?;
        self.validate_vertical_policy(message_type, payload)
    }

    fn validate_vertical_policy(&self, message_type: &str, payload: &Value) -> Result<(), SchemaValidationError> {
        if !matches!(message_type, "lead" | "call" | "post" | "ping") {
            return Ok(());
        }
        let attributes = match payload.get("attributes").and_then(Value::as_object) {
            Some(value) => value,
            None => return Ok(()),
        };
        let vertical = if message_type == "ping" {
            payload.get("vertical").and_then(Value::as_str)
        } else {
            attributes.get("vertical").and_then(Value::as_str)
        };
        let Some(vertical) = vertical else { return Ok(()); };
        let schema_name = format!("verticals/{vertical}.json");
        let key = self.find_key(&schema_name).ok_or_else(|| {
            SchemaValidationError(format!("vertical schema '{vertical}' not found"))
        })?;
        let schema = self.schemas[key].as_object().ok_or_else(|| {
            SchemaValidationError(format!("vertical schema '{vertical}' is not an object"))
        })?;
        let mut vertical_attributes = attributes.clone();
        if message_type == "ping" {
            vertical_attributes.entry("vertical".to_string()).or_insert_with(|| Value::String(vertical.to_string()));
            if !vertical_attributes.contains_key("schema_version") {
                if let Some(version) = schema.get("properties")
                    .and_then(Value::as_object)
                    .and_then(|properties| properties.get("schema_version"))
                    .and_then(Value::as_object)
                    .and_then(|definition| definition.get("const"))
                {
                    vertical_attributes.insert("schema_version".to_string(), version.clone());
                }
            }
        }
        self.validate(&schema_name, &Value::Object(vertical_attributes))?;
        if message_type == "ping" {
            validate_ping_safe_fields(attributes, schema, "attributes")?;
        }
        Ok(())
    }

    pub fn validate_offer(&self, offer: &Value) -> Result<(), SchemaValidationError> { self.validate("schemas/offer.json", offer) }
    pub fn validate_vertical(&self, vertical: &str, attributes: &Value) -> Result<(), SchemaValidationError> { self.validate(&format!("verticals/{vertical}.json"), attributes) }

    fn find_key(&self, name: &str) -> Option<&String> {
        let normalized = name.trim_start_matches('/').replace("schemas/", "").replace("verticals/", "vertical:").trim_end_matches(".json").to_string();
        self.schemas.keys().find(|key| {
            let candidate = key.trim_end_matches(".json").replace("schemas/", "").replace("verticals/", "vertical:");
            candidate == normalized || candidate.ends_with(&normalized)
        })
    }
}

fn validate_ping_safe_fields(
    value: &serde_json::Map<String, Value>,
    schema: &serde_json::Map<String, Value>,
    path: &str,
) -> Result<(), SchemaValidationError> {
    let properties = schema
        .get("properties")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    for (field, child) in value {
        if path == "attributes" && (field == "vertical" || field == "schema_version") {
            continue;
        }
        let definition = properties.get(field).and_then(Value::as_object);
        if definition.and_then(|item| item.get("ping_safe")).and_then(Value::as_bool) != Some(true) {
            return Err(SchemaValidationError(format!(
                "{path}.{field} is not tagged ping_safe: true"
            )));
        }
        if let Some(child_properties) = definition
            .and_then(|item| item.get("properties"))
            .and_then(Value::as_object)
        {
            if let Some(child_object) = child.as_object() {
                validate_ping_safe_fields(child_object, child_properties, &format!("{path}.{field}"))?;
            }
        }
    }
    Ok(())
}

fn inline_core_refs(node: &mut Value, core: Option<&Value>) {
    if let Some(object) = node.as_object_mut() {
        if let Some(reference) = object.get("$ref").and_then(Value::as_str) {
            if let (Some(core), Some(name)) = (core, reference.split("#/$defs/").nth(1)) {
                if reference.contains("core.json") {
                    if let Some(definition) = core.get("$defs").and_then(|defs| defs.get(name)) {
                        *node = definition.clone();
                        inline_core_refs(node, Some(core));
                        return;
                    }
                }
            }
        }
        for value in object.values_mut() { inline_core_refs(value, core); }
    } else if let Some(array) = node.as_array_mut() {
        for value in array { inline_core_refs(value, core); }
    }
}
