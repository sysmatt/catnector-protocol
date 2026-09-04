# Catnector Protocol — Project Planning

Status: **`SPEC.md` v1 drafted** (2026-09-03). Governance and licensing are
settled. Remaining work is the two LICENSE files, the reference mock server,
and the conformance checker.

**The protocol definition belongs in this repo — in `SPEC.md`, in full.**
That is the repo's deliverable and the thing every implementation is written
against.

**This planning document is not that file and must not become a draft of
it.** It covers licensing, governance, what artifacts ship, and what is
still undecided. A planning doc that also carried a draft protocol would put
two drifting copies inside one repo — precisely the problem `hamqsy` and
`catnector` already have between them.

`SPEC.md` now exists and holds the protocol. `catnector`'s planning doc
still carries its own copy of the design in §3, §5.1, §6 and §8.4; reducing
that to a pointer is the reconciliation described in §7.

## 1. What this repo is

The open specification for the protocol catnector speaks to a spotting site,
plus the two artifacts that make the specification adoptable:

- the normative spec document,
- a **reference mock server**, and
- a **conformance checker** a site can run against its own endpoint to learn
  whether it is actually compatible.

The reference server and checker were placed here rather than in `catnector`
deliberately (catnector `docs/PLANNING.md` §3.5): they are deliverables of
the spec, not fixtures of one client. Catnector's CI consumes the mock
server, so the reference implementation is exercised continuously instead of
rotting alongside the document.

## 2. Relationship to catnector and HamQSY

- **`catnector`** — the GPLv3 desktop client. The first implementation of
  the client side, not the definition of it.
- **`hamqsy`** — the reference implementation of the *server* side. Runs the
  endpoint catnector talks to by default.
- **This repo** — the definition both of those implement, and which any
  other site can implement with no involvement from HamQSY.

The protocol carries catnector's name because catnector is the client that
motivated it, not because the protocol is catnector-proprietary.

## 3. Licensing (settled)

**Split, because this repo holds two different kinds of thing:**

| Content | License |
|---|---|
| Spec document, examples, prose | **CC-BY 4.0** |
| Reference mock server, conformance checker, schemas | **Apache-2.0** |

The CC-BY decision was originally made when this repo was going to hold only
a document. Once it also holds code, CC-BY alone becomes actively wrong:
it is a content license with no patent grant and no software warranty
disclaimer, and Creative Commons itself advises against using it for
software.

More importantly it would work *against* the goal that motivated it. The
point of a permissive spec license was friction-free adoption — but an
adopting site's most likely first move is to copy the reference server as a
skeleton, and an ambiguously-licensed reference implementation reintroduces
exactly the hesitation CC-BY was chosen to remove.

**Apache-2.0 rather than MIT** for the explicit patent grant, which matters
more for a protocol than for an ordinary library. **Deliberately not GPL:**
catnector's copyleft is right for a distributed end-user application and
wrong for a reference implementation whose whole purpose is to be vendored
into other people's stacks.

Two LICENSE files, with the scope of each stated plainly in the README so
nobody has to guess which covers what.

## 4. Governance (settled)

**Governed by the creator: Matt Hoskins, K2TTA.** Changes are proposed via
issues; HamQSY runs the reference server implementation; there is no
committee and no formal standards process.

This is written down not because it is elaborate but because leaving it
unstated does damage in both directions — it either over-promises a
standards body that does not exist, or leaves a site considering adoption
unsure whether it is buying into someone's private format. Neither is true,
and saying so costs one paragraph.

## 5. Wire decisions

**All wire decisions now live in `SPEC.md`.** They are not restated here,
for the reason given at the top of this document. Recorded only as decision
history, with pointers:

| Decision | Where |
|---|---|
| Frequency as integer hertz, never floats | `SPEC.md` §6.1 |
| Hamlib mode tokens normative; unknown mode refused, never guessed | `SPEC.md` §6.1 |
| Display-only `follow_state`, required in v1 | `SPEC.md` §7.5 |
| `ack`/`nack` with reason codes — the wire mechanism `req` refusal needed | `SPEC.md` §7.6 |
| Bearer token in the `Authorization` header, not a query parameter | `SPEC.md` §5.2 |
| `Sec-WebSocket-Protocol: catnector.v1` for version selection | `SPEC.md` §5.3 |
| Heartbeat: client pings every 10 s, five misses is dead, site stales at 60 s | `SPEC.md` §10 |
| Timestamps as integer UTC epoch milliseconds | `SPEC.md` §6.1 |
| Coalesce inbound control, never apply a backlog in sequence | `SPEC.md` §9.2 |

