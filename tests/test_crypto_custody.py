"""
Tests for Cryptographic Chain-of-Custody Digital Seal (HMAC-SHA256).
"""

import pytest
from engine.crypto_custody import generate_custody_seal, verify_custody_seal


def test_generate_and_verify_valid_seal():
    seal = generate_custody_seal(
        lot_id="lot_001",
        batch_id="b_001",
        rider_id="r_aarav",
        shelter_id="s_cp",
        quantity=40,
        item_name="Cooked Rice & Dal",
        temp_celsius=65.0,
    )

    assert "token" in seal
    assert "signature" in seal
    assert seal["payload"]["lot_id"] == "lot_001"
    assert seal["payload"]["temp_c"] == 65.0

    valid, payload, msg = verify_custody_seal(seal["token"])
    assert valid is True
    assert payload is not None
    assert payload["lot_id"] == "lot_001"
    assert payload["quantity"] == 40
    assert "Valid authentic" in msg


def test_tampered_payload_fails_verification():
    import base64
    import json

    seal = generate_custody_seal(
        lot_id="lot_001",
        batch_id="b_001",
        rider_id="r_aarav",
        shelter_id="s_cp",
        quantity=40,
        item_name="Cooked Rice & Dal",
    )

    parts = seal["token"].split(".")
    # Tamper with the quantity in payload
    decoded = json.loads(base64.urlsafe_b64decode(parts[0] + "==").decode())
    decoded["quantity"] = 999
    tampered_encoded = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")
    tampered_token = f"{tampered_encoded}.{parts[1]}"

    valid, payload, msg = verify_custody_seal(tampered_token)
    assert valid is False
    assert payload is None
    assert "mismatch" in msg.lower() or "failed" in msg.lower()


def test_malformed_token_fails_gracefully():
    valid, payload, msg = verify_custody_seal("totally-invalid-token")
    assert valid is False
    assert payload is None
    assert "Invalid token structure" in msg
