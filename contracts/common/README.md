# COMMON-ENVELOPE

`COMMON-ENVELOPE/v1` is the shared, payload-agnostic identity contract for V5 runtime artifacts.  Business payloads are validated by their own schemas after this envelope is validated.

`content_hash` is SHA-256 of canonical UTF-8 JSON for `{envelope, payload}`, with `envelope.content_hash` and `envelope.storage_locator` omitted.  Omitting the self-referential hash is necessary to make the value calculable; omitting the locator makes location migration identity-neutral.  Canonical JSON sorts object keys, preserves array order, emits no whitespace, and rejects NaN/infinity.

The runtime registry is local-only beneath `V5_RUNTIME_ROOT`; no registry, audit event, or payload belongs in Git.  `released` is intentionally refused by the EAS-15 registry.
