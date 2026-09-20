import numpy as np
import pandas as pd
import pytest

from readmission_risk.models.baselines import reason_code_lookup


def test_reason_code_lookup_matches_hand_computed_smoothed_rates():
    train_codes = pd.Series(["A", "A", "A", "B", None, None])
    y = np.array([1, 1, 0, 0, 0, 1])  # prevalence 0.5
    test_codes = pd.Series(["A", "B", None, "Z"], index=[10, 20, 30, 40])  # non-default index must not matter

    proba = reason_code_lookup(train_codes, y, test_codes, smoothing=10.0)

    expected = [
        (2 + 10 * 0.5) / (3 + 10),  # A: 2 of 3 positive, smoothed toward 0.5
        (0 + 10 * 0.5) / (1 + 10),  # B
        (1 + 10 * 0.5) / (2 + 10),  # null reason is its own key
        0.5,  # unseen code -> train prevalence
    ]
    np.testing.assert_allclose(proba, expected)


def test_reason_code_lookup_does_not_mutate_its_inputs():
    train_codes = pd.Series(["A", "B", "A", "B"])
    test_codes = pd.Series(["A", None])
    y = np.array([1, 0, 1, 0])
    codes_before, test_before, y_before = train_codes.copy(), test_codes.copy(), y.copy()
    reason_code_lookup(train_codes, y, test_codes)
    pd.testing.assert_series_equal(train_codes, codes_before)
    pd.testing.assert_series_equal(test_codes, test_before)
    np.testing.assert_array_equal(y, y_before)


@pytest.mark.parametrize("smoothing", [0.0, 5.0])
def test_reason_code_lookup_probabilities_stay_in_unit_interval(smoothing):
    rng = np.random.default_rng(0)
    codes = pd.Series(rng.choice(["A", "B", "C", None], size=200))
    y = rng.integers(0, 2, size=200)
    proba = reason_code_lookup(codes, y, codes, smoothing=smoothing)
    assert ((proba >= 0) & (proba <= 1)).all()
