"""Server-side contract for the Explorer research client.

Everything the client may not invent lives here: the pinned item bank and its
hash, the canonical consent texts, the score allow-list and the scoring key.
The client renders what this module serves and displays only the fields the
allow-list names.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import settings

_DATA = Path(__file__).resolve().parents[1] / "data"
INSTRUMENT_ID = "apgi-explorer-35"
ANALYSIS_MODE = "exploratory"
ITEM_IDS = frozenset(range(1, 36))
NOMINAL_ITEM_IDS = frozenset({7, 9})

# subscale -> (response field, user-facing label). Only these fields leave the
# server; the three unlabelled subscales stay server-side until reviewed.
SCORE_ALLOWLIST: dict[str, dict[str, str]] = {
    "epsilon": {
        "field": "reported_reactivity_to_the_unexpected",
        "label": "Reported reactivity to the unexpected",
    },
    "precision": {
        "field": "reported_information_prioritisation",
        "label": "Reported information prioritisation",
    },
    "threshold": {
        "field": "reported_attentional_threshold",
        "label": "Reported attentional threshold",
    },
    "somatic": {
        "field": "reported_body_signal_attention",
        "label": "Reported body-signal attention",
    },
}


def canonical_json(value: Any) -> str:
    """The one canonical serialisation hashed by both client and server."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def item_bank() -> dict[str, Any]:
    bank = json.loads((_DATA / "instruments" / f"{INSTRUMENT_ID}.json").read_text("utf-8"))
    bank["sha256"] = sha256_hex(canonical_json({k: v for k, v in bank.items() if k != "sha256"}))
    return bank


@lru_cache(maxsize=1)
def consent_texts() -> list[dict[str, Any]]:
    texts = json.loads((_DATA / "consent_texts.json").read_text("utf-8"))["texts"]
    for text in texts:
        text["sha256"] = sha256_hex(text["body"])
    return texts


def consent_text_for(consent_type: str, locale: str = "en") -> dict[str, Any] | None:
    """Canonical text for the *current configured* version of a purpose."""
    current = settings.consent_current_versions.get(consent_type)
    for text in consent_texts():
        if (
            text["consent_type"] == consent_type
            and text["locale"] == locale
            and text["version"] == current
        ):
            return text
    return None


def research_enabled() -> bool:
    """Research mode is on only when explicitly enabled AND every text is approved."""
    if not settings.explorer_research_enabled:
        return False
    return all(
        (consent_text_for(ct, "en") or {}).get("status") == "approved"
        for ct in settings.consent_current_versions
    )


def validate_payload(payload: dict[str, Any]) -> str | None:
    """Return an error message when an Explorer answer payload breaks the bank."""
    if set(payload) - {"item_id", "value", "previous_value", "latency_us", "instrument_sha256"}:
        return "unknown payload field"
    item_id, value = payload.get("item_id"), payload.get("value")
    if type(item_id) is not int or item_id not in ITEM_IDS:
        return "unknown item_id"
    if type(value) is not int or not 1 <= value <= 4:
        return "value outside 1-4"
    previous = payload.get("previous_value")
    if previous is not None and (type(previous) is not int or not 1 <= previous <= 4):
        return "previous_value outside 1-4"
    latency = payload.get("latency_us")
    if latency is not None and (type(latency) is not int or latency < 0):
        return "latency_us must be a non-negative integer"
    if payload.get("instrument_sha256") != item_bank()["sha256"]:
        return "instrument_sha256 does not match the pinned item bank"
    return None


def score_answers(answers: dict[int, int]) -> dict[str, float]:
    """Equal-weight item means on the 12.5/37.5/62.5/87.5 display scale.

    Items 7 and 9 are nominal and excluded from ordinal means. Subscales are
    independent: no cross-subscale terms, no reliability weights, no intervals.
    """
    if set(answers) != ITEM_IDS:
        raise ValueError("all 35 items are required")
    scores: dict[str, float] = {}
    for index, subscale in enumerate(
        ["epsilon", "precision", "threshold", "somatic", "interactions", "temporal", "environment"]
    ):
        ids = [i for i in range(index * 5 + 1, index * 5 + 6) if i not in NOMINAL_ITEM_IDS]
        scores[subscale] = sum((answers[i] - 0.5) * 25 for i in ids) / len(ids)
    return scores


def allowlisted(scores: dict[str, float]) -> dict[str, float]:
    return {
        entry["field"]: round(scores[subscale], 4) for subscale, entry in SCORE_ALLOWLIST.items()
    }
