from __future__ import annotations

from intelligence.opportunity.opportunity_manager import OpportunityManager, OpportunityState


class TestFreshSymbolStartsWatching:
    def test_get_creates_watching(self):
        mgr = OpportunityManager()
        opp = mgr.get("BTCUSDT")
        assert opp.state == OpportunityState.WATCHING
        assert opp.history == []


class TestFullLifecycle:
    def test_watching_to_candidate_to_confirmed_to_open_to_weakening_to_exit(self):
        mgr = OpportunityManager()
        s = "BTCUSDT"

        opp = mgr.update(s, exp107_fired_long=True, confirmation_label="DEFER",
                         confirmation_score=0.1, has_open_position=False, open_time=1)
        assert opp.state == OpportunityState.CANDIDATE

        opp = mgr.update(s, exp107_fired_long=True, confirmation_label="CONFIRM",
                         confirmation_score=0.8, has_open_position=False, open_time=2)
        assert opp.state == OpportunityState.CONFIRMED

        # a position is opened by a later phase (decision engine) -- opportunity_manager is
        # told about it via has_open_position, it never decides this itself
        opp = mgr.update(s, exp107_fired_long=True, confirmation_label="CONFIRM",
                         confirmation_score=0.8, has_open_position=True, open_time=3)
        assert opp.state == OpportunityState.OPEN

        opp = mgr.update(s, exp107_fired_long=True, confirmation_label="WEAKEN",
                         confirmation_score=-0.3, has_open_position=True, open_time=4)
        assert opp.state == OpportunityState.WEAKENING

        opp = mgr.update(s, exp107_fired_long=True, confirmation_label="INVALIDATE",
                         confirmation_score=-1.2, has_open_position=True, open_time=5)
        assert opp.state == OpportunityState.EXIT_CANDIDATE

        # position finally closed -- no more open position, no more fire -- back to WATCHING
        opp = mgr.update(s, exp107_fired_long=False, confirmation_label="NO_SIGNAL",
                         confirmation_score=0.0, has_open_position=False, open_time=6)
        assert opp.state == OpportunityState.WATCHING

    def test_history_is_append_only_and_never_rewritten(self):
        mgr = OpportunityManager()
        s = "ETHUSDT"
        mgr.update(s, True, "DEFER", 0.1, False, 1)
        mgr.update(s, True, "CONFIRM", 0.8, False, 2)
        opp = mgr.update(s, True, "CONFIRM", 0.8, True, 3)
        assert len(opp.history) == 3
        assert opp.history[0] == (1, "WATCHING", "CANDIDATE")
        assert opp.history[1] == (2, "CANDIDATE", "CONFIRMED")
        assert opp.history[2] == (3, "CONFIRMED", "OPEN")
        first_entry_snapshot = opp.history[0]
        mgr.update(s, False, "NO_SIGNAL", 0.0, False, 4)  # OPEN -> ... more transitions happen
        assert opp.history[0] == first_entry_snapshot  # earlier entries never mutated


class TestInvalidatedIsNotSticky:
    def test_invalidated_returns_to_watching_once_signal_stops(self):
        mgr = OpportunityManager()
        s = "SOLUSDT"
        opp = mgr.update(s, True, "CONFIRM", 0.8, False, 1)
        assert opp.state == OpportunityState.CONFIRMED
        opp = mgr.update(s, True, "INVALIDATE", -1.0, False, 2)
        assert opp.state == OpportunityState.INVALIDATED
        opp = mgr.update(s, False, "NO_SIGNAL", 0.0, False, 3)
        assert opp.state == OpportunityState.WATCHING

    def test_new_firing_after_invalidated_can_become_candidate_again(self):
        mgr = OpportunityManager()
        s = "SOLUSDT"
        mgr.update(s, True, "INVALIDATE", -1.0, False, 1)
        opp = mgr.update(s, True, "DEFER", 0.0, False, 2)
        assert opp.state == OpportunityState.CANDIDATE


class TestCyclesInStateCounter:
    def test_counter_resets_on_transition_and_increments_otherwise(self):
        mgr = OpportunityManager()
        s = "BNBUSDT"
        opp = mgr.update(s, True, "DEFER", 0.0, False, 1)
        assert opp.cycles_in_state == 1
        opp = mgr.update(s, True, "DEFER", 0.0, False, 2)  # same state (CANDIDATE) again
        assert opp.cycles_in_state == 2
        opp = mgr.update(s, True, "CONFIRM", 0.8, False, 3)  # transitions to CONFIRMED
        assert opp.cycles_in_state == 1


class TestSymbolsAreFullyIndependent:
    """Directly exercises the brief's 'a signal on one symbol must not be dropped because
    another symbol already has an open position' rule (sections 15-16)."""

    def test_five_symbols_five_independent_states(self):
        mgr = OpportunityManager()
        mgr.update("BTCUSDT", True, "CONFIRM", 0.8, True, 1)     # already OPEN
        mgr.update("ETHUSDT", True, "CONFIRM", 0.8, False, 1)    # newly CONFIRMED
        mgr.update("SOLUSDT", True, "DEFER", 0.1, False, 1)      # CANDIDATE
        mgr.update("BNBUSDT", False, "NO_SIGNAL", 0.0, False, 1)  # WATCHING
        mgr.update("XRPUSDT", True, "INVALIDATE", -1.0, False, 1)  # INVALIDATED

        all_states = {s: opp.state for s, opp in mgr.all().items()}
        assert all_states == {
            "BTCUSDT": OpportunityState.OPEN,
            "ETHUSDT": OpportunityState.CONFIRMED,
            "SOLUSDT": OpportunityState.CANDIDATE,
            "BNBUSDT": OpportunityState.WATCHING,
            "XRPUSDT": OpportunityState.INVALIDATED,
        }

    def test_updating_one_symbol_never_touches_another(self):
        mgr = OpportunityManager()
        mgr.update("BTCUSDT", True, "CONFIRM", 0.8, True, 1)
        before = mgr.get("BTCUSDT")
        mgr.update("ETHUSDT", True, "INVALIDATE", -1.0, False, 2)
        after = mgr.get("BTCUSDT")
        assert before.state == after.state == OpportunityState.OPEN
        assert before.cycles_in_state == after.cycles_in_state
