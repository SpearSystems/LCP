# Contributing to LCP

LCP is an open standard. Contributions are welcome via public pull
request. No membership, dues, or approval required.

## Process

1. Fork the repo, make your change.
2. Sign the Contributor License Agreement (see [CLA.md](CLA.md)) —
   one-time, irrevocable, grants the project rights to distribute
   contributions under Apache 2.0.
3. Open a pull request.

## What belongs in the core vs. extensions

- **Core** changes (envelope, canonical core, message types, error
  taxonomy, versioning) are breaking-adjacent — they require a MINOR or
  MAJOR version bump and must clear the simplicity budget (a competent
  developer must understand the core in two documents).
- **Vertical schemas** are additive — new vertical = new file in
  `verticals/`, no core change.
- **Extensions** are namespaced `{org}.{division}.{purpose}` and
  registered in [EXTENSION-REGISTRY.md](EXTENSION-REGISTRY.md).

## Anti-capture

Any entity that introduces mandatory participation fees, access
restrictions, proprietary data requirements, or vendor-specific
dependencies into the core LCP specification is acting in violation of
LCP governance. Implementations that impose such restrictions on
conformant LCP participants are themselves non-conformant.
