"""Conformance checker for a Catnector Protocol *site* endpoint.

Point it at a site and it reports whether that site behaves as SPEC.md
requires. A site should be able to run this against its own staging endpoint
and know it is compatible, rather than finding out through somebody's radio.

Server-side conformance only. A client-side mode may follow.

Run::

    catnector-conformance --token cnx1_...
    catnector-conformance --token cnx1_... --mock-base http://127.0.0.1:8799

``--mock-base`` lets the checker trigger the site-initiated behaviour a human
would otherwise cause by clicking "Tune my rig". Without it, and without
``--interactive``, those checks are skipped rather than guessed at.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from . import schemas, tokens

SUBPROTOCOL = "catnector.v1"
PROTOCOL_VERSION = 1

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
_COLOUR = {PASS: "\033[32m", FAIL: "\033[31m", WARN: "\033[33m", SKIP: "\033[90m"}


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""
    spec: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", spec: str = "") -> Result:
        result = Result(name, status, detail, spec)
        self.results.append(result)
        return result

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    def render(self, colour: bool = True) -> str:
        lines = []
        for r in self.results:
            tag = f"{_COLOUR[r.status]}{r.status:<4}\033[0m" if colour else f"{r.status:<4}"
            line = f"  {tag}  {r.name}"
            if r.spec:
                line += f"  [{r.spec}]"
            lines.append(line)
            if r.detail:
                lines.append(f"          {r.detail}")
        lines.append("")
        lines.append(f"  {self.count(PASS)} passed, {self.count(FAIL)} failed, "
                     f"{self.count(WARN)} warnings, {self.count(SKIP)} skipped")
        return "\n".join(lines)

    @property
    def ok(self) -> bool:
        return self.count(FAIL) == 0


def _msg(mtype: str, mid: str, **fields: Any) -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "type": mtype, "id": mid, **fields}


class Checker:
    def __init__(self, token: tokens.Token, mock_base: str | None = None,
                 interactive: bool = False, timeout: float = 10.0) -> None:
        self.token = token
        self.mock_base = mock_base.rstrip("/") if mock_base else None
        self.interactive = interactive
        self.timeout = timeout
        self.report = Report()
        self.wellknown: dict[str, Any] = {}
        self.ws_url = ""
        self._n = 0

    def nid(self) -> str:
        self._n += 1
        return f"chk{self._n}"

    # ------------------------------------------------------------- helpers

    async def _recv(self, ws, want: str | None = None, timeout: float | None = None):
        """Read the next schema-valid message, optionally of a given type."""
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                raw = await asyncio.wait_for(ws.receive(), remaining)
            except asyncio.TimeoutError:
                return None
            if raw.type is aiohttp.WSMsgType.TEXT:
                message = json.loads(raw.data)
                schemas.validate_message(message)
                if want is None or message.get("type") == want:
                    return message
            elif raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE,
                              aiohttp.WSMsgType.ERROR):
                return {"type": "__closed__", "code": ws.close_code}

    async def _connect(self, session: aiohttp.ClientSession, *, token: str | None = None,
                       subprotocol: str = SUBPROTOCOL):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        # Our own asyncio timeouts bound every read; aiohttp's ws_connect
        # timeout argument has changed shape across versions, so leave it out.
        return await session.ws_connect(self.ws_url, headers=headers,
                                        protocols=[subprotocol])

    async def _handshake(self, ws) -> dict[str, Any] | None:
        await ws.send_str(json.dumps(_msg(
            "hello", self.nid(),
            client={"name": "catnector-conformance", "version": "1.0"},
            features=["ack", "follow_state"],
            rig={"profile": "conformance dummy", "health": "ok"})))
        return await self._recv(ws, "welcome")

    async def _trigger(self, session: aiohttp.ClientSession, prompt: str,
                       path: str, body: dict[str, Any]) -> asyncio.Task | None:
        """Make the site do something a person would otherwise click.

        In interactive mode this only *asks*: it never waits for a keystroke.
        The socket is already open and reading, so whether the operator acts
        before or after the message is printed makes no difference — which
        removes the "am I supposed to press Enter first?" ambiguity that a
        confirmation prompt creates.
        """
        if self.mock_base:
            return asyncio.create_task(session.post(f"{self.mock_base}{path}",
                                                    json=body))
        if self.interactive:
            print(f"\n  \033[1m→ On the site: {prompt}\033[0m")
            print(f"    (waiting up to {int(self.human_timeout)}s — no need to "
                  f"press anything here)")
        return None

    async def _await_human(self, ws, want: str):
        """Wait for a message the operator's action should have caused."""
        timeout = self.human_timeout if self.interactive else self.timeout
        return await self._recv(ws, want, timeout=timeout)

    def _note_fields(self, message: dict[str, Any]) -> None:
        """Record optional fields the site used, for the §8.3 check."""
        for field in message:
            if field not in _ENVELOPE_FIELDS:
                self._fields_seen.setdefault(str(message.get("type", "?")), set()).add(field)

    # --------------------------------------------------------------- checks

    async def check_discovery(self, session: aiohttp.ClientSession) -> bool:
        url = self.token.wellknown_url
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                if resp.status != 200:
                    self.report.add("discovery reachable", FAIL,
                                    f"{url} returned HTTP {resp.status}", "§4.2")
                    return False
                self.wellknown = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            self.report.add("discovery reachable", FAIL, f"{url}: {exc}", "§4.2")
            return False
        self.report.add("discovery reachable", PASS, url, "§4.2")

        try:
            schemas.validate_wellknown(self.wellknown)
            self.report.add("capability document valid", PASS, spec="§4.2")
        except schemas.SchemaError as exc:
            self.report.add("capability document valid", FAIL, str(exc), "§4.2")
            return False

        versions = self.wellknown.get("protocol_versions", [])
        if PROTOCOL_VERSION in versions:
            self.report.add("advertises protocol version 1", PASS, spec="§4.2")
        else:
            self.report.add("advertises protocol version 1", FAIL,
                            f"advertises {versions}", "§4.2")
            return False

        self.ws_url = self.wellknown["websocket_url"]
        if tokens.is_local(self.token.host):
            self.report.add("TLS required", SKIP, "local host is exempt", "§5.1")
        elif self.ws_url.startswith("wss://"):
            self.report.add("TLS required", PASS, spec="§5.1")
        else:
            self.report.add("TLS required", FAIL,
                            f"non-local endpoint offers {self.ws_url}", "§5.1")

        interval = self.wellknown.get("telemetry_interval_ms", 1000)
        self.report.add("telemetry interval advertised", PASS, f"{interval} ms", "§9.1")
        return True

    async def check_upgrade_guards(self, session: aiohttp.ClientSession) -> None:
        try:
            ws = await self._connect(session, token=None)
            await ws.close()
            self.report.add("rejects missing bearer token", FAIL,
                            "upgrade succeeded with no Authorization header", "§5.2")
        except Exception:  # noqa: BLE001 - any refusal is a pass
            self.report.add("rejects missing bearer token", PASS, spec="§5.2")

        try:
            ws = await self._connect(session, token=self.token.site_token,
                                     subprotocol="catnector.v99")
            closed = await self._recv(ws, timeout=3.0)
            code = (closed or {}).get("code")
            await ws.close()
            if code == 4003:
                self.report.add("rejects unsupported version", PASS,
                                "closed 4003", "§5.3")
            else:
                self.report.add("rejects unsupported version", FAIL,
                                f"accepted catnector.v99 (close code {code})", "§5.3")
        except Exception:  # noqa: BLE001
            self.report.add("rejects unsupported version", PASS,
                            "upgrade refused", "§5.3")

    async def check_session(self, session: aiohttp.ClientSession) -> None:
        try:
            ws = await self._connect(session, token=self.token.site_token)
        except Exception as exc:  # noqa: BLE001
            self.report.add("handshake", FAIL, f"could not connect: {exc}", "§7.1")
            return

        try:
            welcome = await self._handshake(ws)
            if welcome is None:
                self.report.add("handshake", FAIL, "no welcome received", "§7.2")
                return
            self.report.add("handshake", PASS,
                            f"session {welcome.get('session', {}).get('id')}", "§7.2")

            identity = welcome.get("session", {})
            if identity.get("callsign") or identity.get("user"):
                self.report.add("welcome carries display identity", PASS,
                                f"callsign={identity.get('callsign')!r}", "§7.2")
            else:
                self.report.add("welcome carries display identity", WARN,
                                "client cannot show who it is authenticated as", "§7.2")

            # heartbeat
            ping_id = self.nid()
            await ws.send_str(json.dumps(_msg("ping", ping_id)))
            pong = await self._recv(ws, "pong", timeout=5.0)
            if pong and pong.get("re") == ping_id:
                self.report.add("heartbeat ping/pong", PASS, spec="§10")
            elif pong:
                self.report.add("heartbeat ping/pong", FAIL,
                                f"pong.re was {pong.get('re')!r}, expected {ping_id!r}", "§10")
            else:
                self.report.add("heartbeat ping/pong", FAIL, "no pong", "§10")

            await self._check_telemetry_rate(ws)
            await self._check_control(session, ws)
        finally:
            await ws.close()

    async def _check_telemetry_rate(self, ws) -> None:
        interval = self.wellknown.get("telemetry_interval_ms", 1000)
        for _ in range(4):
            await ws.send_str(json.dumps(_msg(
                "report", self.nid(), ts=int(time.time() * 1000),
                freq=14074000, mode="USB", passband=2400,
                rig={"profile": "conformance dummy", "health": "ok"})))
            await asyncio.sleep(0.02)
        complaint = await self._recv(ws, "error", timeout=3.0)
        if complaint and complaint.get("code") == "rate_exceeded":
            self.report.add("enforces telemetry interval", PASS,
                            f"complained about reports faster than {interval} ms", "§9.1")
        else:
            self.report.add("enforces telemetry interval", WARN,
                            "site accepted reports faster than its advertised interval; "
                            "clients must self-throttle, but a site SHOULD notice", "§9.1")

    async def _check_control(self, session: aiohttp.ClientSession, ws) -> None:
        names = ("site sends a usable set_rig", "site tolerates a refusal",
                 "follow_state is set", "follow_state is cleared")
        if not (self.mock_base or self.interactive):
            for name in names:
                self.report.add(
                    name, SKIP,
                    "the site has to be made to do this — pass --interactive to "
                    "be prompted, or --mock-base to drive a reference site",
                    "§7.4")
            return

        await self._check_set_rig_accepted(session, ws)
        await self._check_set_rig_refused(session, ws)
        await self._check_follow_state(session, ws)

    async def _check_set_rig_accepted(self, session: aiohttp.ClientSession, ws) -> None:
        task = await self._trigger(
            session,
            "press \"Tune my rig\" next to any spot",
            "/mock/set_rig",
            {"freq": 14195000, "mode": "USB", "source": "conformance check"})
        control = await self._await_human(ws, "set_rig")
        if task:
            (await task).close()
        if control is None:
            self.report.add("site sends a usable set_rig", SKIP,
                            "no set_rig arrived", "§7.4")
            return

        self._note_fields(control)
        problems = []
        freq = control.get("freq")
        if not isinstance(freq, int) or isinstance(freq, bool):
            problems.append(f"freq must be an integer number of hertz, got {freq!r}")
        mode = control.get("mode")
        if mode is not None and mode not in _MODES:
            problems.append(f"{mode!r} is not a hamlib mode token")

        await ws.send_str(json.dumps(_msg("ack", self.nid(), re=control["id"])))
        if problems:
            self.report.add("site sends a usable set_rig", FAIL,
                            "; ".join(problems), "§6.1")
        else:
            self.report.add("site sends a usable set_rig", PASS,
                            f"freq={freq} mode={mode} source={control.get('source')!r}",
                            "§7.4")

    async def _check_set_rig_refused(self, session: aiohttp.ClientSession, ws) -> None:
        """A client refuses tunes routinely — the session must survive it.

        Refusals are ordinary: the radio is transmitting, the frequency is
        outside its range, the operator has automatic tuning off. A site that
        drops the session over one would disconnect people for using the
        product correctly.
        """
        task = await self._trigger(
            session,
            "press \"Tune my rig\" once more — this check will decline it",
            "/mock/set_rig",
            {"freq": 14195000, "mode": "USB", "source": "refusal check"})
        control = await self._await_human(ws, "set_rig")
        if task:
            (await task).close()
        if control is None:
            self.report.add("site tolerates a refusal", SKIP,
                            "no second set_rig arrived", "§7.6")
            return

        self._note_fields(control)
        await ws.send_str(json.dumps(_msg(
            "nack", self.nid(), re=control["id"], reason="rejected",
            detail="declined by the conformance checker")))

        probe = self.nid()
        await ws.send_str(json.dumps(_msg("ping", probe)))
        if await self._recv(ws, "pong", timeout=10.0):
            self.report.add("site tolerates a refusal", PASS,
                            "the session survived a nack", "§7.6")
        else:
            self.report.add("site tolerates a refusal", FAIL,
                            "the session ended after a refusal; refusals are "
                            "routine and must not disconnect anyone", "§7.6")

    async def _check_follow_state(self, session: aiohttp.ClientSession, ws) -> None:
        task = await self._trigger(
            session,
            "start following an operator (\"QSY Follow\")",
            "/mock/follow_state", {"following": "W1ABC"})
        state = await self._await_human(ws, "follow_state")
        if task:
            (await task).close()
        if state is None:
            self.report.add("follow_state is set", SKIP,
                            "no follow_state arrived", "§7.5")
            self.report.add("follow_state is cleared", SKIP,
                            "the follow was never seen to start", "§7.5")
            return

        self._note_fields(state)
        if state.get("following"):
            self.report.add("follow_state is set", PASS,
                            f"following {state['following']!r}", "§7.5")
        else:
            self.report.add("follow_state is set", FAIL,
                            "follow_state arrived with nothing in it", "§7.5")

        task = await self._trigger(
            session,
            "stop following (unfollow, or have that operator un-spot)",
            "/mock/follow_state", {"following": None})
        cleared = await self._await_human(ws, "follow_state")
        if task:
            (await task).close()
        if cleared is None:
            self.report.add("follow_state is cleared", SKIP,
                            "no second follow_state arrived", "§7.5")
        elif cleared.get("following") is None:
            self.report.add("follow_state is cleared", PASS,
                            "the site sends follow_state=null when a follow ends",
                            "§7.5")
        else:
            self.report.add(
                "follow_state is cleared", FAIL,
                "the site never cleared follow_state — every follower's client "
                "would go on claiming a follow that is over", "§7.5")

    async def check_supersede(self, session: aiohttp.ClientSession) -> None:
        try:
            first = await self._connect(session, token=self.token.site_token)
            if await self._handshake(first) is None:
                self.report.add("one session per account", FAIL,
                                "first session did not complete a handshake", "§11")
                await first.close()
                return
            second = await self._connect(session, token=self.token.site_token)
            await self._handshake(second)
            closed = await self._recv(first, timeout=self.timeout)
            code = first.close_code or (closed or {}).get("code")
            await first.close()
            await second.close()
            if code == 4001:
                self.report.add("one session per account", PASS,
                                "older session closed 4001", "§11")
            else:
                self.report.add("one session per account", FAIL,
                                f"older session was not closed with 4001 (got {code})", "§11")
        except Exception as exc:  # noqa: BLE001
            self.report.add("one session per account", FAIL, str(exc), "§11")

    # ------------------------------------------------------------------ run

    async def run(self) -> Report:
        async with aiohttp.ClientSession() as session:
            if await self.check_discovery(session):
                await self.check_upgrade_guards(session)
                await self.check_session(session)
                await self.check_supersede(session)
        self.report.add("staleness does not end a follow", SKIP,
                        "not observable from outside; verify by inspection", "§10")
        return self.report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check a site endpoint against the Catnector Protocol.")
    parser.add_argument("--token", required=True, help="a cnx1_ token for the site")
    parser.add_argument("--mock-base",
                        help="base URL of a reference mock site's /mock/ control surface, "
                             "used to trigger site-initiated behaviour")
    parser.add_argument("--interactive", action="store_true",
                        help="prompt a human to trigger site-initiated behaviour")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--no-colour", action="store_true")
    args = parser.parse_args(argv)

    try:
        token = tokens.decode(args.token)
    except tokens.TokenError as exc:
        print(f"token: {exc}", file=sys.stderr)
        return 2

    print(f"Catnector Protocol conformance check")
    print(f"  endpoint: {token.host}\n")
    checker = Checker(token, args.mock_base, args.interactive, args.timeout)
    report = asyncio.run(checker.run())
    print(report.render(colour=not args.no_colour))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
