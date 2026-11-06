"""DEF-04: clearance needs complete, exactly-matching scope and well-formed evidence."""

import pytest

from app.modules.scoring.exposure import SCOPE_FIELDS, ExposureGate, GateTableError, load_gate_table

SCOPE = {f: f"{f}-1" for f in SCOPE_FIELDS}


def _table(sha="a" * 64):
    base = load_gate_table()
    return {
        **base,
        "latent_parameters": {
            "ignition_threshold": {
                "status": "cleared",
                "evidence": {
                    "validation_id": "V1",
                    "dataset_sha256": sha,
                    "reviewer": "r",
                    "scope": dict(SCOPE),
                },
            },
            "somatic_bias": {"status": "withheld", "evidence": None},
        },
    }


def test_zero_context_is_never_cleared():
    assert ExposureGate(_table()).is_cleared("ignition_threshold") is False


def test_partial_or_extra_context_is_not_cleared():
    gate = ExposureGate(_table())
    partial = dict(list(SCOPE.items())[:4])
    assert gate.is_cleared("ignition_threshold", **partial) is False
    assert gate.is_cleared("ignition_threshold", **SCOPE, extra="x") is False


def test_full_matching_context_is_cleared_and_mismatch_is_not():
    gate = ExposureGate(_table())
    assert gate.is_cleared("ignition_threshold", **SCOPE) is True
    assert gate.is_cleared("ignition_threshold", **{**SCOPE, "population": "other"}) is False
    assert gate.is_cleared("somatic_bias", **SCOPE) is False
    assert gate.is_cleared("unknown", **SCOPE) is False


def test_gate_state_uses_caller_context():
    gate = ExposureGate(_table())
    no_ctx = gate.gate_state("s", "ignition_threshold")
    assert no_ctx.identifiability_gate[0].exposed is False
    ok = gate.gate_state("s", "ignition_threshold", context=SCOPE)
    assert ok.identifiability_gate[0].exposed is True
    other = gate.gate_state("s", "ignition_threshold", context={**SCOPE, "task_design": "x"})
    assert other.identifiability_gate[0].exposed is False


def test_malformed_dataset_hash_is_rejected_at_load_and_at_use():
    from app.modules.scoring.exposure import _validate_clearances

    with pytest.raises(GateTableError):
        _validate_clearances(_table(sha="not-a-hash")["latent_parameters"])
    assert ExposureGate(_table(sha="nope")).is_cleared("ignition_threshold", **SCOPE) is False
