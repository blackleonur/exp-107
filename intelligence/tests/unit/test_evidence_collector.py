from __future__ import annotations

from intelligence.confirmation.evidence import Strength
from intelligence.confirmation.evidence_collector import collect
from intelligence.core.market_buffer import BAR_MS, SYMBOLS, MarketBuffer


def _row(open_time, px, v=100.0):
    return [open_time, px, px + 0.3, px - 0.3, px + 0.1, v, open_time + BAR_MS - 1,
           v * 10, 10, v * 5, v * 5]


def _buffer_monotonic(n_bars: int = 600, step: float = 0.3) -> MarketBuffer:
    start = (1_700_000_000_000 // 3_600_000) * 3_600_000
    per = {}
    for s in SYMBOLS:
        rows = []
        px = 100.0
        for i in range(n_bars):
            px += step
            rows.append(_row(start + i * BAR_MS, px))
        per[s] = rows
    buf = MarketBuffer(keep_bars=n_bars)
    buf._rebuild(per)
    return buf


class TestCollectReturnsOneBundlePerSymbol:
    def test_bundle_has_expected_symbol_and_time(self):
        buf = _buffer_monotonic()
        bundle = collect("BTCUSDT", buf)
        assert bundle.symbol == "BTCUSDT"
        assert bundle.open_time == int(buf.grid[-1])

    def test_all_eight_sources_represented(self):
        buf = _buffer_monotonic()
        bundle = collect("BTCUSDT", buf)
        sources = {e.source for e in bundle.items}
        assert sources == {
            "structure_engine", "candle_engine", "volatility_engine", "magnitude_engine",
            "liquidity_engine", "cross_sectional_engine", "regime_engine", "direction_engine",
        }


class TestUnsuppliedOptionalDataIsUnknownNotFabricated:
    def test_cross_sectional_unknown_when_not_supplied(self):
        buf = _buffer_monotonic()
        bundle = collect("BTCUSDT", buf)   # no cross_sectional_directions passed
        cs = [e for e in bundle.items if e.source == "cross_sectional_engine"]
        assert len(cs) == 1
        assert cs[0].strength == Strength.UNKNOWN

    def test_btc_regime_unknown_when_not_supplied(self):
        buf = _buffer_monotonic()
        bundle = collect("BTCUSDT", buf)   # no btc_ohlcv passed
        btc = [e for e in bundle.items if e.source == "regime_engine"]
        assert len(btc) == 1
        assert btc[0].strength == Strength.UNKNOWN

    def test_liquidation_data_always_unknown(self):
        buf = _buffer_monotonic()
        bundle = collect("BTCUSDT", buf)
        liq = [e for e in bundle.items if e.feature == "LIQUIDATION_DATA"]
        assert len(liq) == 1
        assert liq[0].strength == Strength.UNKNOWN


class TestMagnitudeUsesNativeOneMinuteSeriesNotResampled:
    """Regression test for the unit bug caught while writing test_cycle_runner.py: magnitude
    must be measured in 1-minute bars (DEFAULT_MAGNITUDE_HORIZON_BARS=240 == 4 hours), never
    against the 60-minute-resampled series structure/candle use."""

    def test_magnitude_state_is_measurable_with_only_a_few_hundred_1m_bars(self):
        # 600 native 1-minute bars is far too few to support a meaningful 240-BAR horizon on a
        # 60-MINUTE series (that would need 240*60 = 14,400 native minutes = 10 days of data),
        # but is exactly enough for a 240-bar horizon measured in 1-minute bars (4 hours). If
        # the unit bug were still present, this would report UNKNOWN.
        buf = _buffer_monotonic(n_bars=600)
        bundle = collect("BTCUSDT", buf)
        mag = [e for e in bundle.items if e.source == "magnitude_engine"][0]
        assert mag.strength != Strength.UNKNOWN


class TestCrossSectionalWiredThrough:
    def test_supplied_directions_produce_a_real_read(self):
        buf = _buffer_monotonic()
        directions = {s: 1 for s in SYMBOLS}   # unanimous LONG
        bundle = collect("BTCUSDT", buf, cross_sectional_directions=directions)
        cs = [e for e in bundle.items if e.source == "cross_sectional_engine"][0]
        assert cs.strength == Strength.SUPPORT

    def test_divergent_symbol_produces_conflict(self):
        buf = _buffer_monotonic()
        directions = {s: 1 for s in SYMBOLS}
        directions["BTCUSDT"] = -1   # BTC alone disagrees with the market
        bundle = collect("BTCUSDT", buf, cross_sectional_directions=directions)
        cs = [e for e in bundle.items if e.source == "cross_sectional_engine"][0]
        assert cs.strength == Strength.CONFLICT


class TestDirectionEngineStrengthScalesWithConfidence:
    def test_strong_uptrend_produces_support_or_strong_support(self):
        buf = _buffer_monotonic()
        bundle = collect("BTCUSDT", buf)
        d = [e for e in bundle.items if e.source == "direction_engine"][0]
        assert d.strength in (Strength.SUPPORT, Strength.STRONG_SUPPORT)


class TestEmptyBufferProducesUnknownEverywhere:
    def test_no_crash_and_mostly_unknown(self):
        buf = MarketBuffer(keep_bars=10)
        bundle = collect("BTCUSDT", buf)
        # every measured-from-price-history source should honestly report UNKNOWN
        for source in ("structure_engine", "candle_engine", "volatility_engine",
                      "magnitude_engine", "direction_engine"):
            items = [e for e in bundle.items if e.source == source]
            assert all(e.strength == Strength.UNKNOWN for e in items), source
