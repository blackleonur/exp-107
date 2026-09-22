from __future__ import annotations

from intelligence.decision.decision_engine import Decision
from intelligence.decision.journal import render


_DEFAULT_SUPPORT = ["1H bullish", "previous move HIGH"]
_DEFAULT_CONFLICT = ["BTC neutral"]


def _decision(action="ENTER", support=None, conflict=None, confidence=0.71):
    return Decision(
        symbol="BTCUSDT", open_time=1, action=action,
        primary_reason="EXP-107 LONG + multi-timeframe confirmation",
        supporting_evidence=_DEFAULT_SUPPORT if support is None else support,
        contradicting_evidence=_DEFAULT_CONFLICT if conflict is None else conflict,
        risk="MEDIUM", confidence=confidence, alternative_action="WAIT if risk worsens")


class TestRenderContainsAllRequiredFields:
    def test_all_sections_present(self):
        text = render(_decision(), exp107_side="LONG", confirmation_label="CONFIRM",
                      opportunity_state="CONFIRMED", portfolio_note="2 correlated LONG positions")
        for expected in ("DECISION: BTCUSDT", "EXP107: LONG", "CONFIRMATION: CONFIRM",
                        "OPPORTUNITY STATE: CONFIRMED", "SUPPORT:", "1H bullish",
                        "CONTRADICTION:", "BTC neutral", "RISK: MEDIUM",
                        "PORTFOLIO: 2 correlated LONG positions", "DECISION: ENTER",
                        "REASON: EXP-107 LONG + multi-timeframe confirmation",
                        "ALTERNATIVE_ACTION: WAIT if risk worsens"):
            assert expected in text

    def test_score_formatted_to_two_decimals(self):
        text = render(_decision(confidence=0.7123), exp107_side="LONG",
                      confirmation_label="CONFIRM", opportunity_state="CONFIRMED")
        assert "score=0.71" in text


class TestEmptyEvidenceLists:
    def test_none_placeholder_when_no_support(self):
        text = render(_decision(support=[]), exp107_side="LONG", confirmation_label="CONFIRM",
                      opportunity_state="CONFIRMED")
        lines = text.splitlines()
        support_idx = lines.index("SUPPORT:")
        assert lines[support_idx + 1] == "- (none)"

    def test_none_placeholder_when_no_conflict(self):
        text = render(_decision(conflict=[]), exp107_side="LONG", confirmation_label="CONFIRM",
                      opportunity_state="CONFIRMED")
        lines = text.splitlines()
        idx = lines.index("CONTRADICTION:")
        assert lines[idx + 1] == "- (none)"


class TestNoExp107Signal:
    def test_no_signal_placeholder(self):
        text = render(_decision(action="IGNORE"), exp107_side=None,
                      confirmation_label="NO_SIGNAL", opportunity_state="WATCHING")
        assert "EXP107: NO SIGNAL" in text


class TestNanConfidence:
    def test_nan_confidence_formatted_as_na(self):
        text = render(_decision(confidence=float("nan")), exp107_side="LONG",
                      confirmation_label="DEFER", opportunity_state="CANDIDATE")
        assert "score=n/a" in text


class TestNoPortfolioNote:
    def test_default_placeholder(self):
        text = render(_decision(), exp107_side="LONG", confirmation_label="CONFIRM",
                      opportunity_state="CONFIRMED")
        assert "PORTFOLIO: (no note)" in text
