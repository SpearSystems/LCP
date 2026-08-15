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
        let mut schemas = HashMap::new();
        Self::load_directory(root.as_ref(), &mut schemas)?;
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
        self.validate(&format!("schemas/{message_type}.json"), payload)
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
