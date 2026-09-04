# Catnector Protocol — Version 1

**Status:** draft
**Protocol version:** 1
**Document revision:** 2026-09-03
**License:** CC-BY 4.0 (this document). Code in this repository is
Apache-2.0 — see `LICENSE-CODE`.

---

## 1. Introduction

The Catnector Protocol lets a website stage an amateur radio operator's
transceiver — set its frequency and mode — over an authenticated, persistent
connection to software running on the operator's own computer.

Its motivating use case is spot-following: a spotting site shows a list of
stations on the air, and an operator clicks to tune their radio to one of
them, optionally continuing to track that station as it moves.

The protocol is open. Any site may implement the server side; any client may
implement the client side. `catnector` is the reference client and `hamqsy`
the reference server, but neither is privileged by this specification.

### 1.1 Scope

This document defines:

- how a client discovers and authenticates to a site,
- the message framing and envelope,
- the messages exchanged in both directions,
- how the protocol evolves without breaking deployed clients.

### 1.2 Non-goals

**Transmission control is out of scope and will not be added.** The protocol
has no PTT, keying, or power-control message, and a conforming client MUST
NOT key the transmitter as a result of any message defined here. A
compromised token or a hostile server can retune a radio; it cannot transmit
with it. This is a deliberate and permanent limit on the blast radius.

Also out of scope: logging, spot submission, chat, and any site-side data
model. Those are each site's own business.

### 1.3 Conformance language

The key words MUST, MUST NOT, REQUIRED, SHOULD, SHOULD NOT, MAY and OPTIONAL
are to be interpreted as described in RFC 2119.

---

## 2. Terminology

| Term | Meaning |
|---|---|
| **client** | Software on the operator's computer that controls the radio. |
| **site** / **endpoint** | The server implementing this protocol. |
| **session** | One authenticated WebSocket connection between them. |
| **rig** | The transceiver the client controls. |
| **operator** | The licensed human. Always the one who transmits. |
| **follow** | A site-side relationship causing one operator's tuning to track another's. All follow bookkeeping is server-side; see §7.3. |

---

## 3. Connection lifecycle

```
  ┌────────┐                                  ┌──────┐
  │ client │                                  │ site │
  └───┬────┘                                  └──┬───┘
      │ 1. decode token → host                   │
      │                                          │
      │ 2. GET https://host/.well-known/catnector│
      │─────────────────────────────────────────>│
      │<─────────────────────────────────────────│  capability document
      │                                          │
      │ 3. WSS connect (Bearer token,            │
      │    Sec-WebSocket-Protocol: catnector.v1) │
      │─────────────────────────────────────────>│
      │                                          │
      │ 4. hello ───────────────────────────────>│
      │<──────────────────────────────── welcome │
      │                                          │
      │ 5. report ─────────────────────────────> │  (throttled telemetry)
      │<──────────────────────────────── set_rig │  (control push)
      │    ack / nack ─────────────────────────> │
      │<─────────────────────────── follow_state │  (display only)
      │    ping ───────────────────────────────> │
      │<─────────────────────────────────── pong │
      │                                          │
      │ 6. close (see §11 for close codes)       │
```

---

## 4. Discovery

### 4.1 Tokens

A client is configured with a single token, which carries the endpoint
hostname so that no separate server-URL entry step is required.

```
cnx1_<payload>_<check>
```

- `payload` — base64url, unpadded, of the UTF-8 encoding of a JSON object:

  ```json
  { "h": "hamqsy.app", "t": "<site-issued bearer token>" }
  ```

  `h` is a hostname, optionally with a port (`"lab.example:8443"`). `t` is
  opaque to this specification — the site's own bearer token, in whatever
  form the site issues.

- `check` — the first 6 characters of the base64url, unpadded, encoding of
  `SHA-256(payload)`, where `payload` is the ASCII text above.

A client MUST verify `check` before use and MUST report a damaged token
distinctly from a rejected one. Truncation by mail clients and chat windows
is the common failure, and "that token looks damaged" is a far more
actionable message than "authentication failed."

**The encoding is not encryption.** A token is a credential of equivalent
sensitivity to a password, MUST be stored as such, and MUST NOT be logged.

### 4.2 Capability document

