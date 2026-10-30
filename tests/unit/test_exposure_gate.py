"""PR 2b: exposure gate, allow-list start-up check, outbound-field CI test, dev-route stripping."""

import pytest

from app import main as app_main
from app.main import create_app
from app.modules.scoring import exposure
from app.modules.scoring.exposure import ExposureGate, GateTableError, GateViolation

LATENT_FIELDS = {
    "ignition_threshold",
    "precision_exteroceptive",
    "precision_interoceptive",
    "prediction_error_exteroceptive",
    "prediction_error_interoceptive",
    "somatic_bias",
    "heart_rate",
    "cortisol",
    "temperature",
}


def test_unknown_field_raises_never_nulls():
    with pytest.raises(GateViolation):
        ExposureGate().check({"session_id": "s", "precision_exteroceptive": None})


def test_nested_unknown_field_raises():
    with pytest.raises(GateViolation):
        ExposureGate().check({"identifiability_gate": [{"parameter": "x", "heart_rate": 70}]})


def test_output_is_stamped_with_gate_table_version():
    gate = ExposureGate()
    assert gate.check({"session_id": "s"})["_gate_table_version"] == gate.version


def test_service_refuses_to_start_without_valid_allow_list(monkeypatch, tmp_path):
    bad = tmp_path / "gate_table.yaml"
    bad.write_text("gate_table_version: '1'\n")
    monkeypatch.setattr(exposure, "GATE_TABLE_PATH", bad)
    exposure.load_gate_table.cache_clear()
    try:
        with pytest.raises(GateTableError):
            exposure.load_gate_table()
    finally:
        monkeypatch.undo()
        exposure.load_gate_table.cache_clear()


def test_gated_route_schemas_only_expose_allow_listed_fields():
    """CI gate: fail on any non-allowed outbound field name in the latent routes' schemas."""
    app = create_app(test_mode=True)
    spec = app.openapi()
    gate = ExposureGate()

    def props(schema):
        if "$ref" in schema:
            schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
        yield from schema.get("properties", {})
        for sub in schema.get("properties", {}).values():
            if sub.get("type") == "array":
                yield from props(sub["items"])
            elif "$ref" in sub:
                yield from props(sub)

    checked = 0
    for path, item in spec["paths"].items():
        if path.rsplit("/", 1)[-1] in {
            "ignition-history",
            "interoception",
            "prediction-errors",
            "somatic-markers",
            "state-vector",
        }:
            schema = item["get"]["responses"]["200"]["content"]["application/json"]["schema"]
            names = set(props(schema))
            assert names <= gate.allowed, f"{path}: {names - gate.allowed}"
            assert not names & LATENT_FIELDS
            checked += 1
    assert checked == 5


@pytest.mark.parametrize(
    "path", ["/v1/users/create-default", "/v1/profiling/memory/start", "/v1/profiling/system"]
)
def test_dev_routes_absent_in_production(monkeypatch, path):
    assert path in create_app(test_mode=True).openapi()["paths"]
    monkeypatch.setattr(app_main.settings, "environment", "production")
    assert path not in create_app(test_mode=True).openapi()["paths"]


def _cleared_table(**scope_overrides):
    scope = {
        "model": "apgi",
        "model_version": "1.0",
        "observation_model": "gaussian",
        "task_design": "oddball-v1",
        "population": "adult-online",
        **scope_overrides,
    }
    return {
        "gate_table_version": "9.9.9",
        "outbound_allow_list": ["parameter"],
        "latent_parameters": {
            "precision_exteroceptive": {
                "status": "cleared",
                "evidence": {
                    "validation_id": "val-1",
                    "dataset_sha256": "a" * 64,
                    "reviewer": "r",
                    "scope": scope,
                },
            }
        },
    }


def test_clearance_is_scoped_to_the_paradigm_where_recovery_was_shown():
    from app.modules.scoring.exposure import _validate_clearances

    table = _cleared_table()
    _validate_clearances(table["latent_parameters"])
    gate = ExposureGate(table)
    assert gate.is_cleared("precision_exteroceptive", task_design="oddball-v1")
    assert not gate.is_cleared("precision_exteroceptive", task_design="other-task")
    assert not gate.is_cleared("somatic_bias")


def test_clearance_without_evidence_or_scope_fails_closed_at_load():
    import pytest

    from app.modules.scoring.exposure import GateTableError, _validate_clearances

    table = _cleared_table(population="")
    with pytest.raises(GateTableError, match="scope.population"):
        _validate_clearances(table["latent_parameters"])
    table = _cleared_table()
    del table["latent_parameters"]["precision_exteroceptive"]["evidence"]["reviewer"]
    with pytest.raises(GateTableError, match="reviewer"):
        _validate_clearances(table["latent_parameters"])
