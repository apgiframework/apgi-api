# mypy: disable-error-code="index,call-overload,comparison-overlap,misc,type-arg"
"""
Scientific data-quality indicators (S4-ANL-04, first slice).

The master plan's "Scientific quality dashboard" lists fourteen indicators.
Each is returned with an explicit status:

* ``measured``   - computed from stored data
* ``partial``    - computed, but part of the indicator has no stored source
* ``unmeasured`` - no data source exists yet; ``value`` is None and ``reason`` says why

Everything is aggregate; no participant or session identifiers are returned.
Filters (study, battery/protocol, origin) apply wherever the source can be
scoped to sessions; indicators that cannot be scoped say so.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import (
    AcquisitionUpload,
    Battery,
    OpsJob,
    ParticipantSession,
    ParticipantSessionStatus,
    StudyEnrollment,
    TrialEvent,
)
from app.services import job_tracker
from app.services.ops_monitor import research_scope, with_origin

MAX_WAVES = 4


def _indicator(
    n: int,
    key: str,
    name: str,
    reason: str,
    status: str,
    value: Any = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "n": n,
        "key": key,
        "name": name,
        "why": reason,
        "status": status,
        "value": value,
        "note": note,
    }


def _unmeasured(n: int, key: str, name: str, why: str, note: str) -> Dict[str, Any]:
    return _indicator(n, key, name, why, "unmeasured", None, note)


def _rate(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def _base_query(
    db: Session, study_id: Optional[str], battery_id: Optional[str], origin: Optional[str]
) -> Any:
    q = with_origin(db.query(ParticipantSession), origin)
    if study_id:
        q = q.filter(ParticipantSession.study_id == study_id)
    if battery_id:
        q = q.filter(ParticipantSession.battery_id == battery_id)
    return q


def compute(
    db: Session,
    study_id: Optional[str] = None,
    battery_id: Optional[str] = None,
    origin: Optional[str] = None,
) -> Dict[str, Any]:
    in_filter = _base_query(db, study_id, battery_id, origin)
    sessions: List[ParticipantSession] = research_scope(in_filter).all()
    withheld = in_filter.count() - len(sessions)
    ids = {s.participant_session_id for s in sessions}
    total = len(sessions)
    out: List[Dict[str, Any]] = []

    def safe(n: int, key: str, name: str, why: str, fn: Callable[[], Dict[str, Any]]) -> None:
        try:
            out.append(fn())
        except Exception:
            db.rollback()
            out.append(_unmeasured(n, key, name, why, "Source query failed; see server logs."))

    # 1 sessions by protocol
    def protocols() -> Dict[str, Any]:
        names = (
            {
                b.battery_id: f"{b.name} v{b.version}"
                for b in db.query(Battery).filter(
                    Battery.battery_id.in_({s.battery_id for s in sessions})
                )
            }
            if sessions
            else {}
        )
        dist = Counter(names.get(s.battery_id, "unknown") for s in sessions)
        return _indicator(
            1,
            "sessions_by_protocol",
            "Participant sessions by protocol",
            "Research progress",
            "measured",
            dict(dist),
        )

    safe(
        1,
        "sessions_by_protocol",
        "Participant sessions by protocol",
        "Research progress",
        protocols,
    )

    # 2 complete vs interrupted
    def completion() -> Dict[str, Any]:
        c = Counter(str(getattr(s.status, "value", s.status)) for s in sessions)
        v = {
            "total": total,
            "completed": c["completed"],
            "abandoned": c["abandoned"],
            "in_progress": c["in_progress"],
            "completion_rate": _rate(c["completed"], total),
            "interruption_rate": _rate(c["abandoned"], total),
        }
        return _indicator(
            2, "completion", "Complete versus interrupted sessions", "Data yield", "measured", v
        )

    safe(2, "completion", "Complete versus interrupted sessions", "Data yield", completion)

    # 3 missing trial events
    def seq_gaps() -> Dict[str, Any]:
        rows = (
            db.query(
                TrialEvent.participant_session_id,
                func.min(TrialEvent.seq_no),
                func.max(TrialEvent.seq_no),
                func.count(TrialEvent.seq_no),
            )
            .filter(TrialEvent.seq_no.isnot(None))
            .group_by(TrialEvent.participant_session_id)
            .all()
        )
        rows = [r for r in rows if r[0] in ids]
        missing = [(hi - lo + 1) - n for _, lo, hi, n in rows]
        v = {
            "sessions_checked": len(rows),
            "sessions_with_gaps": sum(1 for m in missing if m > 0),
            "missing_events": sum(m for m in missing if m > 0),
        }
        return _indicator(
            3,
            "missing_events",
            "Missing trial events / sequence gaps",
            "Data integrity",
            "measured",
            v,
        )

    safe(3, "missing_events", "Missing trial events / sequence gaps", "Data integrity", seq_gaps)

    # 4 chunk gaps (acquisition sessions are not linked to participant sessions)
    def chunk_gaps() -> Dict[str, Any]:
        by_session: Dict[str, List[int]] = {}
        for sid, seq in db.query(AcquisitionUpload.session_id, AcquisitionUpload.sequence).filter(
            AcquisitionUpload.status == "accepted", AcquisitionUpload.sequence.isnot(None)
        ):
            by_session.setdefault(sid, []).append(seq)
        gaps = {k: (max(v) - min(v) + 1) - len(set(v)) for k, v in by_session.items()}
        v = {
            "acquisition_sessions": len(by_session),
            "sessions_with_gaps": sum(1 for g in gaps.values() if g > 0),
            "missing_chunks": sum(g for g in gaps.values() if g > 0),
        }
        return _indicator(
            4,
            "chunk_gaps",
            "Signal chunk gaps",
            "Acquisition completeness",
            "partial",
            v,
            "Acquisition sessions are not linked to participant sessions, so study, "
            "protocol and origin filters do not apply.",
        )

    safe(4, "chunk_gaps", "Signal chunk gaps", "Acquisition completeness", chunk_gaps)

    # 5 device / tier
    def tiers() -> Dict[str, Any]:
        dist = Counter(s.device_tier or "unknown" for s in sessions)
        return _indicator(
            5,
            "device_tier",
            "Device, browser and tier distribution",
            "Measurement comparability",
            "partial",
            {
                "tier": dict(dist),
                "browser_family": dict(
                    Counter(x.browser_family or "not reported" for x in sessions)
                ),
            },
            "Device model is not stored; browser is client-reported.",
        )

    safe(
        5,
        "device_tier",
        "Device, browser and tier distribution",
        "Measurement comparability",
        tiers,
    )

    # 6 administration mode / app version
    def admin_mode() -> Dict[str, Any]:
        v = {
            "administration_mode": dict(
                Counter(x.administration_mode or "not reported" for x in sessions)
            ),
            "app_version": dict(Counter(x.app_version or "not reported" for x in sessions)),
            "sessions_not_reporting_mode": sum(1 for x in sessions if not x.administration_mode),
        }
        return _indicator(
            6,
            "admin_mode_version",
            "Administration mode and app-version distribution",
            "Mode effects; instrument-version consistency",
            "measured",
            v,
            "Values are client-reported at session creation; older sessions report nothing.",
        )

    safe(
        6,
        "admin_mode_version",
        "Administration mode and app-version distribution",
        "Mode effects; instrument-version consistency",
        admin_mode,
    )

    # 7 clock sync
    def clock() -> Dict[str, Any]:
        p95s = sorted(
            float(s.timing_error_distribution["p95"])
            for s in sessions
            if isinstance(s.timing_error_distribution, dict)
            and "p95" in s.timing_error_distribution
        )
        v = {
            "sessions_with_timing": len(p95s),
            "sessions_without_timing": total - len(p95s),
            "median_session_p95_error_ms": p95s[len(p95s) // 2] if p95s else None,
            "worst_session_p95_error_ms": p95s[-1] if p95s else None,
        }
        return _indicator(
            7, "clock_sync", "Clock synchronisation quality", "Timing accuracy", "measured", v
        )

    safe(7, "clock_sync", "Clock synchronisation quality", "Timing accuracy", clock)

    # 8 exclusions
    def exclusions() -> Dict[str, Any]:
        screened = [s for s in sessions if isinstance(s.screening, dict)]
        reasons: Counter = Counter()
        for s in screened:
            reasons.update(s.screening.get("exclusion_reasons", []))
        excluded = sum(1 for s in screened if s.norm_eligible is False)
        v = {
            "screened": len(screened),
            "unscreened": total - len(screened),
            "excluded": excluded,
            "exclusion_rate": _rate(excluded, len(screened)),
            "reasons": dict(reasons),
        }
        return _indicator(
            8,
            "exclusions",
            "Artifact rejection and validity-screen exclusion rates",
            "Signal and data quality",
            "measured",
            v,
        )

    safe(
        8,
        "exclusions",
        "Artifact rejection and validity-screen exclusion rates",
        "Signal and data quality",
        exclusions,
    )

    # 9 analysis completion
    def analysis() -> Dict[str, Any]:
        done = [s for s in sessions if s.status == ParticipantSessionStatus.COMPLETED]
        scored = sum(1 for s in done if s.scores is not None)
        jobs: Counter = Counter()
        if ids:
            for j in db.query(OpsJob).filter(
                OpsJob.kind == job_tracker.SCORING, OpsJob.ref.in_(ids)
            ):
                jobs[job_tracker.classify(j)] += 1
        v = {
            "completed_sessions": len(done),
            "scored": scored,
            "awaiting_scoring": len(done) - scored,
            "scoring_rate": _rate(scored, len(done)),
            "scoring_jobs": dict(jobs),
        }
        return _indicator(
            9, "analysis_status", "Analysis completion status", "Pipeline health", "measured", v
        )

    safe(9, "analysis_status", "Analysis completion status", "Pipeline health", analysis)

    # 10 versions
    def versions() -> Dict[str, Any]:
        v = {
            "model_version": dict(Counter(s.model_version_id or "unscored" for s in sessions)),
            "tier_config_version": dict(
                Counter(s.tier_config_version or "unset" for s in sessions)
            ),
            "battery": protocols()["value"] if sessions else {},
        }
        return _indicator(
            10,
            "versions",
            "Protocol, battery and gate version distribution",
            "Version consistency",
            "partial",
            v,
            "Gate (K-gate) versions are not recorded per session.",
        )

    safe(
        10,
        "versions",
        "Protocol, battery and gate version distribution",
        "Version consistency",
        versions,
    )

    # 11 partitions (assigned per participant at enrolment; immutable once set)
    def partitions() -> Dict[str, Any]:
        pids = {x.participant_id for x in sessions}
        enr = (
            db.query(StudyEnrollment).filter(StudyEnrollment.participant_id.in_(pids))
            if pids
            else None
        )
        if enr is not None and study_id:
            enr = enr.filter(StudyEnrollment.study_id == study_id)
        rows = enr.all() if enr is not None else []
        part_of = {(e.participant_id, e.study_id): e.partition for e in rows}
        # A participant with no enrollment row has no partition either.
        participants = Counter(
            part_of.get((pid, sid)) or "unassigned"
            for pid, sid in {(x.participant_id, x.study_id) for x in sessions}
        )
        by_session = Counter(
            part_of.get((x.participant_id, x.study_id)) or "unassigned" for x in sessions
        )
        v = {
            "participants": dict(participants),
            "sessions": dict(by_session),
            "unassigned_participants": participants.get("unassigned", 0),
        }
        return _indicator(
            11,
            "holdout_separation",
            "Confirmatory/exploratory and holdout separation",
            "Inferential integrity",
            "partial",
            v,
            "Storage and a set-once assignment endpoint exist; no assignment policy is automated, "
            "so partitions are only as complete as researchers make them.",
        )

    safe(
        11,
        "holdout_separation",
        "Confirmatory/exploratory and holdout separation",
        "Inferential integrity",
        partitions,
    )
    out.append(
        _unmeasured(
            12,
            "kgate_classifications",
            "Prediction and K-gate classifications",
            "Scientific findings",
            "No validation results are registered yet (S4-ANL-05).",
        )
    )
    out.append(
        _unmeasured(
            13,
            "reproduction",
            "Reproduction success",
            "Independent verification",
            "No reproduction runs are recorded yet (S4-ANL-03).",
        )
    )

    # 14 retention per wave
    def retention() -> Dict[str, Any]:
        max_idx: Dict[str, int] = {}
        for s in sessions:
            if s.status == ParticipantSessionStatus.COMPLETED:
                max_idx[s.participant_id] = max(
                    max_idx.get(s.participant_id, 0), int(s.session_index)
                )
        base = len(max_idx)
        waves = {
            f"wave_{k}": {
                "participants": sum(1 for m in max_idx.values() if m >= k),
                "retention": _rate(sum(1 for m in max_idx.values() if m >= k), base),
            }
            for k in range(1, MAX_WAVES + 1)
        }
        return _indicator(
            14,
            "retention",
            "Retention per wave",
            "Attrition and MNAR risk",
            "measured",
            {"participants_completing_wave_1": base, "waves": waves},
            "Counts completed sessions by session_index; the wave schedule itself is not stored.",
        )

    safe(14, "retention", "Retention per wave", "Attrition and MNAR risk", retention)

    out.sort(key=lambda i: i["n"])
    available = sum(1 for i in out if i["status"] in ("measured", "partial"))
    return {
        "filters": {"study_id": study_id, "battery_id": battery_id, "origin": origin},
        "session_count": total,
        "withheld_sessions": withheld,
        "withheld_reason": "Erased participants, and non-synthetic sessions without active "
        "research-participation consent, are excluded from research-purpose views.",
        "indicators": out,
        "available": available,
        "planned": 14,
    }
