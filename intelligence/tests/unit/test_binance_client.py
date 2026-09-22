"""Unit tests for intelligence.core.binance_client. No real network calls -- _get is
monkeypatched so these tests are deterministic and offline. Integration tests that hit the
real Binance API live in intelligence/tests/integration/."""
from __future__ import annotations

import pytest

from intelligence.core.binance_client import (BinanceClient, BinanceClientError, BookTicker)


class TestNoTradingImports:
    def test_module_asserts_no_trading_libs(self):
        import sys
        for name in ("ccxt", "binance.client"):
            assert name not in sys.modules


class TestBookTickerFallback:
    def test_uses_bid_ask_when_valid(self, monkeypatch):
        c = BinanceClient()
        monkeypatch.setattr(c, "_get", lambda path, params: {"bidPrice": "10.0",
                                                              "askPrice": "10.1"})
        bt = c.book_ticker("BTCUSDT")
        assert bt.kind == "bid_ask"
        assert bt.bid == 10.0 and bt.ask == 10.1
        assert bt.mid == pytest.approx(10.05)

    def test_falls_back_to_mid_on_error(self, monkeypatch):
        c = BinanceClient()

        def fake_get(path, params):
            if "bookTicker" in path:
                raise BinanceClientError("simulated outage")
            return [[0, 0, 0, 0, "99.5", 0, 0, 0, 0, 0, 0]]  # kline row, close=99.5

        monkeypatch.setattr(c, "_get", fake_get)
        bt = c.book_ticker("BTCUSDT")
        assert bt.kind == "mid_fallback"
        assert bt.bid == bt.ask == 99.5

    def test_falls_back_when_book_is_crossed(self, monkeypatch):
        c = BinanceClient()

        def fake_get(path, params):
            if "bookTicker" in path:
                return {"bidPrice": "10.5", "askPrice": "10.0"}  # bid > ask: invalid
            return [[0, 0, 0, 0, "10.2", 0, 0, 0, 0, 0, 0]]

        monkeypatch.setattr(c, "_get", fake_get)
        bt = c.book_ticker("BTCUSDT")
        assert bt.kind == "mid_fallback"


class TestSpreadBp:
    def test_spread_bp_calculation(self):
        bt = BookTicker("BTCUSDT", bid=100.0, ask=100.1, kind="bid_ask")
        assert bt.spread_bp == pytest.approx((0.1 / 100.05) * 1e4)


class TestRetryBackoff:
    def test_retries_then_raises_client_error(self, monkeypatch):
        c = BinanceClient(retries=3, timeout=1.0)
        calls = {"n": 0}

        def always_fail(*a, **k):
            calls["n"] += 1
            raise TimeoutError("simulated")

        monkeypatch.setattr("urllib.request.urlopen", always_fail)
        monkeypatch.setattr("time.sleep", lambda s: None)
        with pytest.raises(BinanceClientError):
            c.klines("BTCUSDT")
        assert calls["n"] == 3

    def test_succeeds_after_transient_failure(self, monkeypatch):
        c = BinanceClient(retries=3, timeout=1.0)
        calls = {"n": 0}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"[[1,2,3]]"

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] < 2:
                raise TimeoutError("simulated")
            return FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", flaky)
        monkeypatch.setattr("time.sleep", lambda s: None)
        result = c.klines("BTCUSDT")
        assert result == [[1, 2, 3]]
        assert calls["n"] == 2
