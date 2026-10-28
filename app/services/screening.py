"""
Effort, fraud and validity screening; device tiers; form counterbalancing
(tracker S3-SCR-01, S3-BAT-01.6, S3-BAT-04).

Everything here is a pure function over plain data so the rules are unit-
testable and the thresholds live in one versioned place. Screening never
blocks ingestion: every outcome is stored as data on the session
(`ParticipantSession.screening`) and only *norm eligibility* is derived from
it, so exclusion rates can be reported honestly in the dataset card.
"""

from __future__ import annotations

import hashlib
import hmac
import statistics
from typing import Any, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import (
    Battery,
    Participant,
    ParticipantSession,
    ParticipantSessionStatus,
    StudyEnrollment,
    TrialEvent,
)

# Versioned: bump `version` whenever any threshold changes. The version is
# stored on every session (ParticipantSession.tier_config_version).
TIER_CONFIG: dict[str, Any] = {
    "version": "tier_config-1.0.0",
    "a_max_p95_timing_error_ms": 8.0,
    "b_max_p95_timing_error_ms": 20.0,
    "a_max_dropped_frame_fraction": 0.01,
    "b_max_dropped_frame_fraction": 0.05,
}

SCREENING_CONFIG: dict[str, Any] = {
    "fast_response_ms": 150.0,
    "fast_response_session_fraction": 0.10,
    "invariant_min_trials": 10,
    "straight_line_min_items": 8,
    "straight_line_run_fraction": 0.90,
    "zigzag_min_items": 8,
    "max_failed_attention_checks": 2,
    "bot_min_completion_seconds": 30.0,
}


# ---------------------------------------------------------------------------
# Hashing (duplicate detection; flag, never block)
# ---------------------------------------------------------------------------


def _pepper() -> bytes:
    key = settings.audit_signing_key or settings.pii_encryption_key or settings.jwt_secret_key
    if not key:
        raise RuntimeError("No server secret configured for identifier hashing")
    return key.encode("utf-8")


def hash_identifier(value: str) -> str:
    """Peppered HMAC-SHA256 of a normalised identifier (email, fingerprint)."""
    return hmac.new(_pepper(), value.strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Device quality tiers
# ---------------------------------------------------------------------------


def timing_error_distribution(errors_ms: Sequence[float]) -> dict[str, float]:
    """Summarise per-trial timing errors by quantiles, not only the mean."""
    if not errors_ms:
        return {"n": 0}
    xs = sorted(abs(float(e)) for e in errors_ms)

    def q(p: float) -> float:
        return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]

    return {
        "n": len(xs),
        "mean": round(statistics.fmean(xs), 3),
        "median": round(q(0.5), 3),
        "p95": round(q(0.95), 3),
        "max": round(xs[-1], 3),
    }


def assign_tier(
    telemetry: dict[str, Any], failed_attention_checks: int, config: Optional[dict[str, Any]] = None
) -> tuple[str, list[str]]:
    """Return (tier, reasons). Tier C is excluded from norms; B only feeds
    sensitivity analyses; A feeds primary norms."""
    cfg = config or TIER_CONFIG
    reasons_c: list[str] = []
    reasons_b: list[str] = []

    p95 = float(telemetry.get("timing_error", {}).get("p95", 0.0))
    dropped = float(telemetry.get("dropped_frame_fraction", 0.0))

    if telemetry.get("is_mobile"):
        reasons_c.append("mobile_device")
    if int(telemetry.get("visibility_changes", 0)) > 0:
        reasons_c.append("tab_switch")
    if failed_attention_checks >= SCREENING_CONFIG["max_failed_attention_checks"]:
        reasons_c.append("failed_attention_checks")
    if p95 > cfg["b_max_p95_timing_error_ms"]:
        reasons_c.append("major_timing_jitter")
    if dropped > cfg["b_max_dropped_frame_fraction"]:
        reasons_c.append("dropped_frames")
    if reasons_c:
        return "C", reasons_c

    if p95 > cfg["a_max_p95_timing_error_ms"]:
        reasons_b.append("moderate_timing_noise")
    if dropped > cfg["a_max_dropped_frame_fraction"]:
        reasons_b.append("some_dropped_frames")
    if not telemetry.get("fullscreen", False):
        reasons_b.append("not_fullscreen")
    if failed_attention_checks == 1:
        reasons_b.append("one_failed_attention_check")
    if reasons_b:
        return "B", reasons_b
    return "A", []


# ---------------------------------------------------------------------------
# Effort / validity flags
# ---------------------------------------------------------------------------


def flag_fast_responses(rts_ms: Sequence[Optional[float]]) -> dict[str, Any]:
    cfg = SCREENING_CONFIG
    valid = [r for r in rts_ms if r is not None]
    fast = sum(1 for r in valid if r < cfg["fast_response_ms"])
    fraction = fast / len(valid) if valid else 0.0
    return {
        "n_fast": fast,
        "fraction": round(fraction, 4),
        "flag": bool(valid) and fraction > cfg["fast_response_session_fraction"],
    }


