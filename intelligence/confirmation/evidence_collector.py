"""
EXP-124 -- evidence collector.

RESEARCH / PAPER ONLY. Calls every evidence engine built in Phases 4-7 (candle, structure,
volatility, magnitude, liquidity, cross-sectional, BTC/market regime, independent direction) for
one symbol at one cycle, and translates each engine's DESCRIPTIVE output into an `Evidence`
entry -- SUPPORT/CONFLICT read relative to a LONG signal, since LONG is the only direction
EXP-107 ever fires (CODEBASE_MAP.md section 2.2). This mapping is simple, fixed, and fully
documented below -- NOT a trained or calibrated weighting. Actual weighting of how much any of
this should matter happens in feature_registry.py from tracked outcomes, never here.

"VOLATILITY_HIGH != BUY", "HIGH AGREEMENT != ENTRY" (brief sections 8, 10) are enforced by
construction: every mapping below only ever produces evidence ABOUT an already-fired LONG
signal (confirmation_engine.confirm() only runs when exp107.is_long_fire is already true) --
nothing here can trigger anything by itself.
"""
from __future__ import annotations

from intelligence.confirmation.evidence import Evidence, EvidenceBundle, Strength
from intelligence.core.market_buffer import MarketBuffer
from intelligence.engines import candle_engine, cross_sectional_engine, direction_engine
from intelligence.engines import liquidity_engine, magnitude_engine, regime_engine
from intelligence.engines import structure_engine, volatility_engine

MAG_SUPPORT_STATES = {"HIGH", "EXTREME"}
VOL_SUPPORT_STATES = {"HIGH", "EXTREME"}
AGREEMENT_SUPPORT_RATIO = 0.7
STRONG_DIRECTION_CONFIDENCE = 0.75

DEFAULT_STRUCTURE_TIMEFRAME_MIN = 60
DEFAULT_VOLATILITY_TIMEFRAME_MIN = 60
DEFAULT_MAGNITUDE_HORIZON_BARS = 240   # "4H" against a 1-minute-bar series
DEFAULT_DIRECTION_TIMEFRAMES_MIN = (15, 60, 240)


