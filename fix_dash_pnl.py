"""
fix_dash_pnl.py — One-time correction for DASHUSDT manual_close_100pct PnL.

Bug: manual close via Telegram recorded exit_price=entry_price → PnL=$0.
Real PnL on Bybit was -$8.

Run ONCE on VPS:  python fix_dash_pnl.py
"""
import json
from pathlib import Path

REAL_PNL = -8.0
SYMBOL = "DASHUSDT"

p = Path("pnl_store.json")
if not p.exists():
    print("pnl_store.json not found")
    raise SystemExit(1)

data = json.loads(p.read_text(encoding="utf-8"))
closed = data["closed_trades"]

# Find most recent matching trade
target_idx = None
for i in range(len(closed) - 1, -1, -1):
    t = closed[i]
    if (t.get("symbol") == SYMBOL
        and t.get("reason", "").startswith("manual_close")
        and abs(t.get("pnl_usdt", 0)) < 0.01):  # PnL ~0 = the buggy record
        target_idx = i
        break

if target_idx is None:
    print(f"No buggy {SYMBOL} manual_close trade found (already fixed?)")
    raise SystemExit(0)

trade = closed[target_idx]
old_pnl = trade.get("pnl_usdt", 0)
diff = REAL_PNL - old_pnl

print(f"Found trade #{target_idx}:")
print(f"  symbol     = {trade.get('symbol')}")
print(f"  close_time = {trade.get('close_time')}")
print(f"  reason     = {trade.get('reason')}")
print(f"  old PnL    = {old_pnl:+.4f}")
print(f"  real PnL   = {REAL_PNL:+.4f}  (correction: {diff:+.4f})")

# Update trade
trade["pnl_usdt"] = REAL_PNL
notional = trade.get("notional", 1)
lev = trade.get("leverage", 1) or 1
margin = notional / lev
trade["pnl_pct"] = round(REAL_PNL / max(margin, 0.01) * 100, 2)
closed[target_idx] = trade

# Update aggregate stats
s = data["stats"]
s["total_realized"] = round(s.get("total_realized", 0) + diff, 4)

# Old PnL was ~0 (counted as win); now it's a loss → flip
if old_pnl >= 0 and REAL_PNL < 0:
    s["wins"] = max(0, s.get("wins", 0) - 1)
    s["losses"] = s.get("losses", 0) + 1

if REAL_PNL < s.get("worst_trade", 0):
    s["worst_trade"] = REAL_PNL
    s["worst_symbol"] = SYMBOL

p.write_text(json.dumps(data, indent=2), encoding="utf-8")

print()
print("Done. New stats:")
print(f"  total_realized = {s['total_realized']:+.4f} USDT")
print(f"  wins / losses  = {s['wins']} / {s['losses']}")
print(f"  worst_trade    = {s['worst_trade']:+.4f} ({s.get('worst_symbol','?')})")
