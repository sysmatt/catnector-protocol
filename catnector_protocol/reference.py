"""Reference site (server) implementation of the Catnector Protocol.

This is a *mock site*: it speaks the protocol correctly but has no spots, no
users and no database. Its purposes are

1. to let a client be developed and tested with no real site, and
2. to be the executable companion to SPEC.md — where the prose and this
   file disagree, one of them is a bug.

It exposes a small ``/mock/`` control surface so tests can make the site do
things a real site would do in response to a human clicking something.

Run::

    catnector-mock-server --port 8799

Then point a client at token payload ``{"h": "localhost:8799", "t": "any"}``.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import time
import uuid
from typing import Any

from aiohttp import WSMsgType, web

from . import schemas

SUBPROTOCOL = "catnector.v1"
PROTOCOL_VERSION = 1
SUPPORTED_FEATURES = ["ack", "follow_state"]

# SPEC.md §11 close codes.
CLOSE_SUPERSEDED = 4001
CLOSE_BAD_TOKEN = 4002
CLOSE_BAD_VERSION = 4003
CLOSE_GOING_AWAY = 4004


def now_ms() -> int:
    return int(time.time() * 1000)


class Session:
    """One authenticated client connection."""

    _ids = itertools.count(1)

    def __init__(self, account: str, ws: web.WebSocketResponse) -> None:
        self.account = account
        self.ws = ws
        self.id = f"s-{uuid.uuid4().hex[:12]}"
        self.hello: dict[str, Any] | None = None
        self.last_report_ms: int | None = None
        self.reports: list[dict[str, Any]] = []
        self.pending: dict[str, asyncio.Future] = {}
        self.rate_violations = 0
        #: Set when a newer session takes over this account (SPEC.md §11).
        self.superseded = asyncio.Event()
        self._seq = itertools.count(1)

    def next_id(self) -> str:
        return f"m{next(self._seq)}"

    async def send(self, message: dict[str, Any]) -> None:
        """Validate then send. A reference implementation checks its own output."""
        schemas.validate_message(message)
        await self.ws.send_str(json.dumps(message))

    async def send_control(self, payload: dict[str, Any], timeout: float = 5.0):
        """Send a ``set_rig`` and wait for the client's ack/nack (SPEC.md §7.6)."""
        message = {"v": PROTOCOL_VERSION, "type": "set_rig",
                   "id": self.next_id(), **payload}
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self.pending[message["id"]] = future
        await self.send(message)
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return {"type": "timeout", "re": message["id"]}
        finally:
            self.pending.pop(message["id"], None)


