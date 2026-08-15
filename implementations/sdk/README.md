# LCP SDKs

> **SDK hub · Page 3 of 6**
>
> Choose a language below. Every SDK follows the same wire contract, HMAC
> profile, schema bundle, generated model set, and compatibility vectors.

LCP SDKs provide idiomatic clients and server helpers for publishers, buyers,
platforms, webhook receivers, and integrations. They do not implement the
reference platform, auction engine, persistence, or MCP itself.

<details open>
<summary><strong>SDK quick navigation</strong></summary>

1. [Choose a language](#language-matrix)
2. [Understand validation and generated models](#validation-and-generated-models)
3. [Run compatibility tests](#run-the-compatibility-checks)
4. [Publish a coordinated release](#release-navigation)

</details>

## Language matrix

| Language | Tier | Location | Primary use |
|---|---:|---|---|
| Python | 1 | [`python/`](python/) | Data, automation, AI, publisher and platform services |
| TypeScript/JavaScript | 1 | [`typescript/`](typescript/) | Web publishers, Node.js, serverless, ad-tech |
| Go | 1 | [`go/`](go/) | Gateways, infrastructure, high-throughput services |
| C#/.NET | 1 | [`csharp/`](csharp/) | Enterprise, CRM, contact-center integrations |
| Java | 2 | [`java/`](java/) | Enterprise JVM services |
| PHP | 2 | [`php/`](php/) | Publisher, WordPress, and Laravel integrations |
| Rust | 2 | [`rust/`](rust/) | Security-sensitive and performance-focused services |
| Ruby | 2 | [`ruby/`](ruby/) | Ruby publisher and SaaS applications |
| Kotlin | 2 | [`kotlin/`](kotlin/) | JVM and Android applications |
| Swift | 3 | [`swift/`](swift/) | Native Apple applications and agent clients |

All SDKs are Apache 2.0 and should be treated as reference implementations
until the package's registry publication and support commitments are enabled.
The full support policy is in [`docs/SDK-ROADMAP.md`](../../docs/SDK-ROADMAP.md).

## Validation and generated models

The canonical sources are [`schemas/`](../../schemas/) and
[`verticals/`](../../verticals/). `tools/generate_sdk_models.py --write` creates
a complete, deterministic `schema-bundle.json` and SHA-256 manifest for every
package. Each SDK also contains generated typed declarations for the envelope,
all seven message payloads, offers, and shared message metadata. Do not edit
files marked `GENERATED FROM schemas/` by hand.

Every runtime validator uses a Draft 2020-12 implementation and accepts the
complete bundle so `$ref` resolution is offline and deterministic. Validate the
raw payload before sending it or handing PII to a CRM. The validator is not a
replacement for authorization, HMAC verification, or durable idempotency.

```bash
python3 tools/generate_sdk_models.py --check
# Release jobs run --write before building packages.
```

The shared test vector and release contract are documented in
[`docs/SDK-ROADMAP.md`](../../docs/SDK-ROADMAP.md#schema-validation-and-code-generation)
and [`test-vectors/sdk/hmac.json`](../../test-vectors/sdk/hmac.json).

## Run the compatibility checks

```bash
python3 tools/check_sdk_schema_sync.py --check
cd python && PYTHONPATH=. python -m unittest discover -s tests -v
cd ../typescript && npm test
cd ../go && go test ./...
cd ../rust && cargo test --locked
cd ../swift && swift test
```

The remaining JVM, .NET, PHP, and Ruby jobs run in
[`.github/workflows/sdk.yml`](../../.github/workflows/sdk.yml) with their
native package managers and schema-validator dependencies.

## Release navigation

Package publication is coordinated by `SDK_VERSION` and the tag workflow:

- [Python / PyPI Trusted Publishing](python/)
- [TypeScript / npm provenance](typescript/)
- [C# / NuGet Trusted Publishing](csharp/)
- [Java and Kotlin / Maven Central](java/)
- [PHP / Packagist tag mirroring](php/)
- [Rust / crates.io Trusted Publishing](rust/)
- [Ruby / RubyGems Trusted Publishing](ruby/)
- [Go / versioned module tags](go/)
- [Swift / Swift Package Manager tags](swift/)

The [tagged release guide](../../docs/RELEASE.md) explains the required
`v<SDK_VERSION>` tag, the workflow gate, published coordinates, and how adopters
verify the signed release manifest and notes.

Read the [release and support policy](../../docs/SDK-ROADMAP.md#package-publication)
before enabling the protected release environment.

---

**Previous:** [Documentation home](../../docs/README.md) · **Next:** [Python SDK](python/)
