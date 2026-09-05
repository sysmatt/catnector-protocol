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
| `schema/` | JSON Schema for every message and the capability document. Normative. |
| `catnector_protocol/` | Schema loader, token codec, reference site, conformance checker. |
| `tests/` | End-to-end tests. The reference site must pass the conformance checker. |
| `docs/PLANNING.md` | Design decisions and their reasoning. Not normative. |
| `CHANGELOG.md` | Protocol and document history. |

## Try it

```sh
pip install -e .

# a mock site, speaking the protocol with no database behind it
catnector-mock-server --port 8799

# in another shell: check that site against the specification
catnector-conformance --token "$(python -c \
  'from catnector_protocol.tokens import encode; print(encode("localhost:8799","any"))')" \
  --mock-base http://127.0.0.1:8799
```

Point the same checker at your own endpoint to find out whether it conforms:

```sh
catnector-conformance --token cnx1_... --interactive
```

Most checks run on their own. Four need the site to actually *do* something a
person would click, so `--interactive` prompts for them:

```
  → On the site: press "Tune my rig" next to any spot
    (waiting up to 120s — no need to press anything here)
```

**There is no key to press.** The checker is already connected and listening,
so act whenever you like — before or after the prompt appears makes no
difference. Without `--interactive` (or `--mock-base`) those four report
`SKIP` rather than being assumed to pass.

Note that the run deliberately opens a second session to verify the
one-session-per-account rule, so it will disconnect itself partway through.
Use an account you are not relying on elsewhere.

The mock site also accepts `POST /mock/set_rig` and `POST /mock/follow_state`
so a client under development has something to react to:

```sh
curl -X POST http://127.0.0.1:8799/mock/set_rig \
  -d '{"freq":14195000,"mode":"USB","source":"Following W1ABC"}'
```

## Development

```sh
pip install -e . pytest pytest-asyncio
pytest
```

The end-to-end test runs the conformance checker against the reference site.
It is what keeps the specification, the schemas, the reference implementation
and the checker from drifting apart — if they disagree, the repository finds
out rather than an implementer does.

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
