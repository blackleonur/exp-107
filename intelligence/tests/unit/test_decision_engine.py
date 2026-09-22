from __future__ import annotations

import pytest

from intelligence.confirmation.confirmation_engine import ConfirmationResult
from intelligence.decision.decision_engine import decide, risk_label
from intelligence.core.exp107_signal import Exp107Signal
from intelligence.risk.risk_engine import RiskAssessment


def _confirmation(label, score=1.0, support=None, conflict=None):
    return ConfirmationResult(open_time=1, symbol="BTCUSDT", exp107_status="OK",
                              exp107_fired_long=True, confirmation_score=score, label=label,
                              n_evidence_considered=1, n_unknown=0,
                              weighted_support=support or [], weighted_conflict=conflict or [])


def _risk(penalty=0.0):
    return RiskAssessment("BTCUSDT", 10.0, 50.0, 14.38, -4.38, 0.0, 0.0, 10_000.0, "note",
                          risk_penalty=penalty)


class TestHardInvariantEnterRequiresExp107Fire:
    def test_enter_unreachable_when_exp107_unavailable(self):
        exp107 = Exp107Signal(status="UNAVAILABLE", symbol="BTCUSDT")
        d = decide("BTCUSDT", 1, exp107, _confirmation("CONFIRM"), has_open_position=False)
        assert d.action != "ENTER"
        assert d.action == "IGNORE"

    def test_enter_unreachable_when_exp107_ok_but_not_fired(self):
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=False, side="LONG")
        d = decide("BTCUSDT", 1, exp107, _confirmation("CONFIRM"), has_open_position=False)
        assert d.action != "ENTER"

    def test_enter_unreachable_when_exp107_fired_but_short(self):
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="SHORT")
        d = decide("BTCUSDT", 1, exp107, _confirmation("CONFIRM"), has_open_position=False)
        assert d.action != "ENTER"

    def test_enter_unreachable_even_with_confirm_label_if_not_fired_long(self):
        """A CONFIRM label alone must never be sufficient -- exp107.is_long_fire is the gate."""
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=False, side="LONG")
        for label in ("CONFIRM", "WEAKEN", "DEFER", "INVALIDATE"):
            d = decide("BTCUSDT", 1, exp107, _confirmation(label), has_open_position=False)
            assert d.action != "ENTER", f"label={label} must not reach ENTER"

    def test_enter_reachable_only_with_fire_and_confirm(self):
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="LONG")
        d = decide("BTCUSDT", 1, exp107, _confirmation("CONFIRM"), has_open_position=False,
                  risk=_risk(0.0))
        assert d.action == "ENTER"


class TestNoOpenPositionBranches:
    FIRED_LONG = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="LONG")

    def test_weaken_gives_wait(self):
        d = decide("BTCUSDT", 1, self.FIRED_LONG, _confirmation("WEAKEN"),
                  has_open_position=False)
        assert d.action == "WAIT"

    def test_defer_gives_wait(self):
        d = decide("BTCUSDT", 1, self.FIRED_LONG, _confirmation("DEFER"),
                  has_open_position=False)
        assert d.action == "WAIT"

    def test_invalidate_gives_ignore(self):
        d = decide("BTCUSDT", 1, self.FIRED_LONG, _confirmation("INVALIDATE"),
                  has_open_position=False)
        assert d.action == "IGNORE"

    def test_confirm_with_high_risk_gives_wait_not_enter(self):
        d = decide("BTCUSDT", 1, self.FIRED_LONG, _confirmation("CONFIRM"),
                  has_open_position=False, risk=_risk(0.9))
        assert d.action == "WAIT"
        assert d.risk == "HIGH"

    def test_confirm_with_low_risk_enters(self):
        d = decide("BTCUSDT", 1, self.FIRED_LONG, _confirmation("CONFIRM"),
                  has_open_position=False, risk=_risk(0.1))
        assert d.action == "ENTER"
        assert d.risk == "LOW"


class TestOpenPositionBranches:
    def test_position_recommendation_exit_wins(self):
        exp107 = Exp107Signal(status="UNAVAILABLE", symbol="BTCUSDT")  # irrelevant while open
        d = decide("BTCUSDT", 1, exp107, _confirmation("DEFER"), has_open_position=True,
                  position_recommendation="EXIT")
        assert d.action == "EXIT"

    def test_position_recommendation_reduce(self):
        exp107 = Exp107Signal(status="UNAVAILABLE", symbol="BTCUSDT")
        d = decide("BTCUSDT", 1, exp107, _confirmation("DEFER"), has_open_position=True,
                  position_recommendation="REDUCE")
        assert d.action == "REDUCE"

    def test_position_recommendation_hold_default(self):
        exp107 = Exp107Signal(status="UNAVAILABLE", symbol="BTCUSDT")
        d = decide("BTCUSDT", 1, exp107, _confirmation("DEFER"), has_open_position=True,
                  position_recommendation="HOLD")
        assert d.action == "HOLD"

    def test_open_position_never_re_enters(self):
        """An already-open position must never produce a second ENTER, even with a fresh
        CONFIRM read -- ENTER is only reachable from the has_open_position=False branch."""
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="LONG")
        d = decide("BTCUSDT", 1, exp107, _confirmation("CONFIRM"), has_open_position=True,
                  position_recommendation="HOLD")
        assert d.action != "ENTER"
        assert d.action == "HOLD"


class TestRiskLabel:
    def test_none_risk_is_unknown(self):
        assert risk_label(None) == "UNKNOWN"

    def test_thresholds(self):
        assert risk_label(_risk(0.0)) == "LOW"
        assert risk_label(_risk(0.29)) == "LOW"
        assert risk_label(_risk(0.3)) == "MEDIUM"
        assert risk_label(_risk(0.69)) == "MEDIUM"
        assert risk_label(_risk(0.7)) == "HIGH"


class TestEvidenceCarriedThrough:
    def test_support_and_conflict_lists_passed_through(self):
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="LONG")
        conf = _confirmation("CONFIRM", support=["x.y=SUPPORT"], conflict=["a.b=CONFLICT"])
        d = decide("BTCUSDT", 1, exp107, conf, has_open_position=False, risk=_risk(0.0))
        assert d.supporting_evidence == ["x.y=SUPPORT"]
        assert d.contradicting_evidence == ["a.b=CONFLICT"]