def collect(symbol: str, buffer: MarketBuffer,
           structure_timeframe_min: int = DEFAULT_STRUCTURE_TIMEFRAME_MIN,
           volatility_timeframe_min: int = DEFAULT_VOLATILITY_TIMEFRAME_MIN,
           magnitude_horizon_bars: int = DEFAULT_MAGNITUDE_HORIZON_BARS,
           direction_timeframes_min: tuple[int, ...] = DEFAULT_DIRECTION_TIMEFRAMES_MIN,
           btc_ohlcv=None, cross_sectional_directions: dict[str, int | None] | None = None,
           book_ticker=None, depth: dict | None = None, premium_index: dict | None = None,
           open_interest: dict | None = None) -> EvidenceBundle:
    ohlcv = buffer.resample(symbol, structure_timeframe_min)
    open_time = int(buffer.grid[-1]) if len(buffer.grid) else 0
    items: list[Evidence] = []

    # 1. market structure
    struct = structure_engine.structure_state(ohlcv)
    if struct is None:
        items.append(Evidence("structure_engine", "TREND_STATE", Strength.UNKNOWN,
                              "insufficient history"))
    elif struct.trend == "BULLISH":
        items.append(Evidence("structure_engine", "TREND_STATE", Strength.SUPPORT, struct.trend))
    elif struct.trend == "BEARISH":
        items.append(Evidence("structure_engine", "TREND_STATE", Strength.CONFLICT, struct.trend))
    else:
        items.append(Evidence("structure_engine", "TREND_STATE", Strength.NEUTRAL, struct.trend))

    # 2. candle / price action, same timeframe as structure
    candle = candle_engine.latest(ohlcv)
    if candle is None:
        items.append(Evidence("candle_engine", "REJECTION", Strength.UNKNOWN,
                              "insufficient history"))
    else:
        if candle.rejection == "lower":
            items.append(Evidence("candle_engine", "REJECTION", Strength.SUPPORT,
                                  "lower-wick rejection"))
        elif candle.rejection == "upper":
            items.append(Evidence("candle_engine", "REJECTION", Strength.CONFLICT,
                                  "upper-wick rejection"))
        else:
            items.append(Evidence("candle_engine", "REJECTION", Strength.NEUTRAL, "none"))
        if candle.engulfing == "bullish":
            items.append(Evidence("candle_engine", "ENGULFING", Strength.SUPPORT,
                                  "bullish engulfing"))
        elif candle.engulfing == "bearish":
            items.append(Evidence("candle_engine", "ENGULFING", Strength.CONFLICT,
                                  "bearish engulfing"))

    # 3. volatility (its own timeframe -- may differ from structure's)
    vol_ohlcv = buffer.resample(symbol, volatility_timeframe_min)
    vol = volatility_engine.latest(vol_ohlcv)
    if vol is None or vol.regime == "UNKNOWN":
        items.append(Evidence("volatility_engine", "VOLATILITY_REGIME", Strength.UNKNOWN,
                              "insufficient history"))
    elif vol.regime in VOL_SUPPORT_STATES:
        items.append(Evidence("volatility_engine", "VOLATILITY_REGIME_HIGH", Strength.SUPPORT,
                              vol.regime, value=vol.atr_percentile))
    else:
        items.append(Evidence("volatility_engine", "VOLATILITY_REGIME", Strength.NEUTRAL,
                              vol.regime, value=vol.atr_percentile))

    # 4. previous-move magnitude -- ALWAYS against the native 1-minute series, never the
    # resampled `ohlcv` above: magnitude_horizon_bars follows magnitude_engine.py's own
    # convention (e.g. 240 == "4H") measured directly in 1-minute bars, exactly like
    # scripts/d_features.py's own windows/lags are measured in minutes over the native series,
    # never over a resampled higher-timeframe candle. Applying a bar-count horizon meant for
    # 1-minute bars to `ohlcv` (60-minute bars by default) would silently turn "4H" into "10
    # days" -- caught while writing intelligence/tests/unit/test_cycle_runner.py, fixed here.
    mag_ohlcv = buffer.resample(symbol, 1)
    mag = magnitude_engine.latest(mag_ohlcv, magnitude_horizon_bars)
    if mag is None or mag.state == "UNKNOWN":
        items.append(Evidence("magnitude_engine", "MAGNITUDE_STATE", Strength.UNKNOWN,
                              "insufficient history"))
    elif mag.state in MAG_SUPPORT_STATES:
        items.append(Evidence("magnitude_engine", "MAGNITUDE_STATE_HIGH", Strength.SUPPORT,
                              mag.state, value=mag.percentile_rank))
    else:
        items.append(Evidence("magnitude_engine", "MAGNITUDE_STATE", Strength.NEUTRAL,
                              mag.state, value=mag.percentile_rank))

    # 5. liquidity / volume anomalies (liquidation data is always UNKNOWN -- no endpoint exists)
    liq = liquidity_engine.compute(
        ohlcv, wick_rejection=(candle.rejection if candle else "none"), book_ticker=book_ticker,
        depth=depth, premium_index=premium_index, open_interest=open_interest)
    if liq is None:
        items.append(Evidence("liquidity_engine", "VOLUME_SPIKE", Strength.UNKNOWN,
                              "insufficient history"))
    else:
        if liq.volume_spike:
            items.append(Evidence("liquidity_engine", "VOLUME_SPIKE", Strength.SUPPORT,
                                  "volume z-score spike", value=liq.volume_zscore))
        else:
            items.append(Evidence("liquidity_engine", "VOLUME_SPIKE", Strength.NEUTRAL,
                                  "no spike"))
        items.append(Evidence("liquidity_engine", "LIQUIDATION_DATA", Strength.UNKNOWN,
                              "no endpoint available -- see CODEBASE_MAP.md liquidity note"))

    # 6. cross-sectional agreement (needs every symbol's direction supplied by the caller --
    #    this engine has no way to compute the other 9 symbols' reads on its own)
    if cross_sectional_directions is not None:
        cs = cross_sectional_engine.cross_sectional_state(cross_sectional_directions, open_time)
        align = cross_sectional_engine.symbol_alignment(symbol, cs)
        if align == "ALIGNED" and cs.agreement_ratio >= AGREEMENT_SUPPORT_RATIO:
            items.append(Evidence("cross_sectional_engine", "AGREEMENT", Strength.SUPPORT,
                                  f"{cs.agreement_ratio:.0%} {cs.majority_direction}",
                                  value=cs.agreement_ratio))
        elif align == "DIVERGENT":
            items.append(Evidence("cross_sectional_engine", "AGREEMENT", Strength.CONFLICT,
                                  f"symbol diverges from {cs.majority_direction} majority",
                                  value=cs.agreement_ratio))
        elif align == "UNKNOWN":
            items.append(Evidence("cross_sectional_engine", "AGREEMENT", Strength.UNKNOWN,
                                  "symbol not in cross-sectional set"))
        else:
            items.append(Evidence("cross_sectional_engine", "AGREEMENT", Strength.NEUTRAL,
                                  align, value=cs.agreement_ratio))
    else:
        items.append(Evidence("cross_sectional_engine", "AGREEMENT", Strength.UNKNOWN,
                              "not supplied this cycle"))

    # 7. BTC / market regime
    if btc_ohlcv is not None:
        btc = regime_engine.btc_regime(btc_ohlcv)
        if btc is None:
            items.append(Evidence("regime_engine", "BTC_TREND", Strength.UNKNOWN,
                                  "insufficient BTC history"))
        elif btc.trend_state == "BULLISH":
            items.append(Evidence("regime_engine", "BTC_TREND", Strength.SUPPORT, btc.trend_state))
        elif btc.trend_state == "BEARISH":
            items.append(Evidence("regime_engine", "BTC_TREND", Strength.CONFLICT, btc.trend_state))
        else:
            items.append(Evidence("regime_engine", "BTC_TREND", Strength.NEUTRAL, btc.trend_state))
    else:
        items.append(Evidence("regime_engine", "BTC_TREND", Strength.UNKNOWN,
                              "BTC data not supplied this cycle"))

    # 8. independent, heuristic, multi-timeframe direction estimate (SECONDARY evidence only --
    #    see direction_engine.py's own module docstring)
    dest = direction_engine.estimate(buffer, symbol, list(direction_timeframes_min))
    if dest.direction == "UNKNOWN":
        items.append(Evidence("direction_engine", "DIRECTION_ESTIMATE", Strength.UNKNOWN,
                              "insufficient history"))
    elif dest.direction == "LONG":
        strength = (Strength.STRONG_SUPPORT if dest.confidence >= STRONG_DIRECTION_CONFIDENCE
                   else Strength.SUPPORT)
        items.append(Evidence("direction_engine", "DIRECTION_ESTIMATE", strength,
                              f"LONG confidence={dest.confidence:.2f}", value=dest.confidence))
    elif dest.direction == "SHORT":
        items.append(Evidence("direction_engine", "DIRECTION_ESTIMATE", Strength.CONFLICT,
                              f"SHORT confidence={dest.confidence:.2f}", value=dest.confidence))
    else:
        items.append(Evidence("direction_engine", "DIRECTION_ESTIMATE", Strength.NEUTRAL,
                              "NEUTRAL"))

    return EvidenceBundle(open_time=open_time, symbol=symbol, items=items)
