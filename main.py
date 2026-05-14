"""
main.py — AI Quant Trading Bot v2.0

DATA LAYER      MarketFeed  — direct HTTP OHLCV (Bybit/OKX/Gate.io)
ANALYZER LAYER  SignalEngine — AI quant signal (multi-provider)
EXECUTION LAYER CEXExecutor (Bybit futures) | DEXExecutor (Hyperliquid)
PNL TRACKER     PnLTracker  — dry-run simulation + live stats
MEMORY          TradeMemory — AI learning + adaptive risk
"""
import sys
import os
from dotenv import load_dotenv

load_dotenv()

# Fix Windows console encoding for Unicode box-drawing characters
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import asyncio
from pathlib import Path
from typing import Any, Dict, List
from datetime import datetime

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
(_ROOT / "logs").mkdir(exist_ok=True)


# ── Pro Dashboard ──────────────────────────────────────────────────────────

def _clr(text: str, code: str) -> str:
    """ANSI color wrapper."""
    codes = {
        "green":  "\033[92m", "red":    "\033[91m",
        "yellow": "\033[93m", "cyan":   "\033[96m",
        "white":  "\033[97m", "grey":   "\033[90m",
        "bold":   "\033[1m",  "reset":  "\033[0m",
    }
    return f"{codes.get(code,'')}{text}{codes['reset']}"


def _pnl_color(val: float) -> str:
    if val > 0:
        return _clr(f"+{val:,.4f}", "green")
    if val < 0:
        return _clr(f"{val:,.4f}", "red")
    return _clr(f"{val:,.4f}", "grey")


def _pct_color(val: float) -> str:
    if val > 0:
        return _clr(f"+{val:.2f}%", "green")
    if val < 0:
        return _clr(f"{val:.2f}%", "red")
    return _clr(f"{val:.2f}%", "grey")


def print_dashboard(
    scan: int,
    signals: List[Dict],
    executed: int,
    balance: float,
    exchange: str,
    dry_run: bool,
    next_scan: int,
    data_sources: Dict[str, str],
    pnl_stats: Dict,
    open_positions: List[Dict],
    recent_trades: List[Dict],
    adaptive_mode: str = "normal",
) -> None:
    W = 66
    now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    mode = _clr("DRY RUN", "yellow") if dry_run else _clr("LIVE", "green")

    print()
    print(_clr("=" * W, "cyan"))
    title = f"  AI QUANT BOT  [{mode}{_clr('', 'reset')}]  |  {exchange}  |  Scan #{scan:04d}"
    print(title)
    print(f"  {_clr(now, 'grey')}")
    print(_clr("=" * W, "cyan"))

    # ── Account ───────────────────────────────────────────────
    realized = pnl_stats.get("realized_pnl", 0.0)
    unrealized = pnl_stats.get("unrealized_pnl", 0.0)
    total_trades = pnl_stats.get("total_trades", 0)
    win_rate = pnl_stats.get("win_rate", 0.0)
    wins = pnl_stats.get("wins", 0)
    losses = pnl_stats.get("losses", 0)
    open_cnt = pnl_stats.get("open_count", 0)

    print(f"  {_clr('ACCOUNT', 'bold')}")
    print(f"  Balance      : {_clr(f'{balance:>12,.2f} USDT', 'white')}")
    print(f"  Realized PnL : {_pnl_color(realized):>20}  USDT")
    print(f"  Unrealized   : {_pnl_color(unrealized):>20}  USDT")
    print(f"  Positions    : {open_cnt} open  |  Next scan: {next_scan}s")

    # ── Adaptive mode ────────────────────────────────────────
    if adaptive_mode != "normal":
        mode_colors = {"defensive": "red", "conservative": "yellow", "aggressive": "green"}
        mc = mode_colors.get(adaptive_mode, "grey")
        print(f"  AI Mode      : {_clr(adaptive_mode.upper(), mc)}")

    # ── Stats ─────────────────────────────────────────────────
    if total_trades > 0:
        print(_clr("-" * W, "cyan"))
        print(f"  {_clr('PERFORMANCE', 'bold')}")
        wr_col = _clr(f"{win_rate:.1f}%", "green" if win_rate >= 50 else "red")
        print(f"  Win Rate     : {wr_col}  ({wins}W / {losses}L / {total_trades} trades)")
        best = pnl_stats.get("best_trade", 0.0)
        worst = pnl_stats.get("worst_trade", 0.0)
        bsym = pnl_stats.get("best_symbol", "")
        wsym = pnl_stats.get("worst_symbol", "")
        if best != 0:
            print(f"  Best Trade   : {_pnl_color(best)} USDT  {_clr(bsym, 'grey')}")
        if worst != 0:
            print(f"  Worst Trade  : {_pnl_color(worst)} USDT  {_clr(wsym, 'grey')}")

    # ── Open positions ────────────────────────────────────────
    if open_positions:
        print(_clr("-" * W, "cyan"))
        print(f"  {_clr('OPEN POSITIONS', 'bold')}")
        for p in open_positions:
            sym = p["symbol"]
            act = p["action"]
            act_col = _clr(f"^{act}", "green") if act == "LONG" else _clr(f"v{act}", "red")
            entry = p.get("entry_price", 0)
            cur = p.get("current_price", 0)
            upnl = p.get("unrealized_pnl", 0.0)
            upct = p.get("unrealized_pnl_pct", 0.0)
            lev = p.get("leverage", 0)
            remaining = p.get("remaining_pct", 1.0)
            trailing = p.get("trailing_active", False)

            remain_str = f" {remaining*100:.0f}%" if remaining < 1.0 else ""
            trail_str = _clr(" [T]", "cyan") if trailing else ""

            print(f"  {act_col:8s}  {_clr(sym, 'white'):<14s}"
                  f"  {_clr('E:', 'grey')}{entry:>10,.4f}"
                  f"  {_clr('C:', 'grey')}{cur:>10,.4f}"
                  f"  {_pnl_color(upnl)} ({_pct_color(upct)})  {lev}x"
                  f"{_clr(remain_str, 'yellow')}{trail_str}")

    # ── Recent trades ─────────────────────────────────────────
    if recent_trades:
        print(_clr("-" * W, "cyan"))
        print(f"  {_clr('RECENT TRADES', 'bold')}")
        for t in reversed(recent_trades[-4:]):
            sym = t["symbol"]
            act = t["action"]
            pnl = float(t["pnl_usdt"])
            pct = float(t["pnl_pct"])
            reason = t.get("reason", "")
            reason_sym = {"stop_loss": "SL", "take_profit": "TP",
                          "trailing_stop": "TS"}.get(reason, reason[:6])
            act_s = _clr("^", "green") if act == "LONG" else _clr("v", "red")
            print(f"  {act_s}  {_clr(sym, 'white'):<14s}"
                  f"  {_pnl_color(pnl):>20}  ({_pct_color(pct)})  {reason_sym}")

    # ── Data sources ──────────────────────────────────────────
    if data_sources:
        src_items = list(data_sources.items())
        src_str = "  ".join(_clr(f"{s}:{e}", "grey") for s, e in src_items[:5])
        print(_clr("-" * W, "cyan"))
        print(f"  {_clr('DATA', 'bold')}  {src_str}")

    # ── Signals ───────────────────────────────────────────────
    active_signals = [s for s in signals if s.get("action", "HOLD") in ("LONG", "SHORT")]
    hold_signals   = [s for s in signals if s.get("action", "HOLD") == "HOLD"]

    print(_clr("-" * W, "cyan"))
    print(f"  {_clr('SIGNALS', 'bold')}"
          + (f"  {_clr(f'({len(hold_signals)} HOLD -- details in Telegram)', 'grey')}" if hold_signals else ""))

    if not active_signals:
        print(f"  {_clr('No actionable signals this cycle.', 'grey')}")
    else:
        for s in active_signals:
            action = s.get("action", "HOLD")
            sym    = s.get("symbol", "?")
            score  = float(s.get("score", 0))
            conf   = float(s.get("confidence", 0)) * 100
            reason = (s.get("reason") or "").strip()

            tag    = _clr("^ LONG ", "green") if action == "LONG" else _clr("v SHORT", "red")
            score_col = _clr(f"{score:.1f}", "green" if score >= 7 else ("yellow" if score >= 5.5 else "grey"))
            conf_col  = _clr(f"{conf:.0f}%", "green" if conf >= 70 else ("yellow" if conf >= 60 else "grey"))

            print(f"  {tag}  {_clr(sym, 'white'):<14s}"
                  f"  Score:{score_col:>8}  Conf:{conf_col:>7}")

            entry  = float(s.get("entry_price", 0))
            sl     = float(s.get("stop_loss", 0))
            tps    = [float(v) for v in s.get("take_profit", []) if v]
            lev    = s.get("leverage", 0)
            size   = s.get("position_size_percent", 0)
            risk   = s.get("risk_level", "")
            risk_c = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red"}.get(risk, "grey")

            print(f"         {_clr('Entry', 'grey')} {_clr(f'{entry:>12,.4f}', 'white')}"
                  f"  {_clr('SL', 'grey')} {_clr(f'{sl:>12,.4f}', 'red')}")
            for i, tp in enumerate(tps, 1):
                print(f"         {_clr(f'TP{i}  ', 'grey')} {_clr(f'{tp:>12,.4f}', 'green')}")
            print(f"         Lev:{_clr(f'{lev}x', 'white')}  "
                  f"Size:{_clr(f'{size}%', 'white')}  "
                  f"Risk:{_clr(risk, risk_c)}")

            if reason:
                words, line = reason.split(), "         "
                for w in words:
                    if len(line) + len(w) + 1 > W - 4:
                        print(_clr(line.rstrip(), "grey"))
                        line = "         " + w + " "
                    else:
                        line += w + " "
                if line.strip():
                    print(_clr(line.rstrip(), "grey"))

            trail = s.get("trailing_stop", {})
            if isinstance(trail, dict) and trail.get("enabled"):
                print(f"         {_clr('Trailing: ' + trail.get('rule', ''), 'cyan')}")
            print()

    print(_clr("=" * W, "cyan"))


# ── Signal helpers ─────────────────────────────────────────────────────────

def _dedup_signals(signals: List[Dict]) -> List[Dict]:
    """
    Remove duplicate signals for the same symbol (AI sometimes hallucinates
    repeats when input is long). Strategy:
      - Keep first occurrence of each symbol
      - But if a later occurrence has a higher score, prefer it
    """
    from utils.logger import logger
    if not signals:
        return signals
    best_per_sym: Dict[str, Dict] = {}
    order: List[str] = []
    for s in signals:
        sym = s.get("symbol")
        if not sym:
            continue
        prev = best_per_sym.get(sym)
        if prev is None:
            best_per_sym[sym] = s
            order.append(sym)
        else:
            cur_score = float(s.get("score", 0))
            prev_score = float(prev.get("score", 0))
            if cur_score > prev_score:
                best_per_sym[sym] = s
    deduped = [best_per_sym[sym] for sym in order]
    if len(deduped) < len(signals):
        logger.warning(
            f"[Signals] Deduped {len(signals)} → {len(deduped)} "
            f"(AI returned {len(signals) - len(deduped)} duplicate symbols)"
        )
    return deduped


def extract_signals(result: Dict) -> tuple:
    if "market_scan" in result:
        scan = _dedup_signals(result["market_scan"])
        return scan, result.get("best_candidate", "NONE")
    if "action" in result:
        sym = result.get("symbol", "NONE")
        best = sym if result.get("action") != "HOLD" else "NONE"
        return [result], best
    return [], "NONE"