The client MUST fetch `https://<h>/.well-known/catnector` before connecting.
The response is `application/json`:

```json
{
  "protocol_versions": [1],
  "websocket_url": "wss://hamqsy.app/catnector/v1",
  "telemetry_interval_ms": 1000,
  "features": ["follow_state"],
  "site_name": "HamQSY",
  "site_url": "https://hamqsy.app"
}
```

| Field | Req. | Meaning |
|---|---|---|
| `protocol_versions` | yes | Protocol versions the site accepts, as integers. |
| `websocket_url` | yes | Where to connect. MUST be `wss:` except as permitted by §5.1. |
| `telemetry_interval_ms` | no | Desired minimum interval between client reports. Default 1000. |
| `features` | no | Optional features the site supports (§8.3). |
| `site_name`, `site_url` | no | For display, so the operator can see which site they are connected to. |

Keeping the WebSocket URL here rather than inside the token lets a site move
its endpoint without reissuing every token it has ever handed out.

---

## 5. Transport

The session is a WebSocket carrying JSON text frames, one message per frame.

This is deliberately a plain WebSocket rather than any vendor channel
protocol: implementing the server side requires only a WebSocket server and
a JSON parser, which exist in every language.

### 5.1 TLS

The WebSocket URL MUST use `wss:`, and the capability document MUST be
fetched over `https:`.

**Sole exception:** hosts `localhost`, `127.0.0.1` and `::1` MAY use `ws:`
and `http:`. This mirrors the browser secure-context rule and exists so that
the reference server and conformance checker can run without minting
certificates. Clients MUST NOT offer a general "disable TLS" option.

### 5.2 Authentication

The client MUST present the site token in an `Authorization` header on the
WebSocket upgrade request:

```
Authorization: Bearer <t from the token payload>
```

Note that this is the inner `t` value, not the whole `cnx1_…` wrapper.

Consequence, accepted deliberately: browsers cannot set headers on WebSocket
connections, so a browser-based client is not possible without a
query-parameter fallback that this version does not define. Clients here are
native applications, and keeping credentials out of URLs — where they land
in logs and proxy history — is worth more than browser reach.

### 5.3 Version selection

The client MUST offer the protocol version in the WebSocket subprotocol
header:

```
Sec-WebSocket-Protocol: catnector.v1
```

A site that does not support the offered version MUST fail the upgrade or
close with `4003` (§11). Failing at connect time is preferable to
discovering the mismatch mid-session.

---

## 6. Message envelope

Every message is a JSON object with at least:

| Field | Type | Req. | Meaning |
|---|---|---|---|
| `v` | integer | yes | Protocol major version. `1` for this document. |
| `type` | string | yes | Message type (§7). |
| `id` | string | yes | Unique within the session, assigned by the sender. |
| `re` | string | no | The `id` this message answers. |
| `req` | array of string | no | Field names the receiver MUST understand (§8.2). |
| `ts` | integer | no | UTC milliseconds since the Unix epoch. |

Unknown top-level fields MUST be ignored, subject to §8.

### 6.1 Data types

**Frequency is an integer number of hertz.** Never a float, never
kilohertz, never a string. `14074000` is 14.074 MHz. Floating-point
frequencies produce rounding errors that surface to operators as "my radio
tunes one hertz low," and are not permitted anywhere in this protocol.

**Passband is an integer number of hertz**, the filter width.

**Mode is a hamlib mode token**, uppercase: `USB`, `LSB`, `CW`, `CWR`,
`RTTY`, `RTTYR`, `AM`, `FM`, `WFM`, `AMS`, `PKTLSB`, `PKTUSB`, `PKTFM`,
`ECSSUSB`, `ECSSLSB`, `FAX`, `SAM`, `SAL`, `SAH`, `DSB`.

Hamlib's vocabulary is normative because both ends deal with hamlib-driven
radios in practice; a parallel vocabulary would buy nominal independence at
the cost of two mapping tables and a class of translation bug. A receiver
that does not recognise a mode token MUST refuse the message (§7.4) and MUST
NOT substitute a similar mode.

**Timestamps** are integer UTC milliseconds since the Unix epoch.

---

## 7. Messages

### 7.1 `hello` — client to site

First message after the socket opens.

