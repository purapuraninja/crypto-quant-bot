"""
inject_sl_tp.py — Inject SL/TP ke posisi synced_from_exchange di pnl_store.json

Script ini:
1. Baca posisi terbuka di pnl_store.json
2. Fetch harga + ATR (1H, 14-period) dari Bybit
3. Hitung SL/TP berdasarkan ATR + posisi harga saat ini
4. Deteksi state (below TP1 / BE zone / trailing zone)
5. Update pnl_store.json

Run: python inject_sl_tp.py
"""

import json
import shutil
import requests
from pathlib import Path
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────
PNL_STORE    = Path(__file__).parent / "pnl_store.json"
ATR_PERIOD   = 14
ATR_INTERVAL = "60"     # 1H candles untuk hitung ATR
ATR_LIMIT    = 50       # jumlah candle

# Multiplier SL/TP (pakai swing-style karena posisi ini swing legacy)
SL_MULT  = 1.5   # SL = entry ± 1.5×ATR
TP1_MULT = 1.5   # TP1 = entry ± 1.5×ATR
TP2_MULT = 2.5   # TP2 = entry ± 2.5×ATR
TP3_MULT = 4.0   # TP3 = entry ± 4.0×ATR
TRAIL_MULT = 1.5 # trailing pct = ATR_pct × 1.5

# ── Helpers ───────────────────────────────────────────────────────────────

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def backup(path):
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = path.parent / f"pnl_store_backup_{ts}.json"
    shutil.copy2(path, dst)
    print(f"  [backup] {dst.name}")
    return dst

def fetch_candles(symbol, interval=ATR_INTERVAL, limit=ATR_LIMIT):
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear",
        "symbol":   symbol,
        "interval": interval,
        "limit":    limit,
    }
    r = requests.get(url, params=params, timeout=8)
    r.raise_for_status()
    raw = r.json().get("result", {}).get("list", [])
    # Bybit → newest first, reverse
    candles = []
    for item in reversed(raw):
        candles.append({
            "open":  float(item[1]),
            "high":  float(item[2]),
            "low":   float(item[3]),
            "close": float(item[4]),
        })
    return candles

def calc_atr(candles, period=ATR_PERIOD):
    trs = []
    for i in range(1, len(candles)):
        h  = candles[i]["high"]
        l  = candles[i]["low"]
        pc = candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0
    return sum(trs[-period:]) / period

def current_price(candles):
    return candles[-1]["close"] if candles else 0

def fmt(v, sym=""):
    """Format price with appropriate decimals."""
    if v > 1000:  return f"{v:.2f}"
    if v > 10:    return f"{v:.3f}"
    if v > 0.1:   return f"{v:.4f}"
    return f"{v:.6f}"

def clamp_trail(atr_pct, leverage):
    """Same logic as pnl_tracker._calc_trail_pct (swing-aware)."""
    from config import Config
    is_swing = Config.TRADING_MODE == "swing"
    mult = 2.0 if is_swing else TRAIL_MULT
    t = atr_pct * mult
    t = max(t, 0.8)
    t = min(t, 8.0)
    if is_swing:
        if leverage >= 30:  t = min(t, 4.0)
        elif leverage >= 8: t = min(t, 3.0)
    else:
        if leverage >= 8:  t = min(t, 2.0)
        elif leverage >= 5: t = min(t, 4.0)
    return round(t, 2)

