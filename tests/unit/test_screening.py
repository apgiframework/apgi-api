"""Unit tests for effort/fraud/validity screening, device tiers and form
counterbalancing (tracker S3-SCR-01, S3-BAT-01.6, S3-BAT-04)."""

from collections import Counter

from app.services import screening as sc


class TestTiers:
    def test_tier_a_clean_session(self) -> None:
        t = {"timing_error": {"p95": 3.0}, "fullscreen": True}
        assert sc.assign_tier(t, 0) == ("A", [])

    def test_tier_b_moderate_noise_or_windowed(self) -> None:
        assert sc.assign_tier({"timing_error": {"p95": 12.0}, "fullscreen": True}, 0)[0] == "B"
        assert sc.assign_tier({"timing_error": {"p95": 1.0}, "fullscreen": False}, 0)[0] == "B"

    def test_tier_c_reasons(self) -> None:
        base = {"timing_error": {"p95": 1.0}, "fullscreen": True}
        assert sc.assign_tier({**base, "is_mobile": True}, 0) == ("C", ["mobile_device"])
        assert sc.assign_tier({**base, "visibility_changes": 1}, 0)[0] == "C"
        assert sc.assign_tier({**base, "timing_error": {"p95": 50.0}}, 0)[0] == "C"
        assert sc.assign_tier(base, 2) == ("C", ["failed_attention_checks"])

    def test_timing_distribution_keeps_quantiles(self) -> None:
        d = sc.timing_error_distribution([1, 2, 3, 4, 100])
        assert d["n"] == 5 and d["max"] == 100 and d["median"] == 3
        assert sc.timing_error_distribution([]) == {"n": 0}


class TestFlags:
    def test_fast_responses(self) -> None:
        assert sc.flag_fast_responses([100.0] * 5 + [400.0] * 5)["flag"] is True
        assert sc.flag_fast_responses([400.0] * 10)["flag"] is False
        assert sc.flag_fast_responses([None, None])["flag"] is False

    def test_invariant_requires_key_and_confidence(self) -> None:
        keys = ["f"] * 12
        assert sc.flag_invariant_responding(keys, [3] * 12) is True
        assert sc.flag_invariant_responding(keys, [1, 2, 3] * 4) is False
        assert sc.flag_invariant_responding(["f", "j"] * 6, [3] * 12) is False
        assert sc.flag_invariant_responding(["f"] * 3, [3] * 3) is False

    def test_straight_lining_and_zigzag(self) -> None:
        assert sc.flag_straight_lining([3] * 10) is True
        assert sc.flag_straight_lining([1, 2, 3, 4, 5, 2, 4, 1, 3, 5]) is False
        assert sc.flag_zigzag([1, 5] * 5) is True
        assert sc.flag_zigzag([1, 2, 3, 4, 5, 6, 7, 8]) is True
        assert sc.flag_zigzag([1, 3, 2, 5, 4, 1, 3, 2]) is False

    def test_bot_signals(self) -> None:
        assert sc.flag_bot("x", 100.0, True) == [
            "honeypot_filled",
            "webdriver_detected",
        ]
        assert sc.flag_bot(None, 5.0) == ["implausibly_fast_completion"]
        assert sc.flag_bot(None, 300.0) == []


class TestCounterbalancing:
    def test_williams_square_balances_positions(self) -> None:
        labels = ["A", "B", "C", "D"]
        rows = [sc.balanced_latin_square(labels, r) for r in range(4)]
        for pos in range(4):
            assert Counter(r[pos] for r in rows) == Counter(labels)
        pairs = Counter((r[i], r[i + 1]) for r in rows for i in range(3))
        assert max(pairs.values()) == 1  # each ordered pair exactly once

    def test_odd_and_single_forms(self) -> None:
        rows = [sc.balanced_latin_square(["A", "B", "C"], r) for r in range(3)]
        for pos in range(3):
            assert Counter(r[pos] for r in rows) == Counter("ABC")
        assert sc.balanced_latin_square(["A"], 5) == ["A"]
        assert sc.balanced_latin_square([], 0) == []

    def test_expected_form_rotates_by_session_index(self) -> None:
        order = ["B", "C", "A"]
        assert [sc.expected_form_label(order, i) for i in range(1, 5)] == ["B", "C", "A", "B"]
        assert sc.expected_form_label([], 1) is None


def test_identifier_hash_is_stable_and_case_insensitive() -> None:
    assert sc.hash_identifier(" A@x.com ") == sc.hash_identifier("a@x.com")
    assert sc.hash_identifier("a@x.com") != sc.hash_identifier("b@x.com")