def flag_invariant_responding(
    keys: Sequence[Optional[str]], confidences: Sequence[Optional[float]]
) -> bool:
    """Same key on every trial *and* zero confidence variance."""
    cfg = SCREENING_CONFIG
    ks = [k for k in keys if k is not None]
    if len(ks) < cfg["invariant_min_trials"]:
        return False
    cs = [c for c in confidences if c is not None]
    zero_conf_var = len(cs) < 2 or statistics.pvariance(cs) == 0
    return len(set(ks)) == 1 and zero_conf_var


def flag_straight_lining(responses: Sequence[int]) -> bool:
    cfg = SCREENING_CONFIG
    if len(responses) < cfg["straight_line_min_items"]:
        return False
    modal = max(set(responses), key=list(responses).count)
    return bool(list(responses).count(modal) / len(responses) >= cfg["straight_line_run_fraction"])


def flag_zigzag(responses: Sequence[int]) -> bool:
    """Strict alternation between two values (1,5,1,5,...) or a monotone
    sweep repeated, the two patterns careless questionnaire responders emit."""
    cfg = SCREENING_CONFIG
    if len(responses) < cfg["zigzag_min_items"]:
        return False
    a, b = responses[0], responses[1]
    if a != b and all(r == (a if i % 2 == 0 else b) for i, r in enumerate(responses)):
        return True
    diffs = [responses[i + 1] - responses[i] for i in range(len(responses) - 1)]
    return len(set(diffs)) == 1 and diffs[0] != 0 and len(set(responses)) == len(responses)


def flag_bot(
    honeypot_value: Optional[str], completion_seconds: Optional[float], webdriver: bool = False
) -> list[str]:
    reasons = []
    if honeypot_value:
        reasons.append("honeypot_filled")
    if webdriver:
        reasons.append("webdriver_detected")
    if (
        completion_seconds is not None
        and completion_seconds < SCREENING_CONFIG["bot_min_completion_seconds"]
    ):
        reasons.append("implausibly_fast_completion")
    return reasons


# ---------------------------------------------------------------------------
# Session-level screening
# ---------------------------------------------------------------------------


def _find_duplicates(db: Session, session: ParticipantSession) -> list[str]:
    reasons: list[str] = []
    for column, label in (
        (ParticipantSession.device_fingerprint_hash, "duplicate_device_fingerprint"),
        (ParticipantSession.email_hash, "duplicate_email_hash"),
    ):
        value = getattr(session, column.key)
        if not value:
            continue
        clash = (
            db.query(ParticipantSession.participant_session_id)
            .filter(
                column == value,
                ParticipantSession.participant_id != session.participant_id,
            )
            .first()
        )
        if clash:
            reasons.append(label)
    return reasons


