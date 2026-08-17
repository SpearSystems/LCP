# frozen_string_literal: true

require "json"

module LcpSdk
  class SchemaValidationError < StandardError; end

  class SchemaValidator
    def initialize(schema_documents)
      require "json_schemer"
      # Keep a private copy so self-references in core.json can be made
      # fragment-local without mutating the caller's canonical documents.
      @documents = schema_documents.transform_values do |document|
        JSON.parse(JSON.generate(document))
      end
      @documents.each_value do |document|
        rewrite_core_refs(document) if document["$id"] == "https://lcp.dev/schemas/core.json"
      end
      @ids = {}
      @documents.each do |name, document|
        id = document.fetch("$id", name)
        @ids[normalize(name)] = id
        @ids[normalize(id)] = id
      end
    end

    def self.from_directory(root)
      documents = {}
      Dir.glob(File.join(root, "**", "*.json")).each do |path|
        name = path.delete_prefix("#{root}/")
        documents[name] = JSON.parse(File.read(path))
      end
      if File.basename(root.to_s) == "schemas"
        vertical_root = File.join(File.dirname(root), "verticals")
        Dir.glob(File.join(vertical_root, "**", "*.json")).each do |path|
          name = path.delete_prefix("#{vertical_root}/")
          documents["verticals/#{name}"] = JSON.parse(File.read(path))
        end
      end
      new(documents)
    end

    def validate(schema_name, instance)
      id = @ids.fetch(normalize(schema_name)) { raise KeyError, "unknown LCP schema: #{schema_name}" }
      schema = @documents.values.find { |candidate| candidate["$id"] == id } || @documents.fetch(schema_name)
      errors = JSONSchemer.schema(schema, ref_resolver: lambda do |uri|
        reference = uri.to_s
        @documents.values.find { |candidate| candidate["$id"].to_s == reference } ||
          @documents[reference] ||
          @documents[reference.sub(%r{.*/}, "")]
      end).validate(instance).to_a
      raise SchemaValidationError, "LCP schema validation failed for #{schema_name}: #{errors.first}" unless errors.empty?
      true
    end

    def validate_envelope(envelope)
      validate("schemas/envelope.json", envelope)
      type = envelope.dig("lcp", "message", "type")
      raise SchemaValidationError, "missing lcp.message.type" unless type.is_a?(String)
      payload = envelope.dig("lcp", "payload")
      validate("schemas/#{type}.json", payload)
      validate_vertical_policy(type, payload)
    end

    def validate_vertical_policy(type, payload)
      return unless %w[lead call post ping].include?(type) && payload.is_a?(Hash)
      attributes = payload["attributes"]
      return unless attributes.is_a?(Hash)
      vertical = type == "ping" ? payload["vertical"] : attributes["vertical"]
      return unless vertical.is_a?(String) && !vertical.empty?
      schema = @documents[normalize("verticals/#{vertical}.json")] ||
        @documents["verticals/#{vertical}.json"] ||
        @documents["#{vertical}.json"]
      raise SchemaValidationError, "Vertical schema '#{vertical}' not found" unless schema.is_a?(Hash)
      vertical_attributes = JSON.parse(JSON.generate(attributes))
      if type == "ping"
        vertical_attributes["vertical"] ||= vertical
        vertical_attributes["schema_version"] ||= schema.dig("properties", "schema_version", "const") || "1.0.0"
      end
      validate_vertical(vertical, vertical_attributes)
      validate_ping_safe(vertical, attributes, schema, "attributes") if type == "ping"
    end

    def validate_ping_safe(vertical, value, schema, path)
      return unless value.is_a?(Hash)
      properties = schema.fetch("properties", {})
      value.each do |name, child|
        next if path == "attributes" && %w[vertical schema_version].include?(name)
        definition = properties[name]
        unless definition.is_a?(Hash) && definition["ping_safe"] == true
          raise SchemaValidationError, "#{path}.#{name} is not tagged ping_safe: true in vertical '#{vertical}'"
        end
        validate_ping_safe(vertical, child, definition, "#{path}.#{name}") if child.is_a?(Hash) && definition["properties"].is_a?(Hash)
      end
    end

    def validate_offer(offer)
      validate("schemas/offer.json", offer)
    end

    def validate_vertical(vertical, attributes)
      validate("verticals/#{vertical}.json", attributes)
    end

    private

    def rewrite_core_refs(value)
      case value
      when Hash
        if value["$ref"].is_a?(String)
          value["$ref"] = value["$ref"].sub(/\Acore\.json(?=#)/, "")
        end
        value.each_value { |child| rewrite_core_refs(child) }
      when Array
        value.each { |child| rewrite_core_refs(child) }
      end
    end

    def normalize(name)
      name.to_s.tr("\\", "/").sub(%r{^/}, "").sub(%r{^(schemas|verticals)/}, "").sub(/\.json$/, "")
    end
  end
end
