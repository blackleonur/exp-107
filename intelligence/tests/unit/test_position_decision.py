from __future__ import annotations

from intelligence.position.position_decision import (INVALIDATE_STREAK_FOR_EXIT,
                                                       WEAKEN_STREAK_FOR_REDUCE,
                                                       PositionDecisionEngine)


class TestHysteresisPreventsSingleCycleFlip:
    def test_single_weaken_stays_hold(self):
        eng = PositionDecisionEngine()
        d = eng.decide("BTCUSDT", "WEAKEN")
        assert d.recommendation == "HOLD"
        assert d.consecutive_weaken == 1

    def test_single_invalidate_stays_hold(self):
        eng = PositionDecisionEngine()
        d = eng.decide("BTCUSDT", "INVALIDATE")
        assert d.recommendation == "HOLD"
        assert d.consecutive_invalidate == 1


class TestReduceRequiresStreak:
    def test_reaches_reduce_at_exact_threshold(self):
        eng = PositionDecisionEngine()
        d = None
        for _ in range(WEAKEN_STREAK_FOR_REDUCE):
            d = eng.decide("BTCUSDT", "WEAKEN")
        assert d.recommendation == "REDUCE"
        assert d.consecutive_weaken == WEAKEN_STREAK_FOR_REDUCE

    def test_one_short_of_threshold_still_holds(self):
        eng = PositionDecisionEngine()
        d = None
        for _ in range(WEAKEN_STREAK_FOR_REDUCE - 1):
            d = eng.decide("BTCUSDT", "WEAKEN")
        assert d.recommendation == "HOLD"

    def test_streak_reset_by_an_intervening_confirm(self):
        eng = PositionDecisionEngine()
        for _ in range(WEAKEN_STREAK_FOR_REDUCE - 1):
            eng.decide("BTCUSDT", "WEAKEN")
        eng.decide("BTCUSDT", "CONFIRM")   # resets the streak -- one good cycle undoes it
        d = eng.decide("BTCUSDT", "WEAKEN")
        assert d.recommendation == "HOLD"
        assert d.consecutive_weaken == 1


class TestExitRequiresStreak:
    def test_reaches_exit_at_exact_threshold(self):
        eng = PositionDecisionEngine()
        d = None
        for _ in range(INVALIDATE_STREAK_FOR_EXIT):
            d = eng.decide("BTCUSDT", "INVALIDATE")
        assert d.recommendation == "EXIT"

    def test_exit_takes_priority_over_reduce(self):
        # a run of WEAKEN followed by enough INVALIDATE to cross the exit threshold -- EXIT,
        # not REDUCE, since consecutive_weaken is reset the moment INVALIDATE starts appearing
        eng = PositionDecisionEngine()
        eng.decide("BTCUSDT", "WEAKEN")
        eng.decide("BTCUSDT", "WEAKEN")
        for _ in range(INVALIDATE_STREAK_FOR_EXIT):
            d = eng.decide("BTCUSDT", "INVALIDATE")
        assert d.recommendation == "EXIT"


class TestNoSlTpFabrication:
    def test_hold_and_reduce_never_claim_real_sl_tp_by_default(self):
        eng = PositionDecisionEngine()
        d = eng.decide("BTCUSDT", "DEFER")
        assert d.would_adjust_sl_tp_note == ""

    def test_reduce_note_is_explicitly_labeled_research_only_when_present(self):
        eng = PositionDecisionEngine()
        for _ in range(WEAKEN_STREAK_FOR_REDUCE):
            d = eng.decide("BTCUSDT", "WEAKEN", mfe_bp=50.0)
        assert d.recommendation == "REDUCE"
        assert "RESEARCH ONLY" in d.would_adjust_sl_tp_note
        assert "not a real order" in d.would_adjust_sl_tp_note


class TestSymbolsIndependent:
    def test_two_symbols_have_independent_streaks(self):
        eng = PositionDecisionEngine()
        eng.decide("BTCUSDT", "WEAKEN")
        eng.decide("BTCUSDT", "WEAKEN")
        d_eth = eng.decide("ETHUSDT", "WEAKEN")
        assert d_eth.consecutive_weaken == 1
        assert d_eth.recommendation == "HOLD"


class TestRemove:
    def test_remove_clears_streak_state(self):
        eng = PositionDecisionEngine()
        eng.decide("BTCUSDT", "WEAKEN")
        eng.decide("BTCUSDT", "WEAKEN")
        eng.remove("BTCUSDT")
        d = eng.decide("BTCUSDT", "WEAKEN")
        assert d.consecutive_weaken == 1  # started fresh, not continuing the old streak