# ── Telegram command processor ─────────────────────────────────────────────

async def _process_telegram_commands(
    notifier: Any, executor: Any, pnl: Any
) -> None:
    """
    Poll Telegram for user commands and execute them immediately.

    Supported:
      close SYMBOL PCT  → partial/full close via Bybit market order
      positions         → show current open positions
      help              → show command list
    """
    from utils.logger import logger

    commands = await notifier.check_commands()
    if not commands:
        return

    open_positions = pnl._store.get("open_positions", {})

    for cmd in commands:
        ctype = cmd.get("type")

        # ── help ──────────────────────────────────────────────
        if ctype == "help":
            await notifier.send_help()

        # ── positions ──────────────────────────────────────────
        elif ctype == "positions":
            await notifier.send_positions(open_positions)

        # ── perf — performance breakdown by AI tier & symbol ─────
        elif ctype == "perf":
            try:
                breakdown = pnl.get_performance_breakdown()
                lines = ["<b>Performance Breakdown</b>\n"]

                # By AI tier
                lines.append("<u>By AI Tier:</u>")
                tiers = sorted(
                    breakdown["by_tier"].items(),
                    key=lambda kv: -kv[1]["pnl"],
                )
                for tier, d in tiers:
                    lines.append(
                        f"  <code>{tier}</code>\n"
                        f"    Trades: {d['trades']}  WR: {d['win_rate']:.0f}%  "
                        f"PnL: <b>{d['pnl']:+.4f}</b> USDT\n"
                        f"    Best: {d['best']:+.4f}  Worst: {d['worst']:+.4f}"
                    )

                lines.append("\n<u>By Symbol (top 8 by PnL):</u>")
                syms = sorted(
                    breakdown["by_symbol"].items(),
                    key=lambda kv: -kv[1]["pnl"],
                )[:8]
                for sym, d in syms:
                    lines.append(
                        f"  <code>{sym}</code>: {d['trades']}t "
                        f"WR{d['win_rate']:.0f}% PnL <b>{d['pnl']:+.4f}</b>"
                    )

                lines.append("\n<u>Worst symbols (bottom 5):</u>")
                worst = sorted(
                    breakdown["by_symbol"].items(),
                    key=lambda kv: kv[1]["pnl"],
                )[:5]
                for sym, d in worst:
                    if d["pnl"] >= 0:
                        break
                    lines.append(
                        f"  <code>{sym}</code>: {d['trades']}t "
                        f"WR{d['win_rate']:.0f}% PnL {d['pnl']:+.4f}"
                    )

                await notifier.send("\n".join(lines))
            except Exception as exc:
                await notifier.send(f"perf error: {exc}")

        # ── close SYMBOL PCT ──────────────────────────────────
        elif ctype == "close":
            sym = cmd["symbol"]
            pct = cmd["pct"]

            # Validate: symbol must be in open positions
            if sym not in open_positions:
                # Try without USDT suffix match
                matches = [k for k in open_positions if sym in k]
                if len(matches) == 1:
                    sym = matches[0]
                else:
                    await notifier.send(
                        f"No open position for <code>{sym}</code>.\n"
                        f"Open: {', '.join(f'<code>{s}</code>' for s in open_positions) or 'none'}"
                    )
                    continue

            pos    = open_positions[sym]
            action = pos.get("action", "LONG")
            entry  = pos.get("entry_price", 0)
            lev    = pos.get("leverage", 1)

            await notifier.send(
                f"Closing <code>{sym}</code> {pct:.0f}%...\n"
                f"Side: {action}  Entry: {entry:.6f}  Lev: {lev}x"
            )

            if hasattr(executor, "partial_close"):
                result = await executor.partial_close(sym, action, pct)
            else:
                result = None

            if result and not result.get("dry_run"):
                closed_qty = result.get("closed_qty", "?")
                total_qty  = result.get("total_qty", "?")

                # Update pnl tracker: full close or mark partial
                if pct >= 99.9:
                    # Full close — exit price priority chain:
                    #   1. Executor's VWAP fill from /v5/execution/list (authoritative)
                    #   2. Bybit ticker lastPrice (approximation, slippage possible)
                    #   3. Entry price (last resort — flag for later reconciliation)
                    exit_price = float(result.get("price", 0) or 0)
                    src = "execution_list" if exit_price > 0 else None

                    if exit_price <= 0:
                        # Fallback: query last price from Bybit ticker
                        try:
                            tick = await asyncio.to_thread(
                                executor._get,
                                "/v5/market/tickers",
                                {"category": "linear", "symbol": executor._to_bybit(sym)},
                            )
                            if tick and tick.get("list"):
                                exit_price = float(tick["list"][0].get("lastPrice", 0))
                                if exit_price > 0:
                                    src = "ticker_lastprice"
                        except Exception as e:
                            logger.error(
                                f"[CMD] {sym} close: ticker fallback failed: {e}"
                            )

                    close_reason = "manual_close_100pct"
                    if exit_price <= 0:
                        exit_price = entry  # last-resort, will give $0 PnL
                        src = "entry_fallback"
                        # Mark reason so reconciliation script can find these later
                        close_reason = "manual_close_100pct_unreconciled"
                        logger.error(
                            f"[CMD] {sym} close: BOTH execution_list AND ticker failed. "
                            f"PnL recorded as $0 — check Bybit and run reconciliation."
                        )
                    else:
                        logger.info(
                            f"[CMD] {sym} close: exit_price={exit_price} (src={src})"
                        )
                    trade = pnl.record_close(sym, exit_price, reason=close_reason)
                    if trade:
                        await notifier.alert_pnl_close(trade)
                    else:
                        await notifier.send(
                            f"Closed <code>{sym}</code> {closed_qty}/{total_qty} qty\n"
                            f"(PnL tracked on Bybit — check app)"
                        )
                else:
                    await notifier.send(
                        f"Closed <code>{sym}</code> {pct:.0f}%\n"
                        f"Qty: {closed_qty} of {total_qty}\n"
                        f"Remaining position still open. Check Bybit for realized PnL."
                    )

                logger.info(
                    f"[CMD] Close {sym} {pct:.0f}%: "
                    f"qty={closed_qty}/{total_qty} action={action}"
                )

            elif result and result.get("dry_run"):
                await notifier.send(
                    f"[DRY RUN] Would close <code>{sym}</code> {pct:.0f}% — no real order sent."
                )
            else:
                await notifier.send(
                    f"Close <code>{sym}</code> {pct:.0f}% FAILED.\n"
                    f"Check logs for details."
                )


# ── Anti-overtrading helpers ───────────────────────────────────────────────

def _daily_realized_loss(pnl: Any) -> float:
    """
    Return total realized LOSS (negative value) for trades closed today (UTC).

    Uses UTC date so reset is consistent regardless of VPS timezone.
    Bybit & most crypto exchanges measure days in UTC — using UTC here keeps
    bot's daily limit aligned with exchange perpetual funding cycle (00:00 UTC).
    """
    from datetime import timezone
    today = datetime.now(timezone.utc).date()
    total = 0.0
    for t in pnl._store.get("closed_trades", []):
        close_time = t.get("close_time", "")
        if not close_time:
            continue
        try:
            trade_date = datetime.strptime(close_time, "%Y-%m-%d %H:%M:%S").date()
            if trade_date == today:
                p = t.get("pnl_usdt", 0.0)
                if p < 0:
                    total += p
        except Exception:
            pass
    return total  # always <= 0


def _symbol_trade_count_today(pnl: Any, symbol: str) -> int:
    """Count trades (open + closed today UTC) for a given symbol."""
    from datetime import timezone
    today = datetime.now(timezone.utc).date()
    count = 0
    for t in pnl._store.get("closed_trades", []):
        if t.get("symbol") != symbol:
            continue
        close_time = t.get("close_time", "")
        if close_time:
            try:
                trade_date = datetime.strptime(close_time, "%Y-%m-%d %H:%M:%S").date()
                if trade_date == today:
                    count += 1
            except Exception:
                pass
    if symbol in pnl._store.get("open_positions", {}):
        count += 1
    return count


