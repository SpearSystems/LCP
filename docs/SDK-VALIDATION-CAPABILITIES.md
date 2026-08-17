# SDK validator capabilities

Every published SDK implements the same language-neutral validation
contract. Agreement is enforced by the shared corpus in
`test-vectors/sdk/validation-corpus.json`: each SDK runs the corpus in CI and
must match the expected pass/fail outcome of every fixture. The reference
outcomes are pinned by the Python validator in
`tools/tests/test_validation_corpus.py`.

| Contract check | Corpus fixtures | Python | TypeScript | Go | C# | Java | PHP | Rust | Ruby | Kotlin | Swift |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Draft 2020-12 envelope validation (structure + version) | sdk-001, sdk-003 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Message-type dispatch (closed enum, unknown type rejected) | sdk-004 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Payload required fields per message type | sdk-010, sdk-013 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Complete vertical validation for lead/call/post attributes | sdk-011 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Strict ping allowlist plus vertical `ping_safe` enforcement | sdk-002, sdk-012 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Offer validation | sdk-013, sdk-014 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Format assertions (uuid, date-time, date, email, uri) | sdk-005..sdk-009 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Offline `$ref` resolution from the bundled schema set | all fixtures | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

Schema names are resolved through preloaded registries in every SDK; no
implementation interpolates attacker-controlled names into filesystem paths.

## Where each SDK runs the corpus

| SDK | Harness | CI invocation |
| --- | --- | --- |
| Python | `implementations/sdk/python/tests/test_validation_corpus.py` | `unittest discover` (sdk workflow) |
| TypeScript | `implementations/sdk/typescript/test/validation-corpus.test.mjs` | `npm test` |
| Go | `TestSharedValidationCorpus` in `lcp_test.go` | `go test ./...` |
| C# | `tests/Program.cs` | `dotnet run --project tests/LcpSdk.Tests.csproj` |
| Java | `ValidationCorpusTest.java` | `mvn test` |
| Kotlin | `ValidationCorpusTest.kt` | `gradle test` |
| PHP | `tests/shared_vector.php` | `php tests/shared_vector.php` |
| Rust | `shared_validation_corpus` in `src/lib.rs` | `cargo test --locked` |
| Ruby | `test/shared_vector_test.rb` | `ruby test/shared_vector_test.rb` |
| Swift | `testSharedValidationCorpus` in `SharedVectorTests.swift` | `swift test` (requires `LCP_REPO_ROOT`) |

## Keeping this report honest

This table is hand-maintained. `tools/tests/test_sdk_capability_doc.py` guards
against staleness by requiring the document to exist, name every published
SDK, and reference the shared corpus. If a validator stops passing a corpus
check, its CI job turns red — the corpus, not this page, is the source of
truth.