```json
{
  "v": 1, "type": "hello", "id": "c1",
  "client": { "name": "catnector", "version": "0.1.0" },
  "features": ["ack", "follow_state"],
  "rig": { "profile": "FT-991 shack", "health": "ok" }
}
```

`features` is the client's supported optional-feature list (§8.3).
`rig.profile` is the operator-chosen nickname of the active rig profile, for
display on the site. `rig.health` is as in §7.2.

### 7.2 `welcome` — site to client

```json
{
  "v": 1, "type": "welcome", "id": "s1", "re": "c1",
  "session": { "id": "s-01J...", "user": "matt@example.com", "callsign": "K2TTA" },
  "telemetry_interval_ms": 1000,
  "features": ["follow_state"]
}
```

The client MUST display the resolved `callsign` and the site identity to the
operator. **Callsigns are not unique** — sites are not required to verify
them, and one callsign may belong to several accounts (club and contest
operation makes this normal, not anomalous). Clients MUST NOT treat a
callsign as an identifier.

`telemetry_interval_ms` here overrides the capability document.

### 7.3 `report` — client to site

Sent no more often than the negotiated telemetry interval (§9.1).

```json
{
  "v": 1, "type": "report", "id": "c17", "ts": 1788451200123,
  "freq": 14074000, "mode": "USB", "passband": 2400,
  "rig": { "profile": "FT-991 shack", "health": "ok" }
}
```

`rig.health` is one of:

| Value | Meaning |
|---|---|
| `ok` | Rig reachable, values current. |
| `offline` | Rig not reachable — cable, permissions, control software gone. |
| `error` | Rig reachable but misbehaving. |

**Rig health is distinct from session health.** "Connected to the site, rig
offline" is a common and real state, and a site MUST be able to display it
rather than dropping the operator's spot or continuing to show a stale
frequency as though it were live. When health is not `ok`, `freq` and `mode`
carry the last known good values and `ts` is when they were read — so a site
can show their age rather than implying currency.

### 7.4 `set_rig` — site to client

The only control message. It instructs the client to stage the rig.

```json
{
  "v": 1, "type": "set_rig", "id": "s42",
  "freq": 14195000, "mode": "USB", "passband": 2400,
  "source": "Following W1ABC"
}
```

| Field | Req. | Meaning |
|---|---|---|
| `freq` | yes | Target frequency, Hz. |
| `mode` | no | Target mode. Unchanged if absent. |
| `passband` | no | Filter width, Hz. Client MAY ignore. |
| `source` | no | Opaque display text: who caused this and why. |

**A one-shot tune and a follow update are the same message.** The client
cannot distinguish them and does not need to — all follow bookkeeping is
server-side. `source` exists only so the operator can be told why the radio
moved; a client that ignores it behaves correctly.

The client MUST respond with `ack` or `nack` (§7.6). A client MUST NOT apply
a `set_rig` partially: if any part is unacceptable, the whole message is
refused.

Clients apply safety limits of their own before obeying — see §12.

### 7.5 `follow_state` — site to client

Display only.

```json
{ "v": 1, "type": "follow_state", "id": "s43", "following": "W1ABC" }
```

`following` is a display string, or `null` when no follow is active.

This message exists because `source` on `set_rig` is per-message and cannot
keep a persistent "Following W1ABC" indicator honest: when a follow ends by
manual unfollow or by the target un-spotting, no further `set_rig` arrives,
and an indicator driven by the last control message would go on claiming a
follow that is over.

A client MUST NOT derive behaviour from this message — only display. It
acquires no follow state machine, and remains unable to tell a one-shot tune
from a follow update.

Sites MUST send `follow_state` whenever the state changes, including to
`null`.

### 7.6 `ack` and `nack` — client to site

Every `set_rig` MUST be answered.

```json
{ "v": 1, "type": "ack", "id": "c18", "re": "s42" }
```

```json
{ "v": 1, "type": "nack", "id": "c18", "re": "s42",
  "reason": "out_of_range",
  "detail": "21.300 MHz is outside the rig's transmit ranges" }
```

