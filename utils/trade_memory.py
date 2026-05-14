"""
utils/trade_memory.py — Trade Memory & AI Learning System.

Stores trade outcomes, market conditions at entry/exit, and
generates learning context for AI signal analysis.

Features:
  - Record every trade with full context (market conditions, AI signal, outcome)
  - Compute per-symbol, per-setup, per-condition performance stats
  - Generate compact learning summary for AI system prompt injection
  - Adaptive rules: win streaks → aggressive, losing streaks → conservative
  - Weekly/daily performance tracking

Data stored in: trade_memory.json (auto-created in project root)
"""

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from utils.logger import logger

_MEMORY_PATH = Path("trade_memory.json")


def _now() -> datetime:
    return datetime.now()


def _now_str() -> str:
    return _now().strftime("%Y-%m-%d %H:%M:%S")


def _load_memory() -> Dict:
    if _MEMORY_PATH.exists():
        try:
            return json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "trades": [],
        "symbol_stats": {},
        "setup_stats": {},
        "daily_pnl": {},
        "streak": {"current": 0, "type": "none"},  # +N = win streak, -N = loss streak
        "version": 2,
    }


def _save_memory(mem: Dict) -> None:
    try:
        _MEMORY_PATH.write_text(json.dumps(mem, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[Memory] Could not save: {exc}")


class TradeMemory:
    """
    AI Learning Memory — records trades, computes statistics,
    generates learning context for AI prompt injection.
    """

    def __init__(self) -> None:
        self._mem = _load_memory()

    # ── Record trade entry context ────────────────────────────────

    def record_entry_context(
        self,
        symbol: str,
        action: str,
        signal: Dict,
        market_snapshot: Dict,
    ) -> None:
        """Store market conditions at entry time for later analysis."""
        entry_ctx = {
            "symbol": symbol,
            "action": action,
            "entry_time": _now_str(),
            "entry_price": float(signal.get("entry_price", 0)),
            "stop_loss": float(signal.get("stop_loss", 0)),
            "take_profit": signal.get("take_profit", []),
            "leverage": int(signal.get("leverage", 1)),
            "ai_score": float(signal.get("score", 0)),
            "ai_confidence": float(signal.get("confidence", 0)),
            "risk_level": signal.get("risk_level", ""),
            "reason": signal.get("reason", ""),
            # Market conditions at entry
            "market": {
                "rsi": market_snapshot.get("rsi", 0),
                "structure": market_snapshot.get("market_structure", ""),
                "htf_bias": market_snapshot.get("htf_bias", ""),
                "volume": market_snapshot.get("volume_condition", ""),
                "volume_trend": market_snapshot.get("volume_trend", ""),
                "atr_pct": market_snapshot.get("atr_pct", 0),
                "funding": market_snapshot.get("funding_rate", 0),
                "oi_condition": market_snapshot.get("open_interest_condition", ""),
            },
        }
        # Store temporarily until trade closes
        self._mem.setdefault("pending_entries", {})[symbol] = entry_ctx
        _save_memory(self._mem)

    # ── Record trade close result ─────────────────────────────────

    def record_trade_result(
        self,
        symbol: str,
        exit_price: float,
        pnl_usdt: float,
        pnl_pct: float,
        reason: str,
        duration_mins: int = 0,
    ) -> None:
        """Record completed trade with full context for learning."""
        pending = self._mem.get("pending_entries", {}).pop(symbol, {})

        trade = {
            "symbol": symbol,
            "action": pending.get("action", "?"),
            "entry_price": pending.get("entry_price", 0),
            "exit_price": exit_price,
            "pnl_usdt": round(pnl_usdt, 4),
            "pnl_pct": round(pnl_pct, 2),
            "reason": reason,  # stop_loss, take_profit, trailing_stop, etc
            "ai_score": pending.get("ai_score", 0),
            "ai_confidence": pending.get("ai_confidence", 0),
            "risk_level": pending.get("risk_level", ""),
            "leverage": pending.get("leverage", 1),
            "market": pending.get("market", {}),
            "entry_time": pending.get("entry_time", ""),
            "exit_time": _now_str(),
            "duration_mins": duration_mins,
        }

        self._mem["trades"].append(trade)
        self._update_symbol_stats(trade)
        self._update_setup_stats(trade)
        self._update_daily_pnl(trade)
        self._update_streak(trade)

        _save_memory(self._mem)
        logger.info(
            f"[Memory] Trade recorded: {symbol} {trade['action']} "
            f"PnL={pnl_usdt:+.4f} score={trade['ai_score']:.1f} "
            f"streak={self._mem['streak']['current']:+d}"
        )

    # ── Stats updaters ────────────────────────────────────────────

    def _update_symbol_stats(self, trade: Dict) -> None:
        sym = trade["symbol"]
        stats = self._mem["symbol_stats"].setdefault(sym, {
            "trades": 0, "wins": 0, "losses": 0,
            "total_pnl": 0, "avg_score": 0, "scores_sum": 0,
            "best_pnl": 0, "worst_pnl": 0,
            "long_wins": 0, "long_total": 0,
            "short_wins": 0, "short_total": 0,
        })
        stats["trades"] += 1
        stats["total_pnl"] = round(stats["total_pnl"] + trade["pnl_usdt"], 4)
        stats["scores_sum"] += trade["ai_score"]
        stats["avg_score"] = round(stats["scores_sum"] / stats["trades"], 2)

        is_win = trade["pnl_usdt"] >= 0
        if is_win:
            stats["wins"] += 1
            if trade["pnl_usdt"] > stats["best_pnl"]:
                stats["best_pnl"] = trade["pnl_usdt"]
        else:
            stats["losses"] += 1
            if trade["pnl_usdt"] < stats["worst_pnl"]:
                stats["worst_pnl"] = trade["pnl_usdt"]

        if trade["action"] == "LONG":
            stats["long_total"] += 1
            if is_win:
                stats["long_wins"] += 1
        elif trade["action"] == "SHORT":
            stats["short_total"] += 1
            if is_win:
                stats["short_wins"] += 1

    def _update_setup_stats(self, trade: Dict) -> None:
        """Track performance by setup type (structure + htf_bias + action)."""
        mkt = trade.get("market", {})
        key = f"{mkt.get('structure', '?')}_{mkt.get('htf_bias', '?')}_{trade['action']}"

        stats = self._mem["setup_stats"].setdefault(key, {
            "trades": 0, "wins": 0, "total_pnl": 0, "avg_pnl": 0,
        })
        stats["trades"] += 1
        is_win = trade["pnl_usdt"] >= 0
        if is_win:
            stats["wins"] += 1
        stats["total_pnl"] = round(stats["total_pnl"] + trade["pnl_usdt"], 4)
        stats["avg_pnl"] = round(stats["total_pnl"] / stats["trades"], 4)

    def _update_daily_pnl(self, trade: Dict) -> None:
        today = _now().strftime("%Y-%m-%d")
        day = self._mem["daily_pnl"].setdefault(today, {"pnl": 0, "trades": 0, "wins": 0})
        day["pnl"] = round(day["pnl"] + trade["pnl_usdt"], 4)
        day["trades"] += 1
        if trade["pnl_usdt"] >= 0:
            day["wins"] += 1

    def _update_streak(self, trade: Dict) -> None:
        streak = self._mem["streak"]
        if trade["pnl_usdt"] >= 0:
            if streak["type"] == "win":
                streak["current"] += 1
            else:
                streak["current"] = 1
                streak["type"] = "win"
        else:
            if streak["type"] == "loss":
                streak["current"] -= 1
            else:
                streak["current"] = -1
                streak["type"] = "loss"

    # ── Adaptive Rules ────────────────────────────────────────────

    def get_adaptive_params(self, account_balance: float) -> Dict:
        """
        Return adaptive trading parameters based on memory:
        - risk_multiplier: scale position size (0.5x to 1.3x)
        - min_score: minimum AI score to accept trades (5.0 to 7.0)
        - max_leverage_mult: leverage multiplier (0.6x to 1.0x)
        - mode: "aggressive" / "normal" / "conservative" / "defensive"
        """
        trades = self._mem["trades"]
        streak = self._mem["streak"]["current"]

        # Default params
        params = {
            "risk_multiplier": 1.0,
            "min_score": 5.5,
            "max_leverage_mult": 1.0,
            "mode": "normal",
            "reason": "",
        }

        if len(trades) < 3:
            params["reason"] = "insufficient_data"
            return params

        # Recent performance (last 10 trades)
        recent = trades[-10:]
        recent_pnl = sum(t["pnl_usdt"] for t in recent)
        recent_wins = sum(1 for t in recent if t["pnl_usdt"] >= 0)
        recent_wr = recent_wins / len(recent) * 100

        # Weekly PnL
        week_ago = (_now() - timedelta(days=7)).strftime("%Y-%m-%d")
        weekly_pnl = sum(
            d["pnl"] for date, d in self._mem["daily_pnl"].items()
            if date >= week_ago
        )

        # === Adaptive logic ===

        # Defensive: 5+ consecutive losses OR weekly drawdown > 25%
        # (Small accounts ~$150 naturally have 15-20% weekly swings from normal
        # SL hits. 25% = genuine crisis, not normal variance.)
        if streak <= -5 or (weekly_pnl < 0 and abs(weekly_pnl) > account_balance * 0.25):
            params["risk_multiplier"] = 0.5
            params["min_score"] = 7.0
            params["max_leverage_mult"] = 0.6
            params["mode"] = "defensive"
            params["reason"] = f"streak={streak} weekly_pnl={weekly_pnl:+.2f}"
            return params

        # Conservative: 3 consecutive losses OR recent WR < 35%
        if streak <= -3 or recent_wr < 35:
            params["risk_multiplier"] = 0.7
            params["min_score"] = 6.5
            params["max_leverage_mult"] = 0.8
            params["mode"] = "conservative"
            params["reason"] = f"streak={streak} wr={recent_wr:.0f}%"
            return params

        # Aggressive: 3+ consecutive wins AND recent WR > 65% AND weekly PnL > 0
        if streak >= 3 and recent_wr > 65 and weekly_pnl > 0:
            params["risk_multiplier"] = 1.3
            params["min_score"] = 5.5
            params["max_leverage_mult"] = 1.0
            params["mode"] = "aggressive"
            params["reason"] = f"streak=+{streak} wr={recent_wr:.0f}% weekly=+{weekly_pnl:.2f}"
            return params

        # Normal
        params["reason"] = f"streak={streak:+d} wr={recent_wr:.0f}%"
        return params

    # ── AI Learning Context ───────────────────────────────────────

    def generate_learning_context(self, symbols: List[str] = None) -> str:
        """
        Generate compact learning summary for AI prompt injection.
        Adds ~200-400 tokens to the system prompt.
        """
        trades = self._mem["trades"]
        if len(trades) < 2:
            return ""

        lines = []

        # Overall stats
        total = len(trades)
        wins = sum(1 for t in trades if t["pnl_usdt"] >= 0)
        total_pnl = sum(t["pnl_usdt"] for t in trades)
        wr = wins / total * 100 if total > 0 else 0
        lines.append(f"HISTORY: {total} trades, WR={wr:.0f}%, PnL={total_pnl:+.1f}U")

        # Streak + mode
        adaptive = self.get_adaptive_params(10000)  # balance doesn't matter for mode
        lines.append(f"MODE: {adaptive['mode']} (streak={self._mem['streak']['current']:+d})")

        # Top performing symbols (3+ trades, sorted by WR)
        sym_stats = self._mem["symbol_stats"]
        good_syms = []
        bad_syms = []
        for sym, s in sym_stats.items():
            if s["trades"] < 2:
                continue
            wr = s["wins"] / s["trades"] * 100 if s["trades"] > 0 else 0
            if wr >= 60 and s["total_pnl"] > 0:
                good_syms.append((sym, wr, s["total_pnl"], s["trades"]))
            elif wr < 40 and s["total_pnl"] < 0:
                bad_syms.append((sym, wr, s["total_pnl"], s["trades"]))

        if good_syms:
            good_syms.sort(key=lambda x: -x[1])
            top = good_syms[:3]
            lines.append("STRONG: " + ", ".join(
                f"{s}({wr:.0f}%WR,{pnl:+.1f}U,{n}t)" for s, wr, pnl, n in top
            ))

        if bad_syms:
            bad_syms.sort(key=lambda x: x[1])
            bottom = bad_syms[:3]
            lines.append("WEAK: " + ", ".join(
                f"{s}({wr:.0f}%WR,{pnl:+.1f}U,{n}t)" for s, wr, pnl, n in bottom
            ))

        # Setup performance (which setups work)
        setup_stats = self._mem["setup_stats"]
        good_setups = []
        bad_setups = []
        for setup, s in setup_stats.items():
            if s["trades"] < 3:
                continue
            wr = s["wins"] / s["trades"] * 100 if s["trades"] > 0 else 0
            if wr >= 65:
                good_setups.append((setup, wr, s["trades"]))
            elif wr < 35:
                bad_setups.append((setup, wr, s["trades"]))

        if good_setups:
            good_setups.sort(key=lambda x: -x[1])
            lines.append("GOOD_SETUP: " + ", ".join(
                f"{s}({wr:.0f}%,{n}t)" for s, wr, n in good_setups[:3]
            ))

        if bad_setups:
            bad_setups.sort(key=lambda x: x[1])
            lines.append("BAD_SETUP: " + ", ".join(
                f"{s}({wr:.0f}%,{n}t)" for s, wr, n in bad_setups[:3]
            ))

        # Recent trades (last 5)
        recent = trades[-5:]
        recent_str = []
        for t in recent:
            outcome = "W" if t["pnl_usdt"] >= 0 else "L"
            recent_str.append(
                f"{t['symbol']}:{t['action'][0]}:{outcome}:{t['pnl_usdt']:+.1f}U"
            )
        if recent_str:
            lines.append("RECENT: " + ", ".join(recent_str))

        # Symbol-specific hints for current scan
        if symbols:
            hints = []
            for sym in symbols:
                s = sym_stats.get(sym)
                if not s or s["trades"] < 2:
                    continue
                wr = s["wins"] / s["trades"] * 100 if s["trades"] > 0 else 0
                long_wr = (s["long_wins"] / s["long_total"] * 100
                           if s["long_total"] > 0 else 0)
                short_wr = (s["short_wins"] / s["short_total"] * 100
                            if s["short_total"] > 0 else 0)
                if abs(long_wr - short_wr) > 20 and s["trades"] >= 3:
                    better = "L" if long_wr > short_wr else "S"
                    hints.append(f"{sym}:prefer_{better}({long_wr:.0f}%L/{short_wr:.0f}%S)")
            if hints:
                lines.append("HINTS: " + ", ".join(hints[:5]))

        if not lines:
            return ""

        return "\n\nLEARNING_DATA:\n" + "\n".join(lines)

    # ── Getters ───────────────────────────────────────────────────

    def get_streak(self) -> int:
        return self._mem["streak"]["current"]

    def get_total_trades(self) -> int:
        return len(self._mem["trades"])

    def get_symbol_stats(self, symbol: str) -> Optional[Dict]:
        return self._mem["symbol_stats"].get(symbol)

    def get_weekly_pnl(self) -> float:
        week_ago = (_now() - timedelta(days=7)).strftime("%Y-%m-%d")
        return sum(
            d["pnl"] for date, d in self._mem["daily_pnl"].items()
            if date >= week_ago
        )

    def get_recent_trades(self, n: int = 10) -> List[Dict]:
        return self._mem["trades"][-n:]
