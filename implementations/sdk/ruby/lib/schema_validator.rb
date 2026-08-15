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
      validate("schemas/#{type}.json", envelope.dig("lcp", "payload"))
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
