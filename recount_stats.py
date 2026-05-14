"""
recount_stats.py — One-time script untuk recalculate wins/losses/total_trades
dari closed_trades yang sudah ada di pnl_store.json.

Jalankan SAAT BOT MATI:
    python recount_stats.py

Script ini membuat backup otomatis sebelum mengubah apapun.
"""

import json
import shutil
from pathlib import Path
from datetime import datetime
from collections import defaultdict

STORE_PATH = Path("pnl_store.json")
BACKUP_PATH = Path(f"pnl_store_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")


def main():
    if not STORE_PATH.exists():
        print("❌ pnl_store.json tidak ditemukan. Pastikan script dijalankan di root project.")
        return

    # ── Backup dulu ───────────────────────────────────────────────
    shutil.copy(STORE_PATH, BACKUP_PATH)
    print(f"✅ Backup dibuat: {BACKUP_PATH}")

    store = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    closed = store.get("closed_trades", [])

    if not closed:
        print("ℹ️  Tidak ada closed_trades. Tidak ada yang diubah.")
        return

    # ── Hitung ulang dari closed_trades ───────────────────────────
    # Hanya hitung full closes (bukan partial TP1/TP2)
    full_closes = [t for t in closed if not t.get("partial")]

    total   = 0
    wins    = 0
    losses  = 0
    total_realized = 0.0
    best_trade  =  0.0
    worst_trade =  0.0
    best_symbol  = ""
    worst_symbol = ""

    # by_tier dan by_symbol untuk display saja (tidak disimpan ke store)
    by_symbol = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0})

    for t in full_closes:
        pnl = float(t.get("pnl_usdt", 0))
        sym = t.get("symbol", "?")
        total += 1
        total_realized = round(total_realized + pnl, 4)

        if pnl >= 0:
            wins += 1
            by_symbol[sym]["wins"] += 1
            if pnl > best_trade:
                best_trade  = pnl
                best_symbol = sym
        else:
            losses += 1
            by_symbol[sym]["losses"] += 1
            if pnl < worst_trade:
                worst_trade  = pnl
                worst_symbol = sym

        by_symbol[sym]["trades"] += 1
        by_symbol[sym]["pnl"]    = round(by_symbol[sym]["pnl"] + pnl, 4)

    # ── Preview sebelum simpan ────────────────────────────────────
    old_s = store.get("stats", {})
    print("\n── SEBELUM ──────────────────────────────────────")
    print(f"  total_trades : {old_s.get('total_trades', '?')}")
    print(f"  wins         : {old_s.get('wins', '?')}")
    print(f"  losses       : {old_s.get('losses', '?')}")
    wr_old = old_s.get('wins', 0) / max(old_s.get('total_trades', 1), 1) * 100
    print(f"  win rate     : {wr_old:.1f}%")
    print(f"  total_realized: {old_s.get('total_realized', '?')}")

    print("\n── SESUDAH (dari recount) ───────────────────────")
    print(f"  total_trades : {total}  (full closes only, skip partials)")
    print(f"  wins         : {wins}")
    print(f"  losses       : {losses}")
    wr_new = wins / max(total, 1) * 100
    print(f"  win rate     : {wr_new:.1f}%")
    print(f"  total_realized: {total_realized}")
    print(f"  best_trade   : +{best_trade:.4f} ({best_symbol})")
    print(f"  worst_trade  : {worst_trade:.4f} ({worst_symbol})")

    print("\n── PER SYMBOL ───────────────────────────────────")
    for sym, v in sorted(by_symbol.items(), key=lambda x: -x[1]["trades"]):
        wr = v["wins"] / max(v["trades"], 1) * 100
        pnl_sign = "+" if v["pnl"] >= 0 else ""
        print(f"  {sym:<14} {v['trades']:>3} trades  {v['wins']}W/{v['losses']}L  WR={wr:.0f}%  PnL={pnl_sign}{v['pnl']:.4f}")

    # ── Konfirmasi ────────────────────────────────────────────────
    print("\n" + "─" * 50)
    confirm = input("Simpan perubahan ke pnl_store.json? (y/n): ").strip().lower()
    if confirm not in ("y", "yes", "ya"):
        print("❌ Dibatalkan. pnl_store.json tidak diubah.")
        return

    # ── Patch stats di store ──────────────────────────────────────
    store["stats"]["total_trades"]   = total
    store["stats"]["wins"]           = wins
    store["stats"]["losses"]         = losses
    store["stats"]["total_realized"] = total_realized
    store["stats"]["best_trade"]     = best_trade
    store["stats"]["best_symbol"]    = best_symbol
    store["stats"]["worst_trade"]    = worst_trade
    store["stats"]["worst_symbol"]   = worst_symbol

    STORE_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")
    print(f"\n✅ pnl_store.json diupdate.")
    print(f"   Backup tersimpan di: {BACKUP_PATH}")
    print(f"   Win Rate baru: {wr_new:.1f}% ({wins}W / {losses}L / {total} trades)")


if __name__ == "__main__":
    main()
