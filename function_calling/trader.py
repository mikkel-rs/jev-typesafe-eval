"""Ten ordinary trading-assistant functions over one-minute bars, plus a cached TypeSafe client.

Rebuilt from the TypeSafe "Function calling" cookbook, whose own trader.py is not published.
ASSUMPTION: the bars are synthetic (seeded random walk, 67 sessions x 390 minutes x 6 tickers
= 156,780 rows, same count as the cookbook). The experiment is about dispatch, not the market.
"""

from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from cooksafe import JsonCache
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

Symbol = Literal["SPY", "NVDA", "AMD", "AAPL", "MSFT", "TSLA"]
Window = Literal["1d", "1w", "1mo", "3mo"]
SYMBOLS = ["SPY", "NVDA", "AMD", "AAPL", "MSFT", "TSLA"]
SESSIONS = {"1d": 1, "1w": 5, "1mo": 21, "3mo": 63}
START = {"SPY": 640.0, "NVDA": 205.0, "AMD": 170.0, "AAPL": 245.0, "MSFT": 440.0, "TSLA": 335.0}
VOL = {"SPY": 0.010, "NVDA": 0.028, "AMD": 0.030, "AAPL": 0.015, "MSFT": 0.014, "TSLA": 0.035}


@lru_cache(maxsize=1)
def load() -> pl.DataFrame:
    """One-minute OHLCV bars: 67 sessions, 390 minutes each, six tickers."""
    rng = np.random.default_rng(16)
    days = pl.date_range(pl.date(2026, 6, 1), pl.date(2026, 9, 30), "1d", eager=True)
    days = [d for d in days if d.weekday() < 5][:67]
    minutes = np.arange(390)
    u_shape = 1.0 + 1.6 * ((minutes - 195) / 195.0) ** 2  # busy open and close, quiet lunch
    market = rng.normal(0, 1, (len(days), 390))
    frames = []
    for sym in SYMBOLS:
        beta = 0.6 if sym != "SPY" else 1.0
        own = rng.normal(0, 1, (len(days), 390))
        shock = (beta * market + (1 - beta) * own) * VOL[sym] / np.sqrt(390) * np.sqrt(u_shape)
        close = START[sym] * np.exp(np.cumsum(shock.ravel()))
        open_ = np.concatenate([[START[sym]], close[:-1]])
        wiggle = np.abs(rng.normal(0, VOL[sym] / 60, close.size)) * close
        volume = (rng.gamma(2.0, 1.0, close.size) * np.tile(u_shape, len(days)) * 4000).astype(int)
        ts = np.concatenate(
            [np.datetime64(f"{d}T09:30", "ms") + minutes.astype("timedelta64[m]") for d in days]
        )
        frames.append(
            pl.DataFrame(
                {
                    "ts": ts,
                    "symbol": sym,
                    "open": open_,
                    "high": np.maximum(open_, close) + wiggle,
                    "low": np.minimum(open_, close) - wiggle,
                    "close": close,
                    "volume": volume,
                }
            )
        )
    return pl.concat(frames)


def _bars(symbol: str, window: str, resolution: str = "1m") -> pl.DataFrame:
    df = load().filter(pl.col("symbol") == symbol)
    last_days = df["ts"].dt.date().unique().sort().tail(SESSIONS[window])
    df = df.filter(pl.col("ts").dt.date().is_in(last_days.implode()))
    if resolution == "1m":
        return df
    return (
        df.sort("ts")
        .group_by_dynamic("ts", every=resolution)
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
        )
    )


def _ret(symbol: str, window: str) -> float:
    b = _bars(symbol, window)
    return float(b["close"][-1] / b["open"][0] - 1)


def list_symbols() -> str:
    return "tickers: " + ", ".join(SYMBOLS)


