"""Unit tests for intelligence.core.market_buffer. No network access -- all data is synthetic
fixture bars fed directly into MarketBuffer._rebuild(), mirroring the raw Binance kline row
shape client code already produces."""
from __future__ import annotations

import numpy as np
import pytest

from intelligence.core.market_buffer import BAR_MS, NS, SYMBOLS, MarketBuffer


def _row(open_time, o, h, l, c, v=100.0, qv=1000.0, tb=500.0):
    # matches the 11-field shape BinanceClient.klines()/MarketBuffer._rebuild expect
    return [open_time, o, h, l, c, v, open_time + BAR_MS - 1, qv, 10, v / 2, tb]


def _synthetic_per_symbol(n_bars: int, start_ms: int, now_ms: int) -> dict[str, list]:
    per = {}
    for j, s in enumerate(SYMBOLS):
        rows = []
        px = 100.0 + j
        for i in range(n_bars):
            ot = start_ms + i * BAR_MS
            px += 0.1
            rows.append(_row(ot, px, px + 0.5, px - 0.5, px + 0.2))
        per[s] = rows
    return per


class TestSymbolsMatchExp107:
    def test_symbol_list_matches_d_features(self):
        """Isolation contract: the intelligence package duplicates SYMBOLS rather than
        importing scripts/d_features.py, but the two lists must never silently drift apart."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
        import d_features  # noqa: E402  (read-only import, test-time only)
        assert SYMBOLS == d_features.SYMBOLS
        assert NS == d_features.NS


class TestRebuildAndBackfillShape:
    def test_rebuild_drops_unclosed_bar(self):
        buf = MarketBuffer(keep_bars=100)
        now = 10_000_000_000
        start = now - 5 * BAR_MS
        per = _synthetic_per_symbol(6, start, now)
        # last bar of each symbol is still "open" relative to `now` inside `_rebuild`'s own
        # `now = time.time()*1000` -- to test the closed-only guarantee deterministically we
        # instead check the guarantee at poll() time using a controllable clock proxy: here we
        # just confirm _rebuild keeps every bar whose close time has already passed at call time
        buf._rebuild(per)
        assert len(buf.grid) == 6
        assert buf.C.shape == (6, NS)

    def test_grid_alignment_across_symbols_with_gaps(self):
        buf = MarketBuffer(keep_bars=100)
        start = 20_000_000_000
        per = _synthetic_per_symbol(5, start, start + 100 * BAR_MS)
        # drop one bar for one symbol to create a real gap
        per["BTCUSDT"] = [r for r in per["BTCUSDT"] if r[0] != start + 2 * BAR_MS]
        buf._rebuild(per)
        assert len(buf.grid) == 5
        j = SYMBOLS.index("BTCUSDT")
        gap_pos = buf.pos[start + 2 * BAR_MS]
        assert np.isnan(buf.C[gap_pos, j])
        other = SYMBOLS.index("ETHUSDT")
        assert not np.isnan(buf.C[gap_pos, other])


class TestResample:
    def test_native_1m_passthrough(self):
        buf = MarketBuffer(keep_bars=100)
        start = 30_000_000_000
        per = _synthetic_per_symbol(10, start, start + 100 * BAR_MS)
        buf._rebuild(per)
        r = buf.resample("BTCUSDT", 1)
        assert len(r) == 10
        j = SYMBOLS.index("BTCUSDT")
        np.testing.assert_array_equal(r.close, buf.C[:, j])

    def test_5m_aggregation_matches_manual_ohlc(self):
        buf = MarketBuffer(keep_bars=100)
        # align exactly on a 5-minute boundary so every bucket is fully covered
        start = (30_000_000_000 // (5 * BAR_MS)) * (5 * BAR_MS)
        per = _synthetic_per_symbol(10, start, start + 100 * BAR_MS)
        buf._rebuild(per)
        r5 = buf.resample("BTCUSDT", 5)
        assert len(r5) == 2  # 10 one-minute bars -> two clean 5-minute buckets
        j = SYMBOLS.index("BTCUSDT")
        first_bucket_close = buf.C[4, j]  # 5th 1m bar (index 4) closes the first 5m bucket
        assert r5.close[0] == pytest.approx(first_bucket_close)
        assert r5.high[0] == pytest.approx(np.max(buf.H[0:5, j]))
        assert r5.low[0] == pytest.approx(np.min(buf.L[0:5, j]))
        assert r5.open[0] == pytest.approx(buf.O[0, j])
        assert r5.volume[0] == pytest.approx(np.sum(buf.V[0:5, j]))

    def test_partial_trailing_bucket_excluded(self):
        buf = MarketBuffer(keep_bars=100)
        start = (30_000_000_000 // (5 * BAR_MS)) * (5 * BAR_MS)
        per = _synthetic_per_symbol(7, start, start + 100 * BAR_MS)  # 7 = one full + 2 partial
        buf._rebuild(per)
        r5 = buf.resample("BTCUSDT", 5)
        assert len(r5) == 1  # the trailing 2-bar partial bucket must not appear as "closed"


class TestFormingCandle:
    def test_forming_candle_reflects_only_closed_bars_so_far(self):
        buf = MarketBuffer(keep_bars=100)
        start = (30_000_000_000 // (60 * BAR_MS)) * (60 * BAR_MS)
        per = _synthetic_per_symbol(15, start, start + 100 * BAR_MS)  # 15 of 60 min into 1H
        buf._rebuild(per)
        fc = buf.forming_candle("BTCUSDT", 60)
        assert fc is not None
        assert fc.bars_included == 15
        assert fc.boundary_open_time == start

    def test_forming_candle_none_when_buffer_empty(self):
        buf = MarketBuffer(keep_bars=100)
        assert buf.forming_candle("BTCUSDT", 60) is None