class MockSite:
    def __init__(self, telemetry_interval_ms: int, host: str, port: int,
                 scheme: str = "ws") -> None:
        self.telemetry_interval_ms = telemetry_interval_ms
        self.host = host
        self.port = port
        self.scheme = scheme
        #: account -> Session. SPEC.md §11: one live session per account.
        self.sessions: dict[str, Session] = {}
        self.log: list[str] = []

    def note(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {text}"
        self.log.append(line)
        print(line, flush=True)

    # ---------------------------------------------------------------- HTTP

    async def wellknown(self, request: web.Request) -> web.Response:
        advertised = request.headers.get("Host") or f"{self.host}:{self.port}"
        document = {
            "protocol_versions": [PROTOCOL_VERSION],
            "websocket_url": f"{self.scheme}://{advertised}/catnector/v1",
            "telemetry_interval_ms": self.telemetry_interval_ms,
            "features": SUPPORTED_FEATURES,
            "site_name": "Catnector Reference Mock Site",
            "site_url": f"http://{advertised}/",
        }
        schemas.validate_wellknown(document)
        return web.json_response(document)

    async def mock_status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "sessions": [
                {"account": s.account, "session_id": s.id,
                 "client": (s.hello or {}).get("client"),
                 "reports_received": len(s.reports),
                 "last_report": s.reports[-1] if s.reports else None,
                 "rate_violations": s.rate_violations}
                for s in self.sessions.values()
            ],
            "log": self.log[-50:],
        })

    def _only_session(self) -> Session | None:
        return next(iter(self.sessions.values()), None)

    async def mock_set_rig(self, request: web.Request) -> web.Response:
        session = self._only_session()
        if session is None:
            return web.json_response({"error": "no session connected"}, status=409)
        body = await request.json()
        payload = {k: v for k, v in body.items()
                   if k in ("freq", "mode", "passband", "source", "req")}
        if "freq" not in payload:
            return web.json_response({"error": "freq required"}, status=400)
        result = await session.send_control(payload)
        return web.json_response({"result": result})

    async def mock_follow_state(self, request: web.Request) -> web.Response:
        session = self._only_session()
        if session is None:
            return web.json_response({"error": "no session connected"}, status=409)
        body = await request.json()
        await session.send({"v": PROTOCOL_VERSION, "type": "follow_state",
                            "id": session.next_id(),
                            "following": body.get("following")})
        return web.json_response({"sent": True})

    # ----------------------------------------------------------- WebSocket

    async def websocket(self, request: web.Request) -> web.StreamResponse:
        # SPEC.md §5.3 — version selection fails at connect time.
        offered = [p.strip() for p in
                   request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
        if SUBPROTOCOL not in offered:
            self.note(f"upgrade refused: subprotocol {offered!r}")
            return web.Response(status=400, text="unsupported protocol version")

        # SPEC.md §5.2 — bearer token on the upgrade request.
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or not authorization[7:].strip():
            self.note("upgrade refused: missing or malformed bearer token")
            return web.Response(status=401, text="bearer token required")
        token = authorization[7:].strip()

        ws = web.WebSocketResponse(protocols=[SUBPROTOCOL], heartbeat=None)
        await ws.prepare(request)

        # The mock treats the token itself as the account, so two clients
        # sharing a token collide exactly as they would on a real site.
        account = token
        session = Session(account, ws)

        previous = self.sessions.get(account)
        if previous is not None:
            # Signal rather than close from here: the close frame must be sent
            # by the task that owns that socket, or aiohttp aborts the
            # connection and the peer sees 1006 instead of 4001.
            self.note(f"session {previous.id} superseded by a new connection")
            previous.superseded.set()
        self.sessions[account] = session
        self.note(f"session {session.id} opened for account {account!r}")

        try:
            await self._run(session)
        finally:
            if self.sessions.get(account) is session:
                del self.sessions[account]
            self.note(f"session {session.id} closed")
        return ws

    async def _run(self, session: Session) -> None:
        while True:
            receiving = asyncio.ensure_future(session.ws.receive())
            taken_over = asyncio.ensure_future(session.superseded.wait())
            done, _ = await asyncio.wait({receiving, taken_over},
                                         return_when=asyncio.FIRST_COMPLETED)
            if taken_over in done:
                receiving.cancel()
                await session.ws.close(code=CLOSE_SUPERSEDED,
                                       message=b"session superseded")
                return
            taken_over.cancel()
            raw = receiving.result()
            if raw.type in (WSMsgType.CLOSE, WSMsgType.CLOSING,
                            WSMsgType.CLOSED, WSMsgType.ERROR):
                return
            if raw.type is not WSMsgType.TEXT:
                continue
            try:
                message = json.loads(raw.data)
                mtype = schemas.validate_message(message)
            except (json.JSONDecodeError, schemas.SchemaError) as exc:
                await self._error(session, None, "bad_message", str(exc))
                continue

            if session.hello is None and mtype != "hello":
                await session.ws.close(code=1002, message=b"hello expected first")
                return

            handler = getattr(self, f"_on_{mtype}", None)
            if handler is None:
                await self._error(session, message.get("id"), "unexpected_type",
                                  f"a site does not accept {mtype!r}")
                continue
            await handler(session, message)

    async def _error(self, session: Session, re: str | None, code: str,
                     detail: str) -> None:
        message = {"v": PROTOCOL_VERSION, "type": "error",
                   "id": session.next_id(), "code": code, "detail": detail[:512]}
        if re:
            message["re"] = re
        await session.send(message)

    async def _on_hello(self, session: Session, message: dict[str, Any]) -> None:
        session.hello = message
        client = message.get("client", {})
        self.note(f"  hello from {client.get('name')} {client.get('version')} "
                  f"features={message.get('features')}")
        await session.send({
            "v": PROTOCOL_VERSION, "type": "welcome", "id": session.next_id(),
            "re": message["id"],
            "session": {"id": session.id, "user": f"{session.account}@example.invalid",
                        "callsign": "K2TTA"},
            "telemetry_interval_ms": self.telemetry_interval_ms,
            "features": SUPPORTED_FEATURES,
        })

    async def _on_report(self, session: Session, message: dict[str, Any]) -> None:
        arrived = now_ms()
        if session.last_report_ms is not None:
            gap = arrived - session.last_report_ms
            # A small tolerance: scheduling jitter is not a protocol violation.
            if gap < self.telemetry_interval_ms * 0.9:
                session.rate_violations += 1
                await self._error(session, message["id"], "rate_exceeded",
                                  f"reports {gap}ms apart, interval is "
                                  f"{self.telemetry_interval_ms}ms")
        session.last_report_ms = arrived
        session.reports.append(message)
        rig = message.get("rig", {})
        self.note(f"  report freq={message.get('freq')} mode={message.get('mode')} "
                  f"rig={rig.get('profile')!r} health={rig.get('health')}")

    async def _on_ping(self, session: Session, message: dict[str, Any]) -> None:
        await session.send({"v": PROTOCOL_VERSION, "type": "pong",
                            "id": session.next_id(), "re": message["id"]})

    async def _on_ack(self, session: Session, message: dict[str, Any]) -> None:
        self.note(f"  ack for {message.get('re')}")
        future = session.pending.get(message.get("re", ""))
        if future and not future.done():
            future.set_result(message)

    async def _on_nack(self, session: Session, message: dict[str, Any]) -> None:
        self.note(f"  nack for {message.get('re')}: {message.get('reason')} "
                  f"({message.get('detail')})")
        future = session.pending.get(message.get("re", ""))
        if future and not future.done():
            future.set_result(message)

    # ------------------------------------------------------------- wiring

    def app(self) -> web.Application:
        application = web.Application()
        application.add_routes([
            web.get("/.well-known/catnector", self.wellknown),
            web.get("/catnector/v1", self.websocket),
            web.get("/mock/status", self.mock_status),
            web.post("/mock/set_rig", self.mock_set_rig),
            web.post("/mock/follow_state", self.mock_follow_state),
        ])
        return application


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reference mock site for the Catnector Protocol.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--telemetry-interval-ms", type=int, default=1000,
                        help="interval the site asks clients to report at")
    args = parser.parse_args(argv)

    site = MockSite(args.telemetry_interval_ms, args.host, args.port)
    print(f"Catnector reference mock site on http://{args.host}:{args.port}")
    print(f"  capability document  GET  /.well-known/catnector")
    print(f"  websocket            GET  /catnector/v1")
    print(f"  push a tune          POST /mock/set_rig     {{\"freq\":14195000,\"mode\":\"USB\"}}")
    print(f"  push follow state    POST /mock/follow_state {{\"following\":\"W1ABC\"}}")
    print(f"  inspect              GET  /mock/status")
    web.run_app(site.app(), host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
