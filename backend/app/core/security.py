"""Cryptographic helpers for OAuth token encryption, state signing, and Meta callbacks."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class TokenCipher:
    """Encrypts and decrypts sensitive OAuth access tokens at rest using Fernet."""

    def __init__(self, key: str | None = None, fallback_secret: str | None = None) -> None:
        settings = get_settings()
        raw_key = (key if key is not None else settings.token_encryption_key).strip()
        if raw_key:
            try:
                self._fernet = Fernet(raw_key.encode("utf-8"))
            except ValueError:
                # Allow arbitrary secret strings (e.g. Render generateValue) to derive a valid 32-byte Fernet key
                digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
                self._fernet = Fernet(base64.urlsafe_b64encode(digest))
        else:
            secret = (fallback_secret or settings.app_secret_key).encode("utf-8")
            digest = hashlib.sha256(secret).digest()
            derived = base64.urlsafe_b64encode(digest)
            self._fernet = Fernet(derived)

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Unable to decrypt stored access token.") from exc


@lru_cache
def get_token_cipher() -> TokenCipher:
    return TokenCipher()


def create_signed_state(payload: dict, secret: str | None = None) -> str:
    """Create a URL-safe HMAC-SHA256 signed OAuth state parameter."""
    key = (secret or get_settings().app_secret_key).encode("utf-8")
    body = {
        **payload,
        "nonce": secrets.token_urlsafe(12),
        "iat": int(time.time()),
    }
    raw_json = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded_body = base64.urlsafe_b64encode(raw_json).decode("utf-8").rstrip("=")
    sig = hmac.new(key, encoded_body.encode("utf-8"), hashlib.sha256).digest()
    encoded_sig = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"{encoded_body}.{encoded_sig}"


def verify_signed_state(state: str, max_age_seconds: int = 900, secret: str | None = None) -> dict:
    """Verify and decode a signed OAuth state parameter."""
    if "." not in state:
        raise ValueError("Malformed state token.")
    encoded_body, encoded_sig = state.split(".", 1)
    key = (secret or get_settings().app_secret_key).encode("utf-8")
    expected_sig = hmac.new(key, encoded_body.encode("utf-8"), hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected_sig).decode("utf-8").rstrip("=")
    if not hmac.compare_digest(encoded_sig, expected_b64):
        raise ValueError("Invalid OAuth state signature.")

    padded = encoded_body + "=" * (-len(encoded_body) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    if int(time.time()) - int(decoded.get("iat", 0)) > max_age_seconds:
        raise ValueError("OAuth state token has expired.")
    return decoded


def verify_meta_signed_request(signed_request: str, app_secret: str) -> dict:
    """Verify Meta's signed_request payload for automated Data Deletion callbacks."""
    if "." not in signed_request:
        raise ValueError("Invalid Meta signed_request format.")
    encoded_sig, encoded_payload = signed_request.split(".", 1)
    sig_padded = encoded_sig + "=" * (-len(encoded_sig) % 4)
    payload_padded = encoded_payload + "=" * (-len(encoded_payload) % 4)

    sig = base64.urlsafe_b64decode(sig_padded)
    expected = hmac.new(
        app_secret.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Invalid Meta signed_request signature.")
    return json.loads(base64.urlsafe_b64decode(payload_padded).decode("utf-8"))
