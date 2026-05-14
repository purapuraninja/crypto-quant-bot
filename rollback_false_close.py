"""
rollback_false_close.py — One-time recovery script.

Use case: P1 (time-exit) fired but executor.partial_close() failed (e.g. auth bug
in cex_executor.py wasn't uploaded yet), so:
  - pnl_store recorded the position as CLOSED
  - But position is still OPEN on Bybit
  - Stats are now polluted with false closes

This script:
  1. Removes the most recent N "time_exit_*" closed_trades from pnl_store
  2. Reverses their PnL impact on stats (total_realized, wins/losses)
  3. Leaves open_positions empty for those symbols — next bot startup will
     auto-sync them back from Bybit with a fresh open_time

Run ONCE before restarting the bot. Safe to re-run (idempotent if list empty).
"""
import json
from pathlib import Path

STORE = Path("pnl_store.json")
# Symbols that were falsely closed — adjust if different
FALSE_CLOSED = {"HUSDT", "SIRENUSDT", "DASHUSDT", "LINKUSDT"}

if not STORE.exists():
    print("pnl_store.json not found — nothing to rollback")
    raise SystemExit(0)

data = json.loads(STORE.read_text(encoding="utf-8"))
closed = data.get("closed_trades", [])
stats = data.get("stats", {})

# Find most recent time_exit closes for the affected symbols
to_remove = []
seen_syms = set()
for i in range(len(closed) - 1, -1, -1):
    t = closed[i]
    if (t.get("symbol") in FALSE_CLOSED
        and str(t.get("reason", "")).startswith("time_exit")
        and t.get("symbol") not in seen_syms):
        to_remove.append(i)
        seen_syms.add(t["symbol"])
    if len(seen_syms) == len(FALSE_CLOSED):
        break

if not to_remove:
    print("No matching false time_exit closes found — already rolled back?")
    raise SystemExit(0)

print(f"Found {len(to_remove)} false closes to rollback:")
for idx in to_remove:
    t = closed[idx]
    print(f"  - {t['symbol']:14s} PnL={t['pnl_usdt']:+.4f} reason={t['reason']}")

# Reverse stats
for idx in sorted(to_remove, reverse=True):
    t = closed[idx]
    pnl = float(t.get("pnl_usdt", 0))
    stats["total_realized"] = round(stats.get("total_realized", 0) - pnl, 4)
    stats["total_trades"] = max(0, stats.get("total_trades", 0) - 1)
    if pnl >= 0:
        stats["wins"] = max(0, stats.get("wins", 0) - 1)
    else:
        stats["losses"] = max(0, stats.get("losses", 0) - 1)
    closed.pop(idx)

data["closed_trades"] = closed
data["stats"] = stats

STORE.write_text(json.dumps(data, indent=2), encoding="utf-8")
print("\nRollback complete. Stats restored.")
print(f"  total_realized = {stats['total_realized']:+.4f}")
print(f"  total_trades   = {stats['total_trades']}")
print(f"  wins / losses  = {stats['wins']} / {stats['losses']}")
print("\nNext bot startup will re-sync the 4 positions from Bybit with fresh open_time.")
