"""
eval_bot.py — Daily evaluation report generator.
Sends comprehensive trading metrics to Telegram every 07:00 WIB.
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from config import Config


def calculate_metrics(pnl_store: Dict) -> Dict:
    """
    Calculate comprehensive trading metrics from pnl_tracker store.

    Returns: {
        'total_trades': int,
        'win_rate_pct': float,
        'win_count': int,
        'loss_count': int,
        'total_pnl_usdt': float,
        'avg_roi_pct': float,
        'best_roi_pct': float,
        'worst_roi_pct': float,
        'profit_factor': float,
        'current_positions': int,
        'current_balance': float,
        'dca_count': int,
        'dca_success_rate_pct': float,
        'avg_hold_time_hours': float,
        'top_symbol': str,
    }
    """
    closed_trades = pnl_store.get("closed_trades", [])
    open_positions = pnl_store.get("open_positions", {})
    stats = pnl_store.get("stats", {})

    # Basic counts
    total_trades = len(closed_trades)
    winners = [t for t in closed_trades if t.get("pnl_usdt", 0) > 0]
    losers = [t for t in closed_trades if t.get("pnl_usdt", 0) <= 0]
    win_count = len(winners)
    loss_count = len(losers)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0

    # PnL
    total_pnl = stats.get("total_realized", 0.0)
    gross_profit = sum(t.get("pnl_usdt", 0) for t in winners)
    gross_loss = abs(sum(t.get("pnl_usdt", 0) for t in losers))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0

    # ROI stats
    roi_values = [t.get("roi_pct", 0) for t in closed_trades]
    avg_roi = sum(roi_values) / total_trades if total_trades > 0 else 0
    best_roi = max(roi_values) if roi_values else 0
    worst_roi = min(roi_values) if roi_values else 0

    # Hold time (hours)
    hold_times = []
    for t in closed_trades:
        entry_ts = t.get("entry_ts", 0)
        close_ts = t.get("close_ts", 0)
        if entry_ts and close_ts:
            hold_hours = (close_ts - entry_ts) / 3600
            hold_times.append(hold_hours)
    avg_hold_time = sum(hold_times) / len(hold_times) if hold_times else 0

    # DCA count from open positions
    dca_count = 0
    for pos in open_positions.values():
        dca_count += pos.get("dca_count", 0)
    dca_count += sum(t.get("dca_count", 0) for t in closed_trades)

    # Top symbol by trade count
    symbol_count = {}
    for t in closed_trades:
        sym = t.get("symbol", "?")
        symbol_count[sym] = symbol_count.get(sym, 0) + 1
    top_symbol = max(symbol_count, key=symbol_count.get) if symbol_count else "—"

    return {
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 2),
        "win_count": win_count,
        "loss_count": loss_count,
        "total_pnl_usdt": round(total_pnl, 2),
        "avg_roi_pct": round(avg_roi, 2),
        "best_roi_pct": round(best_roi, 2),
        "worst_roi_pct": round(worst_roi, 2),
        "profit_factor": round(profit_factor, 2),
        "current_positions": len(open_positions),
        "current_balance": round(Config.ACCOUNT_BALANCE, 2),  # Base balance (add unrealized later if needed)
        "dca_count": dca_count,
        "dca_success_rate_pct": 0.0,  # Not easily trackable from current structure
        "avg_hold_time_hours": round(avg_hold_time, 2),
        "top_symbol": top_symbol,
    }


def format_telegram_report(metrics: Dict) -> str:
    """
    Format metrics as Telegram HTML report.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Status based on performance (use HTML entities instead of emoji for safer encoding)
    status_icon = "✓" if metrics["win_rate_pct"] >= 50 else "!"
    pnl_sign = "+" if metrics["total_pnl_usdt"] > 0 else ""

    report = f"""<b>{status_icon} DAILY REPORT — {timestamp}</b>

<b>PERFORMANCE</b>
Win Rate: <code>{metrics['win_rate_pct']:.1f}%</code> ({metrics['win_count']}W / {metrics['loss_count']}L)
Total Trades: <code>{metrics['total_trades']}</code>
Avg ROI: <code>{metrics['avg_roi_pct']:+.2f}%</code>
Best Trade: <code>{metrics['best_roi_pct']:+.2f}%</code>
Worst Trade: <code>{metrics['worst_roi_pct']:+.2f}%</code>

<b>P&L</b>
Total PnL: <code>{pnl_sign}{metrics['total_pnl_usdt']:.2f} USDT</code>
Profit Factor: <code>{metrics['profit_factor']:.2f}</code>
Current Balance: <code>{metrics['current_balance']:.2f} USDT</code>

<b>POSITIONS</b>
Open Positions: <code>{metrics['current_positions']}</code>
Top Symbol: <code>{metrics['top_symbol']}</code>
Avg Hold Time: <code>{metrics['avg_hold_time_hours']:.1f}h</code>

<b>DCA</b>
DCA Triggered: <code>{metrics['dca_count']}x</code>
Success Rate: <code>{metrics['dca_success_rate_pct']:.1f}%</code>

<b>CONFIG</b>
Mode: <code>{Config.TRADING_MODE.upper()}</code>
Leverage: <code>5-10x</code>
SL Mode: <code>{Config.SL_MODE.upper()}</code>
Exchange: <code>{Config.EXCHANGE_MODE}</code>"""

    return report.strip()


async def send_daily_report(pnl_tracker, notifier) -> bool:
    """
    Calculate metrics and send to Telegram.
    Returns True if successful.
    """
    try:
        # Calculate metrics
        metrics = calculate_metrics(pnl_tracker._store)

        # Format and send
        report = format_telegram_report(metrics)
        await notifier.send(report)

        # Also log to file for archival
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics,
        }

        import os
        log_path = "logs/daily_reports.jsonl"
        os.makedirs("logs", exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")

        # Regenerate HTML report from latest pnl_store.json
        try:
            from pathlib import Path
            from utils.report_generator import generate_html, _load_store
            store = _load_store()
            generate_html(store, Path("bot_performance.html"))
        except Exception as html_err:
            import logging
            logging.getLogger(__name__).warning(f"[EVAL] HTML report gen failed: {html_err}")

        return True

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"[EVAL] Daily report failed: {e}")
        return False
