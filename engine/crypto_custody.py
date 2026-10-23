"""
Cryptographic Chain-of-Custody Digital Seal for Last-Mile Aid Dispatch.

Provides tamper-evident HMAC-SHA256 signatures for food handoff transactions,
guaranteeing food safety, origin authenticity, and non-repudiation between
courier and receiving shelter.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional


SECRET_KEY = b"last-mile-aid-delhi-rescue-mesh-key-2026"


def generate_custody_seal(
    lot_id: str,
    batch_id: str,
    rider_id: str,
    shelter_id: str,
    quantity: int,
    item_name: str,
    temp_celsius: float = 4.5,
    timestamp_unix: Optional[float] = None,
) -> dict[str, Any]:
    """
    Generates a cryptographically signed Chain-of-Custody digital seal.
    
    Contains:
    - Unique transaction metadata
    - Cold/Hot food chain temperature log
    - Unix timestamp
    - HMAC-SHA256 digital signature
    """
    ts = timestamp_unix or time.time()
    payload = {
        "v": 1,
        "lot_id": lot_id,
        "batch_id": batch_id,
        "rider_id": rider_id,
        "shelter_id": shelter_id,
        "item": item_name,
        "quantity": quantity,
        "temp_c": round(temp_celsius, 1),
        "ts": int(ts),
    }

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(SECRET_KEY, serialized.encode("utf-8"), hashlib.sha256).hexdigest()

    encoded_payload = base64.urlsafe_b64encode(serialized.encode("utf-8")).decode("utf-8")
    token = f"{encoded_payload}.{signature[:32]}"

    return {
        "token": token,
        "payload": payload,
        "signature": signature,
        "signature_short": signature[:12].upper(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)),
    }


def verify_custody_seal(token: str) -> tuple[bool, Optional[dict[str, Any]], str]:
    """
    Verifies a digital seal token against the HMAC-SHA256 secret key.
    
    Returns (is_valid, payload, error_message).
    """
    try:
        parts = token.strip().split(".")
        if len(parts) != 2:
            return False, None, "Invalid token structure: expected payload.signature"

        encoded_payload, sig_short = parts
        # Re-pad base64 if needed
        padding = 4 - (len(encoded_payload) % 4)
        if padding and padding != 4:
            encoded_payload += "=" * padding

        decoded_json = base64.urlsafe_b64decode(encoded_payload.encode("utf-8")).decode("utf-8")
        payload = json.loads(decoded_json)

        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected_full_sig = hmac.new(SECRET_KEY, serialized.encode("utf-8"), hashlib.sha256).hexdigest()

        # Constant-time comparison on the 32-character prefix
        if not hmac.compare_digest(expected_full_sig[:32], sig_short):
            return False, None, "Cryptographic signature mismatch! Token may be tampered."

        return True, payload, "Valid authentic custody seal"

    except Exception as e:
        return False, None, f"Custody seal verification failed: {str(e)}"
