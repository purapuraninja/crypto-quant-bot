"""
utils/pnl_tracker.py — PnL tracker for both DRY RUN and LIVE mode.

Dry Run : simulates position lifecycle from signal data, persists to JSON.
Live    : queries realized PnL from Bybit API + tracks open positions.

Data stored in: pnl_store.json (auto-created in project root)
"""

import json
import time
import math
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from utils.logger import logger

_STORE_PATH = Path("pnl_store.json")


def _now_str() -> str:
    """UTC timestamp for trade records — keeps reset time consistent
    regardless of VPS timezone (matches Bybit funding cycle 00:00 UTC)."""
    from datetime import timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _load_store() -> Dict:
    if _STORE_PATH.exists():
        try:
            return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "open_positions": {},
        "closed_trades": [],
        "stats": {
            "total_realized": 0.0,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "best_symbol": "",
            "worst_symbol": "",
        }
    }


def _save_store(store: Dict) -> None:
    try:
        _STORE_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[PnL] Could not save store: {exc}")


class PnLTracker:
    """
    Tracks PnL for dry-run simulated trades and live Bybit trades.
    All monetary values in USDT.
    """

    def __init__(self, account_balance: float) -> None:
        self.account_balance = account_balance
        self._store = _load_store()
        # Cooldown set: symbols recently closed by tracker (SL/TP/trail/time-exit).
        # Sync should NOT re-add these for ~30s to give Bybit time to also close.
        # Without this cooldown, sync detects orphan position on Bybit and re-adds
        # right after we logically closed it.
        # {symbol: epoch_seconds_when_closed}
        self._recent_closes: Dict[str, float] = {}

    # ── Position lifecycle ─────────────────────────────────────────

    def record_entry(self, signal: Dict, exec_result: Dict) -> None:
        """Record a new position entry (dry run or live)."""
        sym = signal.get("symbol", "?")
        action = signal.get("action", "HOLD")
        entry = float(signal.get("entry_price", 0))

        # CRITICAL: Use the SL/entry that EXECUTOR actually placed on Bybit,
        # NOT AI's signal SL. When SL_MODE=roi, executor REPLACES AI's tight
        # SL with wide -100% ROI SL. If tracker stores AI's tight value,
        # local check_sl_tp fires PREMATURELY (defeating the wide-buffer
        # purpose of SL_MODE=roi and preventing DCA from triggering).
        sl = float(exec_result.get("stop_loss", 0)) or float(signal.get("stop_loss", 0))
        # Same for entry — actual fill price > AI's planned price
        actual_entry = float(exec_result.get("entry_price", 0))
        if actual_entry > 0:
            entry = actual_entry

        tps = [float(v) for v in signal.get("take_profit", []) if v]
        lev = int(signal.get("leverage", 1))
        size_pct = float(signal.get("position_size_percent", 1.0))
        notional = self.account_balance * (size_pct / 100.0) * lev

        # Parse trailing stop config from signal
        trail_cfg = signal.get("trailing_stop", {})
        if not isinstance(trail_cfg, dict):
            trail_cfg = {}

        pos = {
            "symbol": sym,
            "action": action,
            "entry_price": entry,
            "stop_loss": sl,
            "original_sl": sl,
            "take_profit": tps,
            "leverage": lev,
            "notional": round(notional, 2),
            "remaining_pct": 1.0,   # 100% of position remaining
            "size_pct": size_pct,
            "open_time": _now_str(),
            "status": exec_result.get("status", "unknown"),
            "peak_price": entry,
            "trailing_active": False,
            "trailing_activation_price": float(trail_cfg.get("activation_price", 0)),
            "trailing_trail_pct": float(trail_cfg.get("trail_pct", 3.0)),
            "tp_hits": [],         # track which TPs have been hit
            "dca_count": 0,        # number of DCA additions
            "ai_tier": signal.get("_ai_tier", "?"),  # which AI generated this
            "ai_score": float(signal.get("score", 0)),
            "ai_confidence": float(signal.get("confidence", 0)),
        }
        self._store["open_positions"][sym] = pos
        _save_store(self._store)
        logger.info(f"[PnL] Entry recorded: {action} {sym} @ {entry:,.4f} notional={notional:,.2f}")

    def record_close(self, symbol: str, exit_price: float, reason: str = "manual") -> Optional[Dict]:
        """Close a position and calculate realized PnL."""
        pos = self._store["open_positions"].pop(symbol, None)
        if not pos:
            return None

        entry = pos["entry_price"]
        notional = pos["notional"]
        action = pos["action"]

        if entry <= 0:
            return None

        if action == "LONG":
            pnl = (exit_price - entry) / entry * notional
        else:
            pnl = (entry - exit_price) / entry * notional

        pnl = round(pnl, 4)
        pnl_pct = round(pnl / (notional / pos["leverage"]) * 100, 2)

        trade = {
            "symbol": symbol,
            "action": action,
            "entry_price": entry,
            "exit_price": exit_price,
            "pnl_usdt": pnl,
            "pnl_pct": pnl_pct,
            "notional": notional,
            "leverage": pos["leverage"],
            "reason": reason,
            "open_time": pos["open_time"],
            "close_time": _now_str(),
            # Carry over AI metadata for performance analysis
            "ai_tier": pos.get("ai_tier", "?"),
            "ai_score": pos.get("ai_score", 0),
            "ai_confidence": pos.get("ai_confidence", 0),
        }
        self._store["closed_trades"].append(trade)

        # Update stats
        s = self._store["stats"]
        s["total_realized"] = round(s["total_realized"] + pnl, 4)
        s["total_trades"] += 1
        if pnl >= 0:
            s["wins"] += 1
            if pnl > s["best_trade"]:
                s["best_trade"] = pnl
                s["best_symbol"] = symbol
        else:
            s["losses"] += 1
            if pnl < s["worst_trade"]:
                s["worst_trade"] = pnl
                s["worst_symbol"] = symbol

        _save_store(self._store)
        # Mark this symbol as "recently closed" so sync doesn't immediately
        # re-add it (Bybit may take a few seconds to also reflect the close)
        self._recent_closes[symbol] = time.time()
        logger.info(f"[PnL] Closed {symbol}: PnL={pnl:+.4f} USDT ({pnl_pct:+.2f}%) reason={reason}")
        return trade

    def partial_close_record(
        self,
        symbol: str,
        exit_price: float,
        pct_to_close: float,
        reason: str = "tp_partial",
    ) -> Optional[Dict]:
        """
        Record a PARTIAL close — reduces remaining notional, books partial realized PnL,
        keeps the position open with the residual size.

        pct_to_close is % of REMAINING size (e.g. 30 = close 30% of what's left).
        After all partials reach 100% remaining_pct → full close.
        """
        pos = self._store["open_positions"].get(symbol)
        if not pos:
            return None
        if exit_price <= 0 or pct_to_close <= 0:
            return None

        entry    = pos["entry_price"]
        notional = pos["notional"]
        action   = pos["action"]
        leverage = pos["leverage"]
        remaining_pct = pos.get("remaining_pct", 1.0)

        if entry <= 0 or notional <= 0:
            return None

        portion = max(0.0, min(pct_to_close / 100.0, 1.0))
        closed_notional = notional * portion

        if action == "LONG":
            pnl = (exit_price - entry) / entry * closed_notional
        else:
            pnl = (entry - exit_price) / entry * closed_notional

        pnl = round(pnl, 4)
        margin_portion = closed_notional / max(leverage, 1)
        pnl_pct = round(pnl / max(margin_portion, 0.01) * 100, 2)

        # Reduce position size
        new_notional = round(notional - closed_notional, 6)
        new_remaining = round(remaining_pct * (1 - portion), 6)
        pos["notional"] = new_notional
        pos["remaining_pct"] = max(0.0, new_remaining)
        _save_store(self._store)

        trade = {
            "symbol": symbol,
            "action": action,
            "entry_price": entry,
            "exit_price": exit_price,
            "pnl_usdt": pnl,
            "pnl_pct": pnl_pct,
            "notional": round(closed_notional, 4),
            "leverage": leverage,
            "reason": reason,
            "open_time": pos["open_time"],
            "close_time": _now_str(),
            "partial": True,
            "remaining_pct_after": pos["remaining_pct"],
        }
        self._store["closed_trades"].append(trade)

        # Update stats — partial PnL counts toward realized total but NOT
        # as a separate trade (the position is still open).
        s = self._store["stats"]
        s["total_realized"] = round(s["total_realized"] + pnl, 4)
        if pnl > 0 and pnl > s["best_trade"]:
            s["best_trade"] = pnl
            s["best_symbol"] = symbol
        elif pnl < 0 and pnl < s["worst_trade"]:
            s["worst_trade"] = pnl
            s["worst_symbol"] = symbol

        # If nothing left → fully close the position (remove from open)
        if pos["remaining_pct"] <= 0.001 or pos["notional"] <= 0.01:
            self._store["open_positions"].pop(symbol, None)
            s["total_trades"] += 1
            if pnl >= 0:
                s["wins"] += 1
            else:
                s["losses"] += 1
            logger.info(f"[PnL] {symbol} fully closed via partials")

        _save_store(self._store)
        logger.info(
            f"[PnL] Partial close {symbol}: {pct_to_close:.0f}% @ {exit_price:.4f} "
            f"PnL={pnl:+.4f} ({pnl_pct:+.2f}%) remaining={pos['remaining_pct']*100:.0f}% "
            f"reason={reason}"
        )
        return trade

    @staticmethod
    def _calc_trail_pct(atr_pct: float, leverage: int) -> float:
        """
        Dynamic trailing stop % based on ATR and leverage.
        Rule: 1.5× ATR, min 0.8%, max 8%.
        Capped tighter for high leverage.
        """
        trail = atr_pct * 1.5
        trail = max(trail, 0.8)   # floor — avoid noise shakeout
        trail = min(trail, 8.0)   # ceiling — don't give back too much
        if leverage >= 8:
            trail = min(trail, 2.0)
        elif leverage >= 5:
            trail = min(trail, 4.0)
        return round(trail, 2)

    def check_sl_tp(self, market_data: List[Dict]) -> List[Dict]:
        """
        BE + Trail strategy:
          TP1 hit → SL moves to entry (breakeven), trailing activates at 1.5×ATR
          TP2 hit → SL advances to TP1 level, trail tightens
          TP3 hit → full close (100% position)
          SL  hit → full close

        Milestone events (be_activated, sl_advanced) are emitted for Telegram
        notification but do NOT close the position or affect stats.
        """
        events = []
        mkt_map = {d["symbol"]: d for d in market_data}

        # Lazy import to avoid circular dependency at module load
        try:
            from config import Config as _Cfg
            _trading_mode = getattr(_Cfg, "TRADING_MODE", "swing")
            _stagnant_h   = getattr(_Cfg, "STAGNANT_HOLD_HOURS", 6.0)
            _stagnant_roi = getattr(_Cfg, "STAGNANT_ROI_PCT", 5.0)
            _max_hold_intra = getattr(_Cfg, "MAX_HOLD_HOURS_INTRADAY", 12.0)
            _max_hold_swing = getattr(_Cfg, "MAX_HOLD_HOURS_SWING", 72.0)
            _partial_enabled = getattr(_Cfg, "PARTIAL_CLOSE_ENABLED", True)
            _tp1_close_pct = getattr(_Cfg, "TP1_CLOSE_PCT", 30.0)
            _tp2_close_pct = getattr(_Cfg, "TP2_CLOSE_PCT", 30.0)
            _partial_min_move = getattr(_Cfg, "PARTIAL_CLOSE_MIN_MOVE_PCT", 0.20)
            _be_lock_pct = getattr(_Cfg, "BE_PROFIT_LOCK_PCT", 0.30)
        except Exception:
            _trading_mode = "swing"
            _stagnant_h, _stagnant_roi = 6.0, 5.0
            _max_hold_intra, _max_hold_swing = 12.0, 72.0
            _partial_enabled = True
            _tp1_close_pct, _tp2_close_pct = 30.0, 30.0
            _partial_min_move = 0.20
            _be_lock_pct = 0.30

        max_hold_h = _max_hold_intra if _trading_mode == "intraday" else _max_hold_swing

        for sym, pos in list(self._store["open_positions"].items()):
            mkt   = mkt_map.get(sym, {})
            price = mkt.get("current_price")
            if price is None:
                continue

            atr_pct = float(mkt.get("atr_pct", 2.0))
            action  = pos["action"]
            sl      = pos["stop_loss"]
            tps     = pos.get("take_profit", [])
            tp_hits = pos.get("tp_hits", [])

            # ── P1: Time-based auto-close ─────────────────────
            # Compute hours held & current ROI. Emit time_exit event so main.py
            # can route to executor.partial_close(100%) for live exchange close.
            entry_for_age = pos["entry_price"]
            leverage_for_age = float(pos.get("leverage", 1))
            hours_held = 0.0
            try:
                # IMPORTANT: open_time stored in UTC by _now_str(), so compare in UTC
                # to avoid TZ offset bug (was treating local-now - UTC-stored = +7h)
                from datetime import timezone
                open_dt = datetime.strptime(pos.get("open_time", ""), "%Y-%m-%d %H:%M:%S")
                open_dt = open_dt.replace(tzinfo=timezone.utc)
                hours_held = (datetime.now(timezone.utc) - open_dt).total_seconds() / 3600.0
            except Exception:
                hours_held = 0.0

            if entry_for_age > 0 and hours_held > 0:
                if action == "LONG":
                    price_pct = (price - entry_for_age) / entry_for_age * 100.0
                else:
                    price_pct = (entry_for_age - price) / entry_for_age * 100.0
                roi_pct = price_pct * leverage_for_age

                # Time-exit detection: identify LOSING positions that hit time threshold.
                # Bot does NOT auto-close — emits event for main.py to ask user
                # via Telegram (like DCA prompt). User decides y/n.
                #
                # Rules:
                # - Profit positions (ROI >= 0): NEVER prompt, never close.
                #   Let trail/TP3 handle exit when reverse.
                # - Losing positions held >= 12h (intraday): prompt user
                # - Losing positions held >= 6h + |ROI|<5% (stagnant): prompt user
                # - Cooldown 6h between re-prompts after user declines
                _decline_at = pos.get("time_exit_declined_at", 0)
                _now_ts = time.time()
                _cooldown_h = 6.0
                _can_prompt = (_now_ts - _decline_at) >= (_cooldown_h * 3600)

                time_exit_reason = None
                if _can_prompt and roi_pct < 0:   # ONLY when losing
                    if hours_held >= max_hold_h:
                        time_exit_reason = "time_exit_max"
                    elif (
                        hours_held >= _stagnant_h
                        and abs(roi_pct) < _stagnant_roi
                    ):
                        time_exit_reason = "time_exit_stagnant"

                if time_exit_reason:
                    logger.info(
                        f"[PnL] {sym} TIME-THRESHOLD {time_exit_reason}: "
                        f"held={hours_held:.1f}h ROI={roi_pct:+.1f}% "
                        f"— asking user via Telegram (no auto-close)"
                    )
                    # Emit USER-DECISION event. Bot does NOT auto-close.
                    # main.py asks via Telegram y/n (like DCA prompt).
                    # Reply Y → close, N → set cooldown 6h, timeout → re-ask next scan.
                    events.append({
                        "symbol": sym,
                        "action": action,
                        "entry_price": entry_for_age,
                        "exit_price": price,
                        "leverage": int(leverage_for_age),
                        "reason": time_exit_reason,
                        "requires_user_decision": True,   # not auto-close anymore
                        "hours_held": round(hours_held, 1),
                        "roi_at_exit": round(roi_pct, 2),
                    })
                    # Don't continue — let SL/TP/Trail still check this scan

            # ── Update peak price ─────────────────────────────
            peak = pos.get("peak_price", pos["entry_price"])
            if action == "LONG" and price > peak:
                pos["peak_price"] = price
                peak = price
            elif action == "SHORT" and price < peak:
                pos["peak_price"] = price
                peak = price

            # ── Update trailing SL ────────────────────────────
            if pos.get("trailing_active", False):
                trail_f = pos.get("trailing_trail_pct", 3.0) / 100.0
                if action == "LONG":
                    t_sl = peak * (1 - trail_f)
                    if t_sl > sl:
                        pos["stop_loss"] = round(t_sl, 6)
                        sl = pos["stop_loss"]
                else:
                    t_sl = peak * (1 + trail_f)
                    if t_sl < sl:
                        pos["stop_loss"] = round(t_sl, 6)
                        sl = pos["stop_loss"]

            # ── Check SL hit → emit close event (main.py will close on Bybit) ──
            # IMPORTANT: do NOT call record_close here in LIVE mode. The trail
            # SL is local-only — Bybit doesn't know about it. We must send a
            # market close order to Bybit FIRST, then update tracker. main.py
            # routes requires_close=True events through executor.partial_close.
            if sl > 0:
                hit_sl = (
                    (action == "LONG"  and price <= sl) or
                    (action == "SHORT" and price >= sl)
                )
                if hit_sl:
                    reason = "trailing_stop" if pos.get("trailing_active") else "stop_loss"
                    logger.info(
                        f"[PnL] {sym} {reason.upper()} hit @ {sl:.6f} "
                        f"(price={price:.6f}) — requesting Bybit close"
                    )
                    events.append({
                        "symbol": sym,
                        "action": action,
                        "entry_price": pos["entry_price"],
                        "exit_price": sl,
                        "leverage": pos["leverage"],
                        "reason": reason,
                        "requires_close": True,
                        "close_pct": 100.0,
                    })
                    continue

            entry = pos["entry_price"]

            # ── TP1 → Breakeven + activate trailing ──────────
            if len(tps) > 0 and 0 not in tp_hits:
                tp1 = tps[0]
                # Guard: TP must be on correct side of entry
                tp1_ok = (action == "LONG" and tp1 > entry) or (action == "SHORT" and tp1 < entry)
                if not tp1_ok:
                    logger.warning(f"[PnL] {sym} TP1={tp1} invalid for {action} (entry={entry}) — skipped")
                    tp_hits.append(0)   # mark as consumed so we don't spam
                    pos["tp_hits"] = tp_hits
                hit = tp1_ok and ((action == "LONG" and price >= tp1) or (action == "SHORT" and price <= tp1))
                if hit:
                    tp_hits.append(0)
                    pos["tp_hits"] = tp_hits
                    # SL → entry + profit lock buffer (covers fees + lock gain)
                    # Without buffer, "BE" exit would actually be -fees = net loss.
                    _lock_f = _be_lock_pct / 100.0
                    if action == "LONG":
                        be_sl = pos["entry_price"] * (1.0 + _lock_f)
                    else:
                        be_sl = pos["entry_price"] * (1.0 - _lock_f)
                    pos["stop_loss"] = round(be_sl, 8)
                    sl = pos["stop_loss"]
                    # After TP1: halve trail_pct so trailing captures profit faster
                    # (prevents "3 breakeven" issue — trail was too wide after TP1)
                    trail_pct = self._calc_trail_pct(atr_pct, pos["leverage"]) * 0.5
                    trail_pct = max(round(trail_pct, 2), 0.5)  # floor 0.5%
                    pos["trailing_trail_pct"] = trail_pct
                    pos["trailing_active"]    = True
                    pos["peak_price"]         = price       # reset peak
                    logger.info(
                        f"[PnL] {sym} TP1 @ {tp1:.4f} → "
                        f"SL=BE+{_be_lock_pct:.2f}%({be_sl:.4f}), trail={trail_pct:.1f}% "
                        f"(ATR={atr_pct:.2f}%, tightened 50%)"
                    )
                    events.append({
                        "symbol": sym, "action": action,
                        "entry_price": pos["entry_price"],
                        "exit_price": price, "pnl_usdt": 0.0, "pnl_pct": 0.0,
                        "leverage": pos["leverage"],
                        "reason": "be_activated", "partial": True, "milestone": True,
                        "trail_pct": trail_pct,
                    })

                    # P2: Emit partial-close request at TP1
                    # Guard: skip partial if TP1 too close to entry (fees would
                    # net negative). Default 0.2% covers Bybit taker fee × 2.
                    tp1_move_pct = abs(tp1 - entry) / entry * 100
                    if (
                        _partial_enabled
                        and _tp1_close_pct > 0
                        and tp1_move_pct >= _partial_min_move
                    ):
                        events.append({
                            "symbol": sym, "action": action,
                            "entry_price": pos["entry_price"],
                            "exit_price": tp1,
                            "leverage": pos["leverage"],
                            "reason": "tp1_partial",
                            "requires_partial_close": True,
                            "close_pct": _tp1_close_pct,
                        })
                    elif _partial_enabled and _tp1_close_pct > 0:
                        logger.info(
                            f"[PnL] {sym} TP1 partial SKIPPED: move {tp1_move_pct:.3f}% "
                            f"< min {_partial_min_move:.2f}% (fees would net negative). "
                            f"BE+trail still active."
                        )

            # ── TP2 → Advance SL to TP1 level ────────────────
            if len(tps) > 1 and 1 not in tp_hits and 0 in tp_hits:
                tp2 = tps[1]
                tp2_ok = (action == "LONG" and tp2 > entry) or (action == "SHORT" and tp2 < entry)
                if not tp2_ok:
                    logger.warning(f"[PnL] {sym} TP2={tp2} invalid for {action} (entry={entry}) — skipped")
                    tp_hits.append(1)
                    pos["tp_hits"] = tp_hits
                hit = tp2_ok and ((action == "LONG" and price >= tp2) or (action == "SHORT" and price <= tp2))
                if hit:
                    tp_hits.append(1)
                    pos["tp_hits"] = tp_hits
                    new_sl = tps[0]
                    advanced = False
                    if action == "LONG" and new_sl > pos["stop_loss"]:
                        pos["stop_loss"] = new_sl
                        advanced = True
                    elif action == "SHORT" and new_sl < pos["stop_loss"]:
                        pos["stop_loss"] = new_sl
                        advanced = True
                    sl = pos["stop_loss"]
                    # Tighten trail slightly at TP2
                    trail_pct = self._calc_trail_pct(atr_pct * 0.8, pos["leverage"])
                    pos["trailing_trail_pct"] = trail_pct
                    logger.info(
                        f"[PnL] {sym} TP2 @ {tp2:.4f} → "
                        f"SL→TP1({new_sl:.4f}), trail={trail_pct:.1f}%"
                    )
                    events.append({
                        "symbol": sym, "action": action,
                        "entry_price": pos["entry_price"],
                        "exit_price": price, "pnl_usdt": 0.0, "pnl_pct": 0.0,
                        "leverage": pos["leverage"],
                        "reason": "sl_advanced", "partial": True, "milestone": True,
                        "new_sl": new_sl, "trail_pct": trail_pct,
                    })

                    # P2: Emit partial-close request at TP2
                    tp2_move_pct = abs(tp2 - entry) / entry * 100
                    if (
                        _partial_enabled
                        and _tp2_close_pct > 0
                        and tp2_move_pct >= _partial_min_move
                    ):
                        events.append({
                            "symbol": sym, "action": action,
                            "entry_price": pos["entry_price"],
                            "exit_price": tp2,
                            "leverage": pos["leverage"],
                            "reason": "tp2_partial",
                            "requires_partial_close": True,
                            "close_pct": _tp2_close_pct,
                        })
                    elif _partial_enabled and _tp2_close_pct > 0:
                        logger.info(
                            f"[PnL] {sym} TP2 partial SKIPPED: move {tp2_move_pct:.3f}% "
                            f"< min {_partial_min_move:.2f}% (fees would net negative). "
                            f"SL still advanced to TP1."
                        )

            # ── TP3 → Full close ──────────────────────────────
            if len(tps) > 2 and 2 not in tp_hits:
                tp3 = tps[2]
                tp3_ok = (action == "LONG" and tp3 > entry) or (action == "SHORT" and tp3 < entry)
                if not tp3_ok:
                    logger.warning(f"[PnL] {sym} TP3={tp3} invalid for {action} (entry={entry}) — skipped")
                    tp_hits.append(2)
                    pos["tp_hits"] = tp_hits
                hit = tp3_ok and ((action == "LONG" and price >= tp3) or (action == "SHORT" and price <= tp3))
                if hit:
                    # Same as SL: emit close event, let main.py close on Bybit FIRST
                    logger.info(
                        f"[PnL] {sym} TP3 hit @ {tp3:.6f} (price={price:.6f}) "
                        f"— requesting Bybit close"
                    )
                    events.append({
                        "symbol": sym,
                        "action": action,
                        "entry_price": pos["entry_price"],
                        "exit_price": tp3,
                        "leverage": pos["leverage"],
                        "reason": "take_profit",
                        "requires_close": True,
                        "close_pct": 100.0,
                    })
                    continue

            _save_store(self._store)

        return events

    # ── Computed metrics ──────────────────────────────────────────

    def unrealized_pnl(self, market_data: List[Dict]) -> float:
        """Calculate total unrealized PnL across all open dry-run positions."""
        prices = {d["symbol"]: d["current_price"] for d in market_data}
        total = 0.0
        for sym, pos in self._store["open_positions"].items():
            price = prices.get(sym)
            if price is None or pos["entry_price"] <= 0:
                continue
            notional = pos["notional"]
            entry = pos["entry_price"]
            if pos["action"] == "LONG":
                pnl = (price - entry) / entry * notional
            else:
                pnl = (entry - price) / entry * notional
            total += pnl
        return round(total, 4)

    def open_positions_summary(self, market_data: List[Dict]) -> List[Dict]:
        """Return enriched open position list with current PnL."""
        prices = {d["symbol"]: d["current_price"] for d in market_data}
        summary = []
        for sym, pos in self._store["open_positions"].items():
            price = prices.get(sym, 0.0)
            entry = pos["entry_price"]
            notional = pos["notional"]
            upnl = 0.0
            upnl_pct = 0.0
            if price > 0 and entry > 0:
                if pos["action"] == "LONG":
                    upnl = (price - entry) / entry * notional
                else:
                    upnl = (entry - price) / entry * notional
                upnl_pct = upnl / (notional / pos["leverage"]) * 100
            summary.append({
                **pos,
                "current_price": price,
                "unrealized_pnl": round(upnl, 4),
                "unrealized_pnl_pct": round(upnl_pct, 2),
            })
        return summary

    def get_stats(self) -> Dict:
        s = self._store["stats"]
        total = s["total_trades"]
        win_rate = (s["wins"] / total * 100) if total > 0 else 0.0
        return {
            "realized_pnl": s["total_realized"],
            "total_trades": total,
            "wins": s["wins"],
            "losses": s["losses"],
            "win_rate": round(win_rate, 1),
            "best_trade": s["best_trade"],
            "worst_trade": s["worst_trade"],
            "best_symbol": s["best_symbol"],
            "worst_symbol": s["worst_symbol"],
            "open_count": len(self._store["open_positions"]),
        }

    def get_recent_trades(self, n: int = 5) -> List[Dict]:
        return self._store["closed_trades"][-n:]

    def get_performance_breakdown(self) -> Dict:
        """
        Detailed perf breakdown: by AI tier, by symbol, win rate, avg win/loss.
        Useful for diagnosing which AI/symbol generates profitable signals.
        """
        from collections import defaultdict
        closed = self._store.get("closed_trades", [])

        by_tier   = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                                          "best": 0.0, "worst": 0.0})
        by_symbol = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                                          "best": 0.0, "worst": 0.0})

        for t in closed:
            # Skip partial closes (per-position counted at full close)
            if t.get("partial"):
                continue
            pnl = float(t.get("pnl_usdt", 0))
            tier = t.get("ai_tier", "?")
            sym = t.get("symbol", "?")
            for bucket in (by_tier[tier], by_symbol[sym]):
                bucket["trades"] += 1
                bucket["pnl"]    = round(bucket["pnl"] + pnl, 4)
                if pnl >= 0:
                    bucket["wins"] += 1
                    if pnl > bucket["best"]:
                        bucket["best"] = pnl
                else:
                    bucket["losses"] += 1
                    if pnl < bucket["worst"]:
                        bucket["worst"] = pnl

        # Compute win rates
        for d in (by_tier, by_symbol):
            for k, v in d.items():
                tot = v["trades"]
                v["win_rate"] = round(v["wins"]/tot*100, 1) if tot > 0 else 0.0
                v["avg_pnl"]  = round(v["pnl"]/tot, 4) if tot > 0 else 0.0

        return {
            "by_tier":   dict(by_tier),
            "by_symbol": dict(by_symbol),
        }

    # ── Live Position Sync ─────────────────────────────────────────

    def sync_positions(
        self,
        bybit_positions: List[Dict],
        closed_pnl_list: List[Dict] = None,
    ) -> Dict:
        """
        Reconcile pnl_store with actual Bybit open positions.

        - Positions on Bybit but not in store → add them
        - Positions in store but not on Bybit → mark closed (with real PnL if available)
        - Both have the position → update entry_price / leverage to actual

        Returns: {"added": [...], "updated": [...], "closed": [...]}
        """
        bybit_map = {p["symbol"]: p for p in bybit_positions}
        store_map = self._store["open_positions"]
        result = {"added": [], "updated": [], "closed": []}

        # Build lookup of recently closed PnL per symbol
        closed_map: Dict[str, Dict] = {}
        if closed_pnl_list:
            for c in closed_pnl_list:
                sym = c.get("symbol", "")
                # Keep the most recent close per symbol
                if sym and sym not in closed_map:
                    closed_map[sym] = c

        # 1. Positions in store but NOT on Bybit → closed on exchange
        for sym in list(store_map.keys()):
            if sym in bybit_map:
                continue

            pos = store_map[sym]
            closed_data = closed_map.get(sym)

            if closed_data and closed_data.get("exit_price", 0) > 0:
                exit_price = closed_data["exit_price"]
                real_pnl = closed_data["realized_pnl"]

                # Use record_close to properly update stats
                trade = self.record_close(sym, exit_price, reason="closed_on_exchange")
                if trade:
                    # Correct PnL to actual Bybit realized PnL
                    diff = real_pnl - trade["pnl_usdt"]
                    if abs(diff) > 0.001:
                        trade["pnl_usdt"] = round(real_pnl, 4)
                        trade["pnl_pct"] = round(
                            real_pnl / max(pos["notional"] / pos["leverage"], 0.01) * 100, 2
                        )
                        # Fix the last closed trade entry
                        if self._store["closed_trades"]:
                            self._store["closed_trades"][-1] = trade
                        # Correct cumulative stats
                        self._store["stats"]["total_realized"] = round(
                            self._store["stats"]["total_realized"] + diff, 4
                        )
                logger.info(
                    f"[Sync] Closed {sym}: exit={exit_price:.4f} pnl={real_pnl:+.4f}"
                )
            else:
                # No exit data — just remove from store
                store_map.pop(sym, None)
                logger.info(f"[Sync] Removed stale {sym} (no exit data from Bybit)")

            result["closed"].append(sym)

        # 2. Positions on Bybit but NOT in store → add them
        # Cooldown: skip add if symbol was just closed by tracker (<60s ago).
        # Bybit may take a few seconds to reflect the close, OR our local
        # SL/TP just fired but Bybit close-order is still in-flight.
        # Without cooldown: tracker closes → sync re-adds → loop forever.
        COOLDOWN_S = 300.0  # 5 min — long enough to prevent open→close→reopen loop
        now_t = time.time()
        # Cleanup stale cooldown entries
        self._recent_closes = {
            s: t for s, t in self._recent_closes.items()
            if now_t - t < COOLDOWN_S
        }

        for sym, bp in bybit_map.items():
            # Skip re-add if just closed locally (cooldown)
            if sym in self._recent_closes:
                age = now_t - self._recent_closes[sym]
                logger.info(
                    f"[Sync] Skip re-add {sym} (closed by tracker {age:.0f}s ago, "
                    f"cooldown {COOLDOWN_S:.0f}s) — Bybit position may still be settling"
                )
                continue

            action = "LONG" if bp["side"] == "long" else "SHORT"
            entry = float(bp["entryPrice"])
            leverage = int(float(bp.get("leverage", 1)))
            contracts = float(bp.get("contracts", 0))
            notional = round(contracts * entry, 2)

            if sym not in store_map:
                # Inherit SL/TP from Bybit if set (avoid orphan position with
                # no protection). bp.stopLoss=0 means Bybit has no SL → DANGER.
                bybit_sl = float(bp.get("stopLoss", 0) or 0)
                bybit_tp = float(bp.get("takeProfit", 0) or 0)
                tps_inherited = [bybit_tp] if bybit_tp > 0 else []

                store_map[sym] = {
                    "symbol": sym,
                    "action": action,
                    "entry_price": entry,
                    "stop_loss": bybit_sl,
                    "original_sl": bybit_sl,
                    "take_profit": tps_inherited,
                    "leverage": leverage,
                    "notional": notional,
                    "remaining_pct": 1.0,
                    "size_pct": 0.0,
                    "open_time": _now_str(),
                    "status": "synced_from_exchange",
                    "peak_price": entry,
                    "trailing_active": False,
                    "trailing_activation_price": 0.0,
                    "trailing_trail_pct": 3.0,
                    "tp_hits": [],
                    "dca_count": 0,
                    "needs_sl_attach": bybit_sl <= 0,   # flag for main.py to set SL
                }
                sl_status = (
                    f"SL={bybit_sl:.6f}" if bybit_sl > 0
                    else "NO_SL_ON_BYBIT (DANGER — needs attach)"
                )
                logger.info(
                    f"[Sync] Added {action} {sym} from Bybit "
                    f"@ {entry:.4f} (lev={leverage}x, notional={notional:.2f}, {sl_status})"
                )
                result["added"].append(sym)
            else:
                # 3. Both have it → update selectively
                pos = store_map[sym]
                old_entry = pos["entry_price"]
                changed = False
                is_synced = pos.get("status") == "synced_from_exchange"

                # Only update entry_price for synced positions — never overwrite
                # bot-opened entry (Bybit avgPrice can differ due to merge/DCA)
                if is_synced and abs(old_entry - entry) / max(entry, 1) > 0.0001:
                    pos["entry_price"] = entry
                    changed = True

                if pos.get("leverage") != leverage:
                    pos["leverage"] = leverage
                    changed = True

                if pos["action"] != action:
                    pos["action"] = action
                    changed = True

                # Update notional to reflect actual size on exchange
                if abs(pos["notional"] - notional) / max(notional, 1) > 0.01:
                    pos["notional"] = notional
                    changed = True

                if changed:
                    entry_str = (
                        f"entry {old_entry:.4f}→{entry:.4f} "
                        if is_synced else f"entry={old_entry:.4f}(kept) "
                    )
                    logger.info(
                        f"[Sync] Updated {sym}: "
                        f"{entry_str}lev={leverage}x"
                    )
                    result["updated"].append(sym)

        _save_store(self._store)
        return result

    def reset(self) -> None:
        """Clear all data (use carefully)."""
        self._store = _load_store()
        self._store["open_positions"] = {}
        self._store["closed_trades"] = []
        self._store["stats"] = {
            "total_realized": 0.0, "total_trades": 0,
            "wins": 0, "losses": 0,
            "best_trade": 0.0, "worst_trade": 0.0,
            "best_symbol": "", "worst_symbol": "",
        }
        _save_store(self._store)