| `reason` | Meaning |
|---|---|
| `unsupported_req` | A field listed in `req` is not understood (§8.2). |
| `unknown_mode` | Mode token not recognised (§6.1). |
| `out_of_range` | Outside the rig's capabilities or the operator's configured limits. |
| `rig_offline` | No rig to command. |
| `manual_mode` | Operator has automatic tuning disabled; offered for manual acceptance instead. |
| `ptt_timeout` | Rig was transmitting and did not stop before the deferral timeout (§12.1). |
| `rejected` | Operator declined, or a client-specific refusal. |

`detail` is human-readable and OPTIONAL. Sites SHOULD surface refusals to
the person who initiated the tune — "that operator's radio could not follow"
is useful; silence is not.

### 7.7 `ping` and `pong`

See §10.

### 7.8 `error` — site to client

An in-band error not severe enough to close the session.

```json
{ "v": 1, "type": "error", "id": "s44", "re": "c17",
  "code": "rate_exceeded", "detail": "reports faster than the negotiated interval" }
```

---

## 8. Forward compatibility

The protocol is expected to gain fields over time. The rules below exist so
that it can, without breaking clients already installed on operators'
computers.

### 8.1 Ignore unknown fields

Receivers MUST ignore top-level and nested fields they do not recognise,
except as required by §8.2. This covers the ordinary case: an older client
dropping a new `passband` field merely gets a slightly wrong filter.

### 8.2 `req` — fields that must be understood

Ignoring an unknown field is not always safe. The motivating case is split
operation:

> A site sends `{"freq": 14195000, "mode": "USB", "split": {"tx_freq":
> 14200000}}` to a client that predates `split`. The client ignores `split`,
> tunes to 14195000, and the operator transmits **on top of the DX station
> they were trying to work.**

Silence would have been safe. *Partial application* was not.

A message MAY therefore carry `req`, listing fields the receiver MUST
understand:

```json
{ "v": 1, "type": "set_rig", "id": "s42", "req": ["split"],
  "freq": 14195000, "mode": "USB", "split": { "tx_freq": 14200000 } }
```

A receiver that does not understand every field named in `req` MUST refuse
the **entire** message — `nack` with reason `unsupported_req` — and MUST NOT
apply any part of it.

### 8.3 Capability negotiation

The client advertises `features` in `hello`; the site advertises `features`
in `welcome` and in its capability document.

**A site MUST NOT send a message depending on an optional feature the client
did not advertise.** This, not §8.2, is the primary mechanism: a site that
knows the client is split-unaware simply never sends split, and `req` never
fires. §8.2 is the safety net for bugs and version skew.

Feature names are lowercase strings. This version defines `ack` and
`follow_state`; both SHOULD be supported by all implementations, and are
listed explicitly so that a future client omitting one is handled
gracefully rather than by assumption.

### 8.4 Version changes

Additive changes — new optional fields, new feature names, new `nack`
reasons — do not change the version. The major version increments only on a
semantic change: altered field meaning, removed field, or changed required
behaviour.

Sites SHOULD support the current and previous major versions.

### 8.5 Reserved for future versions

These field names are reserved and MUST NOT be repurposed: `split`,
`vfo`, `antenna`, `memory`, `bandstack`.

---

## 9. Rate limiting

### 9.1 Client to site

Clients MUST NOT send `report` more often than
`telemetry_interval_ms`, defaulting to 1000 ms.

This is enforced **client-side**. A conforming client does not flood the API
and rely on the site to reject the excess; it does not send the excess. A
site MAY additionally enforce this, and SHOULD respond with `error`
(`rate_exceeded`) rather than closing the session.

The interval is server-configurable so that a site's load characteristics,
or a differentiated service tier, can change it with no client release.

Reports SHOULD be sent when values change; a client MAY send periodic
reports while idle to demonstrate liveness, but the heartbeat (§10) is the
proper mechanism for that.

### 9.2 Site to client

A client MUST protect its rig from excessive control traffic, whether from a
buggy site or a hostile one.

Clients MUST **coalesce** rather than reject: where several `set_rig`
messages arrive within the client's apply window, the client applies the
most recent and discards the intermediates, acknowledging each. Applying
every message in sequence makes the radio chase positions that are already
stale.

