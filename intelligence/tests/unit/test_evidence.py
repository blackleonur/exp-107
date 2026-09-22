from __future__ import annotations

from intelligence.confirmation.evidence import Evidence, EvidenceBundle, Strength


class TestEvidenceBundle:
    def test_by_strength_filters(self):
        items = [
            Evidence("volatility_engine", "VOL_HIGH", Strength.SUPPORT),
            Evidence("magnitude_engine", "MAG_HIGH", Strength.STRONG_SUPPORT),
            Evidence("liquidity_engine", "SPREAD_WIDE", Strength.CONFLICT),
            Evidence("regime_engine", "BTC_REGIME", Strength.UNKNOWN),
        ]
        bundle = EvidenceBundle(open_time=1, symbol="BTCUSDT", items=items)
        assert [e.feature for e in bundle.by_strength(Strength.SUPPORT)] == ["VOL_HIGH"]
        assert [e.feature for e in bundle.by_strength(Strength.CONFLICT)] == ["SPREAD_WIDE"]

    def test_counts(self):
        items = [
            Evidence("a", "f1", Strength.SUPPORT),
            Evidence("b", "f2", Strength.SUPPORT),
            Evidence("c", "f3", Strength.CONFLICT),
        ]
        bundle = EvidenceBundle(open_time=1, symbol="BTCUSDT", items=items)
        counts = bundle.counts()
        assert counts["SUPPORT"] == 2
        assert counts["CONFLICT"] == 1
        assert counts["UNKNOWN"] == 0

    def test_empty_bundle(self):
        bundle = EvidenceBundle(open_time=1, symbol="BTCUSDT")
        assert bundle.items == []
        assert bundle.counts()["SUPPORT"] == 0
