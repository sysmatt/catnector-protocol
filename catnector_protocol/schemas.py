"""Load and apply the protocol's JSON Schemas.

The schemas in ``schema/`` are normative alongside SPEC.md. Both the
reference server and the conformance checker validate every message they send
*and* receive against them, so a schema that drifts from the prose fails the
repository's own tests rather than someone else's implementation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

def _schema_dir() -> Path:
    """Where the schemas live, in an installed wheel or a source tree.

    The wheel force-includes ``schema/`` inside the package; a checkout keeps
    it at the repository root. Checking the package location first means an
    installed copy never reaches outside itself.
    """
    here = Path(__file__).resolve().parent
    packaged = here / "schema"
    return packaged if packaged.is_dir() else here.parent / "schema"


SCHEMA_DIR = _schema_dir()

#: Message ``type`` -> schema filename.
MESSAGE_SCHEMAS = {
    "hello": "hello.json",
    "welcome": "welcome.json",
    "report": "report.json",
    "set_rig": "set_rig.json",
    "follow_state": "follow_state.json",
    "ack": "ack.json",
    "nack": "nack.json",
    "ping": "ping.json",
    "pong": "pong.json",
    "error": "error.json",
}

WELLKNOWN_SCHEMA = "wellknown.json"


class SchemaError(ValueError):
    """A message did not validate against its schema."""


def _load_all() -> tuple[Registry, dict[str, dict[str, Any]]]:
    docs: dict[str, dict[str, Any]] = {}
    resources = []
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        doc = json.loads(path.read_text())
        docs[path.name] = doc
        # Register under the bare filename so "common.json#/$defs/x" resolves
        # without depending on the $id host being reachable.
        resources.append((path.name, Resource.from_contents(doc)))
        if "$id" in doc:
            resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources), docs


_REGISTRY, _DOCS = _load_all()
_VALIDATORS: dict[str, Draft202012Validator] = {
    name: Draft202012Validator(doc, registry=_REGISTRY) for name, doc in _DOCS.items()
}


def validate_against(filename: str, instance: Any) -> None:
    """Validate ``instance`` against the named schema file."""
    validator = _VALIDATORS.get(filename)
    if validator is None:
        raise SchemaError(f"no such schema: {filename}")
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        raise SchemaError(f"{filename}: {where}: {first.message}")


def validate_message(message: Any) -> str:
    """Validate a protocol message. Returns its ``type``.

    Raises :class:`SchemaError` for a non-object, a missing or unknown
    ``type``, or a schema violation.
    """
    if not isinstance(message, dict):
        raise SchemaError("message is not a JSON object")
    mtype = message.get("type")
    if not isinstance(mtype, str):
        raise SchemaError("message has no string 'type'")
    filename = MESSAGE_SCHEMAS.get(mtype)
    if filename is None:
        raise SchemaError(f"unknown message type: {mtype!r}")
    validate_against(filename, message)
    return mtype


def validate_wellknown(document: Any) -> None:
    """Validate a ``/.well-known/catnector`` capability document."""
    validate_against(WELLKNOWN_SCHEMA, document)


def known_message_types() -> list[str]:
    return sorted(MESSAGE_SCHEMAS)
