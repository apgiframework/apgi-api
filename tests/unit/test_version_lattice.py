import pytest

from app.services.version_lattice import (
    ALL_STAMPS,
    MANDATORY_STAMPS,
    StampError,
    build_stamps,
    validate_stamps,
)


def test_build_stamps_has_every_lattice_field():
    stamps = build_stamps(battery_version="1.0")
    assert set(stamps) == set(ALL_STAMPS)
    assert validate_stamps(stamps) is stamps
    assert stamps["api_contract"] == "v1"
    assert "research_participation" in stamps["consent_version"]


@pytest.mark.parametrize("field", MANDATORY_STAMPS)
def test_missing_mandatory_stamp_is_rejected(field):
    stamps = build_stamps(battery_version="1.0")
    del stamps[field]
    with pytest.raises(StampError):
        validate_stamps(stamps)


def test_null_battery_version_rejected():
    with pytest.raises(StampError):
        validate_stamps(build_stamps(battery_version=None))


def test_absent_nullable_stamp_rejected_but_null_allowed():
    stamps = build_stamps(battery_version="1.0", instrument_id="apgi-explorer-35")
    assert stamps["model_id"] is None
    del stamps["client_build"]
    with pytest.raises(StampError):
        validate_stamps(stamps)


def test_unknown_provenance_stamp_rejected():
    with pytest.raises(StampError):
        build_stamps(battery_version="1.0", bogus="x")
