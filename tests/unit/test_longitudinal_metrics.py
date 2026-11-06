def test_classify_change_strict_exceeds_and_degenerate_inputs():
    from app.services.longitudinal_metrics import classify_change

    assert classify_change(0, 0) == "no_reliable_change"
    assert classify_change(5, 0) == "no_reliable_change"  # unknown noise floor
    assert classify_change(5, float("nan")) == "no_reliable_change"
    assert classify_change(float("inf"), 3) == "no_reliable_change"
    assert classify_change(3, 3) == "no_reliable_change"  # equality is not "exceeds"
    assert classify_change(-3, 3) == "no_reliable_change"
    assert classify_change(3.01, 3) == "reliable_increase"
    assert classify_change(-3.01, 3) == "reliable_decrease"
