# LCP Trademark and Usage Policy

LCP (Lead Context Protocol) is an open standard published under Apache 2.0.
The specification, schemas, and reference implementations are free to use,
implement, and distribute without payment, approval, or membership.

## "LCP Compliant" Claims

Conformance is **self-declared** (SPEC.md §11). Any implementation may
claim conformance at a specific tier (L1, L2, L3) if it passes the
corresponding conformance tests in `test-vectors/`.

### Permitted claims

- "LCP-L1 compliant" — passes all L1 test vectors.
- "LCP-L2 compliant" — passes all L1 + L2 test vectors.
- "LCP-L3 compliant" — passes all L1 + L2 + L3 test vectors.
- "Implements LCP v1.0" — implements the v1.0 specification.

### Impermissible claims

- **Claiming a higher tier than achieved.** An implementation that passes
  L1 only may not claim "LCP-L2 compliant."
- **Claiming endorsement.** No implementation may claim official
  endorsement, certification, or approval by the LCP project or its
  maintainers. Conformance is self-declared, not certified.
- **Using LCP branding to imply exclusivity.** No implementation may claim
  to be "the only LCP-compliant platform" or similar exclusivity language.
- **Vendor lock-in claims.** No implementation may claim that LCP requires
  a specific vendor's product, service, or membership.

## Name and Logo

- "LCP" and "Lead Context Protocol" may be used to refer to the protocol
  and to indicate conformance, per the rules above.
- The LCP project does not currently register a trademark. If a trademark
  is registered in the future, this policy will be updated to reflect the
  registration while preserving the open-usage rights above.
- Implementations may use "LCP" in product names (e.g. "Acme LCP Gateway")
  as long as conformance claims are accurate and the protocol itself is not
  misrepresented.

## Enforcement

Inaccurate conformance claims undermine the standard's credibility. The
maintainers may publicly correct inaccurate claims and request that
implementations remove non-compliant claims. This is a community norm, not
a legal enforcement mechanism.