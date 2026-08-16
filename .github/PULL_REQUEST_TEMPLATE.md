## Pull Request Checklist

- [ ] I have read and agree to the [Contributor License Agreement](../governance/CLA.md)
- [ ] My change follows the [contributing guidelines](../governance/CONTRIBUTING.md)
- [ ] If this is a core change (envelope, message types, error taxonomy, versioning):
  - [ ] I have justified it against the simplicity budget (AGENTS.md rule 2)
  - [ ] I have run the universal audit checklist (SPEC.md §12)
  - [ ] I have bumped the version (MINOR for additive, MAJOR for breaking)
- [ ] If this is a schema change:
  - [ ] I have updated the corresponding JSON Schema file(s)
  - [ ] I have updated or added example payloads
  - [ ] I have updated or added test vectors
- [ ] If this is a new vertical:
  - [ ] Every attribute field is tagged `ping_safe: true` or `ping_safe: false`
  - [ ] No core field names are shadowed in `attributes`
- [ ] I have run the conformance runner and all tests pass:
  ```
  python3 test-vectors/conformance.py --verbose
  ```
- [ ] If AI-assisted: I have disclosed AI involvement (see CONTRIBUTING.md)
<!-- The tracked commit-msg hook strips generated attribution from local commits; see CONTRIBUTING.md. -->

## Description

<!-- Describe what this PR changes and why -->