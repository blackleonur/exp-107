"""
EXP-124 -- decision journal.

RESEARCH / PAPER ONLY. Renders a Decision (decision_engine.py) plus its immediate context into
the human-readable block the original brief section 22 asks for. Purely a formatter: no file
I/O here, no database access -- the caller (a later phase's cycle_runner) decides whether and
where to persist the returned string. This keeps the format independently testable without a
filesystem or a database.
"""
from __future__ import annotations

from intelligence.decision.decision_engine import Decision


def _fmt_score(score: float) -> str:
    return "n/a" if score != score else f"{score:.2f}"   # score != score  <=>  NaN


def render(decision: Decision, exp107_side: str | None, confirmation_label: str,
          opportunity_state: str, portfolio_note: str = "") -> str:
    lines = [
        f"DECISION: {decision.symbol}", "",
        f"EXP107: {exp107_side or 'NO SIGNAL'}", "",
        f"CONFIRMATION: {confirmation_label} (score={_fmt_score(decision.confidence)})", "",
        f"OPPORTUNITY STATE: {opportunity_state}", "",
        "SUPPORT:",
    ]
    lines += ([f"- {s}" for s in decision.supporting_evidence] or ["- (none)"])
    lines += ["", "CONTRADICTION:"]
    lines += ([f"- {c}" for c in decision.contradicting_evidence] or ["- (none)"])
    lines += [
        "", f"RISK: {decision.risk}",
        "", f"PORTFOLIO: {portfolio_note or '(no note)'}",
        "", f"DECISION: {decision.action}",
        "", f"REASON: {decision.primary_reason}",
        "", f"ALTERNATIVE_ACTION: {decision.alternative_action}",
    ]
    return "\n".join(lines)
