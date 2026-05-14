"""
execution/dex_executor.py — Hyperliquid Perpetuals DEX execution layer.

Uses the official hyperliquid-python-sdk (synchronous) wrapped with
asyncio.to_thread so it fits the async agent loop.

Handles:
  - Market entry via IoC limit orders
  - Stop-loss trigger orders
  - Take-profit trigger orders
  - Position querying and market-close
  - Full DRY_RUN simulation (no real orders sent)
"""

import asyncio
from typing import Dict, List, Optional

from config import Config
from execution.base_executor import BaseExecutor
from utils.logger import logger


def _load_hl_exchange():
    """
    Lazily import Hyperliquid SDK components.
    Raises ImportError with install hint if not present.
    """
    try:
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        from eth_account import Account
        from eth_account.signers.local import LocalAccount
        return Exchange, Info, constants, Account, LocalAccount
    except ImportError as exc:
        raise ImportError(
            "Hyperliquid SDK not installed. Run: pip install hyperliquid-python-sdk eth-account"
        ) from exc


class DEXExecutor(BaseExecutor):
    """
    Hyperliquid USDC perpetuals executor.

    Hyperliquid SDK is synchronous; all calls are run in a thread pool
    via asyncio.to_thread() to avoid blocking the event loop.
    """

    def __init__(self) -> None:
        self._exchange = None   # hyperliquid Exchange instance
        self._info = None       # hyperliquid Info instance
        self._wallet_address: str = Config.HL_WALLET_ADDRESS

    # ── Initialisation ─────────────────────────────────────────

    def _init_sdk(self) -> None:
        """Initialise SDK clients (called from thread)."""
        if self._exchange is not None:
            return

        Exchange, Info, constants, Account, LocalAccount = _load_hl_exchange()

        api_url = (
            constants.TESTNET_API_URL if Config.HL_TESTNET else constants.MAINNET_API_URL
        )

        if Config.HL_PRIVATE_KEY:
            account: LocalAccount = Account.from_key(Config.HL_PRIVATE_KEY)
            self._exchange = Exchange(account, api_url)
            self._wallet_address = account.address
        else:
            self._exchange = None  # DRY_RUN — no credentials needed

        self._info = Info(api_url, skip_ws=True)

    # ── Symbol helpers ─────────────────────────────────────────

    @staticmethod
    def _to_hl_coin(symbol: str) -> str:
        """
        Convert "BTCUSDT", "BTC/USDT:USDT", or "BTC" → "BTC".
        Hyperliquid uses bare coin names ("BTC", "ETH", "SOL").
        """
        s = symbol.strip().upper()
        # Strip USDT suffix variants
        for suffix in ("/USDT:USDT", "/USDT", "USDT", ":USDT"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
        return s

    # ── Sync helpers (called via to_thread) ────────────────────

    def _sync_get_positions(self) -> List[Dict]:
        self._init_sdk()
        if not self._wallet_address or not self._info:
            return []
        try:
            state = self._info.user_state(self._wallet_address)
            positions = []
            for ap in state.get("assetPositions", []):
                pos = ap.get("position", {})
                szi = float(pos.get("szi", 0) or 0)
                if szi != 0:
                    positions.append(pos)
            return positions
        except Exception as exc:
            logger.error(f"[DEX] get_positions error: {exc}")
            return []

    def _sync_place_order(
        self,
        coin: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool = False,
        order_type: Optional[Dict] = None,
    ):
        """Place an order synchronously."""
        self._init_sdk()
        if self._exchange is None:
            raise RuntimeError("Exchange not initialised — missing HL_PRIVATE_KEY")

        if order_type is None:
            order_type = {"limit": {"tif": "Ioc"}}  # Immediate-or-Cancel (market-like)

        return self._exchange.order(
            coin,
            is_buy,
            size,
            price,
            order_type,
            reduce_only=reduce_only,
        )

    def _sync_close_position(self, coin: str) -> Optional[Dict]:
        """Close an open position for coin synchronously."""
        self._init_sdk()
        positions = self._sync_get_positions()
        for pos in positions:
            if pos.get("coin") == coin:
                szi = float(pos.get("szi", 0) or 0)
                if szi == 0:
                    continue
                is_buy = szi < 0  # Short → close with buy; Long → close with sell
                entry_px = float(pos.get("entryPx", 0) or 0)
                # Use a wide price tolerance for market-like close
                close_px = entry_px * (1.05 if is_buy else 0.95) if entry_px > 0 else 0
                if close_px <= 0:
                    logger.warning(f"[DEX] Cannot compute close price for {coin}")
                    return None
                result, response = self._sync_place_order(
                    coin, is_buy, abs(szi), close_px, reduce_only=True
                )
                return {"result": result, "response": response}
        return None

    # ── BaseExecutor interface ─────────────────────────────────

    async def execute_signal(self, signal: Dict) -> Optional[Dict]:
        action = signal.get("action", "HOLD")
        if action not in ("LONG", "SHORT"):
            return None

        raw_symbol: str = signal.get("symbol", "")
        coin = self._to_hl_coin(raw_symbol)
        entry_price = float(signal.get("entry_price", 0))
        stop_loss = float(signal.get("stop_loss", 0))
        take_profits: List[float] = [
            float(v) for v in signal.get("take_profit", [0, 0, 0]) if v
        ]
        leverage = max(1, int(signal.get("leverage", 5)))
        position_pct = float(signal.get("position_size_percent", 1.0))

        if entry_price <= 0 or stop_loss <= 0:
            logger.warning(f"[DEX] Invalid signal levels — entry:{entry_price} sl:{stop_loss}")
            return None

        # Size: account_balance × position_pct% × leverage / entry_price
        notional = Config.ACCOUNT_BALANCE * (position_pct / 100.0) * leverage
        qty = round(notional / entry_price, 5)
        if qty <= 0:
            logger.warning(f"[DEX] Computed qty <= 0 for {coin}")
            return None

        is_buy = action == "LONG"

        # ── DRY RUN ──────────────────────────────────────────
        if Config.DRY_RUN:
            logger.info(
                f"[DEX][DRY RUN] {action} {coin} @ {entry_price:,} | "
                f"SL:{stop_loss:,} | Lev:{leverage}× | Size:{position_pct}% | Qty:{qty}"
            )
            return {
                "status": "dry_run",
                "exchange": "hyperliquid",
                "symbol": raw_symbol,
                "coin": coin,
                "action": action,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "leverage": leverage,
                "qty": qty,
            }

        # ── LIVE EXECUTION ───────────────────────────────────
        # Set leverage first (Hyperliquid SDK method)
        try:
            def _set_lev():
                self._init_sdk()
                return self._exchange.update_leverage(leverage, coin, is_cross=False)

            lev_result, lev_response = await asyncio.to_thread(_set_lev)
            logger.debug(f"[DEX] Set leverage {leverage}× for {coin}: {lev_response}")
        except Exception as exc:
            logger.warning(f"[DEX] Set leverage failed (non-fatal): {exc}")

        # Entry order (IoC limit at entry_price — behaves like market)
        try:
            order_result, order_response = await asyncio.to_thread(
                self._sync_place_order,
                coin,
                is_buy,
                qty,
                entry_price,
                False,
                {"limit": {"tif": "Ioc"}},
            )
        except Exception as exc:
            logger.error(f"[DEX] Entry order error: {exc}")
            return None

        if order_response.get("status") != "ok":
            logger.error(f"[DEX] Entry order rejected: {order_response}")
            return None

        logger.info(f"[DEX] Entry placed: {action} {qty} {coin} @ {entry_price:,}")

        result: Dict = {
            "status": "executed",
            "exchange": "hyperliquid",
            "symbol": raw_symbol,
            "coin": coin,
            "action": action,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "leverage": leverage,
            "qty": qty,
            "sl_placed": False,
            "tp1_placed": False,
        }

        # Stop-loss trigger order
        try:
            sl_result, sl_response = await asyncio.to_thread(
                self._sync_place_order,
                coin,
                not is_buy,  # Opposite side
                qty,
                stop_loss,
                True,  # reduce_only
                {"trigger": {"triggerPx": str(stop_loss), "isMarket": True, "tpsl": "sl"}},
            )
            if sl_response.get("status") == "ok":
                result["sl_placed"] = True
                logger.info(f"[DEX] SL placed @ {stop_loss:,}")
            else:
                logger.warning(f"[DEX] SL placement failed: {sl_response}")
        except Exception as exc:
            logger.warning(f"[DEX] SL order error (non-fatal): {exc}")

        # Take-profit trigger orders: TP1 (30%), TP2 (30%), TP3 (40%)
        tp_fractions = [0.30, 0.30, 0.40]
        for i, tp_price in enumerate(take_profits[:3]):
            if tp_price <= 0:
                continue
            frac = tp_fractions[i] if i < len(tp_fractions) else 0.40
            tp_qty = round(qty * frac, 6)
            # Last TP gets remaining qty
            if i == len(take_profits[:3]) - 1:
                placed = sum(round(qty * tp_fractions[j], 6) for j in range(i))
                tp_qty = round(qty - placed, 6)
            if tp_qty <= 0:
                continue
            try:
                tp_result, tp_response = await asyncio.to_thread(
                    self._sync_place_order,
                    coin,
                    not is_buy,
                    tp_qty,
                    tp_price,
                    True,
                    {"trigger": {"triggerPx": str(tp_price), "isMarket": True, "tpsl": "tp"}},
                )
                if tp_response.get("status") == "ok":
                    result[f"tp{i+1}_placed"] = True
                    logger.info(f"[DEX] TP{i+1} placed @ {tp_price:,} qty={tp_qty}")
                else:
                    logger.warning(f"[DEX] TP{i+1} placement failed: {tp_response}")
            except Exception as exc:
                logger.warning(f"[DEX] TP{i+1} order error (non-fatal): {exc}")

        return result

    async def close_position(self, symbol: str) -> Optional[Dict]:
        coin = self._to_hl_coin(symbol)

        if Config.DRY_RUN:
            logger.info(f"[DEX][DRY RUN] Close position: {coin}")
            return {"status": "dry_run_closed", "symbol": symbol, "coin": coin}

        try:
            close_result = await asyncio.to_thread(self._sync_close_position, coin)
            if close_result:
                logger.info(f"[DEX] Position closed: {coin}")
                return {"status": "closed", "symbol": symbol, "coin": coin, **close_result}
            logger.info(f"[DEX] No open position found for {coin}")
            return None
        except Exception as exc:
            logger.error(f"[DEX] close_position error: {exc}")
            return None

    async def get_open_positions(self) -> List[Dict]:
        if Config.DRY_RUN:
            return []
        try:
            return await asyncio.to_thread(self._sync_get_positions)
        except Exception as exc:
            logger.error(f"[DEX] get_open_positions error: {exc}")
            return []

    async def close(self) -> None:
        # SDK has no persistent connection to close
        self._exchange = None
        self._info = None
