Gem::Specification.new do |spec|
  spec.name = "lcp-sdk"
  spec.version = "0.1.0"
  spec.summary = "LCP Lead Context Protocol SDK"
  spec.description = "Signing, envelopes, webhooks, and HTTP helpers for LCP."
  spec.authors = ["Spear Systems"]
  spec.license = "Apache-2.0"
  spec.required_ruby_version = ">= 3.0"
  spec.add_runtime_dependency "json_schemer", "~> 2.5"
  spec.files = Dir["lib/**/*", "README.md"]
  spec.require_paths = ["lib"]
end