def screen_session(
    db: Session,
    session: ParticipantSession,
    questionnaire: Optional[Sequence[int]] = None,
    attention_checks: Optional[dict[str, int]] = None,
    bot_signals: Optional[dict[str, Any]] = None,
    telemetry: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Compute and persist the screening outcome for `session`.

    Duplicates and every other flag are recorded, never blocking. Norm
    eligibility (`norm_eligible`) is False for tier C, >=2 failed attention
    checks, bots, and any session after the participant's first eligible one
    for the same battery version.
    """
    trials = (
        db.query(TrialEvent)
        .filter(TrialEvent.participant_session_id == session.participant_session_id)
        .all()
    )
    flags: list[str] = []

    fast = flag_fast_responses([t.rt_ms for t in trials])  # type: ignore[misc]
    if fast["flag"]:
        flags.append("fast_responses")

    keys = [
        (t.response_value or {}).get("key") if isinstance(t.response_value, dict) else None
        for t in trials
    ]
    confs = [
        (t.response_value or {}).get("confidence") if isinstance(t.response_value, dict) else None
        for t in trials
    ]
    if flag_invariant_responding(keys, confs):
        flags.append("invariant_responding")

    if questionnaire:
        if flag_straight_lining(questionnaire):
            flags.append("straight_lining")
        if flag_zigzag(questionnaire):
            flags.append("zigzag_responding")

    checks = attention_checks or {"passed": 0, "failed": 0}
    failed = int(checks.get("failed", 0))
    if failed >= SCREENING_CONFIG["max_failed_attention_checks"]:
        flags.append("failed_attention_checks")

    bot_reasons = flag_bot(
        (bot_signals or {}).get("honeypot"),
        (bot_signals or {}).get("completion_seconds"),
        bool((bot_signals or {}).get("webdriver", False)),
    )
    flags.extend(f"bot:{r}" for r in bot_reasons)

    flags.extend(_find_duplicates(db, session))

    tier = str(session.device_tier) if session.device_tier else None
    if telemetry is not None:
        tier, tier_reasons = assign_tier(telemetry, failed)
    else:
        tier_reasons = []
    if tier:
        session.device_tier = tier  # type: ignore[assignment]

    excluded_reasons = [
        f for f in flags if f in ("failed_attention_checks",) or f.startswith("bot:")
    ]
    if tier == "C":
        excluded_reasons.append("device_tier_c")
    elif tier is None:
        excluded_reasons.append("no_device_telemetry")

    # Hard exclusions first; then one norm-contributing session per participant
    # per battery version (the earliest eligible one wins).
    eligible = not excluded_reasons
    if eligible and _has_earlier_norm_session(db, session):
        eligible = False
        excluded_reasons.append("repeat_session_for_battery_version")

    outcome = {
        "flags": flags,
        "fast_responses": fast,
        "attention_checks": {"passed": int(checks.get("passed", 0)), "failed": failed},
        "device_tier": tier,
        "tier_reasons": tier_reasons,
        "exclusion_reasons": excluded_reasons,
        "tier_config_version": session.tier_config_version or TIER_CONFIG["version"],
    }
    session.screening = outcome  # type: ignore[assignment]
    session.norm_eligible = eligible  # type: ignore[assignment]
    db.commit()
    return outcome


def _has_earlier_norm_session(db: Session, session: ParticipantSession) -> bool:
    battery = db.query(Battery).filter(Battery.battery_id == session.battery_id).first()
    version = battery.version if battery else None
    q = (
        db.query(ParticipantSession.participant_session_id)
        .join(Battery, Battery.battery_id == ParticipantSession.battery_id)
        .filter(
            ParticipantSession.participant_id == session.participant_id,
            ParticipantSession.study_id == session.study_id,
            ParticipantSession.norm_eligible.is_(True),
            ParticipantSession.participant_session_id != session.participant_session_id,
            Battery.version == version,
        )
    )
    return q.first() is not None


def email_hash_for(participant: Participant) -> Optional[str]:
    email = participant.encrypted_contact_email
    return hash_identifier(str(email)) if email else None


# ---------------------------------------------------------------------------
# Exclusion reporting (dataset card)
# ---------------------------------------------------------------------------


def exclusion_report(
    db: Session, study_id: str, battery_id: Optional[str] = None
) -> dict[str, Any]:
    q = db.query(ParticipantSession).filter(
        ParticipantSession.study_id == study_id,
        ParticipantSession.status == ParticipantSessionStatus.COMPLETED,
        ParticipantSession.norm_eligible.isnot(None),
    )
    if battery_id:
        q = q.filter(ParticipantSession.battery_id == battery_id)
    sessions = q.all()

    reasons: dict[str, int] = {}
    tiers: dict[str, int] = {"A": 0, "B": 0, "C": 0}
    excluded = 0
    for s in sessions:
        if s.device_tier in tiers:
            tiers[str(s.device_tier)] += 1
        if not s.norm_eligible:
            excluded += 1
            screening: dict[str, Any] = s.screening or {}  # type: ignore[assignment]
            for r in screening.get("exclusion_reasons", []):
                reasons[r] = reasons.get(r, 0) + 1
    n = len(sessions)
    return {
        "study_id": study_id,
        "battery_id": battery_id,
        "n_screened_sessions": n,
        "n_excluded_from_norms": excluded,
        "exclusion_rate": round(excluded / n, 4) if n else None,
        "by_reason": reasons,
        "by_device_tier": tiers,
        "tier_config_version": TIER_CONFIG["version"],
    }


# ---------------------------------------------------------------------------
# Parallel-form counterbalancing at enrolment
# ---------------------------------------------------------------------------


def balanced_latin_square(labels: Sequence[str], row: int) -> list[str]:
    """Row `row` of a Williams balanced Latin square over `labels`.

    Each form appears equally often in every serial position; for an even
    number of forms it also follows every other form equally often (odd
    counts would need the mirrored square for carry-over balance).
    """
    n = len(labels)
    if n == 0:
        return []
    seq: list[int] = []
    lo, hi = 0, n - 1
    for i in range(n):
        seq.append(lo if i % 2 == 0 else hi)
        if i % 2 == 0:
            lo += 1
        else:
            hi -= 1
    shift = row % n
    return [labels[(v + shift) % n] for v in seq]


def get_or_assign_form_order(db: Session, participant_id: str, study_id: str) -> list[str]:
    """Persist (once) the participant's parallel-form order for this study.

    Rows of the Williams square are handed out in enrolment order, so the
    assignment is balanced across the study and never changes afterwards.
    """
    enrollment = (
        db.query(StudyEnrollment)
        .filter(
            StudyEnrollment.participant_id == participant_id, StudyEnrollment.study_id == study_id
        )
        .first()
    )
    if enrollment is None:
        enrollment = StudyEnrollment(participant_id=participant_id, study_id=study_id)
        db.add(enrollment)
        db.flush()
    if enrollment.form_order:
        return list(enrollment.form_order)

    labels = sorted(
        {
            str(label)
            for (label,) in db.query(Battery.form_label).filter(Battery.study_id == study_id).all()
        }
    )
    n_prior = (
        db.query(func.count(StudyEnrollment.enrollment_id))
        .filter(StudyEnrollment.study_id == study_id, StudyEnrollment.form_order.isnot(None))
        .scalar()
        or 0
    )
    order = balanced_latin_square(labels, n_prior)
    enrollment.form_order = order  # type: ignore[assignment]
    db.flush()
    return order


def expected_form_label(order: Sequence[str], session_index: int) -> Optional[str]:
    return order[(session_index - 1) % len(order)] if order else None
