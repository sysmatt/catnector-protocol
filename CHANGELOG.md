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

### Unreleased

Conformance checker, in response to feedback from the first real run against
an independent implementation:

- Interactive prompts now describe what to click on the site, instead of
  printing the reference site's internal `/mock/` paths at a person.
- No keystroke is asked for. The checker is connected and listening the whole
  time, so acting before or after a prompt is equally fine — the old
  "press Enter" step made the ordering look significant when it was not.
- **Dropped the `split` refusal check.** It asked a site to send a field the
  checker had not advertised, which §8.3 forbids it from sending — so a
  conforming site could not pass it. Refusal tolerance is now checked by
  declining an ordinary tune, which is what happens routinely in practice
  (the radio is transmitting, the frequency is out of range, the operator has
  automatic tuning off).
- Added a passive check that a site sends **only** the optional fields the
  client advertised (§8.3). It needs no trigger and is the rule that keeps
  deployed clients working as the protocol grows.
- `set_rig` messages are now validated as they arrive: integer hertz, and a
  recognised hamlib mode token.
