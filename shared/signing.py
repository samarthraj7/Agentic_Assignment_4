"""Ed25519 identity for nodes. node_id is derived from the public key, so it can't be spoofed."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def load_or_create_key(path: Path) -> Ed25519PrivateKey:
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    path.chmod(0o600)
    return key


def public_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def node_id_for(pub_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(pub_b64)).hexdigest()[:16]


def sign(key: Ed25519PrivateKey, payload: dict) -> str:
    return base64.b64encode(key.sign(canonical(payload))).decode()


def verify(pub_b64: str, payload: dict, signature: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64)).verify(
            base64.b64decode(signature), canonical(payload))
        return True
    except (InvalidSignature, ValueError):
        return False