The apply rate is bounded by the rig itself — a serial-attached transceiver
may need 100–300 ms per command — and clients SHOULD discover this at
runtime rather than assuming. Clients SHOULD additionally impose a ceiling
of roughly one applied change per second.

---

## 10. Heartbeat

WebSocket control frames are handled inconsistently by intermediaries, so
liveness is checked in-band. "The connection is silently dead" is precisely
the failure that matters when an operator believes their radio is following
someone.

- The client MUST send `ping` every **10 seconds** of otherwise-idle time.
- The site MUST answer with `pong` carrying `re` set to the ping's `id`.
- The client MUST treat **five consecutive unanswered pings** as a dead
  connection and reconnect per §11.
- The site SHOULD treat **60 seconds** with no frame from a client as a
  stale session.

The interval is short because the cost is trivial — two small frames every
ten seconds, against telemetry already flowing at up to one report per
second — and the thing being detected is an operator believing their radio
is following someone when the connection is gone. Five misses before
declaring death, rather than two, keeps a brief network hiccup from tearing
down a working session. The site's 60-second threshold is deliberately more
lenient than the client's 50-second one, so the client notices first and
reconnects rather than being declared stale underneath itself.

These values may be revisited if they prove costly at scale.

```json
{ "v": 1, "type": "ping", "id": "c99" }
{ "v": 1, "type": "pong", "id": "s99", "re": "c99" }
```

A stale session is informational. A site MUST NOT end a follow relationship
because a session went stale — a sleeping laptop is not an intention to stop
being followed, and the relationship MUST resume cleanly when telemetry
returns, with no re-subscription.

---

## 11. Session termination and close codes

**One live session per account.** A site MUST permit only one active session
per user account, regardless of token or machine. When a new session
authenticates while one is active, the site MUST accept the new session and
close the older one with `4001`.

Application close codes:

| Code | Meaning | Client behaviour |
|---|---|---|
| `4001` | Session superseded by a newer one | **Terminal.** Explain to the operator; manual reconnect only. |
| `4002` | Token invalid or revoked | **Terminal.** Prompt for a new token. |
| `4003` | Protocol version unsupported | **Terminal.** Prompt to upgrade. |
| `4004` | Server shutting down or under maintenance | Reconnect with backoff. |
| others / network loss | Transient | Reconnect with backoff. |

**Clients MUST NOT automatically reconnect after a terminal close code.**

This is not politeness. Two computers sharing one token that both
auto-reconnect will supersede each other indefinitely, each kicking the
other, forever. Every implementation gets this wrong exactly once — in the
field, on someone's two machines — unless the specification forbids it.

On `4001` a client MUST tell the operator plainly why the session ended,
rather than showing an unexplained disconnected state.

Transient reconnection SHOULD use exponential backoff with jitter.

---

## 12. Client safety requirements

A site instructs; the client decides. These requirements exist because the
protocol lets a website move physical equipment belonging to someone who is
not watching the screen at that moment.

### 12.1 Transmission

A client MUST NOT key the transmitter (§1.2).

If the rig reports that it is transmitting, the client MUST defer the tune
until transmission ends, and MUST abandon it after a timeout rather than
applying it late — answering `nack` with `ptt_timeout`.

Retuning a VFO during transmission can drive an amplifier or antenna tuner
matched for the previous band. Well-designed radios protect themselves; that
is not a reason to move a radio while it is keyed.

### 12.2 Range limits

A client MUST refuse a frequency outside the rig's own capabilities, and
MUST refuse frequencies outside any additional ranges the operator has
configured.

**Clients MUST NOT implement licence-privilege checking.** Worldwide
privilege data is large, drifts constantly, and a wrong answer is worse than
no answer. The operator is licensed and is the only party who transmits;
responsibility for where they transmit remains theirs. Display prominently,
prohibit minimally.

### 12.3 Disclosure

A client MUST make visible, whenever the rig is moved by a `set_rig`, that
the change was remote and what `source` said about its origin. A radio that
retunes itself must always be able to say who moved it.

A client MUST provide a clearly visible means of disabling automatic tuning
and of disconnecting.

---

## 13. Security considerations

- **Tokens are credentials.** They MUST be stored with restrictive
  permissions, MUST NOT be logged, and MUST NOT be placed in URLs.
