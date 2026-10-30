"""CI gates that must hold on every commit (XC-CI-06): frozen OpenAPI, single migration head."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_openapi_has_not_drifted():
    import json

    from scripts.export_openapi import TARGET, build_spec

    frozen = json.loads(TARGET.read_text())
    assert frozen == build_spec(), "run `python scripts/export_openapi.py` and commit the result"


def test_frozen_openapi_pins_the_canonical_research_routes():
    import json

    from scripts.export_openapi import TARGET

    paths = json.loads(TARGET.read_text())["paths"]
    for required in (
        "/v1/participant-sessions/{participant_session_id}/trials",
        "/v1/participant-sessions/{participant_session_id}/events",
        "/v1/participant-sessions/{participant_session_id}/responses",
        "/v1/instruments/{instrument_id}/versions/{version}",
        "/v1/participants/{participant_id}/export",
        "/v1/reference-datasets/{version}/card",
        "/v1/meta",
    ):
        assert required in paths, required
    assert not any(p.startswith("/v1/profiling") for p in paths)


def test_alembic_has_exactly_one_head():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "app" / "alembic"))
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1
