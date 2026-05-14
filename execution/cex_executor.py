"""
execution/cex_executor.py — Bybit USDT Perpetual Futures execution layer.

CEX mode = futures trading only (Bybit USDT linear perpetuals).
Supports One-Way mode (positionIdx=0) for Unified Trading Account.

Uses `requests` (sync HTTP) via asyncio.to_thread to avoid
aiohttp DNS resolution issues with VPN/ISP blocking.
"""

import asyncio
import hashlib
import hmac
import math
import time
import urllib.parse
from typing import Dict, List, Optional

import requests

from config import Config
from execution.base_executor import BaseExecutor
from utils.logger import logger



class CEXExecutor(BaseExecutor):

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.verify = True
        self._session.headers.update({"Content-Type": "application/json"})
        self._base_url = (
            "https://api-testnet.bybit.com"
            if Config.BYBIT_TESTNET
            else "https://api.bybit.com"
        )
        self._markets: Dict = {}  # symbol -> market info cache
        self._markets_loaded: bool = False
        self._recv_window = "5000"

    # ── Auth helpers ──────────────────────────────────────────────

    def _sign(self, params: Dict, query_str: str = "") -> Dict[str, str]:
        """Generate Bybit v5 HMAC-SHA256 signature headers."""
        ts = str(int(time.time() * 1000))
        api_key = Config.BYBIT_API_KEY
        secret = Config.BYBIT_SECRET

        # Use provided query string or build from params (keep original order)
        param_str = query_str
        if not param_str and params:
            param_str = urllib.parse.urlencode(params)

        sign_str = f"{ts}{api_key}{self._recv_window}{param_str}"
        signature = hmac.new(
            secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": self._recv_window,
        }

    def _sign_post(self, body_str: str) -> Dict[str, str]:
        """Generate signature for POST requests with JSON body."""
        ts = str(int(time.time() * 1000))
        api_key = Config.BYBIT_API_KEY
        secret = Config.BYBIT_SECRET

        sign_str = f"{ts}{api_key}{self._recv_window}{body_str}"
        signature = hmac.new(
            secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": self._recv_window,
            "Content-Type": "application/json",
        }

    # ── HTTP wrappers ─────────────────────────────────────────────

    def _get(self, endpoint: str, params: Dict = None, auth: bool = False) -> Optional[Dict]:
        """Sync GET request to Bybit v5 API."""
        url = f"{self._base_url}{endpoint}"
        query_str = urllib.parse.urlencode(params) if params else ""
        headers = self._sign(params or {}, query_str) if auth else {}
        try:
            # Use pre-built query string for consistent signing
            full_url = f"{url}?{query_str}" if query_str else url
            resp = self._session.get(full_url, headers=headers, timeout=15)
            # Guard against empty / non-JSON responses (e.g. 401 on missing auth)
            if not resp.text or not resp.text.strip():
                logger.error(
                    f"[CEX] GET {endpoint} empty response (status={resp.status_code}, "
                    f"auth={auth}) — likely missing/invalid signature"
                )
                return None
            try:
                data = resp.json()
            except ValueError:
                snippet = (resp.text or "")[:200].replace("\n", " ")
                logger.error(
                    f"[CEX] GET {endpoint} non-JSON response "
                    f"(status={resp.status_code}, auth={auth}): {snippet!r}"
                )
                return None
            if data.get("retCode") != 0:
                logger.error(f"[CEX] API error {endpoint}: {data.get('retMsg')}")
                return None
            return data.get("result", {})
        except Exception as exc:
            logger.error(f"[CEX] GET {endpoint} error: {exc}")
            return None

    def _post(self, endpoint: str, body: Dict) -> Optional[Dict]:
        """Sync POST request to Bybit v5 API."""
        import json
        url = f"{self._base_url}{endpoint}"
        body_str = json.dumps(body)
        headers = self._sign_post(body_str)
        try:
            resp = self._session.post(url, data=body_str, headers=headers, timeout=15)
            data = resp.json()
            if data.get("retCode") != 0:
                logger.error(f"[CEX] API error {endpoint}: {data.get('retMsg')} body={body}")
                return None
            return data.get("result", {})
        except Exception as exc:
            logger.error(f"[CEX] POST {endpoint} error: {exc}")
            return None

    # ── Market info ───────────────────────────────────────────────

    def _load_markets_sync(self) -> None:
        """Load linear USDT market info from Bybit."""
        cursor = ""
        all_markets = {}
        while True:
            params = {"category": "linear", "limit": "1000"}
            if cursor:
                params["cursor"] = cursor
            result = self._get("/v5/market/instruments-info", params)
            if result is None:
                break
            for item in result.get("list", []):
                sym = item["symbol"]  # e.g. "BTCUSDT"
                all_markets[sym] = {
                    "symbol": sym,
                    "base": item.get("baseCoin", ""),
                    "quote": item.get("quoteCoin", ""),
                    "tick_size": float(item.get("priceFilter", {}).get("tickSize", "0.01")),
                    "qty_step": float(item.get("lotSizeFilter", {}).get("qtyStep", "0.001")),
                    "min_qty": float(item.get("lotSizeFilter", {}).get("minOrderQty", "0.001")),
                    "min_notional": float(item.get("lotSizeFilter", {}).get("minNotionalValue", "5")),
                }
            cursor = result.get("nextPageCursor", "")
            if not cursor:
                break
        self._markets = all_markets
        self._markets_loaded = True
        logger.debug(f"[CEX] Markets loaded: {len(all_markets)} linear USDT pairs")

    async def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            await asyncio.to_thread(self._load_markets_sync)

    # ── Symbol helpers ────────────────────────────────────────────

    @staticmethod
    def _to_bybit(symbol: str) -> str:
        """Convert any format to Bybit format (e.g. BTCUSDT)."""
        s = symbol.strip().upper()
        # Remove ccxt-style formatting
        s = s.replace("/", "").replace(":USDT", "")
        return s

    @staticmethod
    def _qty_dp(step: float) -> int:
        if step <= 0:
            return 3
        return max(0, math.ceil(-math.log10(step)))

    @staticmethod
    def _price_dp(tick: float) -> int:
        if tick <= 0:
            return 2
        return max(0, math.ceil(-math.log10(tick)))

    # ── SL helpers ────────────────────────────────────────────────

    @staticmethod
    def _calc_roi_sl(action: str, entry: float, leverage: float,
                     price_dp: int = 6,
                     max_price_move_pct: float = 15.0,
                     atr_pct: float = 0.0,
                     mode: str = "swing") -> float:
        """
        Calculate Hybrid SL price (ATR floor + ROI cap), capped at max_price_move_pct.
        """
        buffer = 0.995
        max_f  = max_price_move_pct / 100.0

        # Default ROI target (-75%)
        sl_dist_pct = 0.75 / leverage

        # Hybrid Collar Logic
        if atr_pct > 0:
            if mode == "intraday":
                atr_floor_pct = (atr_pct / 100.0) * 1.5
                roi_cap_pct   = 0.50 / leverage
            else:
                atr_floor_pct = (atr_pct / 100.0) * 2.0
                roi_cap_pct   = 0.75 / leverage

            if atr_floor_pct > roi_cap_pct:
                logger.warning(
                    f"[CEX] Leverage {leverage}x too high for ATR {atr_pct}%. "
                    f"ATR floor ({atr_floor_pct*100:.1f}%) > ROI cap ({roi_cap_pct*100:.1f}%). "
                    f"Trade might be stopped out by noise."
                )

            sl_dist_pct = min(max(atr_floor_pct, sl_dist_pct), roi_cap_pct)

        if action == "LONG":
            sl_roi = entry * (1.0 - sl_dist_pct) * buffer
            sl_cap = entry * (1.0 - max_f)
            sl = max(sl_roi, sl_cap)   # use whichever is CLOSER to entry (less loss)
        else:
            sl_roi = entry * (1.0 + sl_dist_pct) / buffer
            sl_cap = entry * (1.0 + max_f)
            sl = min(sl_roi, sl_cap)   # use whichever is CLOSER to entry (less loss)

        return round(sl, price_dp)

    def _place_sl_sync(self, bybit_symbol: str, action: str,
                       qty: float, sl_price: float) -> Optional[Dict]:
        """Place a single stop-loss order. Returns result or None."""
        close_side = "Sell" if action == "LONG" else "Buy"
        trigger_dir = 2 if action == "LONG" else 1
        body = {
            "category":        "linear",
            "symbol":          bybit_symbol,
            "side":            close_side,
            "orderType":       "Market",
            "qty":             str(qty),
            "positionIdx":     0,
            "reduceOnly":      True,
            "triggerPrice":    str(sl_price),
            "triggerDirection": trigger_dir,
            "triggerBy":       "MarkPrice",
            "orderFilter":     "StopOrder",
        }
        return self._post("/v5/order/create", body)

    def _cancel_all_stop_orders_sync(self, bybit_symbol: str) -> bool:
        """Cancel ALL stop orders (StopOrder filter) for a symbol."""
        result = self._post("/v5/order/cancel-all", {
            "category":    "linear",
            "symbol":      bybit_symbol,
            "orderFilter": "StopOrder",
        })
        return result is not None

    async def update_sl_roi(self, symbol: str, action: str,
                            new_entry: float, leverage: float) -> Optional[float]:
        """
        Called after DCA: cancel existing SL, place new SL at -100% ROI
        from the updated average entry price.

        Returns new SL price, or None on failure.
        """
        bybit_symbol = self._to_bybit(symbol)
        if Config.DRY_RUN:
            sl = self._calc_roi_sl(action, new_entry, leverage)
            logger.info(
                f"[CEX][DRY RUN] update_sl_roi {symbol}: "
                f"new_entry={new_entry:.6f} new_sl={sl:.6f}"
            )
            return sl

        await self._ensure_markets()
        mkt = self._markets.get(bybit_symbol, {})
        tick = mkt.get("tick_size", 0.0001)
        price_dp = self._price_dp(tick)
        qty_step = mkt.get("qty_step", 1)
        min_qty = mkt.get("min_qty", 1)
        qdp = self._qty_dp(qty_step)

        # Get current open position qty from Bybit
        pos_info = await asyncio.to_thread(
            self._get, "/v5/position/list",
            {"category": "linear", "symbol": bybit_symbol}, True
        )
        qty = 0.0
        if pos_info and pos_info.get("list"):
            qty = float(pos_info["list"][0].get("size", 0))

        if qty <= 0:
            logger.warning(f"[CEX] update_sl_roi: no open qty for {symbol}")
            return None

        new_sl = self._calc_roi_sl(action, new_entry, leverage, price_dp)

        # Step 1: cancel existing stop orders
        cancelled = await asyncio.to_thread(
            self._cancel_all_stop_orders_sync, bybit_symbol
        )
        if cancelled:
            logger.info(f"[CEX] Cancelled stop orders for {symbol}")
        else:
            logger.warning(f"[CEX] Could not cancel stop orders for {symbol} — placing anyway")

        # Step 2: place new SL
        sl_result = await asyncio.to_thread(
            self._place_sl_sync, bybit_symbol, action, qty, new_sl
        )
        if sl_result:
            logger.info(
                f"[CEX] New SL placed @ {new_sl:.6f} "
                f"({symbol} {action} avg_entry={new_entry:.6f} {leverage:.0f}x)"
            )
            return new_sl
        else:
            logger.warning(f"[CEX] Failed to place new SL for {symbol}")
            return None

    # ── Manual partial/full close ─────────────────────────────────

    def _get_fill_price_sync(
        self, bybit_symbol: str, order_id: str, max_wait_s: float = 3.0
    ) -> Optional[float]:
        """
        Query /v5/execution/list for actual filled price of a market order.
        Returns volume-weighted avg fill price across all fills, or None if
        Bybit hasn't reported any fill within max_wait_s seconds.

        Bybit market orders typically populate execution-list within ~0.5-2s.
        We poll up to max_wait_s before giving up.
        """
        import time as _time
        if not order_id:
            return None
        deadline = _time.time() + max_wait_s
        while _time.time() < deadline:
            result = self._get(
                "/v5/execution/list",
                {"category": "linear", "symbol": bybit_symbol, "orderId": order_id},
                auth=True,
            )
            if result and result.get("list"):
                fills = result["list"]
                total_qty = 0.0
                weighted_px = 0.0
                for f in fills:
                    try:
                        q = float(f.get("execQty", 0))
                        p = float(f.get("execPrice", 0))
                    except (TypeError, ValueError):
                        continue
                    if q > 0 and p > 0:
                        total_qty += q
                        weighted_px += p * q
                if total_qty > 0:
                    return round(weighted_px / total_qty, 8)
            _time.sleep(0.5)
        logger.warning(
            f"[CEX] Fill price not available within {max_wait_s}s "
            f"for orderId={order_id} ({bybit_symbol})"
        )
        return None

    async def partial_close(
        self, symbol: str, action: str, close_pct: float
    ) -> Optional[Dict]:
        """
        Close close_pct% (1-100) of an open position at market price.

        Args:
            symbol   : e.g. "SIRENUSDT"
            action   : "LONG" or "SHORT" (side of the OPEN position)
            close_pct: percentage to close (25, 50, 75, 100)

        Returns dict with {closed_qty, close_side, symbol, pct, price} or None on failure.
        """
        close_pct = max(1.0, min(100.0, close_pct))
        bybit_symbol = self._to_bybit(symbol)

        # Load market info for qty_step
        if not self._markets_loaded:
            await asyncio.to_thread(self._load_markets_sync)
        mkt = self._markets.get(bybit_symbol, {})
        qty_step = mkt.get("qty_step", 1.0)
        min_qty  = mkt.get("min_qty",  1.0)
        qdp      = self._qty_dp(qty_step)

        if Config.DRY_RUN:
            logger.info(
                f"[CEX][DRY RUN] partial_close {symbol} {close_pct:.0f}% — skipped"
            )
            return {"symbol": symbol, "pct": close_pct, "dry_run": True}

        # 1. Get current position size from Bybit
        pos_info = await asyncio.to_thread(
            self._get, "/v5/position/list",
            {"category": "linear", "symbol": bybit_symbol}, True
        )
        if not pos_info or not pos_info.get("list"):
            logger.warning(f"[CEX] partial_close: no position found for {symbol}")
            return None

        current_qty = float(pos_info["list"][0].get("size", 0))
        if current_qty <= 0:
            logger.warning(f"[CEX] partial_close: position size = 0 for {symbol}")
            return None

        # 2. Calculate qty to close (snap to qty_step, floor so we never over-close)
        raw_close = current_qty * close_pct / 100.0
        close_qty = math.floor(raw_close / qty_step) * qty_step
        close_qty = round(close_qty, qdp)

        # For 100% close, use full qty to avoid rounding leaving a dust position
        if close_pct >= 99.9:
            close_qty = current_qty

        if close_qty < min_qty:
            logger.warning(
                f"[CEX] partial_close: computed qty {close_qty} < min_qty {min_qty} "
                f"({symbol} {close_pct:.0f}% of {current_qty})"
            )
            return None

        # 3. Market close order (reduceOnly)
        close_side = "Sell" if action == "LONG" else "Buy"
        body = {
            "category":    "linear",
            "symbol":      bybit_symbol,
            "side":        close_side,
            "orderType":   "Market",
            "qty":         str(close_qty),
            "positionIdx": 0,
            "reduceOnly":  True,
        }

        result = await asyncio.to_thread(self._post, "/v5/order/create", body)
        if result:
            order_id = result.get("orderId", "")
            # Query actual VWAP fill price from execution list
            # (Bybit market order response does NOT include price)
            fill_price = await asyncio.to_thread(
                self._get_fill_price_sync, bybit_symbol, order_id
            )
            logger.info(
                f"[CEX] partial_close OK: {symbol} {close_side} {close_qty} "
                f"({close_pct:.0f}% of {current_qty})"
                + (f" @ {fill_price}" if fill_price else " (fill price pending)")
            )
            return {
                "symbol":      symbol,
                "action":      action,
                "close_side":  close_side,
                "pct":         close_pct,
                "closed_qty":  close_qty,
                "total_qty":   current_qty,
                "order_id":    order_id,
                "price":       fill_price or 0.0,   # actual VWAP, 0 if unavailable
            }
        else:
            logger.error(f"[CEX] partial_close FAILED for {symbol} {close_pct:.0f}%")
            return None

    # ── Pre-trade ─────────────────────────────────────────────────

    def _set_leverage_sync(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol. Skip margin mode switch on Unified Account."""
        import json as _json
        url = f"{self._base_url}/v5/position/set-leverage"
        body = {
            "category": "linear",
            "symbol": symbol,
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        }
        body_str = _json.dumps(body)
        headers = self._sign_post(body_str)
        try:
            resp = self._session.post(url, data=body_str, headers=headers, timeout=15)
            data = resp.json()
            code = data.get("retCode", -1)
            if code == 0:
                logger.debug(f"[CEX] Leverage {leverage}x set for {symbol}")
            elif code == 110043:
                # "leverage not modified" — already at target, OK
                logger.debug(f"[CEX] Leverage already {leverage}x for {symbol}")
            else:
                logger.warning(f"[CEX] set_leverage {symbol}: {data.get('retMsg')}")
        except Exception as exc:
            logger.warning(f"[CEX] set_leverage {symbol}: {exc}")
        return True

    # ── BaseExecutor ──────────────────────────────────────────────

    async def execute_signal(self, signal: Dict) -> Optional[Dict]:
        action = signal.get("action", "HOLD")
        if action not in ("LONG", "SHORT"):
            return None

        raw_symbol: str = signal.get("symbol", "")
        bybit_symbol = self._to_bybit(raw_symbol)
        leverage = max(1, min(int(signal.get("leverage", 5)), 100))
        entry_price = float(signal.get("entry_price", 0))
        stop_loss = float(signal.get("stop_loss", 0))
        take_profits: List[float] = [
            float(v) for v in signal.get("take_profit", [])
            if isinstance(v, (int, float)) and float(v) > 0
        ]
        position_pct = float(signal.get("position_size_percent", 1.0))

        if entry_price <= 0 or stop_loss <= 0:
            logger.warning(f"[CEX] Invalid levels entry={entry_price} sl={stop_loss}")
            return None

        # DRY RUN
        if Config.DRY_RUN:
            logger.info(
                f"[CEX][DRY RUN] {action} {raw_symbol} "
                f"entry={entry_price:,}  sl={stop_loss:,}  lev={leverage}x"
            )
            return {
                "status": "dry_run",
                "exchange": "bybit_futures",
                "symbol": raw_symbol,
                "action": action,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "leverage": leverage,
                "position_size_percent": position_pct,
            }

        # LIVE
        await self._ensure_markets()

        if bybit_symbol not in self._markets:
            logger.error(f"[CEX] {bybit_symbol} not in Bybit linear markets")
            return None

        mkt = self._markets[bybit_symbol]
        qty_step = mkt["qty_step"]
        min_qty = mkt["min_qty"]
        qdp = self._qty_dp(qty_step)

        # Set leverage
        await asyncio.to_thread(self._set_leverage_sync, bybit_symbol, leverage)

        # Calculate qty
        balance = await self.get_balance() or Config.ACCOUNT_BALANCE
        notional = balance * (position_pct / 100.0) * leverage
        qty = max(round(notional / entry_price, qdp), min_qty)
        # Align to qty_step
        qty = math.floor(qty / qty_step) * qty_step
        qty = round(qty, qdp)
        if qty < min_qty:
            qty = min_qty

        side = "Buy" if action == "LONG" else "Sell"
        close_side = "Sell" if action == "LONG" else "Buy"
        pos_idx = 0  # One-Way mode (Unified Trading Account)

        # Entry order
        entry_body = {
            "category": "linear",
            "symbol": bybit_symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
            "positionIdx": pos_idx,
        }
        entry_result = await asyncio.to_thread(
            self._post, "/v5/order/create", entry_body
        )
        if entry_result is None:
            logger.error(f"[CEX] Entry order failed for {bybit_symbol}")
            return None

        order_id = entry_result.get("orderId", "")
        logger.info(f"[CEX] Entry {action} {qty} {bybit_symbol}: {order_id}")

        result: Dict = {
            "status": "executed",
            "exchange": "bybit_futures",
            "symbol": raw_symbol,
            "action": action,
            "entry_order_id": order_id,
            "qty": qty,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "leverage": leverage,
            "sl_order_id": None,
            "tp1_order_id": None,
        }

        # ── PREVENT "10 working stop orders" Bybit limit ──
        # Cancel any leftover SL/TP from previous positions on this symbol
        # before placing new ones. Each entry creates 4 orders (1 SL + 3 TP);
        # without cleanup, after 2-3 entries → hit limit → TP fails to place →
        # position runs without TP exit (relies on trail/SL only).
        try:
            cleanup_ok = await asyncio.to_thread(
                self._cancel_all_stop_orders_sync, bybit_symbol
            )
            if cleanup_ok:
                logger.info(f"[CEX] Cleaned old conditional orders for {bybit_symbol}")
        except Exception as exc:
            logger.warning(f"[CEX] Order cleanup failed for {bybit_symbol}: {exc}")

        # Stop-loss
        if Config.SL_MODE == "none":
            logger.info(f"[CEX] SL_MODE=none — no SL placed for {bybit_symbol}")

        else:
            # SL_MODE="roi": override AI's SL with -100% ROI price
            # + capped at SL_MAX_PRICE_MOVE_PCT to prevent catastrophic losses
            if Config.SL_MODE == "roi":
                tick = mkt.get("tick_size", 0.0001)
                price_dp = self._price_dp(tick)
                _atr_pct = float(signal.get("_atr_pct", 0.0))
                _mode = str(signal.get("_mode", "swing"))
                
                stop_loss = self._calc_roi_sl(
                    action, entry_price, leverage, price_dp,
                    max_price_move_pct=Config.SL_MAX_PRICE_MOVE_PCT,
                    atr_pct=_atr_pct,
                    mode=_mode
                )
                # Calculate actual % from entry for logging
                if action in ("LONG", "BUY"):
                    sl_pct = (stop_loss - entry_price) / entry_price * 100
                else:
                    sl_pct = (entry_price - stop_loss) / entry_price * 100
                logger.info(
                    f"[CEX] SL_MODE=roi: SL → {stop_loss:.6f} "
                    f"({sl_pct:+.2f}% from entry, cap={Config.SL_MAX_PRICE_MOVE_PCT}%, {leverage}x)"
                )
                # FIX: update result dict dengan ROI SL aktual agar pnl_tracker
                # menyimpan SL yang benar — bukan AI SL yang lebih ketat.
                # Tanpa ini, pnl_tracker memantau SL lama dan close posisi prematur.
                result["stop_loss"] = stop_loss

            sl_result = await asyncio.to_thread(
                self._place_sl_sync, bybit_symbol, action, qty, stop_loss
            )
            if sl_result:
                result["sl_order_id"] = sl_result.get("orderId", "")
                logger.info(f"[CEX] SL placed @ {stop_loss}")
            else:
                logger.warning("[CEX] SL failed (monitor manually)")

        # Take-profit orders: TP1 (30%), TP2 (30%), TP3 (40%)
        tp_fractions = [0.30, 0.30, 0.40]
        for i, tp_price in enumerate(take_profits[:3]):
            if tp_price <= 0:
                continue
            frac = tp_fractions[i] if i < len(tp_fractions) else 0.40
            tp_qty = max(round(qty * frac, qdp), min_qty)
            tp_qty = math.floor(tp_qty / qty_step) * qty_step
            tp_qty = round(tp_qty, qdp)

            # Last TP gets remaining qty
            if i == len(take_profits[:3]) - 1:
                placed = sum(
                    max(round(qty * tp_fractions[j], qdp), min_qty)
                    for j in range(i)
                )
                tp_qty = max(round(qty - placed, qdp), min_qty)
                tp_qty = math.floor(tp_qty / qty_step) * qty_step
                tp_qty = round(tp_qty, qdp)

            tp_body = {
                "category": "linear",
                "symbol": bybit_symbol,
                "side": close_side,
                "orderType": "Market",
                "qty": str(tp_qty),
                "positionIdx": pos_idx,
                "reduceOnly": True,
                "triggerPrice": str(tp_price),
                "triggerDirection": 1 if action == "LONG" else 2,
                "triggerBy": "MarkPrice",
                "orderFilter": "StopOrder",
            }
            tp_result = await asyncio.to_thread(
                self._post, "/v5/order/create", tp_body
            )
            if tp_result:
                result[f"tp{i+1}_order_id"] = tp_result.get("orderId")
                logger.info(f"[CEX] TP{i+1} placed @ {tp_price:,} qty={tp_qty}")
            else:
                logger.warning(f"[CEX] TP{i+1} failed (non-fatal)")

        return result

    async def execute_dca(self, symbol: str, action: str, leverage: int,
                          dca_pct: float = 1.0) -> Optional[Dict]:
        """
        Execute a DCA (Dollar Cost Average) order — add to existing position.

        Args:
            symbol: Raw symbol (e.g. BTCUSDT)
            action: LONG or SHORT
            leverage: Current leverage
            dca_pct: % of balance to add (default 1%)
        """
        bybit_symbol = self._to_bybit(symbol)

        if Config.DRY_RUN:
            logger.info(f"[CEX][DRY RUN] DCA {action} {symbol} +{dca_pct}%")
            return {
                "status": "dry_run_dca",
                "symbol": symbol,
                "action": action,
                "dca_pct": dca_pct,
            }

        await self._ensure_markets()

        if bybit_symbol not in self._markets:
            logger.error(f"[CEX] DCA: {bybit_symbol} not in markets")
            return None

        mkt = self._markets[bybit_symbol]
        qty_step = mkt["qty_step"]
        min_qty = mkt["min_qty"]
        qdp = self._qty_dp(qty_step)

        # Get current price
        ticker = await asyncio.to_thread(
            self._get, "/v5/market/tickers",
            {"category": "linear", "symbol": bybit_symbol}
        )
        if not ticker or not ticker.get("list"):
            logger.error(f"[CEX] DCA: cannot get price for {bybit_symbol}")
            return None
        current_price = float(ticker["list"][0].get("lastPrice", 0))
        if current_price <= 0:
            return None

        balance = await self.get_balance() or Config.ACCOUNT_BALANCE
        notional = balance * (dca_pct / 100.0) * leverage
        qty = max(round(notional / current_price, qdp), min_qty)
        qty = math.floor(qty / qty_step) * qty_step
        qty = round(qty, qdp)
        if qty < min_qty:
            qty = min_qty

        side = "Buy" if action == "LONG" else "Sell"

        dca_body = {
            "category": "linear",
            "symbol": bybit_symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
            "positionIdx": 0,
        }
        result = await asyncio.to_thread(
            self._post, "/v5/order/create", dca_body
        )
        if result is None:
            logger.error(f"[CEX] DCA order failed for {bybit_symbol}")
            return None

        order_id = result.get("orderId", "")
        logger.info(
            f"[CEX] DCA {action} +{qty} {bybit_symbol} @ ~{current_price:,.4f}: {order_id}"
        )
        return {
            "status": "dca_executed",
            "symbol": symbol,
            "action": action,
            "qty": qty,
            "price": current_price,
            "dca_pct": dca_pct,
            "order_id": order_id,
        }

    async def close_position(self, symbol: str) -> Optional[Dict]:
        bybit_symbol = self._to_bybit(symbol)
        if Config.DRY_RUN:
            return {"status": "dry_run_closed", "symbol": symbol}

        # Get open positions for this symbol
        result = await asyncio.to_thread(
            self._get, "/v5/position/list",
            {"category": "linear", "symbol": bybit_symbol}, True
        )
        if result is None:
            return None

        for pos in result.get("list", []):
            size = float(pos.get("size", 0))
            if size > 0:
                side = pos.get("side", "")
                close_side = "Sell" if side == "Buy" else "Buy"
                pos_idx = int(pos.get("positionIdx", 0))

                close_body = {
                    "category": "linear",
                    "symbol": bybit_symbol,
                    "side": close_side,
                    "orderType": "Market",
                    "qty": str(size),
                    "positionIdx": pos_idx,
                    "reduceOnly": True,
                }
                close_result = await asyncio.to_thread(
                    self._post, "/v5/order/create", close_body
                )
                if close_result:
                    logger.info(f"[CEX] Closed {symbol}: {close_result.get('orderId')}")
                    return {"status": "closed", "symbol": symbol,
                            "order_id": close_result.get("orderId")}

        logger.info(f"[CEX] No open position for {symbol}")
        return None

    async def get_open_positions(self) -> List[Dict]:
        if Config.DRY_RUN:
            return []

        result = await asyncio.to_thread(
            self._get, "/v5/position/list",
            {"category": "linear", "settleCoin": "USDT", "limit": "200"},
            True
        )
        if result is None:
            return []

        positions = []
        for pos in result.get("list", []):
            size = float(pos.get("size", 0))
            if size > 0:
                side = pos.get("side", "")
                # Bybit returns SL/TP as strings, "0" if not set
                _sl = pos.get("stopLoss", "0") or "0"
                _tp = pos.get("takeProfit", "0") or "0"
                try: sl_val = float(_sl)
                except (ValueError, TypeError): sl_val = 0.0
                try: tp_val = float(_tp)
                except (ValueError, TypeError): tp_val = 0.0
                positions.append({
                    "symbol": pos.get("symbol", ""),
                    "side": "long" if side == "Buy" else "short",
                    "contracts": size,
                    "entryPrice": float(pos.get("avgPrice", 0)),
                    "leverage": float(pos.get("leverage", 1)),
                    "unrealizedPnl": float(pos.get("unrealisedPnl", 0)),
                    "stopLoss": sl_val,        # 0 = no SL on Bybit (DANGER)
                    "takeProfit": tp_val,
                })
        return positions

    async def get_closed_pnl(self, symbol: str = None, limit: int = 10) -> List[Dict]:
        """
        Fetch recently closed positions with realized PnL from Bybit.
        Used by live position sync to reconcile closed trades.
        """
        if Config.DRY_RUN:
            return []

        params: Dict = {"category": "linear", "limit": str(limit)}
        if symbol:
            params["symbol"] = self._to_bybit(symbol)

        result = await asyncio.to_thread(
            self._get, "/v5/position/closed-pnl", params, True
        )
        if result is None:
            return []

        closed = []
        for item in result.get("list", []):
            closed.append({
                "symbol":        item.get("symbol", ""),
                "side":          "long" if item.get("side") == "Buy" else "short",
                "qty":           float(item.get("qty", 0)),
                "entry_price":   float(item.get("avgEntryPrice", 0)),
                "exit_price":    float(item.get("avgExitPrice", 0)),
                "realized_pnl":  float(item.get("closedPnl", 0)),
                "updated_time":  item.get("updatedTime", ""),
            })
        return closed

    async def get_balance(self) -> Optional[float]:
        """Fetch USDT equity from Bybit unified account."""
        if Config.DRY_RUN:
            return None

        result = await asyncio.to_thread(
            self._get, "/v5/account/wallet-balance",
            {"accountType": "UNIFIED"}, True
        )
        if result is None:
            return None

        for account in result.get("list", []):
            total_equity = float(account.get("totalEquity", 0))
            if total_equity > 0:
                return total_equity
            # Fallback: look in individual coins
            for coin in account.get("coin", []):
                if coin.get("coin") == "USDT":
                    equity = float(coin.get("equity", 0))
                    if equity > 0:
                        return equity
        return None

    async def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass
