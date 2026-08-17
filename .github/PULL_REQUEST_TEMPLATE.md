## Pull Request Checklist

- [ ] I have read and agree to the [Contributor License Agreement](../governance/CLA.md)
- [ ] My change follows the [contributing guidelines](../governance/CONTRIBUTING.md)
- [ ] I read the [LEP process](../governance/LEP.md) and classified this change:
  - [ ] This is not LEP material; the exemption and reason are stated below, or
  - [ ] This is a material change and I linked the applicable **Accepted** LEP and registry row below
  - [ ] If this changes an LEP record, I ran `python tools/check_lep_registry.py`
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
<!-- Run `python3 tools/setup_git_hooks.py` once; the hook strips generated tool attribution while preserving human co-authors. -->

## LEP / exemption reference

<!-- Link the LEP and registry row, or explain why the change is exempt. Draft or Review LEPs do not authorize implementation. -->

## Description

<!-- Describe what this PR changes and why -->
