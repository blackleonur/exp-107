"""Unit tests for intelligence.core.kline_store. Uses an in-memory-equivalent temp file DB --
no network, no dependency on shadow.db."""
from __future__ import annotations

from intelligence.core.kline_store import append_bars, coverage, load_range, connect


def _row(open_time, close=101.0):
    return [open_time, 100.0, 102.0, 99.0, close, 10.0, open_time + 59_999, 500.0, 5, 5.0, 250.0]


class TestAppendAndCoverage:
    def test_empty_store_reports_empty_coverage(self, tmp_path):
        con = connect(tmp_path / "t.db")
        c = coverage(con, "BTCUSDT")
        assert c.is_empty
        assert c.n_bars == 0
        assert c.first_open_time is None

    def test_append_then_coverage(self, tmp_path):
        con = connect(tmp_path / "t.db")
        rows = [_row(1_000_000 + i * 60_000) for i in range(5)]
        n = append_bars(con, "BTCUSDT", rows)
        assert n == 5
        c = coverage(con, "BTCUSDT")
        assert c.n_bars == 5
        assert c.first_open_time == 1_000_000
        assert c.last_open_time == 1_000_000 + 4 * 60_000

    def test_append_is_idempotent_never_overwrites(self, tmp_path):
        con = connect(tmp_path / "t.db")
        rows = [_row(2_000_000, close=101.0)]
        append_bars(con, "ETHUSDT", rows)
        # a second, DIFFERENT bar for the same (symbol, open_time) must not overwrite the first
        append_bars(con, "ETHUSDT", [_row(2_000_000, close=999.0)])
        loaded = load_range(con, "ETHUSDT")
        assert len(loaded) == 1
        assert loaded[0][4] == 101.0  # close column, unchanged

    def test_symbols_are_isolated(self, tmp_path):
        con = connect(tmp_path / "t.db")
        append_bars(con, "BTCUSDT", [_row(3_000_000)])
        append_bars(con, "ETHUSDT", [_row(3_000_000), _row(3_060_000)])
        assert coverage(con, "BTCUSDT").n_bars == 1
        assert coverage(con, "ETHUSDT").n_bars == 2


class TestLoadRange:
    def test_range_filter(self, tmp_path):
        con = connect(tmp_path / "t.db")
        rows = [_row(1_000_000 + i * 60_000) for i in range(10)]
        append_bars(con, "BTCUSDT", rows)
        sub = load_range(con, "BTCUSDT", start_ms=1_000_000 + 3 * 60_000,
                          end_ms=1_000_000 + 6 * 60_000)
        assert len(sub) == 4
        assert sub[0][0] == 1_000_000 + 3 * 60_000
        assert sub[-1][0] == 1_000_000 + 6 * 60_000

    def test_load_range_ordered(self, tmp_path):
        con = connect(tmp_path / "t.db")
        rows = [_row(5_000_000 + i * 60_000) for i in reversed(range(5))]
        append_bars(con, "BTCUSDT", rows)
        loaded = load_range(con, "BTCUSDT")
        times = [r[0] for r in loaded]
        assert times == sorted(times)