def _symbol_hours_since_last_loss(pnl: Any, symbol: str) -> float:
    """Return hours since last losing trade (UTC-aware). 999 if no loss found."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    for t in reversed(pnl._store.get("closed_trades", [])):
        if t.get("symbol") != symbol:
            continue
        if t.get("pnl_usdt", 0) < 0:
            close_time = t.get("close_time", "")
            if close_time:
                try:
                    close_dt = datetime.strptime(close_time, "%Y-%m-%d %H:%M:%S")
                    close_dt = close_dt.replace(tzinfo=timezone.utc)
                    return (now - close_dt).total_seconds() / 3600
                except Exception:
                    pass
    return 999.0


# ── DCA Logic ──────────────────────────────────────────────────────────────

async def _check_dca(
    pnl: Any,
    market_data: List[Dict],
    executor: Any,
    notifier: Any,
    memory: Any,
    account_balance: float,
) -> None:
    """
    Check each open position for DCA opportunity.

    DCA conditions (SL_MODE=none):
    - ROI (leveraged PnL%) reaches DCA_ROI_TRIGGER (default -75%)
    - No more than 1 DCA per position

    DCA conditions (SL_MODE=atr, legacy mode):
    - Position unrealized price PnL < -2%
    - Price moved > 1x ATR from entry
    - HTF bias still supports direction

    If conditions met, send Telegram asking user y/n.
    """
    from config import Config
    from utils.logger import logger

    prices = {d["symbol"]: d for d in market_data}

    for sym, pos in list(pnl._store["open_positions"].items()):
        # Skip if already DCA'd
        if pos.get("dca_count", 0) >= 1:
            continue
        # Skip if user previously rejected DCA for this position (jawab N)
        # Timeout TIDAK set this flag, jadi tetap bisa ditanya ulang nanti.
        if pos.get("dca_declined", False):
            continue

        mkt = prices.get(sym)
        if mkt is None:
            continue

        entry = pos["entry_price"]
        action = pos["action"]
        leverage = float(pos.get("leverage", 1))
        current_price = mkt.get("current_price", 0)
        atr_pct = mkt.get("atr_pct", 0)
        htf_bias = mkt.get("htf_bias", "neutral")
        rsi = mkt.get("rsi", 50)

        if entry <= 0 or current_price <= 0:
            continue

        # Price change % (unleveraged)
        if action == "LONG":
            price_pct = (current_price - entry) / entry * 100
        else:
            price_pct = (entry - current_price) / entry * 100

        # ROI % = price change × leverage (what user actually sees on Bybit)
        roi_pct = price_pct * leverage

        # ── DCA trigger: ROI-based (SL_MODE=roi/none) or price-based (atr mode) ──
        if Config.SL_MODE in ("roi", "none"):
            # Trigger when ROI hits the configured threshold (default -75%)
            if roi_pct >= Config.DCA_ROI_TRIGGER:
                continue  # not losing enough yet

            dca_reasons = [
                f"ROI: {roi_pct:.1f}% (threshold: {Config.DCA_ROI_TRIGGER:.0f}%)",
                f"Price: {price_pct:.1f}% from entry ({leverage:.0f}x leverage)",
                f"HTF bias: {htf_bias}  |  RSI: {rsi:.0f}",
                f"Current: {current_price:,.4f}  |  Entry: {entry:,.4f}",
            ]
        else:
            # Legacy: price-based trigger
            # 1. Losing > 2% on price
            if price_pct >= -2.0:
                continue

            # 2. Price moved at least 1x ATR
            if atr_pct > 0:
                move_pct = abs((current_price - entry) / entry * 100)
                if move_pct < atr_pct:
                    continue

            # 3. HTF still supports direction
            if action == "LONG" and htf_bias == "bearish":
                continue
            if action == "SHORT" and htf_bias == "bullish":
                continue

            # 4. RSI not extreme
            if action == "LONG" and rsi < 20:
                continue
            if action == "SHORT" and rsi > 80:
                continue

            dca_reasons = [
                f"Price drop {price_pct:.1f}% from entry (ROI: {roi_pct:.1f}%)",
                f"HTF bias: {htf_bias}  |  RSI: {rsi:.0f}",
            ]
            if action == "LONG" and rsi < 40:
                dca_reasons.append("RSI oversold zone, potential reversal")
            elif action == "SHORT" and rsi > 60:
                dca_reasons.append("RSI overbought zone, potential reversal")
        dca_reasons.append(f"Current: {current_price:,.4f} | Entry: {entry:,.4f}")

        reason_text = "\n".join(dca_reasons)

        # Ask user via Telegram
        roi_display = f"{roi_pct:.1f}%"
        question = (
            f"⚠️ <b>DCA {action} {sym}?</b>\n\n"
            f"ROI: <b>{roi_display}</b> ({leverage:.0f}x leverage)\n"
            f"{reason_text}\n\n"
            f"DCA +{Config.DCA_SIZE_PCT}% "
            f"({account_balance * Config.DCA_SIZE_PCT / 100:,.2f} USDT)\n"
            f"→ akan menurunkan avg entry\n\n"
            f"Reply <b>y</b> atau <b>n</b> ({Config.DCA_TIMEOUT}s timeout)"
        )

        logger.info(f"[DCA] Asking user for {sym}: ROI={roi_pct:.1f}% price={price_pct:.1f}%")
        answer = await notifier.ask_yes_no(question, Config.DCA_TIMEOUT)

        if answer is True:
            # Execute DCA
            leverage = pos.get("leverage", 5)
            dca_result = await executor.execute_dca(
                sym, action, leverage, Config.DCA_SIZE_PCT
            )
            if dca_result:
                # Update position in pnl_tracker
                pos["dca_count"] = pos.get("dca_count", 0) + 1
                old_notional = pos["notional"]
                dca_notional = account_balance * (Config.DCA_SIZE_PCT / 100.0) * leverage
                new_notional = old_notional + dca_notional

                # Average entry price
                dca_price = dca_result.get("price", current_price)
                new_entry = (
                    (entry * old_notional + dca_price * dca_notional) / new_notional
                )
                pos["entry_price"] = round(new_entry, 8)
                pos["notional"] = round(new_notional, 2)

                from utils.pnl_tracker import _save_store
                _save_store(pnl._store)

                # Update SL to -100% ROI from new avg entry (SL_MODE=roi)
                new_sl_price = None
                if Config.SL_MODE == "roi" and hasattr(executor, "update_sl_roi"):
                    lev = float(pos.get("leverage", leverage))
                    new_sl_price = await executor.update_sl_roi(
                        sym, action, new_entry, lev
                    )
                    if new_sl_price:
                        pos["stop_loss"] = new_sl_price
                        pos["original_sl"] = new_sl_price
                        from utils.pnl_tracker import _save_store
                        _save_store(pnl._store)

                sl_line = (
                    f"New SL: <code>{new_sl_price:,.6f}</code> (-100% ROI)\n"
                    if new_sl_price else ""
                )
                await notifier.send(
                    f"<b>DCA EXECUTED</b> {action} {sym}\n"
                    f"Added: +{Config.DCA_SIZE_PCT}%\n"
                    f"New avg entry: <code>{new_entry:,.4f}</code>\n"
                    f"{sl_line}"
                    f"New notional: <code>{new_notional:,.2f}</code> USDT"
                )
                logger.info(
                    f"[DCA] Executed {sym}: new_entry={new_entry:,.4f} "
                    f"notional={new_notional:,.2f} new_sl={new_sl_price}"
                )
            else:
                await notifier.send(f"[DCA] Order failed for {sym}")
        elif answer is False:
            # User reply N — set flag so we don't ask again for this position
            pos["dca_declined"] = True
            from utils.pnl_tracker import _save_store
            _save_store(pnl._store)
            logger.info(f"[DCA] User rejected DCA for {sym} — won't ask again")
            await notifier.send(f"DCA {sym} skipped (won't ask again for this position).")
        else:
            # Timeout — DON'T set flag, let it ask again next cycle
            logger.info(f"[DCA] Timeout for {sym}, will retry next cycle")
            await notifier.send(f"DCA {sym} timeout — will ask again next scan.")


# ── P3: Quick-Exit on Reversal ─────────────────────────────────────────────

async def _check_quick_exit(
    pnl: Any,
    market_data: List[Dict],
    executor: Any,
    notifier: Any,
    memory: Any,
) -> None:
    """
    Force close a position if technical signals reverse before SL/TP hit.

    Triggers (ALL must be true for safety):
      1. Position age >= QUICK_EXIT_MIN_HOURS (avoid premature exits)
      2. RSI flipped against the position direction:
           LONG  → RSI < QUICK_EXIT_RSI_LONG (e.g. < 35)
           SHORT → RSI > QUICK_EXIT_RSI_SHORT (e.g. > 65)
      3. Market structure flipped against position:
           LONG  → structure == "bearish"
           SHORT → structure == "bullish"
      4. Position is currently in PROFIT (avoid converting paper loss to real loss).
         This is a safety floor — quick-exit is meant to lock in gains, not panic.

    If all conditions met → close 100% via executor and record close.
    """
    from config import Config
    from utils.logger import logger

    if not getattr(Config, "QUICK_EXIT_ENABLED", False):
        return

    rsi_long_thr  = getattr(Config, "QUICK_EXIT_RSI_LONG", 35.0)
    rsi_short_thr = getattr(Config, "QUICK_EXIT_RSI_SHORT", 65.0)
    min_hours     = getattr(Config, "QUICK_EXIT_MIN_HOURS", 1.0)

    mkt_map = {d["symbol"]: d for d in market_data}

    for sym, pos in list(pnl._store.get("open_positions", {}).items()):
        mkt = mkt_map.get(sym)
        if not mkt:
            continue

        # 1. Age check (UTC-aware: open_time stored in UTC by _now_str)
        try:
            from datetime import timezone
            open_dt = datetime.strptime(pos.get("open_time", ""), "%Y-%m-%d %H:%M:%S")
            open_dt = open_dt.replace(tzinfo=timezone.utc)
            hours_held = (datetime.now(timezone.utc) - open_dt).total_seconds() / 3600.0
        except Exception:
            continue

        action = pos.get("action", "")
        entry  = float(pos.get("entry_price", 0))
        lev    = float(pos.get("leverage", 1))
        price  = float(mkt.get("current_price", 0))
        rsi    = float(mkt.get("rsi", 50))
        structure = str(mkt.get("market_structure", "")).lower()

        if entry <= 0 or price <= 0:
            continue

        if action == "LONG":
            price_pct = (price - entry) / entry * 100.0
        else:
            price_pct = (entry - price) / entry * 100.0
        roi_pct = price_pct * lev

        failed_setup_enabled = getattr(Config, "FAILED_SETUP_EXIT_ENABLED", True)
        failed_min_m = getattr(Config, "FAILED_SETUP_MIN_MINUTES", 5.0)
        failed_max_m = getattr(Config, "FAILED_SETUP_MAX_MINUTES", 30.0)
        failed_roi = getattr(Config, "FAILED_SETUP_MAX_ROI_LOSS_PCT", -10.0)
        minutes_held = hours_held * 60.0
        structure_supports = (
            (action == "LONG" and structure in ("uptrend", "breakout")) or
            (action == "SHORT" and structure in ("downtrend", "breakdown"))
        )
        if (
            failed_setup_enabled
            and failed_min_m <= minutes_held <= failed_max_m
            and roi_pct <= failed_roi
            and not structure_supports
        ):
            logger.info(
                f"  [FAILED-SETUP] {sym} {action} early reversal: "
                f"ROI={roi_pct:+.1f}% struct={structure} held={minutes_held:.0f}m"
            )

            live_ok = True
            actual_fill_px = 0.0
            if hasattr(executor, "partial_close"):
                try:
                    res = await executor.partial_close(sym, action, 100.0)
                    live_ok = bool(res) and not (res.get("dry_run") and not Config.DRY_RUN)
                    if res:
                        actual_fill_px = float(res.get("price", 0) or 0)
                except Exception as exc:
                    logger.error(f"  [FAILED-SETUP] {sym} executor close error: {exc}")
                    live_ok = False

            if not live_ok and not Config.DRY_RUN:
                logger.warning(
                    f"  [FAILED-SETUP] {sym} Bybit close FAILED -- keeping tracker open"
                )
                continue

            exit_px_use = actual_fill_px if actual_fill_px > 0 else price
            rec = pnl.record_close(sym, exit_px_use, reason="failed_setup_reversal")
            if rec:
                await notifier.send(
                    f"<b>Failed Setup Exit</b> {action} {sym}\n"
                    f"Struct: {structure}  Held: {minutes_held:.0f}m\n"
                    f"ROI: {roi_pct:+.1f}%\n"
                    f"PnL: <b>{rec['pnl_usdt']:+.4f}</b> USDT ({rec['pnl_pct']:+.2f}%)"
                )
                if memory:
                    memory.record_trade_result(
                        symbol=sym,
                        exit_price=exit_px_use,
                        pnl_usdt=rec["pnl_usdt"],
                        pnl_pct=rec.get("pnl_pct", 0),
                        reason="failed_setup_reversal",
                    )
            continue

        if hours_held < min_hours:
            continue

        # 2. RSI flip
        rsi_flip = False
        if action == "LONG"  and rsi < rsi_long_thr:
            rsi_flip = True
        if action == "SHORT" and rsi > rsi_short_thr:
            rsi_flip = True
        if not rsi_flip:
            continue

        # 3. Structure flip
        structure_flip = (
            (action == "LONG"  and "bear" in structure) or
            (action == "SHORT" and "bull" in structure)
        )
        if not structure_flip:
            continue

        # 4. Must be in profit (safety floor)
        if roi_pct <= 0:
            continue   # not yet profitable — let SL handle it

        logger.info(
            f"  [QUICK-EXIT] {sym} {action} reversal detected: "
            f"RSI={rsi:.0f} struct={structure} ROI={roi_pct:+.1f}% held={hours_held:.1f}h"
        )

        # Execute close
        live_ok = True
        actual_fill_px = 0.0
        if hasattr(executor, "partial_close"):
            res = await executor.partial_close(sym, action, 100.0)
            live_ok = bool(res)
            if res:
                actual_fill_px = float(res.get("price", 0) or 0)

        # Prefer actual VWAP fill from Bybit; fall back to market data price.
        exit_px_use = actual_fill_px if actual_fill_px > 0 else price
        rec = pnl.record_close(sym, exit_px_use, reason="quick_exit_reversal")
        if rec:
            await notifier.send(
                f"⚡ <b>Quick-Exit (Reversal)</b> {action} {sym}\n"
                f"RSI: {rsi:.0f}  Struct: {structure}\n"
                f"Held: {hours_held:.1f}h  ROI locked: {roi_pct:+.1f}%\n"
                f"PnL: <b>{rec['pnl_usdt']:+.4f}</b> USDT ({rec['pnl_pct']:+.2f}%)"
            )
            if memory:
                memory.record_trade_result(
                    symbol=sym,
                    exit_price=price,
                    pnl_usdt=rec["pnl_usdt"],
                    pnl_pct=rec.get("pnl_pct", 0),
                    reason="quick_exit_reversal",
                )

        if not live_ok:
            logger.warning(f"  [QUICK-EXIT] {sym} executor close FAILED — bot tracker still updated")


# ── Live Position Sync ─────────────────────────────────────────────────────

async def _sync_live_positions(
    executor: Any,
    pnl: Any,
    memory: Any = None,
    notifier: Any = None,
) -> None:
    """
    Reconcile pnl_store with actual Bybit positions.
    Called at startup and every scan in live mode.
    """
    from config import Config
    from utils.logger import logger

    if Config.DRY_RUN:
        return

    # Fetch actual open positions from Bybit
    bybit_open = await executor.get_open_positions()

    # Fetch closed PnL for symbols we think are still open
    closed_pnl: List[Dict] = []
    store_syms = list(pnl._store["open_positions"].keys())
    bybit_syms = {p.get("symbol", "") for p in bybit_open}

    for sym in store_syms:
        if sym not in bybit_syms:
            # This position might have been closed — fetch its close data
            try:
                c = await executor.get_closed_pnl(sym, limit=5)
                closed_pnl.extend(c)
            except Exception:
                pass

    # Run sync
    sync_result = pnl.sync_positions(bybit_open, closed_pnl)

    added = sync_result.get("added", [])
    updated = sync_result.get("updated", [])
    closed = sync_result.get("closed", [])

    if added or updated or closed:
        logger.info(
            f"[Sync] Result: added={added} updated={updated} closed={closed}"
        )

        # ── SAFETY: attach SL to orphan synced positions ──
        # If sync added a position with no SL on Bybit (needs_sl_attach flag),
        # immediately set SL at -100% ROI from current entry.
        # This prevents TRUMPUSDT-style liquidation (-160% ROI) where bot has
        # no awareness of position and Bybit has no protective stop.
        for sym in added:
            pos = pnl._store["open_positions"].get(sym, {})
            if pos.get("needs_sl_attach") and hasattr(executor, "update_sl_roi"):
                action = pos.get("action", "")
                entry  = float(pos.get("entry_price", 0))
                lev    = float(pos.get("leverage", 1))
                logger.warning(
                    f"[Sync][SAFETY] {sym} has NO SL on Bybit — "
                    f"attaching ROI-based SL @ entry={entry:.6f} lev={lev}x"
                )
                try:
                    new_sl = await executor.update_sl_roi(sym, action, entry, lev)
                    if new_sl:
                        pos["stop_loss"] = new_sl
                        pos["original_sl"] = new_sl
                        pos["needs_sl_attach"] = False
                        from utils.pnl_tracker import _save_store
                        _save_store(pnl._store)
                        logger.info(
                            f"[Sync][SAFETY] {sym} SL attached @ {new_sl:.6f}"
                        )
                        if notifier:
                            await notifier.send(
                                f"⚠️ <b>SL Auto-Attached</b> {action} {sym}\n"
                                f"Posisi sync dari Bybit tanpa SL → "
                                f"set ke <code>{new_sl:.6f}</code> (-100% ROI)"
                            )
                except Exception as exc:
                    logger.error(f"[Sync][SAFETY] {sym} SL attach failed: {exc}")

        # Notify via Telegram
        if notifier:
            parts = []
            if added:
                parts.append(f"Added: {', '.join(added)}")
            if updated:
                parts.append(f"Updated: {', '.join(updated)}")
            if closed:
                parts.append(f"Closed: {', '.join(closed)}")
            await notifier.send(
                f"<b>Position Sync</b>\n" + "\n".join(parts)
            )

        # Record closed positions in trade memory
        if memory and closed:
            for sym in closed:
                closed_trade = next(
                    (t for t in reversed(pnl._store["closed_trades"])
                     if t["symbol"] == sym),
                    None,
                )
                if closed_trade:
                    memory.record_trade_result(
                        symbol=sym,
                        exit_price=closed_trade.get("exit_price", 0),
                        pnl_usdt=closed_trade.get("pnl_usdt", 0),
                        pnl_pct=closed_trade.get("pnl_pct", 0),
                        reason=closed_trade.get("reason", "closed_on_exchange"),
                    )


# ── Phase 3: Adaptive Rules ────────────────────────────────────────────────

def _apply_adaptive_rules(
    signal: Dict,
    market_snapshot: Dict,
    adaptive_params: Dict,
    memory: Any,
) -> Dict:
    """
    Phase 3 post-processing — applied to every LONG/SHORT signal before execution.

    Rules:
    1. Dynamic SL width — widen SL if tighter than volatility warrants.
       low_vol  (atr < 1%)  : min SL = 1.0x ATR
       med_vol  (1-3%)      : min SL = 1.5x ATR
       high_vol (> 3%)      : min SL = 2.0x ATR

    2. Leverage cap — hard ceiling per adaptive mode, then apply lev_mult.
       defensive    : max 3x
       conservative : max 5x
       normal/aggr  : max 10x (AI suggestion preserved)

    3. Symbol performance penalty — reduce size by 30% when symbol WR < 40%
       (requires ≥3 completed trades in memory for that symbol).
    """
    from utils.logger import logger
    from config import Config as _Cfg

    sym = signal.get("symbol", "?")
    action = signal.get("action", "HOLD")
    entry = float(signal.get("entry_price", 0))
    sl = float(signal.get("stop_loss", 0))
    leverage = int(signal.get("leverage", 5))
    size_pct = float(signal.get("position_size_percent", 2.0))
    atr_pct = float(market_snapshot.get("atr_pct", 1.5))

    # ── 1. Dynamic SL width ───────────────────────────────────────
    if entry > 0 and sl > 0 and atr_pct > 0:
        current_sl_dist_pct = abs(entry - sl) / entry * 100
        if atr_pct < 1.0:
            min_sl_dist_pct = atr_pct * 1.0
        elif atr_pct < 3.0:
            min_sl_dist_pct = atr_pct * 1.5
        else:
            min_sl_dist_pct = atr_pct * 2.0

        # Hard floor: never accept SL tighter than MIN_SL_DISTANCE_PCT
        # (prevents fee-bleed + noise stopouts on low-vol coins)
        min_sl_dist_pct = max(min_sl_dist_pct, _Cfg.MIN_SL_DISTANCE_PCT)

        if current_sl_dist_pct < min_sl_dist_pct:
            new_sl_dist = min_sl_dist_pct / 100.0 * entry
            if action == "LONG":
                new_sl = round(entry - new_sl_dist, 8)
            else:
                new_sl = round(entry + new_sl_dist, 8)
            signal["stop_loss"] = new_sl
            logger.info(
                f"  [P3-SL] {sym} SL widened "
                f"{current_sl_dist_pct:.2f}% → {min_sl_dist_pct:.2f}% "
                f"(ATR={atr_pct:.2f}%)"
            )

    # ── 2. Leverage cap (range: 20-50x) ───────────────────────────
    # Floor 20x (user request — minimum leverage), cap by adaptive mode.
    # Adaptive mult still scales down in high-loss regimes but never below floor.
    mode = adaptive_params.get("mode", "normal")
    lev_mult = adaptive_params.get("max_leverage_mult", 1.0)
    lev_hard_caps = {
        "defensive": int(getattr(_Cfg, "LEVERAGE_CAP_DEFENSIVE", 30)),
        "conservative": int(getattr(_Cfg, "LEVERAGE_CAP_CONSERVATIVE", 40)),
        "normal": int(getattr(_Cfg, "LEVERAGE_CAP_NORMAL", 45)),
        "aggressive": int(getattr(_Cfg, "LEVERAGE_CAP_AGGRESSIVE", 50)),
    }
    lev_cap   = lev_hard_caps.get(mode, int(getattr(_Cfg, "LEVERAGE_CAP_NORMAL", 45)))
    lev_floor = int(getattr(_Cfg, "LEVERAGE_FLOOR", 30))
    lev_floor = min(lev_floor, lev_cap)

    adjusted_lev = max(lev_floor, min(leverage, int(leverage * lev_mult), lev_cap))
    if adjusted_lev != leverage:
        signal["leverage"] = adjusted_lev
        logger.info(
            f"  [P3-LEV] {sym} leverage {leverage}x → {adjusted_lev}x "
            f"(mode={mode}, mult={lev_mult:.1f}x, floor={lev_floor}x, cap={lev_cap}x)"
        )

    # ── 3. Symbol performance penalty ────────────────────────────
    if memory:
        sym_stats = memory.get_symbol_stats(sym)
        if sym_stats and sym_stats.get("trades", 0) >= 3:
            t = sym_stats["trades"]
            w = sym_stats["wins"]
            sym_wr = w / t * 100 if t > 0 else 0
            if sym_wr < 40:
                penalized = round(size_pct * 0.7, 2)
                signal["position_size_percent"] = penalized
                logger.info(
                    f"  [P3-SYM] {sym} size {size_pct:.1f}% → {penalized:.1f}% "
                    f"(WR={sym_wr:.0f}% < 40% over {t} trades)"
                )

    return signal


# ── Dynamic symbol refresh ─────────────────────────────────────────────────

def _passes_smc_retest_filter(signal: Dict, market_snapshot: Dict) -> bool:
    """Reject late chase entries using objective pullback/retest + SMC context."""
    from utils.logger import logger
    from config import Config

    sym = signal.get("symbol", "?")
    action = signal.get("action", "HOLD")
    if action not in ("LONG", "SHORT"):
        return True

    mode = Config.SMC_FILTER_MODE
    if mode == "off":
        return True
    if mode not in ("soft", "strict"):
        logger.warning(f"  [SMC-CONFIG] Unknown SMC_FILTER_MODE={mode!r}; using soft")
        mode = "soft"

    rsi = float(market_snapshot.get("rsi", 50) or 50)
    atr_pct = float(market_snapshot.get("atr_pct", 1.5) or 1.5)
    ema_dist = float(market_snapshot.get("ema20_distance_pct", 0.0) or 0.0)
    premium_discount = str(market_snapshot.get("premium_discount", "equilibrium"))
    structure = str(market_snapshot.get("market_structure", "sideways"))
    volume_trend = str(market_snapshot.get("volume_trend", "flat"))
    htf_bias = str(market_snapshot.get("htf_bias", "neutral"))
    h1_ema_trend = str(market_snapshot.get("h1_ema_trend", "neutral"))
    score = float(signal.get("score", 0.0) or 0.0)
    conf = float(signal.get("confidence", 0.0) or 0.0)

    swept_high = bool(market_snapshot.get("swept_prev_high", False))
    swept_low = bool(market_snapshot.get("swept_prev_low", False))
    bos_bull = bool(market_snapshot.get("bos_bullish", False))
    bos_bear = bool(market_snapshot.get("bos_bearish", False))
    near_support = bool(market_snapshot.get("near_support", False))
    near_resistance = bool(market_snapshot.get("near_resistance", False))
    long_retest = bool(market_snapshot.get("long_retest_zone", False))
    short_retest = bool(market_snapshot.get("short_retest_zone", False))

    strong_soft_signal = (
        score >= Config.SMC_SOFT_BYPASS_SCORE
        and conf >= Config.SMC_SOFT_BYPASS_CONFIDENCE
    )
    trend_aligned_long = (
        structure in ("uptrend", "breakout")
        and htf_bias != "bearish"
        and h1_ema_trend != "bearish"
    )
    trend_aligned_short = (
        structure in ("downtrend", "breakdown")
        and htf_bias != "bullish"
        and h1_ema_trend != "bullish"
    )

    def _reject(tag: str, msg: str, *, hard: bool = False) -> bool:
        if mode == "soft" and not hard and strong_soft_signal:
            logger.warning(
                f"  [{tag}-SOFT] {sym} {msg} -- allow high-conviction "
                f"(score={score:.1f}, conf={conf:.2f})"
            )
            return True
        suffix = ""
        if mode == "soft" and not hard:
            suffix = (
                f" (soft bypass needs score>={Config.SMC_SOFT_BYPASS_SCORE:.1f} "
                f"and conf>={Config.SMC_SOFT_BYPASS_CONFIDENCE:.2f})"
            )
        logger.warning(f"  [{tag}] {sym} {msg} -- skip{suffix}")
        return False

    strict_extension_pct = max(atr_pct * 1.2, 2.0)
    soft_extension_pct = max(atr_pct * 2.0, 3.0)
    if ema_dist > strict_extension_pct:
        hard_extension = mode == "strict" or ema_dist > soft_extension_pct
        if not _reject(
            "SMC-LATE",
            f"{action} price {ema_dist:.2f}% from EMA20 > "
            f"{strict_extension_pct:.2f}%",
            hard=hard_extension,
        ):
            return False

    if mode == "soft":
        if action == "LONG" and structure == "breakout" and volume_trend != "rising" and not long_retest:
            if not _reject(
                "SMC-BREAKOUT",
                "LONG breakout without rising volume/retest",
                hard=True,
            ):
                return False
        if action == "SHORT" and structure == "breakdown" and volume_trend != "rising" and not short_retest:
            if not _reject(
                "SMC-BREAKDOWN",
                "SHORT breakdown without rising volume/retest",
                hard=True,
            ):
                return False

    if mode == "soft":
        if action == "LONG" and trend_aligned_long and not (rsi > 74 and not swept_low):
            return True
        if action == "SHORT" and trend_aligned_short and not (rsi < 26 and not swept_high):
            return True

    if action == "LONG":
        has_context = long_retest or swept_low or bos_bull
        if not has_context:
            if not _reject("SMC", "LONG lacks retest/sweep/BOS context"):
                return False
        if premium_discount == "premium" and not swept_low and not long_retest:
            if not _reject("SMC-PD", "LONG in premium without sweep/retest"):
                return False
        if rsi > 68 and not swept_low:
            if not _reject("SMC-LATE", f"LONG RSI={rsi:.1f} without sell-side sweep"):
                return False
        if near_resistance and not bos_bull:
            if not _reject("SMC-SR", "LONG near resistance without bullish BOS"):
                return False
        if structure == "breakout" and volume_trend != "rising" and not long_retest:
            if not _reject("SMC-BREAKOUT", "LONG breakout without rising volume/retest"):
                return False

    if action == "SHORT":
        has_context = short_retest or swept_high or bos_bear
        if not has_context:
            if not _reject("SMC", "SHORT lacks retest/sweep/BOS context"):
                return False
        if premium_discount == "discount" and not swept_high and not short_retest:
            if not _reject("SMC-PD", "SHORT in discount without sweep/retest"):
                return False
        if rsi < 32 and not swept_high:
            if not _reject("SMC-LATE", f"SHORT RSI={rsi:.1f} without buy-side sweep"):
                return False
        if near_support and not bos_bear:
            if not _reject("SMC-SR", "SHORT near support without bearish BOS"):
                return False
        if structure == "breakdown" and volume_trend != "rising" and not short_retest:
            if not _reject("SMC-BREAKDOWN", "SHORT breakdown without rising volume/retest"):
                return False

    return True

async def _refresh_symbols(pnl: Any) -> bool:
    """
    Fetch top-N symbols by volume and update Config.SYMBOLS in-place.
    Always retains symbols with open positions so SL/TP monitoring is never lost.
    Returns True if the list changed.
    """
    from config import Config
    from data.market_feed import fetch_top_symbols
    from utils.logger import logger

    try:
        new_syms = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: fetch_top_symbols(
                limit=Config.AUTO_SYMBOLS_COUNT,
                min_turnover_usdt=Config.AUTO_SYMBOLS_MIN_TURNOVER_USD,
                blacklist=Config.SYMBOL_BLACKLIST,
                data_only=Config.DATA_ONLY_SYMBOLS,
            )
        )
    except Exception as exc:
        logger.error(f"[SymbolFeed] Refresh failed: {exc}")
        return False

    if not new_syms:
        logger.warning("[SymbolFeed] Empty result — keeping current symbol list")
        return False

    # Always keep symbols with open positions
    open_syms = set(pnl._store.get("open_positions", {}).keys())
    for s in open_syms:
        if s not in new_syms:
            new_syms.append(s)
            logger.info(f"[SymbolFeed] Retained {s} (open position)")

    old_set = set(Config.SYMBOLS)
    new_set = set(new_syms)
    added   = new_set - old_set
    removed = old_set - new_set

    Config.SYMBOLS = new_syms

    if added or removed:
        logger.info(
            f"[SymbolFeed] Updated: {len(new_syms)} symbols  "
            f"(+{len(added)} added, -{len(removed)} removed)"
        )
        if added:
            logger.info(f"[SymbolFeed]   Added  : {sorted(added)[:20]}")
        if removed:
            logger.info(f"[SymbolFeed]   Removed: {sorted(removed)[:20]}")
        return True

    logger.info(f"[SymbolFeed] No change ({len(new_syms)} symbols)")
    return False


# ── Agent loop ─────────────────────────────────────────────────────────────

async def run_agent(feed: Any, engine: Any, executor: Any,
                    notifier: Any, pnl: Any, memory: Any = None,
                    onchain: Any = None) -> None:
    from utils.logger import logger
    from config import Config

    scan_count = 0
    account_balance = Config.ACCOUNT_BALANCE
    _last_symbol_refresh: float = 0.0   # epoch seconds

    while True:
        import time as _time
        scan_count += 1
        logger.info(f"SCAN #{scan_count:04d} starting ...")

        # ── Auto-refresh symbol list ──────────────────────────────────────
        if Config.AUTO_SYMBOLS:
            refresh_interval = Config.AUTO_SYMBOLS_REFRESH_H * 3600
            if _time.time() - _last_symbol_refresh >= refresh_interval:
                await _refresh_symbols(pnl)
                _last_symbol_refresh = _time.time()

        # Fetch real balance in live mode
        if not Config.DRY_RUN and hasattr(executor, "get_balance"):
            real_balance = await executor.get_balance()
            if real_balance is not None and real_balance > 0:
                account_balance = real_balance
                pnl.account_balance = account_balance

        # 1. Fetch market data
        # When all position slots are full, only fetch symbols with open positions
        # (needed for SL/TP monitoring). Skip the full universe to save API calls.
        market_data: List[Dict] = []
        data_sources: Dict[str, str] = {}

        open_syms_now  = set(pnl._store.get("open_positions", {}).keys())
        slots_available = Config.MAX_OPEN_POSITIONS - len(open_syms_now)

        if slots_available <= 0 and Config.AUTO_SYMBOLS:
            # Full — only scan open positions
            scan_symbols = list(open_syms_now) if open_syms_now else Config.SYMBOLS[:1]
            logger.info(
                f"[SymbolFeed] Slots full — scanning only {len(scan_symbols)} "
                f"open position(s) for SL/TP monitoring"
            )
        else:
            # Whitelist + ALWAYS include open positions (even if not in whitelist).
            # Without this, positions opened on now-removed symbols (e.g. ZECUSDT
            # after whitelist update) would have current_price=0 in dashboard
            # and SL/TP/trail/time-exit logic would never fire (no market_data).
            #
            # Data-only symbols (mis. BTCUSDT) di-fetch untuk konteks AI
            # (BTC global bias) tapi DI-EKSEKUSI sebagai HOLD di entry loop
            # via DATA_ONLY_SYMBOLS check. Prevent BTC trade tapi tetap suplai
            # bias data ke prompt.
            whitelist = [s.strip() for s in Config.SYMBOLS if s.strip()]
            data_only = [s.strip() for s in Config.DATA_ONLY_SYMBOLS if s.strip()]
            scan_symbols = list(dict.fromkeys(
                whitelist + data_only + list(open_syms_now)
            ))
            orphans = open_syms_now - set(whitelist) - set(data_only)
            if orphans:
                logger.info(
                    f"[SymbolFeed] Including {len(orphans)} non-whitelist "
                    f"open position(s) for monitoring: {sorted(orphans)}"
                )
            if data_only:
                logger.debug(
                    f"[SymbolFeed] Data-only (no trade): {data_only}"
                )

        tasks = {s: feed.fetch_symbol_data(s) for s in scan_symbols}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for symbol, result in zip(tasks.keys(), results):
            if isinstance(result, Exception) or result is None:
                continue
            data_sources[result["symbol"]] = result.get("_source", "?")
            logger.info(
                f"  OK  {result['symbol']:<14s} "
                f"price={result['current_price']:>14,.4f}  "
                f"RSI={result['rsi']:5.1f}  "
                f"vol={result['volume_condition']:6s}  "
                f"struct={result['market_structure']}"
            )
            market_data.append(result)

        if not market_data:
            logger.warning("No market data this cycle.")
            stats = pnl.get_stats()
            stats["unrealized_pnl"] = 0.0
            print_dashboard(
                scan_count, [], 0, account_balance,
                Config.EXCHANGE_MODE, Config.DRY_RUN, Config.SCAN_INTERVAL,
                {}, stats, [], pnl.get_recent_trades()
            )
            await asyncio.sleep(Config.SCAN_INTERVAL)
            continue

        logger.info(f"Fetched {len(market_data)}/{len(tasks)} symbols")

        # 1b. Live position sync — reconcile pnl_store with Bybit
        if not Config.DRY_RUN:
            await _sync_live_positions(executor, pnl, memory, notifier)

        # 2. Check SL/TP/Trailing for positions (dry-run AND live)
        closed_trades = pnl.check_sl_tp(market_data)
        for trade in closed_trades:
            reason = trade.get("reason", "unknown")
            sym   = trade.get("symbol", "?")
            act   = trade.get("action", "")

            # ── Time-exit: ASK USER via Telegram (no auto-close) ──
            # Triggered when LOSING position held >= MAX_HOLD or STAGNANT.
            # User decides y/n. Y=close, N=cooldown 6h re-prompt, timeout=re-ask.
            # SL/Trail/TP/DCA logic still active independently.
            if trade.get("requires_user_decision"):
                hh = trade.get("hours_held", 0)
                roi_at = trade.get("roi_at_exit", 0)
                exit_px = float(trade.get("exit_price", 0))
                pos = pnl._store["open_positions"].get(sym)
                if not pos:
                    continue   # position gone (closed by other path) — skip

                label = "TIME-EXIT" if reason == "time_exit_max" else "STAGNANT"

                question = (
                    f"⏱ <b>{label} {act} {sym}?</b>\n\n"
                    f"Held: <b>{hh}h</b>  ROI: <b>{roi_at:+.1f}%</b>\n"
                    f"Reason: <code>{reason}</code>\n"
                    f"Current price: <code>{exit_px:.6f}</code>\n"
                    f"Entry: <code>{pos.get('entry_price', 0):.6f}</code>\n\n"
                    f"<b>Close 100% sekarang?</b>\n"
                    f"Reply <b>y</b> = close | <b>n</b> = hold (re-prompt 6h lagi)\n"
                    f"Timeout 120s = re-ask scan berikutnya"
                )

                logger.info(
                    f"  [USER-DECISION] {sym} {label} — asking user "
                    f"(held={hh}h ROI={roi_at:+.1f}%)"
                )
                answer = await notifier.ask_yes_no(question, 120)

                if answer is True:
                    # User says close
                    logger.info(f"  [USER-DECISION] {sym} {label} — user CLOSE confirmed")
                    live_ok = True
                    actual_fill_px = 0.0
                    if hasattr(executor, "partial_close"):
                        try:
                            res = await executor.partial_close(sym, act, 100.0)
                            live_ok = bool(res) and not (res.get("dry_run") and not Config.DRY_RUN)
                            if res:
                                actual_fill_px = float(res.get("price", 0) or 0)
                        except Exception as exc:
                            logger.error(f"  [USER-DECISION] {sym} executor close error: {exc}")
                            live_ok = False
                    if live_ok or Config.DRY_RUN:
                        # Prefer actual VWAP fill from Bybit; fall back to market data price.
                        exit_px_use = actual_fill_px if actual_fill_px > 0 else exit_px
                        rec = pnl.record_close(sym, exit_px_use, reason=reason)
                        if rec:
                            await notifier.send(
                                f"✅ <b>Closed by user</b> {act} {sym}\n"
                                f"Held: {hh}h  ROI: {roi_at:+.1f}%\n"
                                f"PnL: <b>{rec['pnl_usdt']:+.4f}</b> USDT ({rec['pnl_pct']:+.2f}%)"
                            )
                            if memory:
                                memory.record_trade_result(
                                    symbol=sym, exit_price=exit_px,
                                    pnl_usdt=rec["pnl_usdt"], pnl_pct=rec.get("pnl_pct", 0),
                                    reason=reason,
                                )
                elif answer is False:
                    # User says hold — set cooldown 6h before re-prompt
                    import time as _time
                    pos["time_exit_declined_at"] = _time.time()
                    from utils.pnl_tracker import _save_store
                    _save_store(pnl._store)
                    logger.info(
                        f"  [USER-DECISION] {sym} {label} — user HOLD, "
                        f"re-prompt in 6h"
                    )
                    await notifier.send(
                        f"⏸ <b>{label} {sym}</b> — hold per user. "
                        f"Re-prompt in 6h."
                    )
                else:
                    # Timeout — don't set flag, will re-ask next scan
                    logger.info(
                        f"  [USER-DECISION] {sym} {label} — timeout, re-ask next scan"
                    )
                    await notifier.send(
                        f"⏱ <b>{label} {sym}</b> timeout — re-ask next scan."
                    )
                continue

            # ── Generic full-close events (SL, Trail, TP3) ──
            # IMPORTANT: order matters. Close on Bybit FIRST, then record in tracker.
            # If Bybit close fails, do NOT update tracker (prevents desync where
            # bot says closed but Bybit position lingers).
            if trade.get("requires_close"):
                pct = float(trade.get("close_pct", 100.0))
                exit_px = float(trade.get("exit_price", 0))

                # Pretty label per reason
                label_map = {
                    "stop_loss":         "SL-HIT",
                    "trailing_stop":     "TRAIL-HIT",
                    "take_profit":       "TP3-HIT",
                    "time_exit_max":     "TIME-EXIT",
                    "time_exit_stagnant":"STAGNANT",
                }
                label = label_map.get(reason, reason.upper())
                hh = trade.get("hours_held", 0)
                roi_at = trade.get("roi_at_exit", 0)

                logger.info(
                    f"  [{label}] {sym} {reason} — sending close order to Bybit ({pct:.0f}%)"
                )

                # 1) Send the actual close order to Bybit FIRST
                live_ok = True
                actual_fill_px = 0.0
                if hasattr(executor, "partial_close"):
                    try:
                        res = await executor.partial_close(sym, act, pct)
                        live_ok = bool(res) and not (res.get("dry_run") and not Config.DRY_RUN)
                        if res:
                            actual_fill_px = float(res.get("price", 0) or 0)
                    except Exception as exc:
                        logger.error(f"  [{label}] {sym} executor close error: {exc}")
                        live_ok = False

                # 2) ONLY update tracker if Bybit close succeeded (or in DRY_RUN)
                if not live_ok and not Config.DRY_RUN:
                    logger.warning(
                        f"  [{label}] {sym} Bybit close FAILED — keeping position in tracker. "
                        f"Will retry next scan."
                    )
                    continue   # do NOT call record_close — let next scan retry

                # 3) Record close in tracker
                # Prefer actual VWAP fill from Bybit; fall back to market data price.
                exit_px_use = actual_fill_px if actual_fill_px > 0 else exit_px
                rec = pnl.record_close(sym, exit_px_use, reason=reason)
                if rec:
                    if hh:
                        rec["hours_held"] = hh
                        rec["roi_at_exit"] = roi_at
                    icon = {"stop_loss":"🛑", "trailing_stop":"📉", "take_profit":"🎯",
                            "time_exit_max":"⏱", "time_exit_stagnant":"⏱"}.get(reason, "✓")
                    msg_extra = (
                        f"Held: {hh}h  ROI: {roi_at:+.1f}%\n" if hh else ""
                    )
                    await notifier.send(
                        f"{icon} <b>{label}</b> {act} {sym}\n"
                        f"Reason: <code>{reason}</code>\n"
                        f"{msg_extra}"
                        f"PnL: <b>{rec['pnl_usdt']:+.4f}</b> USDT ({rec['pnl_pct']:+.2f}%)"
                    )
                    if memory:
                        memory.record_trade_result(
                            symbol=sym,
                            exit_price=exit_px,
                            pnl_usdt=rec["pnl_usdt"],
                            pnl_pct=rec.get("pnl_pct", 0),
                            reason=reason,
                        )
                continue

            # ── P2: Partial-close events at TP1/TP2 ──
            if trade.get("requires_partial_close"):
                pct = float(trade.get("close_pct", 30.0))
                exit_px = float(trade.get("exit_price", 0))
                logger.info(f"  [PARTIAL-TP] {sym} {reason} — closing {pct:.0f}% @ {exit_px:.4f}")
                # 1) Live close on exchange (Bybit accepts reduceOnly partial)
                if hasattr(executor, "partial_close"):
                    res = await executor.partial_close(sym, act, pct)
                    if not res:
                        logger.warning(f"  [PARTIAL-TP] {sym} executor close failed")
                # 2) Record partial in pnl tracker (books realized PnL for the slice)
                rec = pnl.partial_close_record(sym, exit_px, pct, reason=reason)
                if rec:
                    tp_label = "TP1" if reason.startswith("tp1") else "TP2"
                    await notifier.send(
                        f"💰 <b>{tp_label} Partial Close</b> {act} {sym}\n"
                        f"Closed: {pct:.0f}% @ <code>{exit_px:.4f}</code>\n"
                        f"PnL: <b>{rec['pnl_usdt']:+.4f}</b> USDT ({rec['pnl_pct']:+.2f}%)\n"
                        f"Remaining: {rec.get('remaining_pct_after', 0)*100:.0f}%"
                    )
                continue

            # ── Milestone events (BE activated, SL advanced) ──
            # These don't close the position — just send Telegram notification
            if trade.get("milestone"):
                sym = trade["symbol"]
                act = trade["action"]
                if reason == "be_activated":
                    trail = trade.get("trail_pct", 0)
                    logger.info(f"  [BE] {sym} SL→entry, trail={trail:.1f}%")
                    await notifier.send(
                        f"🔒 <b>Breakeven Activated</b>\n"
                        f"{act} {sym}\n"
                        f"SL moved to entry price\n"
                        f"Trailing: {trail:.1f}% (ATR-based)"
                    )
                elif reason == "sl_advanced":
                    new_sl = trade.get("new_sl", 0)
                    trail = trade.get("trail_pct", 0)
                    logger.info(f"  [SL+] {sym} SL→TP1 @ {new_sl:.4f}, trail={trail:.1f}%")
                    await notifier.send(
                        f"📈 <b>SL Advanced to TP1</b>\n"
                        f"{act} {sym}\n"
                        f"SL → {new_sl:.4f}\n"
                        f"Trail tightened: {trail:.1f}%"
                    )
                continue

            # ── Real close events ─────────────────────────────
            partial = trade.get("partial", False)
            label = "PARTIAL" if partial else "CLOSED"
            logger.info(
                f"  [{label}] {trade['symbol']} {reason}: "
                f"PnL={trade['pnl_usdt']:+.4f} USDT"
            )
            await notifier.alert_pnl_close(trade)

            # Record trade result in memory
            if memory and not partial:
                memory.record_trade_result(
                    symbol=trade["symbol"],
                    exit_price=trade.get("exit_price", 0),
                    pnl_usdt=trade["pnl_usdt"],
                    pnl_pct=trade.get("pnl_pct", 0),
                    reason=reason,
                )

        # 2b. Telegram command processing (close %, positions, help)
        await _process_telegram_commands(notifier, executor, pnl)

        # 2c. P3: Quick-Exit on Reversal — close profitable positions if signals flip
        if Config.QUICK_EXIT_ENABLED and pnl._store["open_positions"]:
            await _check_quick_exit(pnl, market_data, executor, notifier, memory)

        # 2d. DCA check for open positions
        if Config.DCA_ENABLED and pnl._store["open_positions"]:
            await _check_dca(
                pnl, market_data, executor, notifier, memory, account_balance
            )

        # 3. Position capacity (pnl_store is source of truth after sync)
        already_open_symbols = set(pnl._store["open_positions"].keys())
        open_count = len(already_open_symbols)

        available = Config.MAX_OPEN_POSITIONS - open_count
        logger.info(
            f"Positions: {open_count}/{Config.MAX_OPEN_POSITIONS} "
            f"({available} slot free)"
            + (f"  open={list(already_open_symbols)}" if already_open_symbols else "")
        )

        # 4. AI Analysis — skip when all slots full OR circuit breaker hit
        signals: List[Dict] = []
        executed_count = 0
        adaptive_params = {"mode": "normal"}

        # Pre-check: daily loss circuit breaker. If hit, no point calling AI
        # (any signal it generates will be rejected). Save tokens + latency.
        circuit_breaker_hit = False
        if Config.DAILY_LOSS_LIMIT_USDT > 0:
            _daily_loss_now = _daily_realized_loss(pnl)
            if abs(_daily_loss_now) >= Config.DAILY_LOSS_LIMIT_USDT:
                circuit_breaker_hit = True
                logger.warning(
                    f"[CIRCUIT BREAKER] Daily realized loss "
                    f"{_daily_loss_now:.4f} USDT >= limit "
                    f"-{Config.DAILY_LOSS_LIMIT_USDT:.2f} — "
                    f"SKIP AI scan (no entry possible). Resets at UTC midnight."
                )

        if available <= 0:
            logger.info(
                f"All {Config.MAX_OPEN_POSITIONS} slots full — "
                "skip AI scan (monitoring SL/TP/DCA only)"
            )
        elif circuit_breaker_hit:
            pass   # already logged above; just skip AI scan
        else:
            # Generate learning context and adaptive params
            learning_context = ""
            if memory and Config.AI_LEARNING:
                symbols_list = [m.get("symbol", "") for m in market_data]
                learning_context = memory.generate_learning_context(symbols_list)
                adaptive_params = memory.get_adaptive_params(account_balance)

                if learning_context:
                    logger.info(f"[Memory] Learning context: {len(learning_context)} chars")
                if adaptive_params["mode"] != "normal":
                    logger.info(
                        f"[Memory] Adaptive mode: {adaptive_params['mode']} "
                        f"(risk={adaptive_params['risk_multiplier']:.1f}x, "
                        f"min_score={adaptive_params['min_score']:.1f}, "
                        f"lev={adaptive_params['max_leverage_mult']:.1f}x) "
                        f"reason={adaptive_params.get('reason', '')}"
                    )

            # Fetch on-chain regime data (cached 5 min, non-blocking on failure)
            onchain_context = ""
            if onchain is not None:
                try:
                    _sm_syms = Config.SYMBOLS[:10]
                    oc_data = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: onchain.fetch(
                            trading_mode=Config.TRADING_MODE,
                            symbols=_sm_syms,
                        )
                    )
                    from data.onchain_feed import OnChainFeed
                    onchain_context = OnChainFeed.format_for_prompt(
                        oc_data, trading_mode=Config.TRADING_MODE
                    )
                    logger.info(
                        f"[OnChain] regime={oc_data.get('regime')}  "
                        f"SmartMoney={oc_data.get('pos_whale_bias')}  "
                        f"liq={oc_data.get('liq_signal')}  "
                        f"composite={oc_data.get('composite')}  "
                        f"F&G={oc_data.get('fng')}"
                    )
                except Exception as exc:
                    logger.warning(f"[OnChain] Skipped (error: {exc})")

            logger.info(f"Sending {len(market_data)} symbol(s) to AI ...")
            ai_result = await engine.analyze(
                market_data, account_balance,
                learning_context=learning_context,
                adaptive_params=adaptive_params,
                onchain_context=onchain_context,
            )

            if ai_result is None:
                logger.error("AI returned no result.")
            else:
                signals, best_candidate = extract_signals(ai_result)
                logger.info(f"Signals: {len(signals)}  Best: {best_candidate}")

                # Sort: best_candidate first, then by score descending
                def _signal_priority(s):
                    sym = s.get("symbol", "")
                    is_best = 1 if sym == best_candidate else 0
                    score = float(s.get("score", 0))
                    return (-is_best, -score)
                signals.sort(key=_signal_priority)

                for signal in signals:
                    action = signal.get("action", "HOLD")
                    sym    = signal.get("symbol", "?")

                    await notifier.alert_signal(signal)

                    if action == "HOLD":
                        _tier = signal.get('_ai_tier', '?')
                        logger.info(
                            f"  HOLD  {sym:<14s} "
                            f"score={float(signal.get('score',0)):.1f}  "
                            f"conf={float(signal.get('confidence',0))*100:.0f}%  "
                            f"[{_tier}]  {signal.get('reason','')}"
                        )
                        continue

                    # Skip symbols already in an open position
                    if sym in already_open_symbols:
                        logger.info(f"  Already open -- skip {sym}")
                        continue

                    # ── HARD BLACKLIST ───────────────────────────
                    # Reject simbol di SYMBOL_BLACKLIST (.env) — historical loser
                    # yang tidak boleh masuk lagi meski lolos quality gate.
                    _sym_norm = sym.replace("/", "").replace(":USDT", "").upper()
                    if _sym_norm in Config.SYMBOL_BLACKLIST:
                        logger.warning(
                            f"  [BLACKLIST] {sym} di SYMBOL_BLACKLIST -- skip"
                        )
                        continue

                    # ── DATA-ONLY ────────────────────────────────
                    # Simbol ini di-fetch hanya untuk konteks AI (mis. BTC
                    # global bias di prompt). Bot dilarang masuk posisi.
                    if _sym_norm in Config.DATA_ONLY_SYMBOLS:
                        logger.info(
                            f"  [DATA-ONLY] {sym} fetched untuk konteks AI -- no trade"
                        )
                        continue

                    # ── 4 Risk Detection Filters (HARD reject) ───
                    # Bot menolak signal walaupun AI mengeluarkan LONG/SHORT
                    # — sebagai backstop kalau AI mengabaikan prompt.
                    # Auto-block symbols that memory shows are persistently weak.
                    # This prevents repeated re-entry into symbols that have
                    # already produced negative expectancy.
                    if memory:
                        _sym_stats = memory.get_symbol_stats(sym)
                        if _sym_stats and _sym_stats.get("trades", 0) >= 3:
                            _trades = int(_sym_stats.get("trades", 0))
                            _wins = int(_sym_stats.get("wins", 0))
                            _wr = (_wins / _trades * 100) if _trades else 0.0
                            _pnl = float(_sym_stats.get("total_pnl", 0.0))
                            if _pnl < 0 and _wr < 40:
                                logger.warning(
                                    f"  [MEM-BLACKLIST] {sym} WR={_wr:.0f}% "
                                    f"PnL={_pnl:+.2f} over {_trades} trades -- skip"
                                )
                                continue

                    _mkt_for_filter = next(
                        (m for m in market_data if m.get("symbol") == sym), {}
                    )
                    _spread = float(_mkt_for_filter.get("spread_pct", 0.0))
                    _vol_usd = float(_mkt_for_filter.get("volume_24h_usd", 0.0))
                    _upper_wick = float(_mkt_for_filter.get("upper_wick_atr", 0.0))
                    _lower_wick = float(_mkt_for_filter.get("lower_wick_atr", 0.0))
                    _funding_anom = bool(_mkt_for_filter.get("funding_anomaly", False))
                    _funding_z = float(_mkt_for_filter.get("funding_zscore", 0.0))

                    # Filter 1: Spread terlalu lebar
                    if _spread > Config.MAX_SPREAD_PCT:
                        logger.warning(
                            f"  [RISK-SPREAD] {sym} spread={_spread:.3f}% > "
                            f"max {Config.MAX_SPREAD_PCT:.3f}% -- skip"
                        )
                        continue

                    # Filter 2: Wick anomaly (stop-hunt risk) on direction-relevant side
                    # LONG entry → upper wick danger (fake breakout)
                    # SHORT entry → lower wick danger (fake breakdown)
                    _wick_threshold = Config.MAX_WICK_ATR_RATIO
                    if action == "LONG" and _upper_wick >= _wick_threshold:
                        logger.warning(
                            f"  [RISK-WICK] {sym} LONG: upper_wick={_upper_wick:.1f}×ATR "
                            f">= {_wick_threshold} (fake breakout risk) -- skip"
                        )
                        continue
                    if action == "SHORT" and _lower_wick >= _wick_threshold:
                        logger.warning(
                            f"  [RISK-WICK] {sym} SHORT: lower_wick={_lower_wick:.1f}×ATR "
                            f">= {_wick_threshold} (fake breakdown risk) -- skip"
                        )
                        continue

                    # Filter 3: Funding anomaly (forced-liq cascade)
                    if _funding_anom:
                        logger.warning(
                            f"  [RISK-FUNDING] {sym} funding_zscore={_funding_z:.1f} "
                            f"|z|>=3 sigma (cascade risk) -- skip"
                        )
                        continue

                    # Filter 4: Wash trading (Volume vs OI divergence)
                    _wash_susp = bool(_mkt_for_filter.get("wash_suspicion", False))
                    _wash_sev  = _mkt_for_filter.get("wash_severity", "low")
                    _oi_chg    = float(_mkt_for_filter.get("oi_change_pct_24h", 0.0))
                    if _wash_susp and _wash_sev == "high":
                        logger.warning(
                            f"  [RISK-WASH] {sym} HIGH wash suspicion "
                            f"(vol=${_vol_usd/1e6:.0f}M, OI change={_oi_chg:.1f}%) -- skip"
                        )
                        continue
                    # Medium wash: require higher AI score (inline lookup —
                    # sig_score is computed lower; compute here to gate early)
                    _early_score = float(signal.get("score", 0))
                    if _wash_susp and _wash_sev == "medium" and _early_score < 9.0:
                        logger.warning(
                            f"  [RISK-WASH] {sym} medium wash, score {_early_score:.1f}<9.0 "
                            f"(vol=${_vol_usd/1e6:.0f}M, OI={_oi_chg:.1f}%) -- skip"
                        )
                        continue

                    # Filter 5: Thin liquidity warning (not hard-reject, just log)
                    if 0 < _vol_usd < 10_000_000:
                        logger.info(
                            f"  [RISK-VOL] {sym} 24h volume ${_vol_usd/1e6:.1f}M "
                            f"< $10M (thin liquidity, AI score should be high)"
                        )

                    # ── Daily circuit breaker ────────────────────
                    # SMC/retest + anti-late-entry gate. Mode is configurable:
                    # soft by default, strict for the old hard-gate behavior.
                    if not _passes_smc_retest_filter(signal, _mkt_for_filter):
                        continue

                    # Halt new entries if today's realized loss > limit
                    if Config.DAILY_LOSS_LIMIT_USDT > 0:
                        _daily_loss = _daily_realized_loss(pnl)
                        if abs(_daily_loss) >= Config.DAILY_LOSS_LIMIT_USDT:
                            logger.warning(
                                f"  [CIRCUIT BREAKER] Daily loss limit hit: "
                                f"{_daily_loss:.4f} USDT (limit: -{Config.DAILY_LOSS_LIMIT_USDT:.2f}) "
                                f"-- no new entries today"
                            )
                            break  # stop processing all signals for this scan

                    if executed_count >= available:
                        logger.info(f"  Slot limit -- skip {sym}")
                        continue

                    # ── Quality filters: score + confidence (hard floor + adaptive) ──
                    # Hard floor (Config.MIN_SCORE_HARD, MIN_CONFIDENCE_HARD) protects
                    # against bad signals even when adaptive mode is "normal".
                    # Both score AND confidence must pass.
                    adaptive_min_score = adaptive_params.get("min_score", 5.5)
                    min_score = max(adaptive_min_score, Config.MIN_SCORE_HARD)
                    min_conf = Config.MIN_CONFIDENCE_HARD
                    sig_score = float(signal.get("score", 0))
                    sig_conf = float(signal.get("confidence", 0))

                    if sig_score < min_score:
                        logger.info(
                            f"  Score {sig_score:.1f} < min {min_score:.1f} "
                            f"(hard={Config.MIN_SCORE_HARD:.1f}, "
                            f"adaptive={adaptive_params['mode']}) -- skip {sym}"
                        )
                        continue

                    if sig_conf < min_conf:
                        logger.info(
                            f"  Confidence {sig_conf:.2f} < min {min_conf:.2f} "
                            f"-- skip {sym}"
                        )
                        continue

                    # Validate TP direction vs ACTUAL current market price
                    # (not AI's stated entry_price — AI can hallucinate a wrong entry
                    #  that makes its TPs look valid, but they're still below market)
                    _tps = signal.get("take_profit", [])
                    if _tps:
                        _mkt  = next((m for m in market_data if m.get("symbol") == sym), {})
                        _cur  = float(_mkt.get("current_price", 0))
                        _ai_entry = float(signal.get("entry_price", 0))
                        # Use the higher reference price for LONG (conservative),
                        # lower for SHORT — catches both wrong AI entry AND moved market.
                        if action in ("LONG", "BUY"):
                            _ref = max(_cur, _ai_entry) if _ai_entry > 0 else _cur
                            bad  = [t for t in _tps if float(t) <= _ref]
                        else:
                            _ref = min(_cur, _ai_entry) if _ai_entry > 0 else _cur
                            bad  = [t for t in _tps if float(t) >= _ref]
                        if bad and _ref > 0:
                            logger.warning(
                                f"  [REJECT] {sym} {action} invalid TPs {bad} "
                                f"vs market={_cur:.6f} ai_entry={_ai_entry:.6f} — skipping"
                            )
                            continue

                    # ── Risk/Reward filter (uses AI's invalidation SL) ──
                    # Use weighted TP reward instead of TP1-only reward because
                    # TP1 is only a partial close + BE/trail activation. TP3
                    # closes the remainder, so RR should reflect the planned
                    # exit distribution.
                    _rr_tps = signal.get("take_profit", [])
                    _rr_sl  = float(signal.get("stop_loss", 0))
                    _rr_entry = float(signal.get("entry_price", _cur)) or _cur
                    if _rr_tps and _rr_sl > 0 and _rr_entry > 0:
                        _tp1_w = max(0.0, min(float(Config.TP1_CLOSE_PCT) / 100.0, 1.0))
                        _tp2_w = max(0.0, min(float(Config.TP2_CLOSE_PCT) / 100.0, 1.0 - _tp1_w))
                        _tp_weights = [_tp1_w, _tp2_w, max(0.0, 1.0 - _tp1_w - _tp2_w)]
                        _weighted_reward = 0.0
                        if action in ("LONG", "BUY"):
                            _risk   = _rr_entry - _rr_sl
                            for _tp, _w in zip(_rr_tps[:3], _tp_weights):
                                _weighted_reward += max(0.0, float(_tp) - _rr_entry) * _w
                        else:
                            _risk   = _rr_sl - _rr_entry
                            for _tp, _w in zip(_rr_tps[:3], _tp_weights):
                                _weighted_reward += max(0.0, _rr_entry - float(_tp)) * _w
                        if _risk > 0:
                            _rr = _weighted_reward / _risk
                            if _rr < Config.MIN_RR:
                                logger.warning(
                                    f"  [REJECT] {sym} {action} weighted RR={_rr:.2f} < "
                                    f"min={Config.MIN_RR} "
                                    f"(weighted_reward={_weighted_reward:.4f} "
                                    f"risk={_risk:.4f} weights={_tp_weights}) — skipping"
                                )
                                continue

                    # ── Anti-overtrading: daily symbol limit ─────
                    _daily_count = _symbol_trade_count_today(pnl, sym)
                    if _daily_count >= Config.MAX_TRADES_PER_SYMBOL_DAY:
                        logger.info(
                            f"  [SKIP] {sym} daily limit "
                            f"({_daily_count}/{Config.MAX_TRADES_PER_SYMBOL_DAY} trades today)"
                        )
                        continue

                    # ── Anti-overtrading: loss cooldown ──────────
                    _loss_h = _symbol_hours_since_last_loss(pnl, sym)
                    if _loss_h < Config.SYMBOL_LOSS_COOLDOWN_H:
                        logger.info(
                            f"  [SKIP] {sym} loss cooldown "
                            f"({_loss_h:.1f}h < {Config.SYMBOL_LOSS_COOLDOWN_H}h required)"
                        )
                        continue

                    # Clamp position size to configured limits
                    raw_size = float(signal.get("position_size_percent", 2.0))
                    clamped_size = max(
                        Config.MIN_POSITION_SIZE_PCT,
                        min(raw_size, Config.MAX_POSITION_SIZE_PCT),
                    )
                    signal["position_size_percent"] = clamped_size
                    # Warn if AI requested >2x cap — indicates AI is unreliable on
                    # position sizing (may need stricter prompt or model change)
                    if raw_size > Config.MAX_POSITION_SIZE_PCT * 2:
                        logger.warning(
                            f"  [SIZE-WARN] {sym} AI requested {raw_size:.1f}% "
                            f"(cap {Config.MAX_POSITION_SIZE_PCT}%, clamped). "
                            f"AI sizing logic unreliable — review prompt"
                        )

                    # Phase 3: adaptive rules (SL width, leverage cap, symbol penalty)
                    mkt_snap = next(
                        (m for m in market_data if m.get("symbol") == sym), {}
                    )
                    signal = _apply_adaptive_rules(
                        signal, mkt_snap, adaptive_params, memory
                    )

                    # Inject market context for executor's hybrid SL calculation
                    signal["_atr_pct"] = mkt_snap.get("atr_pct", 0.0)
                    signal["_mode"]    = mkt_snap.get("trading_mode", "swing")

                    logger.info(
                        f"  {action:5s} {sym:<14s} "
                        f"entry={float(signal.get('entry_price',0)):,.4f}  "
                        f"sl={float(signal.get('stop_loss',0)):,.4f}  "
                        f"lev={signal.get('leverage',0)}x  "
                        f"size={float(signal.get('position_size_percent', clamped_size)):.1f}%"
                    )

                    exec_result = await executor.execute_signal(signal)
                    if exec_result:
                        executed_count += 1
                        pnl.record_entry(signal, exec_result)
                        already_open_symbols.add(sym)
                        logger.info(f"  Executed: {exec_result}")
                        await notifier.alert_execution(exec_result)

                        # Record entry context in memory
                        if memory:
                            memory.record_entry_context(sym, action, signal, mkt_snap)
                    else:
                        logger.warning(f"  Execution failed: {sym}")

        # 5. Build PnL stats and print dashboard
        stats = pnl.get_stats()
        stats["unrealized_pnl"] = pnl.unrealized_pnl(market_data)
        open_pos_summary = pnl.open_positions_summary(market_data)
        recent = pnl.get_recent_trades(5)

        print_dashboard(
            scan_count, signals, executed_count,
            account_balance, Config.EXCHANGE_MODE,
            Config.DRY_RUN, Config.SCAN_INTERVAL,
            data_sources, stats, open_pos_summary, recent,
            adaptive_mode=adaptive_params.get("mode", "normal"),
        )

        logger.info(
            f"Scan #{scan_count:04d} done -- "
            f"executed={executed_count}  "
            f"realized={stats['realized_pnl']:+.4f} USDT  "
            f"unrealized={stats['unrealized_pnl']:+.4f} USDT  "
            f"sleep={Config.SCAN_INTERVAL}s"
        )
        await asyncio.sleep(Config.SCAN_INTERVAL)


# ── Entry point ────────────────────────────────────────────────────────────

async def main() -> None:
    from config import Config
    from analyzer.signal_engine import SignalEngine
    from data.market_feed import MarketFeed
    from utils.logger import logger
    from utils.telegram_notifier import TelegramNotifier
    from utils.pnl_tracker import PnLTracker
    from utils.trade_memory import TradeMemory

    print(_clr("""
  +----------------------------------------------------------+
  |         AI QUANT TRADING BOT  v2.0                       |
  |         Analyzer + Execution + PnL + AI Memory           |
  +----------------------------------------------------------+
    """, "cyan"))

    try:
        Config.validate()
    except ValueError as exc:
        print(f"[CONFIG ERROR]\n{exc}")
        sys.exit(1)

    logger.info("Configuration loaded:")
    logger.info(Config.summary())

    if Config.EXCHANGE_MODE == "CEX":
        from execution.cex_executor import CEXExecutor
        executor = CEXExecutor()
        logger.info(f"Executor : Bybit Futures (testnet={Config.BYBIT_TESTNET})")
    elif Config.EXCHANGE_MODE == "DEX":
        from execution.dex_executor import DEXExecutor
        executor = DEXExecutor()
        logger.info(f"Executor : Hyperliquid (testnet={Config.HL_TESTNET})")
    else:
        print(f"Unknown EXCHANGE_MODE '{Config.EXCHANGE_MODE}'")
        sys.exit(1)

    feed = MarketFeed()

    # On-chain regime feed (Binance public endpoints + Fear & Greed)
    from data.onchain_feed import OnChainFeed
    onchain = OnChainFeed(binance_api_key=Config.BINANCE_API_KEY)

    # Unified AI provider initialization (with optional tier-2/3 fallback chain)
    ai_cfg = Config.get_ai_config()
    fallback_chain = Config.get_tier_chain()  # may be empty; supports N tiers

    engine = SignalEngine(
        api_key=ai_cfg["api_key"],
        model=ai_cfg["model"],
        provider=ai_cfg["provider"],
        base_url=ai_cfg["base_url"],
        fallback_chain=fallback_chain,
    )

    notifier = TelegramNotifier(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID)
    # Flush stale Telegram commands accumulated while bot was offline.
    # Without this, restart replays old "close X" commands from up to 24h ago.
    if notifier.enabled:
        await asyncio.to_thread(notifier._sync_flush_old)
        logger.info(
            f"[Telegram] Flushed old updates "
            f"(offset reset to {notifier._last_update_id})"
        )

    pnl      = PnLTracker(Config.ACCOUNT_BALANCE)

    # Trade Memory for AI Learning
    memory = TradeMemory() if Config.AI_LEARNING else None
    if memory:
        logger.info(
            f"AI Learning  : ON ({memory.get_total_trades()} trades in memory, "
            f"streak={memory.get_streak():+d})"
        )
    else:
        logger.info("AI Learning  : OFF")

    mode_label = f"{Config.EXCHANGE_MODE} {'[DRY RUN]' if Config.DRY_RUN else '[LIVE]'}"
    await notifier.send(
        f"<b>Bot Started</b> | {mode_label}\n"
        f"Symbols: {len(Config.SYMBOLS)}  Balance: {Config.ACCOUNT_BALANCE:,.0f} USDT\n"
        f"AI: {ai_cfg['provider']}/{ai_cfg['model']}\n"
        f"Learning: {'ON' if memory else 'OFF'}"
    )

    # Startup sync — reconcile pnl_store with actual Bybit positions
    if not Config.DRY_RUN:
        logger.info("[Sync] Running startup position sync ...")
        await _sync_live_positions(executor, pnl, memory, notifier)
        open_syms = list(pnl._store["open_positions"].keys())
        logger.info(f"[Sync] Startup done — {len(open_syms)} open: {open_syms}")

    # Initial auto-symbol load (before first scan)
    if Config.AUTO_SYMBOLS:
        logger.info(
            f"[SymbolFeed] AUTO_SYMBOLS=true — "
            f"fetching top-{Config.AUTO_SYMBOLS_COUNT} by volume ..."
        )
        from data.market_feed import fetch_top_symbols
        initial_syms = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: fetch_top_symbols(
                limit=Config.AUTO_SYMBOLS_COUNT,
                min_turnover_usdt=Config.AUTO_SYMBOLS_MIN_TURNOVER_USD,
                blacklist=Config.SYMBOL_BLACKLIST,
                data_only=Config.DATA_ONLY_SYMBOLS,
            )
        )
        if initial_syms:
            Config.SYMBOLS = initial_syms
            logger.info(
                f"[SymbolFeed] Loaded {len(Config.SYMBOLS)} symbols: "
                f"{Config.SYMBOLS[:10]}{'...' if len(Config.SYMBOLS) > 10 else ''}"
            )
        else:
            logger.warning("[SymbolFeed] Initial fetch failed — using SYMBOLS from .env")

    try:
        await run_agent(feed, engine, executor, notifier, pnl, memory, onchain)
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    except asyncio.CancelledError:
        logger.info("Task cancelled.")
    except Exception as exc:
        logger.exception(f"Fatal: {exc}")
        await notifier.alert_error(str(exc))
    finally:
        logger.info("Shutting down ...")
        for obj in (feed, executor):
            try:
                await obj.close()
            except Exception:
                pass
        await asyncio.sleep(0.3)
        logger.info("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
