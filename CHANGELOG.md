# Changelog

Protocol versions are integers. This document's revisions are dated. A
protocol version changes only on a semantic change (SPEC.md §8.4); additive
changes do not.

## Protocol version 1

### 2026-09-03 — initial

First complete specification, with schemas, a reference site and a
conformance checker.

Decisions worth recording, because their reasoning is not obvious from the
result:

- **Plain WebSocket + JSON**, not a vendor channel protocol, so that
  implementing the server side needs only a WebSocket server and a JSON
  parser.
- **Integer hertz everywhere.** Floating-point frequencies produce rounding
  errors that reach operators as "my radio tunes one hertz low."
- **Hamlib mode tokens are normative.** A parallel vocabulary would buy
  nominal independence at the cost of two mapping tables and a class of
  translation bug.
- **`req` forces refusal rather than partial application.** Ignoring an
  unknown field is safe for a filter width and unsafe for a split
  frequency — where it would put an operator on top of the station they
  were trying to work.
- **`follow_state` is display-only and separate from `set_rig`.** It exists
  so a persistent "Following X" indicator can be cleared when a follow ends,
  without the client acquiring any follow logic.
- **`ack`/`nack` exist because `req` refusal needed a way to be expressed.**
  Without them the forward-compatibility rule was unimplementable.
- **Terminal close codes must not auto-reconnect.** Two machines sharing a
  token that both reconnect will supersede each other indefinitely.
- **The checksum separator is `.`, not `_`.** `_` is part of the base64url
  alphabet, which made the token split ambiguous.
