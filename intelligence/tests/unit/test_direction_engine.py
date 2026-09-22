from __future__ import annotations

import numpy as np

from intelligence.core.market_buffer import BAR_MS, SYMBOLS, MarketBuffer
from intelligence.engines.direction_engine import estimate


def _row(open_time, px, v=100.0):
    return [open_time, px, px + 0.3, px - 0.3, px + 0.1, v, open_time + BAR_MS - 1,
            v * 10, 10, v * 5, v * 5]


def _buffer_monotonic(n_bars: int, step: float = 0.5, start_aligned_to_min: int = 15) -> MarketBuffer:
    """A MarketBuffer whose close price rises monotonically and steadily for every symbol,
    aligned so 1m/5m/15m resampling buckets are all cleanly closed (no partial edge bucket)."""
    tf_ms = start_aligned_to_min * BAR_MS
    start = (50_000_000_000 // tf_ms) * tf_ms
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


class TestUnanimousDirection:
    def test_all_timeframes_agree_long(self):
        buf = _buffer_monotonic(300)
        est = estimate(buf, "BTCUSDT", timeframes_min=[1, 5, 15])
        assert est.direction == "LONG"
        assert est.timeframe_agreement == 1.0
        assert est.n_timeframes_measured == 3
        assert est.confidence == 1.0
        assert est.uncertainty == 0.0
        assert all("opposes majority" not in c for c in est.conflicting_factors)

    def test_downtrend_gives_short(self):
        buf = _buffer_monotonic(300, step=-0.5)
        est = estimate(buf, "ETHUSDT", timeframes_min=[1, 5, 15])
        assert est.direction == "SHORT"
        assert est.timeframe_agreement == 1.0


class TestUnknownOnNoData:
    def test_empty_buffer_is_unknown(self):
        buf = MarketBuffer(keep_bars=10)
        est = estimate(buf, "BTCUSDT", timeframes_min=[1, 5, 15])
        assert est.direction == "UNKNOWN"
        assert np.isnan(est.confidence)
        assert np.isnan(est.timeframe_agreement)
        assert np.isnan(est.uncertainty)
        assert est.n_timeframes_measured == 0

    def test_never_negative_signal_on_unknown(self):
        """UNKNOWN must never be silently converted into a directional read (brief: 'Do not
        convert UNKNOWN into a negative signal')."""
        buf = MarketBuffer(keep_bars=10)
        est = estimate(buf, "BTCUSDT", timeframes_min=[1, 5, 15])
        assert est.direction not in ("LONG", "SHORT")


class TestSampleSizePenalty:
    def test_confidence_capped_when_few_timeframes_measured(self):
        # a short buffer: 1m is measurable, but 60m (60 bars/bucket) is not with only 40 bars
        buf = _buffer_monotonic(40, start_aligned_to_min=1)
        est = estimate(buf, "BTCUSDT", timeframes_min=[1, 60])
        assert est.n_timeframes_measured == 1
        assert est.n_timeframes_total == 2
        # sample_factor = min(1, 1/MIN_TIMEFRAMES_FOR_MEASURED=3) = 1/3 -> confidence < agreement
        assert est.confidence < est.timeframe_agreement
        assert any("only 1/2 timeframes" in c for c in est.conflicting_factors)


class TestConflictingEvidence:
    def test_disagreeing_timeframe_is_recorded_as_conflict(self):
        # Every 5-minute bucket normally gains +2.5 (5 bars * step 0.5) over the previous one,
        # and every 15-minute bucket normally gains +7.5. A dip of exactly 1.0 on the FINAL
        # native 1-minute bar therefore: flips the 1-minute bar-to-bar comparison negative
        # (+0.5 step - 1.0 dip < 0) while leaving the 5m bucket-to-bucket comparison
        # (+2.5 - 1.0 > 0) and the 15m one (+7.5 - 1.0 > 0) both still positive -- because a
        # resampled bucket's "close" is only the LAST native bar's close, a dip bigger than one
        # bucket's own net gain would flip every timeframe identically, which is not what this
        # test is checking.
        tf_ms = 15 * BAR_MS
        start = (50_000_000_000 // tf_ms) * tf_ms
        per = {}
        for s in SYMBOLS:
            rows = []
            px = 100.0
            for i in range(299):
                px += 0.5
                rows.append(_row(start + i * BAR_MS, px))
            px -= 1.0  # final 1m bar dips just enough to flip ONLY the 1-minute read
            rows.append(_row(start + 299 * BAR_MS, px))
            per[s] = rows
        buf = MarketBuffer(keep_bars=300)
        buf._rebuild(per)
        est = estimate(buf, "BTCUSDT", timeframes_min=[1, 5, 15])
        assert est.direction == "LONG"
        assert est.timeframe_agreement < 1.0
        assert any("1m: direction=SHORT (opposes majority)" in c for c in est.conflicting_factors)