def market_summary(window: Window = "1d") -> str:
    rows = sorted(
        ((s, _bars(s, window)) for s in SYMBOLS), key=lambda r: -_ret(r[0], window)
    )
    lines = [f"the board over {window}"]
    for s, b in rows:
        lines.append(f"  {s:<6}{b['close'][-1]:>9.2f}{_ret(s, window):>9.2%}{b['volume'].sum():>15,}")
    return "\n".join(lines)


def plot_price(
    symbol: Symbol,
    style: Literal["line", "candles"] = "line",
    resolution: Literal["1m", "5m", "15m", "1h", "1d"] = "15m",
    window: Window = "1w",
    include_volume: bool = False,
    moving_average: Literal["9", "20", "50"] | None = None,
    log_scale: bool = False,
):
    b = _bars(symbol, window, resolution)
    x = np.arange(b.height)
    fig, axes = plt.subplots(
        2 if include_volume else 1, 1, figsize=(11, 4), sharex=True, squeeze=False,
        gridspec_kw={"height_ratios": [3, 1]} if include_volume else None,
    )
    ax = axes[0][0]
    if style == "candles":
        up = (b["close"] >= b["open"]).to_numpy()
        ax.vlines(x, b["low"], b["high"], color="grey", lw=0.6)
        ax.bar(x, (b["close"] - b["open"]).to_numpy(), bottom=b["open"].to_numpy(),
               color=np.where(up, "tab:green", "tab:red"), width=0.7)
    else:
        ax.plot(x, b["close"])
    if moving_average:
        ax.plot(x, b["close"].rolling_mean(int(moving_average)), lw=1, label=f"MA{moving_average}")
        ax.legend()
    if log_scale:
        ax.set_yscale("log")
    if include_volume:
        axes[1][0].bar(x, b["volume"], color="grey")
    ax.set_title(f"{symbol} {style} {resolution} over {window}")
    return fig


