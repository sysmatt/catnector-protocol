"""End-to-end test: the conformance checker against the reference server.

The reference server must pass the conformance checker. If it stops doing so,
either the server or the checker has drifted from SPEC.md, and the repository
finds out rather than an implementer does.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from aiohttp import web

from catnector_protocol import schemas, tokens
from catnector_protocol.conformance import FAIL, PASS, SKIP, WARN, Checker
from catnector_protocol.reference import MockSite


@contextlib.asynccontextmanager
async def running_site(telemetry_interval_ms: int = 1000):
    """Start the reference mock site on an ephemeral port."""
    site = MockSite(telemetry_interval_ms, "127.0.0.1", 0)
    runner = web.AppRunner(site.app())
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    port = tcp._server.sockets[0].getsockname()[1]
    site.port = port
    try:
        yield site, f"127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_reference_server_passes_conformance():
    async with running_site() as (_site, hostport):
        token = tokens.decode(tokens.encode(hostport, "conformance-token"))
        report = await Checker(token, mock_base=f"http://{hostport}").run()

    rendered = report.render(colour=False)
    assert report.count(FAIL) == 0, f"reference server failed conformance:\n{rendered}"
    assert report.count(PASS) >= 10, rendered

    by_name = {r.name: r for r in report.results}
    for required in ("handshake", "heartbeat ping/pong", "set_rig round trip",
                     "follow_state cleared", "one session per account",
                     "rejects missing bearer token", "rejects unsupported version",
                     "enforces telemetry interval", "tolerates nack"):
        assert by_name[required].status == PASS, f"{required}: {by_name[required]}"


@pytest.mark.asyncio
async def test_skips_observational_checks_without_a_trigger():
    """Without a way to make the site act, those checks are skipped, not guessed."""
    async with running_site() as (_site, hostport):
        token = tokens.decode(tokens.encode(hostport, "no-trigger"))
        report = await Checker(token).run()

    by_name = {r.name: r for r in report.results}
    assert by_name["set_rig round trip"].status == SKIP
    assert by_name["handshake"].status == PASS
    assert report.count(FAIL) == 0


@pytest.mark.asyncio
async def test_superseded_session_is_closed_with_4001():
    async with running_site() as (_site, hostport):
        import aiohttp

        token = tokens.decode(tokens.encode(hostport, "shared-token"))
        url = f"http://{hostport}/catnector/v1"
        headers = {"Authorization": f"Bearer {token.site_token}"}
        async with aiohttp.ClientSession() as http:
            first = await http.ws_connect(url, headers=headers,
                                          protocols=["catnector.v1"])
            second = await http.ws_connect(url, headers=headers,
                                           protocols=["catnector.v1"])
            message = await asyncio.wait_for(first.receive(), 5)
            assert first.close_code == 4001, (message, first.close_code)
            await second.close()


@pytest.mark.asyncio
async def test_every_schema_rejects_a_wrong_version():
    for mtype in schemas.known_message_types():
        with pytest.raises(schemas.SchemaError):
            schemas.validate_message({"v": 2, "type": mtype, "id": "x"})


def test_token_round_trip_is_lossless():
    for host, secret in [("hamqsy.app", "sk_live_9f2c"),
                         ("localhost:8799", "any"),
                         ("lab.example:8443", "a_b-c_d")]:
        decoded = tokens.decode(tokens.encode(host, secret))
        assert (decoded.host, decoded.site_token) == (host, secret)


def test_damaged_token_is_distinguished_from_a_wrong_one():
    good = tokens.encode("hamqsy.app", "sk_live_9f2c")
    with pytest.raises(tokens.TokenDamaged):
        tokens.decode(good[:-2])
    with pytest.raises(tokens.TokenError):
        tokens.decode("not-a-token")


def test_float_frequency_is_rejected():
    """SPEC.md §6.1 — integer hertz, never floats."""
    with pytest.raises(schemas.SchemaError):
        schemas.validate_message(
            {"v": 1, "type": "set_rig", "id": "s1", "freq": 14.074})


def test_unknown_mode_is_rejected_not_coerced():
    with pytest.raises(schemas.SchemaError):
        schemas.validate_message(
            {"v": 1, "type": "set_rig", "id": "s1", "freq": 14074000, "mode": "OLIVIA"})
