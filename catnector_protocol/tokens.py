"""Catnector token encoding and decoding (SPEC.md §4.1).

A token carries the endpoint hostname so that a client is configured by a
single paste, with no separate server-URL step::

    cnx1_<payload>.<check>

``payload`` is unpadded base64url of ``{"h": host, "t": site_token}``;
``check`` is the first 6 characters of unpadded base64url of the SHA-256 of
the payload text.

The checksum is separated by ``.`` rather than ``_`` because ``_`` is part
of the base64url alphabet, which would make the split ambiguous.

The encoding is **not encryption**. A token is a credential equivalent to a
password: store it restrictively, never log it, never put it in a URL.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

PREFIX = "cnx1"
CHECK_LENGTH = 6


class TokenError(ValueError):
    """The token is not a well-formed catnector token."""


class TokenDamaged(TokenError):
    """The token's checksum does not match — it was truncated or altered.

    Distinct from rejection by a server on purpose: "that token looks
    damaged" is actionable, "authentication failed" is not.
    """


@dataclass(frozen=True)
class Token:
    host: str
    site_token: str

    @property
    def https_base(self) -> str:
        scheme = "http" if is_local(self.host) else "https"
        return f"{scheme}://{self.host}"

    @property
    def wellknown_url(self) -> str:
        return f"{self.https_base}/.well-known/catnector"


def split_host_port(host: str) -> tuple[str, str]:
    """Split ``host[:port]`` without mangling IPv6 addresses.

    ``[::1]:8443`` -> ``("::1", "8443")``; ``::1`` -> ``("::1", "")``;
    ``example.org:443`` -> ``("example.org", "443")``.
    """
    text = host.strip()
    if text.startswith("["):
        address, _, rest = text[1:].partition("]")
        return address, rest.lstrip(":")
    if text.count(":") == 1:
        name, _, port = text.partition(":")
        return name, port
    # No colon at all, or several — a bare IPv6 address either way.
    return text, ""


def is_local(host: str) -> bool:
    """Hosts exempt from the TLS requirement (SPEC.md §5.1)."""
    name, _ = split_host_port(host)
    return name.lower() in ("localhost", "127.0.0.1", "::1")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _checksum(payload: str) -> str:
    return _b64(hashlib.sha256(payload.encode("ascii")).digest())[:CHECK_LENGTH]


def encode(host: str, site_token: str) -> str:
    """Build a token for ``host`` carrying ``site_token``."""
    if not host or not site_token:
        raise TokenError("host and site token are both required")
    payload = _b64(json.dumps({"h": host, "t": site_token},
                              separators=(",", ":")).encode("utf-8"))
    return f"{PREFIX}_{payload}.{_checksum(payload)}"


def decode(token: str) -> Token:
    """Parse a token, verifying its checksum first."""
    text = token.strip()
    if not text.startswith(PREFIX + "_"):
        raise TokenError("not a cnx1 token")
    body = text[len(PREFIX) + 1:]
    parts = body.split(".")
    if len(parts) != 2:
        raise TokenError("token is missing its checksum")
    payload, check = parts
    if not payload or not check:
        raise TokenError("token is missing its payload or checksum")
    if _checksum(payload) != check:
        raise TokenDamaged(
            "token checksum does not match — it looks truncated or altered")
    try:
        obj = json.loads(_unb64(payload))
    except Exception as exc:  # noqa: BLE001 - any decode failure is the same to a user
        raise TokenDamaged(f"token payload is not readable: {exc}") from exc
    if not isinstance(obj, dict) or "h" not in obj or "t" not in obj:
        raise TokenError("token payload is missing 'h' or 't'")
    return Token(host=str(obj["h"]), site_token=str(obj["t"]))