# ── Main ──────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  inject_sl_tp.py — Bybit ATR-based SL/TP Injector")
    print("="*60)

    store = load_json(PNL_STORE)
    positions = store.get("open_positions", {})

    # Only process positions missing SL/TP
    targets = {
        sym: pos for sym, pos in positions.items()
        if pos.get("stop_loss", 0) == 0 or pos.get("take_profit", []) == []
    }

    if not targets:
        print("\n  ✓ Semua posisi sudah punya SL/TP. Tidak ada yang perlu diupdate.\n")
        return

    print(f"\n  Posisi yang akan diupdate: {list(targets.keys())}\n")
    backup(PNL_STORE)
    print()

    results = []

    for sym, pos in targets.items():
        print(f"  [{sym}]")
        action   = pos.get("action", "LONG")
        entry    = float(pos.get("entry_price", 0))
        leverage = int(pos.get("leverage", 1))
        is_long  = action in ("LONG", "BUY")

        # Fetch ATR
        try:
            candles = fetch_candles(sym)
        except Exception as e:
            print(f"    ✗ Gagal fetch candle: {e}")
            continue

        atr      = calc_atr(candles)
        cur      = current_price(candles)
        atr_pct  = (atr / cur * 100) if cur > 0 else 0
        trail_pct = clamp_trail(atr_pct, leverage)

        if entry == 0 or atr == 0:
            print(f"    ✗ Entry/ATR tidak valid (entry={entry}, atr={atr})")
            continue

        # ── Hitung SL/TP ─────────────────────────────────────────
        if is_long:
            sl  = round(entry - SL_MULT  * atr, 8)
            tp1 = round(entry + TP1_MULT * atr, 8)
            tp2 = round(entry + TP2_MULT * atr, 8)
            tp3 = round(entry + TP3_MULT * atr, 8)
        else:
            sl  = round(entry + SL_MULT  * atr, 8)
            tp1 = round(entry - TP1_MULT * atr, 8)
            tp2 = round(entry - TP2_MULT * atr, 8)
            tp3 = round(entry - TP3_MULT * atr, 8)

        # ── Deteksi state berdasarkan harga saat ini ──────────────
        trailing_active          = False
        trailing_activation_price = tp1
        sl_final                 = sl
        state                    = "normal"

        if is_long:
            if cur >= tp2:
                # Sudah melewati TP2 → SL naik ke TP1, trailing aktif
                sl_final                  = tp1
                trailing_active           = True
                trailing_activation_price = tp1
                state                     = "trailing (past TP2)"
            elif cur >= tp1:
                # Sudah melewati TP1 → BE, trailing aktif
                sl_final                  = entry
                trailing_active           = True
                trailing_activation_price = tp1
                state                     = "breakeven (past TP1)"
        else:
            if cur <= tp2:
                sl_final                  = tp1
                trailing_active           = True
                trailing_activation_price = tp1
                state                     = "trailing (past TP2)"
            elif cur <= tp1:
                sl_final                  = entry
                trailing_active           = True
                trailing_activation_price = tp1
                state                     = "breakeven (past TP1)"

        # ── Update pnl_store ──────────────────────────────────────
        pos["stop_loss"]                  = sl_final
        pos["original_sl"]                = sl
        pos["take_profit"]                = [tp1, tp2, tp3]
        pos["trailing_active"]            = trailing_active
        pos["trailing_activation_price"]  = trailing_activation_price
        pos["trailing_trail_pct"]         = trail_pct

        # Print summary
        price_vs_entry = ((cur - entry) / entry * 100) if entry > 0 else 0
        print(f"    Entry   : {fmt(entry)}")
        print(f"    Current : {fmt(cur)}  ({price_vs_entry:+.2f}%)")
        print(f"    ATR(1H) : {fmt(atr)}  ({atr_pct:.2f}%)")
        print(f"    State   : {state}")
        print(f"    SL      : {fmt(sl_final)}  (original: {fmt(sl)})")
        print(f"    TP1     : {fmt(tp1)}")
        print(f"    TP2     : {fmt(tp2)}")
        print(f"    TP3     : {fmt(tp3)}")
        print(f"    Trail   : {'✓ AKTIF' if trailing_active else '○ belum'}  ({trail_pct}%)")
        print()

        results.append({
            "symbol": sym, "state": state, "sl": sl_final,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "trailing": trailing_active, "trail_pct": trail_pct,
        })

    # Save
    save_json(PNL_STORE, store)
    print("="*60)
    print(f"  ✓ pnl_store.json diupdate untuk {len(results)} posisi")
    print("  Bot akan mulai manage SL/TP mulai scan berikutnya.")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
