"""Unit tests for token encryption, OAuth state signing, and regex-free text parsing."""

import base64
import hashlib
import hmac
import json

import pytest

from app.core.security import (
    TokenCipher,
    create_signed_state,
    verify_meta_signed_request,
    verify_signed_state,
)
from app.services.brand_scraper import _extract_hex_colors_from_css, extract_hashtags_from_text
from app.workers.publisher import compose_final_caption


def test_token_cipher_roundtrip(cipher: TokenCipher) -> None:
    secret = "EAABsbCS1iHgBAZ_super_secret_token"
    encrypted = cipher.encrypt(secret)
    assert encrypted != secret
    assert cipher.decrypt(encrypted) == secret

    with pytest.raises(ValueError):
        cipher.decrypt("not-valid-fernet-ciphertext")


def test_signed_state_roundtrip_and_tamper_check() -> None:
    state = create_signed_state({"brand_id": "b1", "timezone": "Asia/Kolkata"}, secret="s1")
    decoded = verify_signed_state(state, secret="s1")
    assert decoded["brand_id"] == "b1"
    assert decoded["timezone"] == "Asia/Kolkata"

    tampered = "x" + state[1:]
    with pytest.raises(ValueError):
        verify_signed_state(tampered, secret="s1")


def test_verify_meta_signed_request() -> None:
    app_secret = "meta_secret_123"
    payload = base64.urlsafe_b64encode(
        json.dumps({"user_id": "998877"}).encode("utf-8")
    ).decode("utf-8").rstrip("=")
    sig = hmac.new(app_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    encoded_sig = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    decoded = verify_meta_signed_request(f"{encoded_sig}.{payload}", app_secret)
    assert decoded["user_id"] == "998877"


def test_extract_hashtags_without_regex() -> None:
    text = "Grow faster today! #ReelsIndia, #AI_Tools #reelsindia #Growth#Viral2026!"
    tags = extract_hashtags_from_text(text)
    assert tags == ["#reelsindia", "#ai_tools", "#growth", "#viral2026"]


def test_extract_hex_colors_from_css() -> None:
    css = "body { color: #ffffff; background: #1a2b3c; } .btn { border: 1px solid #FF5733; }"
    colors = _extract_hex_colors_from_css(css)
    assert colors == ["#1A2B3C", "#FF5733"]


def test_compose_final_caption_deduplicates_tags() -> None:
    final = compose_final_caption(
        "Watch till the end #ReelsIndia",
        ["#reelsindia", "#aitools", "creatorgrowth"],
    )
    assert final == "Watch till the end #ReelsIndia\n\n#aitools #creatorgrowth"