- **The token names the host it is sent to.** A malicious token directs a
  client at a server of the attacker's choosing. The blast radius is bounded
  by §1.2 and §12: the worst outcome is a retuned receiver, not a
  transmission. Clients SHOULD show the operator which host they are about
  to connect to when a token is added.
- **Callsigns are self-asserted** and MUST NOT be used for authorization.
- **Sites see live operating patterns** — frequency and mode, continuously,
  tied to an identity. Sites SHOULD treat this as personal information and
  SHOULD retain no more of it than they need.
- **A hostile site can retune a radio repeatedly.** §9.2 and §12 bound the
  damage; a client SHOULD make disconnection immediate and obvious.

---

## 14. Conformance

An implementation conforms if it satisfies every MUST in this document for
the side it implements.

The `conformance/` checker in this repository exercises a site endpoint
against: discovery, token handling, version selection, handshake and
capability negotiation, telemetry interval enforcement, `set_rig` round trip
with `ack`/`nack`, `req` refusal behaviour, `follow_state` transitions
including clearing, heartbeat, and session supersede with close code `4001`.

Passing the checker is necessary, not sufficient. A site is also expected to
honour §10's rule that staleness does not end a follow, which the checker
cannot observe from outside.

---

## Appendix A — Example session

```
→  GET https://hamqsy.app/.well-known/catnector
←  { "protocol_versions": [1],
     "websocket_url": "wss://hamqsy.app/catnector/v1",
     "telemetry_interval_ms": 1000,
     "features": ["follow_state"],
     "site_name": "HamQSY" }

→  WSS connect
   Authorization: Bearer sk_live_9f2c…
   Sec-WebSocket-Protocol: catnector.v1

→  {"v":1,"type":"hello","id":"c1",
    "client":{"name":"catnector","version":"0.1.0"},
    "features":["ack","follow_state"],
    "rig":{"profile":"FT-991 shack","health":"ok"}}

←  {"v":1,"type":"welcome","id":"s1","re":"c1",
    "session":{"id":"s-01J8","user":"matt@example.com","callsign":"K2TTA"},
    "telemetry_interval_ms":1000,
    "features":["follow_state"]}

→  {"v":1,"type":"report","id":"c2","ts":1788451200000,
    "freq":14074000,"mode":"USB","passband":2400,
    "rig":{"profile":"FT-991 shack","health":"ok"}}

←  {"v":1,"type":"set_rig","id":"s2",
    "freq":14195000,"mode":"USB","source":"Tune to W1ABC"}

→  {"v":1,"type":"ack","id":"c3","re":"s2"}

←  {"v":1,"type":"follow_state","id":"s3","following":"W1ABC"}

→  {"v":1,"type":"report","id":"c4","ts":1788451201000,
    "freq":14195000,"mode":"USB","passband":2400,
    "rig":{"profile":"FT-991 shack","health":"ok"}}

←  {"v":1,"type":"set_rig","id":"s4",
    "freq":14195500,"mode":"USB","source":"Following W1ABC"}

→  {"v":1,"type":"ack","id":"c5","re":"s4"}

   … W1ABC goes QRT; the site clears the follow …

←  {"v":1,"type":"follow_state","id":"s5","following":null}

   … the operator's cable is unplugged …

→  {"v":1,"type":"report","id":"c6","ts":1788451260000,
    "freq":14195500,"mode":"USB",
    "rig":{"profile":"FT-991 shack","health":"offline"}}

   … the operator starts catnector on a second computer …

←  close 4001 "session superseded"
   (client explains and does NOT reconnect)
```

## Appendix B — Refused messages

Unknown mode:

```
←  {"v":1,"type":"set_rig","id":"s9","freq":14074000,"mode":"OLIVIA"}
→  {"v":1,"type":"nack","id":"c9","re":"s9","reason":"unknown_mode",
    "detail":"mode OLIVIA is not a recognised token"}
```

Required field not understood:

```
←  {"v":1,"type":"set_rig","id":"s10","req":["split"],
    "freq":14195000,"mode":"USB","split":{"tx_freq":14200000}}
→  {"v":1,"type":"nack","id":"c10","re":"s10","reason":"unsupported_req",
    "detail":"this client does not implement split"}
```

Nothing is applied in either case.
