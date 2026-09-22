from __future__ import annotations

import numpy as np
import pytest

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.cross_sectional_engine import (cross_sectional_state,
                                                          direction_from_return,
                                                          symbol_alignment)


def _ohlcv_closes(closes):
    n = len(closes)
    ot = np.arange(n, dtype=np.int64) * 60_000
    c = np.array(closes, dtype=np.float64)
    h, l, o = c + 0.1, c - 0.1, c.copy()
    v = np.full(n, 100.0)
    return OHLCV(ot, o, h, l, c, v, v * 10, v * 5)


class TestDirectionFromReturn:
    def test_positive(self):
        assert direction_from_return(_ohlcv_closes([10, 11]), horizon_bars=1) == 1

    def test_negative(self):
        assert direction_from_return(_ohlcv_closes([10, 9]), horizon_bars=1) == -1

    def test_flat(self):
        assert direction_from_return(_ohlcv_closes([10, 10]), horizon_bars=1) == 0

    def test_none_when_insufficient_history(self):
        assert direction_from_return(_ohlcv_closes([10]), horizon_bars=1) is None
        assert direction_from_return(_ohlcv_closes([]), horizon_bars=1) is None


class TestCrossSectionalState:
    def test_unanimous_long(self):
        snap = cross_sectional_state({"A": 1, "B": 1, "C": 1})
        assert snap.n_long == 3
        assert snap.agreement_ratio == pytest.approx(1.0)
        assert snap.majority_direction == "LONG"

    def test_split_produces_partial_agreement(self):
        snap = cross_sectional_state({"A": 1, "B": 1, "C": -1, "D": -1, "E": 1})
        assert snap.n_long == 3
        assert snap.n_short == 2
        assert snap.agreement_ratio == pytest.approx(3 / 5)
        assert snap.majority_direction == "LONG"

    def test_exact_tie_is_neutral(self):
        snap = cross_sectional_state({"A": 1, "B": -1})
        assert snap.majority_direction == "NEUTRAL"
        assert snap.agreement_ratio == pytest.approx(0.5)

    def test_none_values_excluded_from_counts(self):
        snap = cross_sectional_state({"A": 1, "B": None, "C": 1})
        assert snap.n_long == 2
        assert "B" not in snap.per_symbol_direction

    def test_all_flat_gives_nan_ratio_neutral(self):
        snap = cross_sectional_state({"A": 0, "B": 0})
        assert np.isnan(snap.agreement_ratio)
        assert snap.majority_direction == "NEUTRAL"
        assert snap.n_flat == 2

    def test_empty_input(self):
        snap = cross_sectional_state({})
        assert np.isnan(snap.agreement_ratio)
        assert snap.majority_direction == "NEUTRAL"


class TestSymbolAlignment:
    def test_aligned(self):
        snap = cross_sectional_state({"A": 1, "B": 1, "C": -1})
        assert symbol_alignment("A", snap) == "ALIGNED"

    def test_divergent(self):
        snap = cross_sectional_state({"A": 1, "B": 1, "C": -1})
        assert symbol_alignment("C", snap) == "DIVERGENT"

    def test_unknown_symbol_not_in_snapshot(self):
        snap = cross_sectional_state({"A": 1, "B": 1})
        assert symbol_alignment("Z", snap) == "UNKNOWN"

    def test_neutral_when_symbol_flat(self):
        snap = cross_sectional_state({"A": 0, "B": 1, "C": 1})
        assert symbol_alignment("A", snap) == "NEUTRAL"

    def test_neutral_when_market_tied(self):
        snap = cross_sectional_state({"A": 1, "B": -1})
        assert symbol_alignment("A", snap) == "NEUTRAL"
