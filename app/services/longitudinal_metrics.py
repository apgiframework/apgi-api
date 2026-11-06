"""
Longitudinal metrics: ICC, SEM, MDC95, computed per repeated-measures index
(Phase 5, governing doc §5.2).

Design choice, stated plainly rather than buried in a formula: this
implements ICC(1,1) (one-way random-effects, single measurement, absolute
agreement) via the classic one-way ANOVA decomposition — the simplest
defensible model when there is no reason to treat "session index" itself as
a systematic fixed effect shared identically across subjects (a two-way
model would be more appropriate if e.g. every subject is assessed on
exactly the same calendar occasions). Revisit this choice before the
"calibrated" release gate if the actual repeated-measures design turns out
to have a shared-occasion structure ICC(2,1)/ICC(3,1) would model better.

Requires a balanced design: exactly `k` measurements per subject, achieved
by the caller intersecting to a fixed pair (or set) of session_index values
before calling in (app/routes/longitudinal.py filters to sessions having
scores at both requested indices).
"""

from __future__ import annotations

import math
import statistics
from typing import NamedTuple, Sequence


class LongitudinalMetrics(NamedTuple):
    n_subjects: int
    k_measurements: int
    icc: float
    sem: float
    mdc95: float


def compute_longitudinal_metrics(measurements: Sequence[Sequence[float]]) -> LongitudinalMetrics:
    """`measurements` is one row per subject, each row exactly `k` values
    (k >= 2) — e.g. [[session1_score, session2_score], ...] across subjects
    who completed both sessions. Requires at least 2 subjects.
    """
    n = len(measurements)
    if n < 2:
        raise ValueError("At least 2 subjects are required to compute ICC/SEM/MDC95.")
    k = len(measurements[0])
    if k < 2 or any(len(row) != k for row in measurements):
        raise ValueError("Every subject must have the same number (>= 2) of measurements.")

    all_values = [v for row in measurements for v in row]
    grand_mean = statistics.mean(all_values)
    subject_means = [statistics.mean(row) for row in measurements]

    # Between-subjects sum of squares and mean square.
    ss_between = k * sum((m - grand_mean) ** 2 for m in subject_means)
    df_between = n - 1
    ms_between = ss_between / df_between

    # Within-subject (error) sum of squares and mean square.
    ss_within = sum((v - subject_means[i]) ** 2 for i, row in enumerate(measurements) for v in row)
    df_within = n * (k - 1)
    ms_within = ss_within / df_within if df_within > 0 else 0.0

    denominator = ms_between + (k - 1) * ms_within
    icc = (ms_between - ms_within) / denominator if denominator != 0 else 0.0
    icc = max(0.0, min(1.0, icc))  # ICC is only meaningful in [0, 1]; clip numerical noise.

    total_sd = statistics.pstdev(all_values) if len(set(all_values)) > 1 else 0.0
    sem = total_sd * math.sqrt(max(0.0, 1 - icc))
    mdc95 = sem * 1.96 * math.sqrt(2)

    return LongitudinalMetrics(
        n_subjects=n,
        k_measurements=k,
        icc=round(icc, 4),
        sem=round(sem, 4),
        mdc95=round(mdc95, 4),
    )


def classify_change(delta: float, mdc95: float) -> str:
    """MDC-gated change classification: a raw delta is only reported as a
    real change once it *exceeds* MDC95 — otherwise it's within measurement
    noise and must not be presented as a change (Phase 5's MDC-gated
    subscription change-reporting requirement).

    Degenerate inputs are never reliable change: a zero, negative, NaN or
    infinite MDC means the noise floor is unknown, and a zero or NaN delta
    is no change at all. Equality with MDC is not "exceeds"."""
    if not (math.isfinite(delta) and math.isfinite(mdc95)) or mdc95 <= 0 or delta == 0:
        return "no_reliable_change"
    if abs(delta) <= mdc95:
        return "no_reliable_change"
    return "reliable_increase" if delta > 0 else "reliable_decrease"
