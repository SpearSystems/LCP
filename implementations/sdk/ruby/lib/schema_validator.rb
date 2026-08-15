# frozen_string_literal: true

module LcpSdk
  class SchemaValidationError < StandardError; end

  class SchemaValidator
    def initialize(schema_documents)
      require "json_schemer"
      @documents = schema_documents
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
        @documents.values.find { |candidate| candidate["$id"] == uri } || @documents[uri]
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

    def normalize(name)
      name.to_s.tr("\\", "/").sub(%r{^/}, "").sub(%r{^(schemas|verticals)/}, "").sub(/\.json$/, "")
    end
  end
end
