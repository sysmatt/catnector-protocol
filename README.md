# catnector-protocol

The open specification for the **Catnector Protocol** — how a website stages
an amateur radio operator's transceiver (frequency and mode) over an
authenticated connection to software running on the operator's own computer.

**→ [`SPEC.md`](SPEC.md) — the specification. Version 1.**

Its motivating use case is spot-following: a spotting site lists stations on
the air, an operator clicks to tune their radio to one, and optionally keeps
tracking that station as it moves.

**The protocol never transmits.** There is no PTT, keying or power-control
message, and a conforming client must not key the transmitter as a result of
anything in this specification. A compromised token can retune a radio; it
cannot transmit with it. That limit is permanent — see `SPEC.md` §1.2.

## Who implements this

| | |
|---|---|
| [`catnector`](https://github.com/sysmatt/catnector) | Reference **client**. Desktop app, Python + PySide6, drives the rig via hamlib. GPLv3. |
| [`hamqsy`](https://github.com/sysmatt/hamqsy) | Reference **server**. The site whose needs the protocol grew from. |

Neither is privileged by the specification. Any site may implement the
server side and interoperate with any conforming client, with no involvement
from HamQSY.

### Known implementations

- HamQSY — reference server implementation
- catnector — reference client implementation

Open a pull request to add yours.

## Repository contents

| Path | What |
|---|---|
| `SPEC.md` | The normative specification. |
| `schema/` | JSON Schema for message shapes. Normative. |
| `reference/` | Reference mock server. |
| `conformance/` | Checker a site can run against its own endpoint. |
| `docs/PLANNING.md` | Design decisions and their reasoning. Not normative. |

## Licensing

Two licenses, by content type:

| Content | License |
|---|---|
| `SPEC.md`, `docs/`, prose and examples | **CC-BY 4.0** — see `LICENSE-DOCS` |
| `schema/`, `reference/`, `conformance/` | **Apache-2.0** — see `LICENSE-CODE` |

The specification is permissively licensed so that implementing it carries
no legal hesitation. The code is Apache-2.0 — deliberately not copyleft —
because an adopting site's first move is usually to copy the reference
server as a skeleton, and that should be friction-free.

## Governance

Governed by the creator, Matt Hoskins, K2TTA. Changes are proposed via
issues. There is no committee and no formal standards process — stated
plainly so that nobody has to guess whether adopting this means buying into
someone's private format or a standards body that does not exist.
