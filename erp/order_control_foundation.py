"""Inactive Sales Order primitives; no runtime registrations or ORM writes.

Snapshot format so-json-v1 is Python JSON (sorted keys, compact separators,
ASCII escapes, UTF-8). It is not RFC 8785 or a command payload normalizer.
Accept JSON-native values only; commercial decimals must already be validated
strings. Finite legacy floats retain Python JSON representation without rounding.
Changing this format requires a new format name, never historical rehashing.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import re


class OrderControlError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise OrderControlError('VALIDATION_ERROR')


@dataclass(frozen=True)
class OrderSnapshot:
    canonical: bytes
    digest: str

    def decode(self):
        """Each caller receives a detached JSON value."""
        return json.loads(self.canonical)


def capture_snapshot(content):
    _json_value(content)
    canonical = json.dumps(content, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=True, allow_nan=False).encode('utf-8')
    return OrderSnapshot(canonical, hashlib.sha256(canonical).hexdigest())


def verify_snapshot(content, digest):
    snapshot = capture_snapshot(content)
    if (type(digest) is not str or not re.fullmatch(r'[0-9a-f]{64}', digest)
            or not hmac.compare_digest(snapshot.digest, digest)):
        raise OrderControlError('REVISION_CONFLICT')
    return snapshot


def require_version(expected, current, *, creating=False):
    """Comparison only; services must still lock/compare-and-write atomically."""
    minimum = 0 if creating else 1
    if type(expected) is not int or not minimum <= expected <= 2**63 - 1:
        raise OrderControlError('VALIDATION_ERROR')
    if current is None:
        raise OrderControlError('LEGACY_REVIEW_REQUIRED')
    if type(current) is not int or not minimum <= current <= 2**63 - 1:
        raise OrderControlError('VALIDATION_ERROR')
    if creating and (expected != 0 or current != 0):
        raise OrderControlError('VALIDATION_ERROR')
    if expected != current:
        raise OrderControlError('VERSION_CONFLICT')


def require_order_revision(order_id, revision_order_id):
    """Trusted WorkflowOrder FK projections only, never conventional order IDs.

    Does not establish row existence, visibility or persisted pointer ownership.
    """
    for value in (order_id, revision_order_id):
        if type(value) is not int or not 1 <= value <= 2**63 - 1:
            raise OrderControlError('VALIDATION_ERROR')
    if order_id != revision_order_id:
        raise OrderControlError('REVISION_CONFLICT')
