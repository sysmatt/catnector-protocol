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

    async def _trigger(self, session: aiohttp.ClientSession, path: str,
                       body: dict[str, Any]) -> asyncio.Task | None:
        if self.mock_base:
            return asyncio.create_task(
                session.post(f"{self.mock_base}{path}", json=body))
        if self.interactive:
            print(f"\n  >>> Now make the site do this, then press Enter: "
                  f"{path} {json.dumps(body)}")
            await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)
            return None
        return None

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
        if not (self.mock_base or self.interactive):
            for name in ("set_rig round trip", "tolerates nack", "follow_state cleared"):
                self.report.add(name, SKIP,
                                "needs --mock-base or --interactive to trigger", "§7.4")
            return

        # 1. a tune the client accepts
        task = await self._trigger(session, "/mock/set_rig",
                                   {"freq": 14195000, "mode": "USB",
                                    "source": "conformance check"})
        control = await self._recv(ws, "set_rig", timeout=self.timeout)
        if control is None:
            self.report.add("set_rig round trip", FAIL, "no set_rig arrived", "§7.4")
            if task:
                task.cancel()
            return
        await ws.send_str(json.dumps(_msg("ack", self.nid(), re=control["id"])))
        self.report.add("set_rig round trip", PASS,
                        f"freq={control.get('freq')} mode={control.get('mode')} "
                        f"source={control.get('source')!r}", "§7.4")
        if task:
            resp = await task
            resp.close()

        # 2. a tune the client refuses - the session must survive it
        task = await self._trigger(session, "/mock/set_rig",
                                   {"freq": 14195000, "mode": "USB",
                                    "req": ["split"], "source": "refusal check"})
        control = await self._recv(ws, "set_rig", timeout=self.timeout)
        if control is None:
            self.report.add("tolerates nack", SKIP, "no second set_rig arrived", "§8.2")
        else:
            await ws.send_str(json.dumps(_msg(
                "nack", self.nid(), re=control["id"], reason="unsupported_req",
                detail="conformance checker does not implement split")))
            if task:
                resp = await task
                resp.close()
            probe = self.nid()
            await ws.send_str(json.dumps(_msg("ping", probe)))
            pong = await self._recv(ws, "pong", timeout=5.0)
            if pong:
                self.report.add("tolerates nack", PASS,
                                "session still alive after a refusal", "§8.2")
            else:
                self.report.add("tolerates nack", FAIL,
                                "session died after a nack", "§8.2")

        # 3. follow_state must be sent, including when it clears
        task = await self._trigger(session, "/mock/follow_state", {"following": "W1ABC"})
        state = await self._recv(ws, "follow_state", timeout=self.timeout)
        if task:
            resp = await task
            resp.close()
        if state is None:
            self.report.add("follow_state cleared", SKIP, "no follow_state seen", "§7.5")
            return
        task = await self._trigger(session, "/mock/follow_state", {"following": None})
        cleared = await self._recv(ws, "follow_state", timeout=self.timeout)
        if task:
            resp = await task
            resp.close()
        if cleared is not None and cleared.get("following") is None:
            self.report.add("follow_state cleared", PASS,
                            "site sends follow_state=null when a follow ends", "§7.5")
        else:
            self.report.add("follow_state cleared", FAIL,
                            "site never cleared follow_state; a client indicator "
                            "would keep claiming a follow that is over", "§7.5")

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