def intraday_pattern(
    symbol: Symbol,
    window: Window = "1mo",
    metric: Literal["volume", "range", "return"] = "volume",
):
    b = _bars(symbol, window).with_columns(
        (pl.col("ts").dt.hour() * 60 + pl.col("ts").dt.minute()).alias("minute"),
        ((pl.col("high") - pl.col("low")) / pl.col("open")).alias("range"),
        (pl.col("close") / pl.col("open") - 1).alias("return"),
    )
    g = b.group_by(pl.col("minute") // 30 * 30).agg(pl.col(metric).mean()).sort("minute")
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.bar([f"{m // 60:02d}:{m % 60:02d}" for m in g["minute"]], g[metric])
    ax.set_title(f"{symbol} average {metric} by half hour over {window}")
    return fig


def compare_returns(symbols: list[Symbol], window: Window = "1mo", normalize: bool = True):
    fig, ax = plt.subplots(figsize=(11, 4))
    for s in symbols:
        b = _bars(s, window, "1h")
        y = b["close"] / b["close"][0] * 100 if normalize else b["close"]
        ax.plot(np.arange(b.height), y, label=s)
    ax.legend()
    ax.set_title(f"{', '.join(symbols)} over {window}" + (" (rebased to 100)" if normalize else ""))
    return fig


def rolling_correlation(
    symbol: Symbol,
    benchmark: Symbol,
    window: Window = "1mo",
    resolution: Literal["5m", "15m", "1h", "1d"] = "1h",
):
    a = _bars(symbol, window, resolution)["close"].pct_change()
    c = _bars(benchmark, window, resolution)["close"].pct_change()
    n = min(a.len(), c.len())
    corr = pl.DataFrame({"a": a[:n], "c": c[:n]}).select(pl.rolling_corr("a", "c", window_size=20))
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(corr.to_series())
    ax.set_title(f"{symbol} vs {benchmark}: 20-bar rolling correlation, {resolution} bars, {window}")
    return fig


def summary_stats(symbol: Symbol, window: Window = "1mo") -> str:
    b = _bars(symbol, window)
    return (
        f"{symbol} over {window}: last {b['close'][-1]:.2f}, return {_ret(symbol, window):.2%}, "
        f"high {b['high'].max():.2f}, low {b['low'].min():.2f}, volume {b['volume'].sum():,}"
    )


def volatility(symbol: Symbol, window: Window = "1mo", annualized: bool = False) -> str:
    r = _bars(symbol, window, "1d")["close"].pct_change().drop_nulls()
    v = float(r.std() or 0.0) * (np.sqrt(252) if annualized else 1)
    return f"{symbol} {'annualized' if annualized else 'daily'} volatility over {window}: {v:.2%}"


def top_movers(
    window: Window = "1d",
    direction: Literal["gainers", "losers"] = "gainers",
    limit: int = 3,
) -> str:
    rows = sorted(((s, _ret(s, window)) for s in SYMBOLS), key=lambda r: r[1],
                  reverse=direction == "gainers")[:limit]
    return f"top {limit} {direction} over {window}\n" + "\n".join(
        f"  {s:<6}{r:>8.2%}" for s, r in rows
    )


def drawdown(symbol: Symbol, window: Window = "1mo", plot: bool = False):
    b = _bars(symbol, window, "15m")
    dd = b["close"] / b["close"].cum_max() - 1
    if not plot:
        return f"{symbol} worst drawdown over {window}: {dd.min():.2%}"
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.fill_between(np.arange(dd.len()), dd, 0, color="tab:red", alpha=0.4)
    ax.set_title(f"{symbol} drawdown over {window} (worst {dd.min():.2%})")
    return fig


TOOLS = {
    f.__name__: f
    for f in (
        list_symbols, market_summary, plot_price, intraday_pattern, compare_returns,
        rolling_correlation, summary_stats, volatility, top_movers, drawdown,
    )
}


class CachedClient:
    """TypeSafe client whose answers are memoized to answers.json, so reruns replay for free.

    ask(state, questions, spec_hash) -> {"answers": {qid: {...}}, "input_tokens", "latency_ms", "model"}
    `questions` are plain dicts: {"type": "choice"|"noul", "instructions": str, "criteria": {...}}.
    The cache key is (state, spec_hash, model); the hash stands in for the question set.
    """

    def __init__(self, path: Path, model: str = "jev-latest") -> None:
        self.model = model
        self._sdk = None
        self._questions: dict = {}

        def ask(state: str, spec_hash: str, model: str) -> dict:
            return self._live(state, model)

        self._cached = JsonCache(path, indent=None)(ask)

    def ask(self, state: str, questions: dict, spec_hash: str) -> dict:
        self._questions = questions
        return self._cached(state, spec_hash, self.model)

    def _live(self, state: str, model: str) -> dict:
        from typesafe_sdk import Choice, Noul, TypeSafeClient

        if self._sdk is None:
            if not os.environ.get("TYPESAFE_API_KEY"):
                raise RuntimeError("TYPESAFE_API_KEY is not set (put it in .env next to trader.py)")
            self._sdk = TypeSafeClient()
        built = {
            qid: Choice(instructions=q["instructions"], criteria=q["criteria"])
            if q["type"] == "choice"
            else Noul(instructions=q["instructions"])
            for qid, q in self._questions.items()
        }
        t0 = time.perf_counter()
        r = self._sdk.system_one(state, built, model=model)
        latency = (time.perf_counter() - t0) * 1000
        answers = {}
        for qid, a in r.answers.items():
            if hasattr(a, "noul"):
                answers[qid] = {"noul": a.noul}
            else:
                answers[qid] = {"choice": a.choice, "confidence": a.confidence,
                                "probabilities": dict(a.probabilities)}
        return {"answers": answers, "input_tokens": r.usage.input_tokens,
                "latency_ms": latency, "model": r.model}


client = CachedClient(HERE / "answers.json", model=os.environ.get("TYPESAFE_MODEL", "jev-latest"))
