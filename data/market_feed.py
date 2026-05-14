"""
data/market_feed.py — Market data fetcher using requests + asyncio.to_thread.

Uses the `requests` library (which respects Windows SSL/cert store) instead of
aiohttp, wrapped in asyncio.to_thread so it fits the async agent loop.

Exchange priority: Bybit → OKX → Gate.io Futures
"""

import asyncio
import math
from typing import Dict, List, Optional, Tuple

import requests

from utils.logger import logger

# Suppress the InsecureRequestWarning from requests when verify=False
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_SESSION = requests.Session()
_SESSION.verify = False   # mirrors curl behaviour — Windows SSL chain issue
_TIMEOUT = 15


# ── Exchange definitions ──────────────────────────────────────────────────

class _Bybit:
    name = "Bybit"

    @staticmethod
    def _sym(s: str) -> str:
        return s.replace("/USDT:USDT","USDT").replace("/","").replace(":","").upper()

    def fetch_ohlcv(self, symbol: str, limit: int, interval: str = "240") -> List[List[float]]:
        sym = self._sym(symbol)
        r = _SESSION.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": sym, "interval": interval, "limit": limit},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json().get("result", {}).get("list", [])
        # Bybit: newest-first [ts, o, h, l, c, vol, turnover]
        result = []
        for row in reversed(rows):
            try:
                result.append([float(row[0]), float(row[1]), float(row[2]),
                                float(row[3]), float(row[4]), float(row[5])])
            except (IndexError, ValueError, TypeError):
                pass
        return result

    def fetch_funding(self, symbol: str) -> float:
        sym = self._sym(symbol)
        r = _SESSION.get(
            "https://api.bybit.com/v5/market/funding/history",
            params={"category": "linear", "symbol": sym, "limit": 1},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json().get("result", {}).get("list", [])
        return float(rows[0].get("fundingRate", 0)) if rows else 0.0

    def fetch_open_interest(self, symbol: str) -> Optional[Dict]:
        """Fetch OI from Bybit v5 API — returns raw value + 24h comparison."""
        sym = self._sym(symbol)
        try:
            r = _SESSION.get(
                "https://api.bybit.com/v5/market/open-interest",
                params={"category": "linear", "symbol": sym, "intervalTime": "1h", "limit": 24},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            rows = r.json().get("result", {}).get("list", [])
            if not rows:
                return None
            # rows[0] = most recent, rows[-1] = oldest
            current = float(rows[0].get("openInterest", 0))
            old = float(rows[-1].get("openInterest", 0)) if len(rows) > 1 else current
            if old == 0:
                return {"value": current, "change_pct": 0.0}
            change = (current - old) / old * 100
            return {"value": current, "change_pct": round(change, 2)}
        except Exception:
            return None

    def fetch_ticker(self, symbol: str) -> Optional[Dict]:
        """
        Fetch real-time bid/ask spread + 24h volume from Bybit ticker.
        Returns: {bid, ask, spread_pct, volume_24h_usd, last_price} or None.

        Spread detection — protects from low-liquidity coins where SL hits noise.
        """
        sym = self._sym(symbol)
        try:
            r = _SESSION.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear", "symbol": sym},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            rows = r.json().get("result", {}).get("list", [])
            if not rows:
                return None
            t = rows[0]
            bid = float(t.get("bid1Price", 0) or 0)
            ask = float(t.get("ask1Price", 0) or 0)
            last = float(t.get("lastPrice", 0) or 0)
            vol_usd = float(t.get("turnover24h", 0) or 0)  # quote-volume USD
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2
                spread_pct = (ask - bid) / mid * 100 if mid > 0 else 0.0
            else:
                spread_pct = 0.0
            return {
                "bid": bid,
                "ask": ask,
                "last_price": last,
                "spread_pct": round(spread_pct, 4),
                "volume_24h_usd": round(vol_usd, 2),
            }
        except Exception:
            return None

    def fetch_funding_history(self, symbol: str, limit: int = 24) -> List[float]:
        """
        Fetch last N funding rates (8h periods, default 24 = ~8 days history).
        Used for funding anomaly detection (z-score).
        """
        sym = self._sym(symbol)
        try:
            r = _SESSION.get(
                "https://api.bybit.com/v5/market/funding/history",
                params={"category": "linear", "symbol": sym, "limit": limit},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            rows = r.json().get("result", {}).get("list", [])
            return [float(row.get("fundingRate", 0)) for row in rows]
        except Exception:
            return []


class _OKX:
    name = "OKX"

    @staticmethod
    def _sym(s: str) -> str:
        s = s.replace("/USDT:USDT","USDT").replace("/","").replace(":","").upper()
        return f"{s[:-4]}-USDT-SWAP" if s.endswith("USDT") else s

    def fetch_ohlcv(self, symbol: str, limit: int, interval: str = "4H") -> List[List[float]]:
        r = _SESSION.get(
            "https://www.okx.com/api/v5/market/candles",
            params={"instId": self._sym(symbol), "bar": interval, "limit": min(limit, 300)},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json().get("data", [])
        # OKX: newest-first [ts, o, h, l, c, vol, ...]
        result = []
        for row in reversed(rows):
            try:
                result.append([float(row[0]), float(row[1]), float(row[2]),
                                float(row[3]), float(row[4]), float(row[5])])
            except (IndexError, ValueError, TypeError):
                pass
        return result

    def fetch_funding(self, symbol: str) -> float:
        r = _SESSION.get(
            "https://www.okx.com/api/v5/public/funding-rate",
            params={"instId": self._sym(symbol)},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json().get("data", [])
        return float(rows[0].get("fundingRate", 0)) if rows else 0.0

    def fetch_open_interest(self, symbol: str) -> Optional[Dict]:
        return None

    def fetch_ticker(self, symbol: str) -> Optional[Dict]:
        return None

    def fetch_funding_history(self, symbol: str, limit: int = 24) -> List[float]:
        return []


class _GateIO:
    name = "Gate.io"

    @staticmethod
    def _sym(s: str) -> str:
        s = s.replace("/USDT:USDT","USDT").replace("/","").replace(":","").upper()
        return f"{s[:-4]}_USDT" if s.endswith("USDT") else s

    def fetch_ohlcv(self, symbol: str, limit: int, interval: str = "4h") -> List[List[float]]:
        r = _SESSION.get(
            "https://fx-api.gateio.ws/api/v4/futures/usdt/candlesticks",
            params={"contract": self._sym(symbol), "interval": interval, "limit": limit},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            return []
        result = []
        for row in data:
            try:
                result.append([
                    float(row.get("t", 0)) * 1000,
                    float(row.get("o", 0)), float(row.get("h", 0)),
                    float(row.get("l", 0)), float(row.get("c", 0)),
                    float(row.get("v", 0)),
                ])
            except (ValueError, TypeError):
                pass
        return result

    def fetch_funding(self, symbol: str) -> float:
        return 0.0

    def fetch_open_interest(self, symbol: str) -> Optional[Dict]:
        return None

    def fetch_ticker(self, symbol: str) -> Optional[Dict]:
        return None

    def fetch_funding_history(self, symbol: str, limit: int = 24) -> List[float]:
        return []


_EXCHANGES = [_Bybit(), _OKX(), _GateIO()]


# ── Sync fetch helpers (run in thread pool) ───────────────────────────────

# Interval mapping: Bybit format → (OKX format, Gate.io format)
_INTERVAL_MAP = {
    "5":   ("5m",  "5m"),
    "15":  ("15m", "15m"),
    "60":  ("1H",  "1h"),
    "240": ("4H",  "4h"),
    "D":   ("1D",  "1d"),
}


def _sync_fetch_ohlcv(
    symbol: str, limit: int, interval: str = "240"
) -> Optional[Tuple[List[List[float]], object]]:
    """Try each exchange synchronously. Returns (candles, exchange) or None."""
    okx_int, gate_int = _INTERVAL_MAP.get(interval, ("4H", "4h"))
    min_candles = max(30, limit // 4)

    for exch in _EXCHANGES:
        try:
            if isinstance(exch, _Bybit):
                rows = exch.fetch_ohlcv(symbol, limit, interval)
            elif isinstance(exch, _OKX):
                rows = exch.fetch_ohlcv(symbol, limit, okx_int)
            else:
                rows = exch.fetch_ohlcv(symbol, limit, gate_int)

            if len(rows) >= min_candles:
                logger.debug(f"[MarketFeed] {exch.name}: {symbol} OK ({len(rows)} candles @{interval})")
                return rows, exch
            logger.debug(f"[MarketFeed] {exch.name}: {symbol} only {len(rows)} candles")
        except requests.exceptions.Timeout:
            logger.debug(f"[MarketFeed] {exch.name}: timeout for {symbol}")
        except requests.exceptions.ConnectionError as exc:
            logger.debug(f"[MarketFeed] {exch.name}: connection error for {symbol}: {exc}")
        except Exception as exc:
            logger.debug(f"[MarketFeed] {exch.name}: {symbol} error: {exc}")
    return None


def _sync_fetch_daily(symbol: str) -> Optional[List[List[float]]]:
    """Fetch Daily candles for higher-timeframe context."""
    bybit = _EXCHANGES[0]  # Bybit primary
    try:
        rows = bybit.fetch_ohlcv(symbol, limit=100, interval="D")
        if len(rows) >= 20:
            return rows
    except Exception:
        pass
    # Fallback to OKX
    okx = _EXCHANGES[1]
    try:
        rows = okx.fetch_ohlcv(symbol, limit=100, interval="1D")
        if len(rows) >= 20:
            return rows
    except Exception:
        pass
    return None


def _sync_fetch_1h(symbol: str) -> Optional[List[List[float]]]:
    """Fetch 1H candles for intermediate-timeframe confirmation."""
    bybit = _EXCHANGES[0]
    try:
        rows = bybit.fetch_ohlcv(symbol, limit=100, interval="60")
        if len(rows) >= 50:
            return rows
    except Exception:
        pass
    okx = _EXCHANGES[1]
    try:
        rows = okx.fetch_ohlcv(symbol, limit=100, interval="1H")
        if len(rows) >= 50:
            return rows
    except Exception:
        pass
    return None


def _sync_fetch_15m(symbol: str) -> Optional[List[List[float]]]:
    """Fetch 15M candles for entry-timing precision."""
    bybit = _EXCHANGES[0]
    try:
        rows = bybit.fetch_ohlcv(symbol, limit=100, interval="15")
        if len(rows) >= 50:
            return rows
    except Exception:
        pass
    okx = _EXCHANGES[1]
    try:
        rows = okx.fetch_ohlcv(symbol, limit=100, interval="15m")
        if len(rows) >= 50:
            return rows
    except Exception:
        pass
    return None


def _sync_fetch_4h(symbol: str) -> Optional[List[List[float]]]:
    """Fetch 4H candles — used as super-HTF in intraday mode."""
    bybit = _EXCHANGES[0]
    try:
        rows = bybit.fetch_ohlcv(symbol, limit=60, interval="240")
        if len(rows) >= 30:
            return rows
    except Exception:
        pass
    okx = _EXCHANGES[1]
    try:
        rows = okx.fetch_ohlcv(symbol, limit=60, interval="4H")
        if len(rows) >= 30:
            return rows
    except Exception:
        pass
    return None


def _sync_fetch_5m(symbol: str) -> Optional[List[List[float]]]:
    """Fetch 5M candles — entry timing precision in intraday mode."""
    bybit = _EXCHANGES[0]
    try:
        rows = bybit.fetch_ohlcv(symbol, limit=100, interval="5")
        if len(rows) >= 50:
            return rows
    except Exception:
        pass
    okx = _EXCHANGES[1]
    try:
        rows = okx.fetch_ohlcv(symbol, limit=100, interval="5m")
        if len(rows) >= 50:
            return rows
    except Exception:
        pass
    return None


def _sync_fetch_funding(symbol: str, exch) -> float:
    try:
        return exch.fetch_funding(symbol)
    except Exception:
        return 0.0


def _sync_fetch_ticker(symbol: str) -> Optional[Dict]:
    """Fetch real-time ticker (bid/ask spread + 24h volume USD) from Bybit."""
    bybit = _EXCHANGES[0]
    try:
        return bybit.fetch_ticker(symbol)
    except Exception:
        return None


def _sync_fetch_funding_history(symbol: str) -> List[float]:
    """Fetch last 24 funding rate periods (~8 days) for anomaly detection."""
    bybit = _EXCHANGES[0]
    try:
        return bybit.fetch_funding_history(symbol, limit=24)
    except Exception:
        return []


def _compute_wick_metrics(highs: List[float], lows: List[float],
                          opens: List[float], closes: List[float],
                          atr_pct: float) -> Dict:
    """
    Compute upper/lower wick ratio of LAST candle vs ATR.
    Detects stop-hunt manipulation (wick 3-5% then reverse).

    Returns: {upper_wick_atr, lower_wick_atr, wick_warning}
    """
    if not (highs and lows and opens and closes):
        return {"upper_wick_atr": 0.0, "lower_wick_atr": 0.0, "wick_warning": False}
    if atr_pct <= 0:
        return {"upper_wick_atr": 0.0, "lower_wick_atr": 0.0, "wick_warning": False}

    h, l, o, c = highs[-1], lows[-1], opens[-1], closes[-1]
    if c <= 0:
        return {"upper_wick_atr": 0.0, "lower_wick_atr": 0.0, "wick_warning": False}

    body_top = max(o, c)
    body_bot = min(o, c)
    upper_wick_pct = (h - body_top) / c * 100 if h > body_top else 0.0
    lower_wick_pct = (body_bot - l) / c * 100 if l < body_bot else 0.0

    # Normalize to ATR — "wick is N times the typical move"
    upper_atr = upper_wick_pct / atr_pct if atr_pct > 0 else 0.0
    lower_atr = lower_wick_pct / atr_pct if atr_pct > 0 else 0.0

    # Warn if wick ≥ 2× ATR (stop-hunt territory)
    warning = upper_atr >= 2.0 or lower_atr >= 2.0

    return {
        "upper_wick_atr": round(upper_atr, 2),
        "lower_wick_atr": round(lower_atr, 2),
        "wick_warning": warning,
    }


def _compute_wash_suspicion(volume_24h_usd: float, oi_change_pct: float,
                             min_volume_check: float = 50_000_000) -> Dict:
    """
    Filter A: deteksi wash trading via divergence Volume vs Open Interest.

    Real volume → posisi baru dibuka → OI bergerak signifikan
    Wash volume → trader yang sama trade ke diri sendiri → OI flat / minor

    Rule:
      Volume > $50M AND |OI change| < 2% (24h) → wash suspicion HIGH

    Bot tidak hard-reject di sini (false positive bisa terjadi pada coin
    yang dalam range / chop), tapi flag ini diberikan ke AI prompt sebagai
    penalty score, dan di main.py jadi tambahan score requirement.

    Returns: {wash_suspicion: bool, wash_severity: "low|medium|high"}
    """
    if volume_24h_usd < min_volume_check:
        # Volume kecil tidak bisa di-judge wash dengan reliable
        return {"wash_suspicion": False, "wash_severity": "low"}

    oi_abs = abs(oi_change_pct)

    # High volume + zero OI move = strong wash signal
    if oi_abs < 1.0:
        return {"wash_suspicion": True, "wash_severity": "high"}
    # Medium signal
    if oi_abs < 2.0:
        return {"wash_suspicion": True, "wash_severity": "medium"}
    # Low signal — volume real kalau OI bergerak ≥2%
    return {"wash_suspicion": False, "wash_severity": "low"}


def _compute_funding_anomaly(history: List[float], current: float,
                              sigma_threshold: float = 3.0) -> Dict:
    """
    Detect funding rate anomaly via z-score from history.
    Funding spike (≥ 3-sigma) = manipulation / forced liquidation cascade risk.

    Returns: {funding_zscore, funding_anomaly}
    """
    if not history or len(history) < 5:
        return {"funding_zscore": 0.0, "funding_anomaly": False}

    n = len(history)
    mean = sum(history) / n
    variance = sum((x - mean) ** 2 for x in history) / n
    stdev = variance ** 0.5

    # Guard against zero / near-zero stdev (all historical values nearly identical).
    # Floor stdev at 0.0001 (= 0.01% funding rate noise) so we still detect spikes
    # when historical variance is artificially low (early data, dead market, etc.).
    stdev_floor = 0.0001
    effective_stdev = max(stdev, stdev_floor)

    zscore = (current - mean) / effective_stdev

    # Additional guard: flag anomaly if absolute deviation from mean is huge,
    # regardless of z-score (e.g. 0.005 funding when mean is 0.0001 = 50× mean).
    abs_deviation = abs(current - mean)
    abs_anomaly = abs_deviation >= 0.003  # 0.3% funding shift = always anomaly

    return {
        "funding_zscore": round(zscore, 2),
        "funding_anomaly": abs(zscore) >= sigma_threshold or abs_anomaly,
    }


def _sync_fetch_oi(symbol: str, exch) -> Optional[Dict]:
    try:
        return exch.fetch_open_interest(symbol)
    except Exception:
        return None


# ── Indicators ────────────────────────────────────────────────────────────

class MarketFeed:
    CANDLE_LIMIT = 300

    def __init__(self) -> None:
        self._best_exchange: Dict[str, object] = {}

    # ── Static indicators ─────────────────────────────────────────

    @staticmethod
    def _normalize(symbol: str) -> str:
        """Any format -> BTCUSDT canonical."""
        s = symbol.strip().upper()
        s = s.replace("/USDT:USDT","USDT").replace("/USDT","USDT").replace("/","").replace(":","")
        if not s.endswith("USDT"):
            s += "USDT"
        return s

    @staticmethod
    def _ema(prices: List[float], period: int) -> float:
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        k = 2.0 / (period + 1)
        val = sum(prices[:period]) / period
        for p in prices[period:]:
            val = p * k + val * (1.0 - k)
        return val

    @staticmethod
    def _rsi(closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(d, 0.0) for d in deltas]
        losses = [max(-d, 0.0) for d in deltas]
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0.0:
            return 100.0
        return round(100.0 - (100.0 / (1.0 + avg_g / avg_l)), 2)

    @staticmethod
    def _vol_condition(volumes: List[float], lookback: int = 20) -> str:
        if len(volumes) < lookback + 1:
            return "normal"
        avg = sum(volumes[-lookback - 1:-1]) / lookback
        if avg == 0:
            return "normal"
        r = volumes[-1] / avg
        return "strong" if r >= 1.5 else ("weak" if r <= 0.6 else "normal")

    @staticmethod
    def _vol_trend(volumes: List[float]) -> str:
        """Volume direction over last 5 bars: rising, falling, flat."""
        if len(volumes) < 6:
            return "flat"
        recent = volumes[-3:]
        older = volumes[-6:-3]
        avg_r = sum(recent) / len(recent)
        avg_o = sum(older) / len(older)
        if avg_o == 0:
            return "flat"
        ratio = avg_r / avg_o
        if ratio > 1.3:
            return "rising"
        if ratio < 0.7:
            return "falling"
        return "flat"

    @staticmethod
    def _volatility(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> str:
        if len(closes) < period + 1:
            return "medium"
        trs = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            for i in range(1, len(closes))
        ]
        pct = (sum(trs[-period:]) / period) / closes[-1] * 100
        return "high" if pct > 3.0 else ("low" if pct < 1.0 else "medium")

    @staticmethod
    def _atr_pct(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Return ATR as percentage of current price."""
        if len(closes) < period + 1:
            return 0.0
        trs = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            for i in range(1, len(closes))
        ]
        atr = sum(trs[-period:]) / period
        return round(atr / closes[-1] * 100, 4)

    @staticmethod
    def _structure(closes: List[float], highs: List[float], lows: List[float], swing: int = 20) -> str:
        if len(closes) < 60:
            return "sideways"
        ema20 = MarketFeed._ema(closes, 20)
        ema50 = MarketFeed._ema(closes, 50)
        cur = closes[-1]
        mom = (cur - closes[-swing]) / closes[-swing]
        rh = max(highs[-swing - 1:-1])
        rl = min(lows[-swing - 1:-1])

        # Breakout detection
        if cur > rh and cur > ema20 > ema50:
            return "breakout"
        # Breakdown detection
        if cur < rl and cur < ema20 < ema50:
            return "breakdown"
        if cur > ema20 > ema50 and mom > 0.01:
            return "uptrend"
        if cur < ema20 < ema50 and mom < -0.01:
            return "downtrend"
        return "sideways"

    @staticmethod
    def _htf_bias(daily_closes: List[float]) -> str:
        """Higher-timeframe bias from Daily candles."""
        if not daily_closes or len(daily_closes) < 50:
            return "neutral"
        ema20 = MarketFeed._ema(daily_closes, 20)
        ema50 = MarketFeed._ema(daily_closes, 50)
        cur = daily_closes[-1]
        if cur > ema20 > ema50:
            return "bullish"
        if cur < ema20 < ema50:
            return "bearish"
        return "neutral"

    @staticmethod
    def _sr(highs: List[float], lows: List[float], lookback: int = 100) -> Tuple[List[float], List[float]]:
        """Support/Resistance with larger lookback and touch-weighted clustering."""
        if len(highs) < lookback:
            lookback = len(highs)
        if lookback < 20:
            return [], []

        def weighted_cluster(vals: List[float], tolerance: float = 0.008) -> List[float]:
            """Cluster values and sort by frequency (most touches first)."""
            clusters: List[Tuple[float, int]] = []  # (level, touch_count)
            for v in vals:
                merged = False
                for i, (level, count) in enumerate(clusters):
                    if abs(v - level) / level < tolerance:
                        # Weighted average of cluster center
                        clusters[i] = (
                            (level * count + v) / (count + 1),
                            count + 1,
                        )
                        merged = True
                        break
                if not merged:
                    clusters.append((v, 1))
            # Sort by touch count descending, return strongest levels
            clusters.sort(key=lambda x: x[1], reverse=True)
            return [round(level, 4) for level, _count in clusters[:5]]

        # Use more data for S/R
        support_vals = lows[-lookback:]
        resist_vals = highs[-lookback:]

        supports = sorted(weighted_cluster(support_vals))[:3]
        resistances = sorted(weighted_cluster(resist_vals))[:3]

        return supports, resistances

    @staticmethod
    def _smc_metrics(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        ema20: float,
        supports: List[float],
        resistances: List[float],
        atr_pct: float,
        lookback: int = 20,
    ) -> Dict:
        """Objective ICT/SMC-style context for entry filters."""
        if len(closes) < lookback + 2 or closes[-1] <= 0:
            return {
                "swept_prev_high": False,
                "swept_prev_low": False,
                "bos_bullish": False,
                "bos_bearish": False,
                "premium_discount": "equilibrium",
                "range_position_pct": 50.0,
                "near_support": False,
                "near_resistance": False,
                "near_ema20": False,
                "ema20_distance_pct": 0.0,
                "long_retest_zone": False,
                "short_retest_zone": False,
            }

        cur = closes[-1]
        prev_high = max(highs[-lookback - 1:-1])
        prev_low = min(lows[-lookback - 1:-1])
        range_high = max(highs[-50:]) if len(highs) >= 50 else max(highs)
        range_low = min(lows[-50:]) if len(lows) >= 50 else min(lows)
        range_span = max(range_high - range_low, cur * 0.001)

        swept_prev_high = highs[-1] > prev_high and cur < prev_high
        swept_prev_low = lows[-1] < prev_low and cur > prev_low
        bos_bullish = cur > prev_high
        bos_bearish = cur < prev_low

        pos_pct = (cur - range_low) / range_span * 100
        if pos_pct >= 60:
            premium_discount = "premium"
        elif pos_pct <= 40:
            premium_discount = "discount"
        else:
            premium_discount = "equilibrium"

        tolerance_pct = max(min(atr_pct * 0.5, 1.5), 0.35)

        def _near_level(levels: List[float]) -> bool:
            for level in levels:
                if level > 0 and abs(cur - level) / cur * 100 <= tolerance_pct:
                    return True
            return False

        near_support = _near_level(supports)
        near_resistance = _near_level(resistances)
        ema20_distance_pct = abs(cur - ema20) / cur * 100 if ema20 > 0 else 0.0
        near_ema20 = ema20_distance_pct <= tolerance_pct

        return {
            "swept_prev_high": swept_prev_high,
            "swept_prev_low": swept_prev_low,
            "bos_bullish": bos_bullish,
            "bos_bearish": bos_bearish,
            "premium_discount": premium_discount,
            "range_position_pct": round(pos_pct, 1),
            "near_support": near_support,
            "near_resistance": near_resistance,
            "near_ema20": near_ema20,
            "ema20_distance_pct": round(ema20_distance_pct, 2),
            "long_retest_zone": near_support or near_ema20,
            "short_retest_zone": near_resistance or near_ema20,
        }

    @staticmethod
    def _oi_condition(oi_data: Optional[Dict]) -> str:
        """Classify open interest change as rising/falling/flat."""
        if oi_data is None:
            return "unknown"
        change = oi_data.get("change_pct", 0.0)
        if change > 5.0:
            return "rising"
        if change < -5.0:
            return "falling"
        return "flat"

    # ── Public async fetch ────────────────────────────────────────

    async def fetch_symbol_data(self, symbol: str) -> Optional[Dict]:
        """
        Async wrapper — runs blocking requests in thread pool.

        SWING mode    : primary=4H, HTF=Daily, mid=1H, entry=15M
        INTRADAY mode : primary=15M, HTF=1H, super-HTF=4H, entry=5M
        """
        from config import Config
        canonical = self._normalize(symbol)
        mode = getattr(Config, "TRADING_MODE", "swing")
        intraday = (mode == "intraday")

        # Primary candles
        primary_interval = "15" if intraday else "240"
        primary_limit    = 200  if intraday else self.CANDLE_LIMIT

        result = await asyncio.to_thread(
            _sync_fetch_ohlcv, canonical, primary_limit, primary_interval
        )
        if result is None:
            logger.warning(f"[MarketFeed] All exchanges failed for {canonical}")
            return None

        ohlcv, exch = result
        self._best_exchange[canonical] = exch

        highs  = [c[2] for c in ohlcv]
        lows   = [c[3] for c in ohlcv]
        closes = [c[4] for c in ohlcv]
        vols   = [c[5] for c in ohlcv]
        n = len(closes)

        ema20  = self._ema(closes, 20)
        ema50  = self._ema(closes, 50)
        ema200 = self._ema(closes[-250:] if n >= 250 else closes, 200)
        supports, resistances = self._sr(highs, lows)

        # ── Concurrent secondary fetches (mode-specific) ──────
        funding_task     = asyncio.to_thread(_sync_fetch_funding, canonical, exch)
        oi_task          = asyncio.to_thread(_sync_fetch_oi, canonical, exch)
        ticker_task      = asyncio.to_thread(_sync_fetch_ticker, canonical)
        funding_hist_task= asyncio.to_thread(_sync_fetch_funding_history, canonical)

        if intraday:
            # HTF=1H, super-HTF=4H, entry=5M
            htf_task      = asyncio.to_thread(_sync_fetch_1h,  canonical)
            super_htf_task= asyncio.to_thread(_sync_fetch_4h,  canonical)
            entry_task    = asyncio.to_thread(_sync_fetch_5m,  canonical)
            (funding, oi_data, ticker, funding_hist,
             htf_ohlcv, super_htf_ohlcv,
             entry_ohlcv) = await asyncio.gather(
                funding_task, oi_task, ticker_task, funding_hist_task,
                htf_task, super_htf_task, entry_task
            )
            mid_ohlcv = None  # not used in intraday
        else:
            # HTF=Daily, mid=1H, entry=15M
            htf_task   = asyncio.to_thread(_sync_fetch_daily, canonical)
            mid_task   = asyncio.to_thread(_sync_fetch_1h,   canonical)
            entry_task = asyncio.to_thread(_sync_fetch_15m,  canonical)
            (funding, oi_data, ticker, funding_hist,
             htf_ohlcv, mid_ohlcv,
             entry_ohlcv) = await asyncio.gather(
                funding_task, oi_task, ticker_task, funding_hist_task,
                htf_task, mid_task, entry_task
            )
            super_htf_ohlcv = None  # not used in swing

        # ── HTF bias ──────────────────────────────────────────
        htf_closes = [c[4] for c in htf_ohlcv] if htf_ohlcv else []
        htf_bias   = self._htf_bias(htf_closes)
        daily_rsi  = self._rsi(htf_closes) if len(htf_closes) >= 15 else None

        # ── h1_* fields: 1H in swing, 4H in intraday ─────────
        ref_ohlcv = super_htf_ohlcv if intraday else mid_ohlcv
        h1_rsi       = None
        h1_structure = "unknown"
        h1_ema_trend = "neutral"
        if ref_ohlcv and len(ref_ohlcv) >= 20:
            rc = [c[4] for c in ref_ohlcv]
            rh = [c[2] for c in ref_ohlcv]
            rl = [c[3] for c in ref_ohlcv]
            h1_rsi       = self._rsi(rc)
            h1_structure = self._structure(rc, rh, rl, swing=10)
            ema8  = self._ema(rc, 8)
            ema21 = self._ema(rc, 21)
            if rc[-1] > ema8 > ema21:
                h1_ema_trend = "bullish"
            elif rc[-1] < ema8 < ema21:
                h1_ema_trend = "bearish"

        # ── m15_* fields: 15M in swing, 5M in intraday ───────
        m15_rsi       = None
        m15_structure = "unknown"
        m15_momentum  = "neutral"
        if entry_ohlcv and len(entry_ohlcv) >= 20:
            ec = [c[4] for c in entry_ohlcv]
            eh = [c[2] for c in entry_ohlcv]
            el = [c[3] for c in entry_ohlcv]
            m15_rsi       = self._rsi(ec)
            m15_structure = self._structure(ec, eh, el, swing=10)
            if len(ec) >= 6:
                avg_new = sum(ec[-3:]) / 3
                avg_old = sum(ec[-6:-3]) / 3
                ratio = (avg_new - avg_old) / avg_old if avg_old > 0 else 0
                if ratio > 0.003:
                    m15_momentum = "bullish"
                elif ratio < -0.003:
                    m15_momentum = "bearish"

        # ── 5 risk-detection metrics ─────────────────────────────────
        atr_pct_val = self._atr_pct(highs, lows, closes)

        # 1. SPREAD detection — spread lebar = noise + fee bleed
        spread_pct      = float(ticker.get("spread_pct", 0.0)) if ticker else 0.0
        volume_24h_usd  = float(ticker.get("volume_24h_usd", 0.0)) if ticker else 0.0

        # 2. WICK detection — stop-hunt manipulation (wick last candle vs ATR)
        opens = [c[1] for c in ohlcv]
        wick_metrics = _compute_wick_metrics(highs, lows, opens, closes, atr_pct_val)

        # 3. FUNDING anomaly — z-score from 8-day history (24×8h periods)
        funding_metrics = _compute_funding_anomaly(
            funding_hist or [], funding,
            sigma_threshold=getattr(__import__('config').Config, 'FUNDING_ANOMALY_SIGMA', 3.0)
        )

        # 4. WASH TRADING (Filter A) — Volume tinggi tapi OI flat
        oi_change_pct = float(oi_data.get("change_pct", 0.0)) if oi_data else 0.0
        wash_metrics = _compute_wash_suspicion(volume_24h_usd, oi_change_pct)
        smc_metrics = self._smc_metrics(
            highs, lows, closes, ema20, supports, resistances, atr_pct_val
        )

        return {
            "symbol":                  canonical,
            "trading_mode":            mode,
            "current_price":           round(closes[-1], 6),
            "ema20":                   round(ema20, 6),
            "ema50":                   round(ema50, 6),
            "ema200":                  round(ema200, 6),
            "rsi":                     self._rsi(closes),
            "volume_condition":        self._vol_condition(vols),
            "volume_trend":            self._vol_trend(vols),
            "funding_rate":            round(funding, 8),
            "open_interest_condition": self._oi_condition(oi_data),
            "volatility_condition":    self._volatility(highs, lows, closes),
            "atr_pct":                 atr_pct_val,
            "market_structure":        self._structure(closes, highs, lows),
            "support_levels":          supports,
            "resistance_levels":       resistances,
            "htf_bias":                htf_bias,
            "daily_rsi":               daily_rsi,
            # Intermediate TF (1H swing / 4H intraday)
            "h1_rsi":                  h1_rsi,
            "h1_structure":            h1_structure,
            "h1_ema_trend":            h1_ema_trend,
            # Entry TF (15M swing / 5M intraday)
            "m15_rsi":                 m15_rsi,
            "m15_structure":           m15_structure,
            "m15_momentum":            m15_momentum,
            # 5 risk-detection signals
            "spread_pct":              spread_pct,
            "volume_24h_usd":          volume_24h_usd,
            "upper_wick_atr":          wick_metrics["upper_wick_atr"],
            "lower_wick_atr":          wick_metrics["lower_wick_atr"],
            "wick_warning":            wick_metrics["wick_warning"],
            "funding_zscore":          funding_metrics["funding_zscore"],
            "funding_anomaly":         funding_metrics["funding_anomaly"],
            "oi_change_pct_24h":       oi_change_pct,
            "wash_suspicion":          wash_metrics["wash_suspicion"],
            "wash_severity":           wash_metrics["wash_severity"],
            "swept_prev_high":         smc_metrics["swept_prev_high"],
            "swept_prev_low":          smc_metrics["swept_prev_low"],
            "bos_bullish":             smc_metrics["bos_bullish"],
            "bos_bearish":             smc_metrics["bos_bearish"],
            "premium_discount":        smc_metrics["premium_discount"],
            "range_position_pct":      smc_metrics["range_position_pct"],
            "near_support":            smc_metrics["near_support"],
            "near_resistance":         smc_metrics["near_resistance"],
            "near_ema20":              smc_metrics["near_ema20"],
            "ema20_distance_pct":      smc_metrics["ema20_distance_pct"],
            "long_retest_zone":        smc_metrics["long_retest_zone"],
            "short_retest_zone":       smc_metrics["short_retest_zone"],
            "_source":                 exch.name,
        }

    async def close(self) -> None:
        pass  # requests.Session is not async — nothing to close


# ── Dynamic symbol list ────────────────────────────────────────────────────

# Symbols to always exclude regardless of volume rank:
#   - Leverage tokens (3L/3S/UP/DOWN/BULL/BEAR)
#   - Stablecoin perps (USDCUSDT, BUSDUSDT, TUSDUSDT, DAIUSDT, FDUSDUSDT)
#   - Pre-delivery/settlement tokens
_EXCLUDE_KEYWORDS = ("3L", "3S", "5L", "5S", "UP", "DOWN", "BULL", "BEAR")
_EXCLUDE_EXACT    = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT", "FDUSDUSDT", "USDPUSDT"}


def _fetch_bybit_tradable_set() -> set:
    """
    Get set of all USDT-perpetual symbols TRADABLE on Bybit.
    Used to cross-check Binance ranking — bot trades on Bybit, so simbol
    yang ranking di Binance tapi tidak ada di Bybit harus di-skip.
    """
    try:
        r = _SESSION.get(
            "https://api.bybit.com/v5/market/instruments-info",
            params={"category": "linear", "limit": 1000},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        items = r.json().get("result", {}).get("list", [])
        # Hanya simbol yang status="Trading" (bukan delivery/pre-launch)
        tradable = {
            item.get("symbol", "")
            for item in items
            if item.get("status") == "Trading"
            and item.get("symbol", "").endswith("USDT")
        }
        logger.info(f"[SymbolFeed] Bybit tradable USDT-perp pool: {len(tradable)} symbols")
        return tradable
    except Exception as exc:
        logger.error(f"[SymbolFeed] Failed to fetch Bybit tradable set: {exc}")
        return set()


def fetch_top_symbols(
    limit: int = 30,
    min_turnover_usdt: float = 30_000_000,
    blacklist: Optional[List[str]] = None,
    data_only: Optional[List[str]] = None,
) -> List[str]:
    """
    Fetch top `limit` USDT-perpetual simbol by 24h volume USD.
    Source: BINANCE primary (volume susah di-wash di sana, ranking lebih real),
    cross-checked dengan Bybit tradable list (karena bot trade di Bybit).

    Filters applied:
      - Symbol must end with "USDT", not leverage/stablecoin token
      - Min 24h Binance turnover >= min_turnover_usdt (default $30M)
      - Symbol MUST exist & be Trading on Bybit (cross-check)
      - Symbol NOT in blacklist (historical loser)
      - Symbol NOT in data_only (data-anchor seperti BTC, no auto-trade)

    Fallback: Bybit-only top-volume kalau Binance API gagal.

    Returns: list of "BTCUSDT", "ETHUSDT", ... (no slash, Bybit format).
    """
    blacklist = set((s or "").upper() for s in (blacklist or []))
    data_only = set((s or "").upper() for s in (data_only or []))

    def _binance_primary(tradable_bybit: set) -> List[str]:
        r = _SESSION.get(
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        items = r.json()
        rows = []
        rejected_tradable = 0
        rejected_blacklist = 0
        rejected_data_only = 0
        rejected_turnover = 0
        for item in items:
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if sym in _EXCLUDE_EXACT:
                continue
            if any(kw in sym for kw in _EXCLUDE_KEYWORDS):
                continue
            if sym in blacklist:
                rejected_blacklist += 1
                continue
            if sym in data_only:
                rejected_data_only += 1
                continue
            try:
                turnover = float(item.get("quoteVolume", 0))
            except (ValueError, TypeError):
                continue
            if turnover < min_turnover_usdt:
                rejected_turnover += 1
                continue
            # Cross-check: simbol harus ada di Bybit
            if tradable_bybit and sym not in tradable_bybit:
                rejected_tradable += 1
                continue
            rows.append((sym, turnover))

        rows.sort(key=lambda x: x[1], reverse=True)
        result = [sym for sym, _ in rows[:limit]]
        logger.info(
            f"[SymbolFeed] Binance ranking: kept={len(result)}/{len(rows)} "
            f"(rejected: tradable={rejected_tradable}, blacklist={rejected_blacklist}, "
            f"data_only={rejected_data_only}, turnover<${min_turnover_usdt/1e6:.0f}M={rejected_turnover})"
        )
        return result

    def _bybit_fallback() -> List[str]:
        r = _SESSION.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        items = r.json().get("result", {}).get("list", [])
        rows = []
        for item in items:
            sym = item.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if sym in _EXCLUDE_EXACT:
                continue
            if any(kw in sym for kw in _EXCLUDE_KEYWORDS):
                continue
            if sym in blacklist:
                continue
            if sym in data_only:
                continue
            try:
                turnover = float(item.get("turnover24h", 0))
            except (ValueError, TypeError):
                continue
            if turnover < min_turnover_usdt:
                continue
            rows.append((sym, turnover))

        rows.sort(key=lambda x: x[1], reverse=True)
        return [sym for sym, _ in rows[:limit]]

    # Cross-check pool dari Bybit (simbol yg tradable)
    bybit_pool = _fetch_bybit_tradable_set()

    try:
        syms = _binance_primary(bybit_pool)
        if syms:
            logger.info(
                f"[SymbolFeed] Binance top-{limit} (Bybit-tradable): {syms[:10]}"
                f"{'...' if len(syms) > 10 else ''}"
            )
            return syms
        logger.warning("[SymbolFeed] Binance returned 0 symbols, falling back to Bybit")
    except Exception as exc:
        logger.warning(f"[SymbolFeed] Binance failed ({exc}), falling back to Bybit")

    try:
        syms = _bybit_fallback()
        logger.info(f"[SymbolFeed] Bybit fallback top-{limit}: {len(syms)} symbols")
        return syms
    except Exception as exc:
        logger.error(f"[SymbolFeed] Both exchanges failed: {exc}")
        return []
