"""
store.py — dead-simple JSON-file persistence for ClaimBack.

Replaces the SQLAlchemy/SQLite layer. One human-readable file (claims.json)
holds every claim as a plain dict, so you can open it, show it in a demo, or
hand-edit it. Not concurrency-safe (full rewrite on save) — fine for a
single-user hackathon demo.

Storage shape:
    {"next_id": 4, "claims": [ {claim dict}, ... ]}

A claim dict uses the same keys the templates already expect (see index /
dashboard / claim_detail templates, which consume plain dicts).
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone

_BASE = os.path.abspath(os.path.dirname(__file__))
_PATH = os.environ.get("CLAIMS_FILE") or os.path.join(_BASE, "claims.json")

# Serialize file writes across Flask's threaded dev server.
_LOCK = threading.Lock()


# ── low-level load / save ────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    if not os.path.exists(_PATH):
        return {"next_id": 1, "claims": []}
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"next_id": 1, "claims": []}
    data.setdefault("next_id", 1)
    data.setdefault("claims", [])
    return data


def _save(data: dict) -> None:
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, _PATH)  # atomic on POSIX


# ── public API ───────────────────────────────────────────────────────────────

def all_claims() -> list[dict]:
    """Every claim, newest first."""
    claims = _load()["claims"]
    return sorted(claims, key=lambda c: c.get("created_at") or "", reverse=True)


def get_claim(claim_id: int) -> dict | None:
    for c in _load()["claims"]:
        if c.get("id") == claim_id:
            return c
    return None


def add_claim(document_names: list[str], ocr_used: bool = False,
              ocr_pages: int = 0, status: str = "pending") -> dict:
    """Create a bare claim record and return it (with its new id)."""
    with _LOCK:
        data = _load()
        claim = {
            "id": data["next_id"],
            "status": status,
            "created_at": _now(),
            "updated_at": _now(),
            "document_names": document_names,
            "ocr_used": ocr_used,
            "ocr_pages": ocr_pages,
            # analysis fields populated later
            "claim_type": None, "summary": None,
            "amount_str": None, "amount_cents": None,
            "strength": None, "strength_reason": None,
            "dispute_primary": None, "dispute_steps": [], "dispute_escalation": None,
            "legal_basis": [],
            "submit_name": None, "submit_method": None,
            "submit_address": None, "submit_form_url": None,
            "draft_letter": None,
            "raw_analysis": None,
            # submission / resolution
            "submission_reference": None,
            "resolved_at": None, "recovered_cents": None, "resolution_note": None,
        }
        data["claims"].append(claim)
        data["next_id"] += 1
        _save(data)
        return claim


def update_claim(claim_id: int, **fields) -> dict | None:
    """Merge arbitrary fields into a claim and bump updated_at."""
    with _LOCK:
        data = _load()
        for c in data["claims"]:
            if c.get("id") == claim_id:
                c.update(fields)
                c["updated_at"] = _now()
                _save(data)
                return c
    return None


def apply_analysis(claim_id: int, analysis: dict) -> dict | None:
    """Populate a claim from a Claude analysis JSON dict (mirrors the old
    Claim.update_from_analysis)."""
    dp = analysis.get("dispute_path", {}) or {}
    st = analysis.get("submit_to", {}) or {}
    raw_amount = analysis.get("amount_at_stake", "Unknown")
    return update_claim(
        claim_id,
        raw_analysis=analysis,
        claim_type=analysis.get("claim_type"),
        summary=analysis.get("summary"),
        strength=analysis.get("strength"),
        strength_reason=analysis.get("strength_reason"),
        legal_basis=analysis.get("legal_basis", []),
        draft_letter=analysis.get("draft_letter"),
        dispute_primary=dp.get("primary"),
        dispute_steps=dp.get("steps", []),
        dispute_escalation=dp.get("escalation"),
        submit_name=st.get("name"),
        submit_method=st.get("method"),
        submit_address=st.get("address"),
        submit_form_url=st.get("form_url"),
        amount_str=raw_amount,
        amount_cents=parse_cents(raw_amount),
        status="analyzed",
    )


def resolve_claim(claim_id: int, recovered_cents: int, note: str = "") -> dict | None:
    return update_claim(
        claim_id,
        status="resolved",
        resolved_at=_now(),
        recovered_cents=recovered_cents,
        resolution_note=note,
    )


def dashboard_stats() -> dict:
    claims = all_claims()
    resolved = [c for c in claims
                if c.get("status") == "resolved" and c.get("recovered_cents")]
    total_recovered = sum(c.get("recovered_cents") or 0 for c in resolved)
    pending = len([c for c in claims
                   if c.get("status") in ("analyzed", "approved", "submitted")])
    return {
        "total": len(claims),
        "resolved_count": len(resolved),
        "pending_count": pending,
        "total_recovered": total_recovered,   # cents
    }


# ── helpers ──────────────────────────────────────────────────────────────────

def parse_cents(amount_str) -> int | None:
    """Parse '$1,234.56' or '€400' → integer cents. None if unparseable."""
    if not amount_str or str(amount_str).lower() in ("unknown", "n/a", "—", ""):
        return None
    digits = re.sub(r"[^\d.]", "", str(amount_str))
    try:
        return int(round(float(digits) * 100))
    except ValueError:
        return None
