"""Unit tests for intelligence.engines.structure_engine.

Every fixture below is small enough (6-10 bars, left=right=1) that the expected swings and
BOS/CHoCH outcome are hand-verified in the accompanying comments, not just asserted blindly.
"""
from __future__ import annotations

import numpy as np
import pytest

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.structure_engine import find_swings, structure_state


def _ohlcv_from_hl(hl: list[tuple[float, float]]) -> OHLCV:
    """close = midpoint of (high, low); open = close (open is unused by this engine)."""
    ot = np.arange(len(hl), dtype=np.int64) * 60_000
    h = np.array([x[0] for x in hl], dtype=np.float64)
    l = np.array([x[1] for x in hl], dtype=np.float64)
    c = (h + l) / 2.0
    o = c.copy()
    v = np.full(len(hl), 100.0)
    return OHLCV(ot, o, h, l, c, v, v * 10, v * 5)


class TestFindSwingsExact:
    """7-bar fixture, left=right=1. Hand-computed swings (see EXP-124 dev notes):
    swing high @1 (12, ""), swing low @2 (9.5, ""), swing high @3 (13, HH),
    swing low @4 (9, LL), swing high @5 (14, HH)."""

    BARS = [(10, 9), (12, 10), (11, 9.5), (13, 10.5), (12, 9), (14, 11), (13, 10)]

    def test_exact_swings(self):
        swings = find_swings(_ohlcv_from_hl(self.BARS), left=1, right=1)
        got = [(s.index, s.kind, s.price, s.label) for s in swings]
        assert got == [
            (1, "high", 12.0, ""),
            (2, "low", 9.5, ""),
            (3, "high", 13.0, "HH"),
            (4, "low", 9.0, "LL"),
            (5, "high", 14.0, "HH"),
        ]

    def test_confirmed_index_is_index_plus_right(self):
        swings = find_swings(_ohlcv_from_hl(self.BARS), left=1, right=1)
        for s in swings:
            assert s.confirmed_index == s.index + 1

    def test_no_event_when_price_never_breaks_a_confirmed_reference(self):
        st = structure_state(_ohlcv_from_hl(self.BARS), left=1, right=1)
        assert st.bias == "NEUTRAL"
        assert st.last_event is None
        assert st.trend == "NEUTRAL"


class TestFirstBreakFromNeutralIsBOS:
    """6-bar fixture, left=right=1: one clean swing high (13 @ i=1), then a bar that closes
    well above it. Since bias starts NEUTRAL, this must be labeled BOS, not CHoCH, and bias
    becomes BULLISH."""

    BARS = [(10, 9), (13, 11), (10, 9), (9, 8), (9, 8), (20, 15)]

    def test_single_clean_swing(self):
        swings = find_swings(_ohlcv_from_hl(self.BARS), left=1, right=1)
        assert [(s.index, s.kind, s.price) for s in swings] == [(1, "high", 13.0)]

    def test_bos_not_choch_on_first_break(self):
        st = structure_state(_ohlcv_from_hl(self.BARS), left=1, right=1)
        assert st.bias == "BULLISH"
        assert st.last_event is not None
        assert st.last_event.index == 5
        assert st.last_event.kind == "BOS"
        assert st.last_event.direction == "up"
        assert st.trend == "BULLISH"


class TestChochAfterBullishBias:
    """Extends the BOS fixture with a swing low and a subsequent break below it -- the break
    happens while bias is BULLISH, so it must be labeled CHoCH, flip bias to BEARISH, and (being
    the very latest bar) put trend into TRANSITION."""

    BARS = [(10, 9), (13, 11), (10, 9), (9, 8), (9, 8), (20, 15),   # -> BOS up @5, bias BULLISH
            (17, 16), (15, 12), (16, 14), (10, 5)]                  # -> CHoCH down @9

    def test_choch_flips_bias_and_sets_transition(self):
        st = structure_state(_ohlcv_from_hl(self.BARS), left=1, right=1)
        assert st.bias == "BEARISH"
        assert st.last_event.index == 9
        assert st.last_event.kind == "CHoCH"
        assert st.last_event.direction == "down"
        assert st.trend == "TRANSITION"


class TestFlatSeriesIsNeutral:
    def test_no_swings_no_events_on_flat_data(self):
        bars = [(10.0, 9.0)] * 30
        ohlcv = _ohlcv_from_hl(bars)
        swings = find_swings(ohlcv)
        assert swings == []
        st = structure_state(ohlcv)
        assert st.bias == "NEUTRAL"
        assert st.last_event is None
        assert st.trend == "NEUTRAL"


class TestEmptyInput:
    def test_structure_state_none_on_empty(self):
        empty = _ohlcv_from_hl([])
        assert structure_state(empty) is None
