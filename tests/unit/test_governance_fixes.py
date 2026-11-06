"""Regression tests for audit items NEW-01/02/04/09 (server grading, consent recheck, prod aliases, erasure key)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base
from app.services.task_grading import grade_trial

KEYS = {"stroop": {"stimulus_field": "stim", "response_field": "resp", "answers": {"red": "r"}}}


def test_grading_is_server_side_and_ungraded_without_key():
    assert grade_trial("stroop", {"stim": "red", "resp": "r"}, KEYS) is True
    assert grade_trial("stroop", {"stim": "red", "resp": "g"}, KEYS) is False
    assert grade_trial("unknown", {"stim": "red", "resp": "r"}, KEYS) is None
    assert grade_trial("stroop", None, KEYS) is None
    assert grade_trial("stroop", {"stim": "blue", "resp": "r"}, KEYS) is None


def test_scoring_discarded_after_consent_withdrawal():
    from app.config import settings
    from app.database.models import (
        Battery,
        Consent,
        ConsentType,
        Participant,
        ParticipantSession,
        Study,
    )
    from app.services.consent import revoke_consent
    from app.services.scoring import score_session

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    participant, study = Participant(), Study(name="s")
    db.add_all([participant, study])
    db.commit()
    battery = Battery(study_id=study.study_id, name="b", version="1.0", form_label="A")
    db.add(battery)
    db.add(
        Consent(
            participant_id=participant.participant_id,
            consent_type=ConsentType.RESEARCH_PARTICIPATION,
            version=settings.consent_current_versions[ConsentType.RESEARCH_PARTICIPATION.value],
            consent_text_hash="0" * 64,
        )
    )
    db.commit()
    session = ParticipantSession(
        participant_id=participant.participant_id,
        study_id=study.study_id,
        battery_id=battery.battery_id,
    )
    db.add(session)
    db.commit()

    revoke_consent(db, participant.participant_id, ConsentType.RESEARCH_PARTICIPATION)
    assert score_session(db, session.participant_session_id) is None
    db.refresh(session)
    assert session.scores is None


def test_bigquery_erasure_uses_pseudonymous_subject_key(monkeypatch):
    import sys
    import types

    from app.config import settings
    from app.tasks import deletion_tasks

    captured = {}

    class _Param:
        def __init__(self, name, typ, value):
            captured["param"] = (name, value)

    class _Job:
        def result(self):
            return None

    class _Client:
        def __init__(self, project=None):
            pass

        def query(self, sql, job_config=None):
            captured["sql"] = sql
            return _Job()

    bq = types.SimpleNamespace(
        Client=_Client,
        QueryJobConfig=lambda query_parameters: None,
        ScalarQueryParameter=_Param,
    )
    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    cloud.bigquery = bq  # type: ignore[attr-defined]
    google.cloud = cloud  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bq)
    monkeypatch.setattr(settings, "bigquery_deidentified_dataset", "ds")
    assert deletion_tasks._delete_from_bigquery("p-1") == "deleted"
    from app.services.bigquery_export import pseudonymous_subject_key

    assert "subject_key = @subject_key" in captured["sql"]
    assert captured["param"] == ("subject_key", pseudonymous_subject_key("p-1"))


@pytest.mark.parametrize("env,expect_dev", [("production", False), ("prod", False), ("test", True)])
def test_dev_routes_stripped_for_every_production_alias(monkeypatch, env, expect_dev):
    from app import main as app_main

    monkeypatch.setattr(app_main.settings, "environment", env)
    paths = set(app_main.create_app(test_mode=True).openapi()["paths"])
    assert ("/v1/users/create-default" in paths) is expect_dev


# ---- NEW-16 / NEW-05 / NEW-15 -------------------------------------------------


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class _User:
    def __init__(self, user_id, roles):
        self.user_id, self.roles = user_id, roles


def test_researcher_needs_study_membership_for_study_and_participant():
    from app.database.models import Participant, Study, StudyEnrollment, User
    from app.services.authorization import Role
    from app.services.study_access import (
        add_study_member,
        can_access_participant,
        can_access_study,
    )

    db = _db()
    u1, u2 = User(username="a", email="a@x.io", password_hash="x"), User(
        username="b", email="b@x.io", password_hash="x"
    )
    s1, s2, p = Study(name="s1"), Study(name="s2"), Participant()
    db.add_all([u1, u2, s1, s2, p])
    db.commit()
    db.add(StudyEnrollment(participant_id=p.participant_id, study_id=s1.study_id))
    db.commit()
    add_study_member(db, s1.study_id, u1.user_id)

    r1 = _User(u1.user_id, [Role.RESEARCHER])
    r2 = _User(u2.user_id, [Role.RESEARCHER])
    admin = _User("adm", [Role.ADMIN])
    assert can_access_study(db, r1, s1.study_id) and not can_access_study(db, r1, s2.study_id)
    assert not can_access_study(db, r2, s1.study_id)
    assert can_access_participant(db, r1, p) and not can_access_participant(db, r2, p)
    assert can_access_study(db, admin, s2.study_id) and can_access_participant(db, admin, p)


def test_sequence_gap_metrics_count_leading_gaps():
    from app.database.models import Battery, Participant, ParticipantSession, Study, TrialEvent
    from app.services import data_quality  # noqa: F401  (import check)

    db = _db()
    p, st = Participant(), Study(name="s")
    db.add_all([p, st])
    db.commit()
    b = Battery(study_id=st.study_id, name="b", version="1", form_label="A")
    db.add(b)
    db.commit()
    ps = ParticipantSession(
        participant_id=p.participant_id, study_id=st.study_id, battery_id=b.battery_id
    )
    db.add(ps)
    db.commit()
    for i, seq in enumerate([2, 3]):  # seq 0 and 1 never arrived
        db.add(
            TrialEvent(
                participant_session_id=ps.participant_session_id,
                event_id=f"e{i}",
                seq_no=seq,
                task_type="t",
                trial_index=i,
            )
        )
    db.commit()
    from app.routes.trials import find_sequence_gaps

    assert find_sequence_gaps(db, ps.participant_session_id) == [0, 1]