Two of these were gaps rather than choices, found while drafting:

- **`ack`/`nack` did not exist.** The forward-compatibility rules required a
  client to *refuse* a message carrying an unsupported `req` field, but no
  message existed with which to say so. Without it the rule was
  unimplementable.
- **`follow_state` did not exist.** `source` on a control message is
  per-message, so a persistent "Following W1ABC" indicator had no way to
  learn that a follow had ended.

Both had to be in v1 — each would otherwise have been a major version bump.

## 6. Conformance checker

A CLI taking an endpoint and a token, running a scripted suite: handshake
and capability negotiation, telemetry interval honored, `set_rig` round
trip, `req`-refusal behavior, and close-code behavior including session
supersede. Emits a pass/fail report.

**Server-side conformance first.** That is the adoption blocker — a site
needs to know its endpoint is correct before it can offer it to users. A
client-side conformance mode can follow later.

## 7. Reconciliation with the other planning docs

Today the protocol design is described in `catnector`'s planning doc, and an
older, now partly superseded copy of the same material sits in `hamqsy`'s.
That is two copies plus this document's pointers to them.

**Once `SPEC.md` exists it becomes the single normative source** — living
here, where a site implementing the protocol can read it without cloning a
client or a Laravel app. Both the `catnector` and `hamqsy` planning docs then
shrink to a short pointer at it, keeping only the *implementation-facing*
notes specific to each side. Until then this repo points outward rather than
copying, so the number of divergent copies does not grow from two to three.

Known drift to resolve at that point: `hamqsy`'s copy predates the plain-WSS
decision (catnector §3.1), the forward-compatibility rules (§3.4), the close
codes (§5.1) and the rig-control layer (§8).

## 8. Repo shape (settled)

| | |
|---|---|
| **Spec format** | Markdown as the normative document, plus normative JSON Schema files for message shapes so implementers can validate mechanically rather than reading prose carefully. AsyncAPI was considered and rejected: the "correct" answer for event-driven APIs, but heavy for an audience that would rather read Markdown than learn a spec format. |
| **Contents** | `SPEC.md`, `schema/*.json`, `reference/`, `conformance/`, `examples/`, `CHANGELOG.md`. |
| **Versioning** | Protocol versions are integers (`v1`, `v2`); the document gets dated revisions. Keeping them separate stops a typo fix from looking like a wire change. |
| **Reference server language** | Python, so `catnector` consumes it directly as a CI fixture. Mild tension — a Python-only reference could read as "the protocol is Pythonic" — mitigated by the transcripts being ground truth. |
| **Known implementations** | A list in the README. Trivial, and what makes an open protocol look alive rather than aspirational. |

`SPEC.md` Appendices A and B are annotated transcripts of a full session and
of refused messages. They are the seed of `examples/`, and are likely the
single most useful artifact for someone writing an implementation.

## 9. Next steps

- Add the two LICENSE files (§3), with the scope of each stated in the
  README, and rewrite the README to point at `SPEC.md`.
- Extract `schema/*.json` from `SPEC.md` §6 and §7, and wire schema
  validation into the reference server so the document and the schemas
  cannot drift apart silently.
- Build the reference mock server, then the conformance checker (§6).
- Reconcile the `catnector` and `hamqsy` planning docs against `SPEC.md`
  (§7) — `catnector` §3/§5.1/§6/§8.4 become pointers; `hamqsy`'s copy needs
  more work, as it predates the plain-WSS decision, the forward-compatibility
  rules, the close codes and the rig-control layer.
- Review `SPEC.md` §12 (client safety requirements) against `catnector` §10
  once catnector's implementation exists — the spec states them as
  requirements on any client, and the two must not drift.
