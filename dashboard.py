"""
dashboard.py — Flask web dashboard server for AI Quant Bot.

Serves:
  GET /           → dashboard.html
  GET /api/data   → JSON with bot state (live-refreshed from pnl_store.json + trade_memory.json)

Run:
  python dashboard.py
  Then open: http://localhost:5050
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import requests as req
from flask import Flask, jsonify, send_from_directory, request

# ── Paths ─────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
PNL_STORE     = BASE_DIR / "pnl_store.json"
TRADE_MEMORY  = BASE_DIR / "trade_memory.json"
DASHBOARD_HTML = BASE_DIR / "dashboard.html"

app = Flask(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _build_api_data() -> dict:
    pnl   = _load_json(PNL_STORE)
    mem   = _load_json(TRADE_MEMORY)

    # ── Config from env (bot writes .env, we read it here too) ───────────
    dry_run      = os.getenv("DRY_RUN", "true").lower() == "true"
    trading_mode = os.getenv("TRADING_MODE", "intraday").lower()
    account_bal  = _safe_float(os.getenv("ACCOUNT_BALANCE", "10000"))

    # ── Open positions ────────────────────────────────────────────────────
    raw_positions = pnl.get("open_positions", {})
    open_positions = []

    for sym, pos in raw_positions.items():
        action      = pos.get("action", "LONG")
        entry_price = _safe_float(pos.get("entry_price", 0))
        leverage    = int(_safe_float(pos.get("leverage", 1)))
        notional    = _safe_float(pos.get("notional", 0))
        trailing    = bool(pos.get("trailing_active", False))
        status      = pos.get("status", "")

        # current_price: try from pos, else fallback to entry (we don't have live price here)
        current_price = _safe_float(pos.get("current_price", entry_price))
        if current_price == 0:
            current_price = entry_price

        # unrealized pnl
        if entry_price > 0 and current_price > 0:
            price_move_pct = (current_price - entry_price) / entry_price * 100
            if action in ("SELL", "SHORT"):
                price_move_pct = -price_move_pct
            unrealized_pnl_pct = price_move_pct * leverage
            unrealized_pnl     = notional * price_move_pct / 100
        else:
            unrealized_pnl_pct = 0.0
            unrealized_pnl     = 0.0

        open_positions.append({
            "symbol":              sym,
            "action":              "BUY" if action in ("BUY", "LONG") else "SELL",
            "entry_price":         entry_price,
            "current_price":       current_price,
            "unrealized_pnl":      round(unrealized_pnl, 2),
            "unrealized_pnl_pct":  round(unrealized_pnl_pct, 2),
            "leverage":            leverage,
            "trailing_active":     trailing,
            "status":              status,
        })

    # ── Closed trades from memory ─────────────────────────────────────────
    all_trades = mem.get("trades", [])

    total_trades = len(all_trades)
    wins         = sum(1 for t in all_trades if _safe_float(t.get("pnl_usdt", 0)) > 0)
    losses       = total_trades - wins
    realized_pnl = sum(_safe_float(t.get("pnl_usdt", 0)) for t in all_trades)

    pnls = [_safe_float(t.get("pnl_usdt", 0)) for t in all_trades]
    best_trade  = max(pnls) if pnls else 0.0
    worst_trade = min(pnls) if pnls else 0.0

    # best / worst symbol by total PnL
    sym_totals: dict[str, float] = {}
    for t in all_trades:
        s = t.get("symbol", "?")
        sym_totals[s] = sym_totals.get(s, 0.0) + _safe_float(t.get("pnl_usdt", 0))
    best_symbol  = max(sym_totals, key=sym_totals.get) if sym_totals else "—"
    worst_symbol = min(sym_totals, key=sym_totals.get) if sym_totals else "—"

    # Win streak
    streak = 0
    for t in reversed(all_trades):
        p = _safe_float(t.get("pnl_usdt", 0))
        if streak == 0:
            streak = 1 if p > 0 else -1
        elif (p > 0 and streak > 0):
            streak += 1
        elif (p < 0 and streak < 0):
            streak -= 1
        else:
            break

    # ── Recent trades (last 10) ───────────────────────────────────────────
    recent_raw = list(reversed(all_trades))[:10]
    recent_trades = []
    for t in recent_raw:
        reason_raw = t.get("reason", "")
        # Normalise reason
        if "tp3" in reason_raw or "take_profit_3" in reason_raw:
            reason = "TP3"
        elif "tp2" in reason_raw or "take_profit_2" in reason_raw:
            reason = "TP2"
        elif "tp1" in reason_raw or "take_profit_1" in reason_raw:
            reason = "TP1"
        elif "trail" in reason_raw:
            reason = "TRAIL"
        elif "sl" in reason_raw or "stop" in reason_raw:
            reason = "SL"
        else:
            reason = reason_raw.upper()[:5]

        close_time = t.get("exit_time", t.get("close_time", "—"))
        # Trim to readable format
        if len(close_time) > 16:
            close_time = close_time[:16]

        recent_trades.append({
            "symbol":      t.get("symbol", "?"),
            "action":      "BUY" if t.get("action", "LONG") in ("BUY","LONG") else "SELL",
            "entry_price": _safe_float(t.get("entry_price", 0)),
            "exit_price":  _safe_float(t.get("exit_price", 0)),
            "pnl_usdt":    round(_safe_float(t.get("pnl_usdt", 0)), 2),
            "pnl_pct":     round(_safe_float(t.get("pnl_pct", 0)), 2),
            "reason":      reason,
            "close_time":  close_time,
        })

    # ── Symbol stats ──────────────────────────────────────────────────────
    symbol_stats: dict[str, dict] = {}
    for t in all_trades:
        s = t.get("symbol", "?")
        p = _safe_float(t.get("pnl_usdt", 0))
        sc = _safe_float(t.get("ai_score", 0))
        if s not in symbol_stats:
            symbol_stats[s] = {"trades": 0, "wins": 0, "total_pnl": 0.0, "_scores": []}
        symbol_stats[s]["trades"] += 1
        if p > 0:
            symbol_stats[s]["wins"] += 1
        symbol_stats[s]["total_pnl"] += p
        if sc > 0:
            symbol_stats[s]["_scores"].append(sc)

    # Clean up and compute avg_score
    clean_ss = {}
    for s, v in symbol_stats.items():
        scores = v.pop("_scores", [])
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
        clean_ss[s] = {
            "trades":    v["trades"],
            "wins":      v["wins"],
            "total_pnl": round(v["total_pnl"], 2),
            "avg_score": avg_score,
        }

    # ── 14-day chart ──────────────────────────────────────────────────────
    today = datetime.utcnow().date()
    day_map: dict[str, float] = {}
    for t in all_trades:
        raw_t = t.get("exit_time", t.get("entry_time", ""))
        try:
            d = raw_t[:10]  # "YYYY-MM-DD"
            day_map[d] = day_map.get(d, 0.0) + _safe_float(t.get("pnl_usdt", 0))
        except Exception:
            pass

    chart_labels = []
    chart_values = []
    for i in range(13, -1, -1):
        day = today - timedelta(days=i)
        d_str = day.strftime("%Y-%m-%d")
        label = day.strftime("%b %-d") if os.name != "nt" else day.strftime("%b %#d")
        chart_labels.append(label)
        chart_values.append(round(day_map.get(d_str, 0.0), 2))

    # ── Balance ───────────────────────────────────────────────────────────
    # Use ACCOUNT_BALANCE + realized_pnl as running balance estimate
    balance = account_bal + realized_pnl

    return {
        "dry_run":       dry_run,
        "trading_mode":  trading_mode,
        "balance":       round(balance, 2),
        "stats": {
            "realized_pnl":  round(realized_pnl, 2),
            "total_trades":  total_trades,
            "wins":          wins,
            "losses":        losses,
            "best_trade":    round(best_trade, 2),
            "worst_trade":   round(worst_trade, 2),
            "best_symbol":   best_symbol,
            "worst_symbol":  worst_symbol,
        },
        "streak":         streak,
        "open_positions": open_positions,
        "recent_trades":  recent_trades,
        "chart_labels":   chart_labels,
        "chart_values":   chart_values,
        "symbol_stats":   clean_ss,
    }


# ── Routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR), "dashboard.html")


@app.route("/api/data")
def api_data():
    try:
        data = _build_api_data()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chart/<symbol>")
def api_chart(symbol):
    """
    Fetch OHLCV candles from Bybit public API + entry/exit markers from bot data.
    Query params:
      interval : Bybit interval (1,5,15,60,240,D) — default 60
      limit    : number of candles — default 100
    """
    interval = request.args.get("interval", "60")
    limit    = min(int(request.args.get("limit", "100")), 200)

    # ── Fetch candles from Bybit public API ───────────────────────
    candles = []
    try:
        url    = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": "linear",
            "symbol":   symbol.upper(),
            "interval": interval,
            "limit":    limit,
        }
        resp = req.get(url, params=params, timeout=8)
        data = resp.json()
        raw  = data.get("result", {}).get("list", [])
        # Bybit returns newest first — reverse for chart
        for item in reversed(raw):
            candles.append({
                "time":   int(item[0]) // 1000,   # ms → unix seconds
                "open":   float(item[1]),
                "high":   float(item[2]),
                "low":    float(item[3]),
                "close":  float(item[4]),
                "volume": float(item[5]),
            })
    except Exception as e:
        return jsonify({"error": f"Bybit fetch failed: {e}"}), 502

    # ── Build markers from bot data ───────────────────────────────
    markers = []
    pnl     = _load_json(PNL_STORE)
    mem     = _load_json(TRADE_MEMORY)
    sym_up  = symbol.upper()

    # Open positions → entry marker
    for sym, pos in pnl.get("open_positions", {}).items():
        if sym != sym_up:
            continue
        open_ts = pos.get("open_time", "")
        try:
            dt = datetime.strptime(open_ts[:19], "%Y-%m-%d %H:%M:%S")
            ts = int(dt.timestamp())
        except Exception:
            ts = candles[0]["time"] if candles else 0
        action = pos.get("action", "LONG")
        markers.append({
            "time":     ts,
            "position": "belowBar" if action in ("LONG","BUY") else "aboveBar",
            "color":    "#23c55e" if action in ("LONG","BUY") else "#f85149",
            "shape":    "arrowUp" if action in ("LONG","BUY") else "arrowDown",
            "text":     f"OPEN {action} @{pos.get('entry_price',0)}",
        })

    # Closed trades → entry + exit markers
    for t in mem.get("trades", []):
        if t.get("symbol", "") != sym_up:
            continue
        action = t.get("action", "LONG")
        pnl_v  = _safe_float(t.get("pnl_usdt", 0))
        reason = t.get("reason", "")

        # Entry marker
        entry_ts_raw = t.get("entry_time", "")
        if entry_ts_raw:
            try:
                dt = datetime.strptime(entry_ts_raw[:19], "%Y-%m-%d %H:%M:%S")
                markers.append({
                    "time":     int(dt.timestamp()),
                    "position": "belowBar" if action in ("LONG","BUY") else "aboveBar",
                    "color":    "#58a6ff",
                    "shape":    "arrowUp" if action in ("LONG","BUY") else "arrowDown",
                    "text":     f"ENTRY @{t.get('entry_price',0)}",
                })
            except Exception:
                pass

        # Exit marker
        exit_ts_raw = t.get("exit_time", "")
        if exit_ts_raw:
            try:
                dt = datetime.strptime(exit_ts_raw[:19], "%Y-%m-%d %H:%M:%S")
                sign = "+" if pnl_v >= 0 else ""
                markers.append({
                    "time":     int(dt.timestamp()),
                    "position": "aboveBar" if action in ("LONG","BUY") else "belowBar",
                    "color":    "#23c55e" if pnl_v >= 0 else "#f85149",
                    "shape":    "arrowDown" if action in ("LONG","BUY") else "arrowUp",
                    "text":     f"{reason.upper()} {sign}{pnl_v:.2f}U",
                })
            except Exception:
                pass

    # Sort markers by time
    markers.sort(key=lambda m: m["time"])

    return jsonify({"candles": candles, "markers": markers})


# ── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    print("=" * 55)
    print("  AI Quant Bot Dashboard")
    print(f"  Open: http://localhost:5050")
    print(f"  pnl_store  : {PNL_STORE}")
    print(f"  trade_memory: {TRADE_MEMORY}")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5050, debug=False)
