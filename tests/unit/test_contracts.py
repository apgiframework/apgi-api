"""Frozen v1.0.0 event contracts: no drift from the live models; fixtures validate."""

import json
from pathlib import Path

import jsonschema
import pytest

from app.schemas.research import TrialEventIngestRequest
from scripts.generate_contracts import trial_event_schema

ROOT = Path(__file__).resolve().parents[2] / "contracts"


def _load(path: str):
    return json.loads((ROOT / path).read_text())


def test_trial_event_schema_matches_live_model():
    assert _load("events/trial-event-1.0.0.json") == json.loads(
        json.dumps(trial_event_schema(), sort_keys=True)
    )


def test_trial_event_fixture_validates_in_schema_and_model():
    fixture = _load("fixtures/trial-event.valid.json")
    jsonschema.validate(fixture, _load("events/trial-event-1.0.0.json"))
    TrialEventIngestRequest(**fixture)


def test_item_response_fixture_validates():
    jsonschema.validate(
        _load("fixtures/item-response.valid.json"), _load("events/item-response-1.0.0.json")
    )


@pytest.mark.parametrize("extra", [{"score": 12}, {"total_score": 3}])
def test_item_response_rejects_client_scores(extra):
    bad = {**_load("fixtures/item-response.valid.json"), **extra}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _load("events/item-response-1.0.0.json"))


def test_trial_event_requires_idempotency_fields():
    bad = {k: v for k, v in _load("fixtures/trial-event.valid.json").items() if k != "event_id"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _load("events/trial-event-1.0.0.json"))


@pytest.mark.parametrize(
    "filename", sorted(__import__("scripts.generate_contracts", fromlist=["x"]).all_schemas())
)
def test_every_frozen_schema_matches_its_live_model(filename):
    from scripts.generate_contracts import all_schemas

    assert _load(f"events/{filename}") == json.loads(
        json.dumps(all_schemas()[filename], sort_keys=True)
    )


def test_server_rejects_incompatible_trial_event_predictably():
    from pydantic import ValidationError

    bad = {**_load("fixtures/trial-event.valid.json"), "unexpected_field": 1}
    with pytest.raises(ValidationError):
        TrialEventIngestRequest(**bad)
