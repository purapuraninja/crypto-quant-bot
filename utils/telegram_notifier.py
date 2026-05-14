"""
utils/telegram_notifier.py — Telegram alert sender + interactive DCA confirmation.

Uses requests + asyncio.to_thread to avoid Windows SSL issues with aiohttp.
Supports send + receive (polling getUpdates) for interactive y/n DCA prompts.
"""

import asyncio
import re
import time
import requests
from typing import Optional, Dict, List

from utils.logger import logger


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        self._last_update_id = 0  # track Telegram update offset
        self._session = requests.Session()

    # ── Send ──────────────────────────────────────────────────────

    def _sync_send(self, text: str) -> bool:
        try:
            r = self._session.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            return r.status_code == 200
        except Exception as exc:
            logger.warning(f"Telegram send failed: {exc}")
            return False

    async def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        return await asyncio.to_thread(self._sync_send, text)

    # ── Receive (polling) ─────────────────────────────────────────

    def _sync_get_updates(self, timeout: int = 1) -> list:
        """Fetch new messages from Telegram (long polling)."""
        try:
            r = self._session.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params={
                    "offset": self._last_update_id + 1,
                    "timeout": timeout,
                    "allowed_updates": '["message"]',
                },
                timeout=timeout + 5,
            )
            data = r.json()
            updates = data.get("result", [])
            if updates:
                self._last_update_id = updates[-1]["update_id"]
            return updates
        except Exception as exc:
            logger.debug(f"Telegram getUpdates: {exc}")
            return []

    def _sync_flush_old(self) -> None:
        """Flush old messages so we only read new replies."""
        try:
            r = self._session.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params={"offset": -1, "timeout": 0},
                timeout=5,
            )
            data = r.json()
            updates = data.get("result", [])
            if updates:
                self._last_update_id = updates[-1]["update_id"]
        except Exception:
            pass

    async def ask_yes_no(self, question: str, timeout_secs: int = 120) -> Optional[bool]:
        """
        Send a question via Telegram and wait for user to reply y/n.

        Returns:
            True  = user replied y/yes/Y
            False = user replied n/no/N
            None  = timeout (no reply within timeout_secs)
        """
        if not self.enabled:
            return None

        # Flush old messages first
        await asyncio.to_thread(self._sync_flush_old)

        # Send the question
        await self.send(question)
        logger.info(f"[Telegram] Waiting {timeout_secs}s for user reply...")

        start = time.time()
        while time.time() - start < timeout_secs:
            updates = await asyncio.to_thread(self._sync_get_updates, 3)
            for upd in updates:
                msg = upd.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = (msg.get("text") or "").strip().lower()

                # Only accept from our chat
                if chat_id != str(self.chat_id):
                    continue

                if text in ("y", "yes", "ya", "ok", "oke"):
                    logger.info("[Telegram] User replied: YES")
                    return True
                elif text in ("n", "no", "tidak", "skip", "nope"):
                    logger.info("[Telegram] User replied: NO")
                    return False

            # Small delay between polls
            await asyncio.sleep(1)

        logger.info("[Telegram] No reply — timeout")
        return None

    # ── Alert helpers ─────────────────────────────────────────────

    async def alert_signal(self, signal: Dict) -> None:
        action = signal.get("action", "HOLD")
        symbol = signal.get("symbol", "?")
        score = signal.get("score", 0.0)
        conf = signal.get("confidence", 0.0) * 100
        emoji = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "⏸"}.get(action, "⏸")

        lines = [f"{emoji} <b>{action}</b> — <code>{symbol}</code>",
                 f"Score: <b>{float(score):.1f}</b>  Conf: <b>{conf:.0f}%</b>"]

        if action in ("LONG", "SHORT"):
            entry = signal.get("entry_price", 0)
            sl = signal.get("stop_loss", 0)
            tp_raw = signal.get("take_profit", [])
            if not isinstance(tp_raw, (list, tuple)):
                tp_raw = [tp_raw]
            tps = [float(v) for v in tp_raw if v]
            lev = signal.get("leverage", 0)
            size = signal.get("position_size_percent", 0)
            risk = signal.get("risk_level", "")
            tp_str = " / ".join(f"{t:,.4f}" for t in tps if t > 0)
            lines += [
                f"Entry : <code>{float(entry):,.4f}</code>",
                f"SL    : <code>{float(sl):,.4f}</code>",
                f"TP    : <code>{tp_str}</code>",
                f"Lev   : <b>{lev}x</b>  Size: <b>{size}%</b>  Risk: {risk}",
            ]
            if signal.get("trailing_stop", {}).get("enabled"):
                lines.append("Trailing stop: <b>ENABLED</b>")

        if signal.get("reason"):
            lines.append(f"\n{signal['reason']}")

        await self.send("\n".join(lines))

    async def alert_execution(self, result: Dict) -> None:
        status = result.get("status", "?")
        symbol = result.get("symbol", "?")
        action = result.get("action", "?")
        entry = result.get("entry_price", 0)
        sl = result.get("stop_loss", 0)
        lev = result.get("leverage", 0)
        dry = status == "dry_run"
        prefix = "[DRY RUN] " if dry else ">> "
        await self.send(
            f"{prefix}ORDER EXECUTED\n"
            f"Symbol : <code>{symbol}</code>\n"
            f"Action : <b>{action}</b>\n"
            f"Entry  : <code>{float(entry):,.4f}</code>\n"
            f"SL     : <code>{float(sl):,.4f}</code>\n"
            f"Lev    : <b>{lev}x</b>"
        )

    async def alert_pnl_close(self, trade: Dict) -> None:
        pnl = float(trade.get("pnl_usdt", 0))
        pnl_pct = float(trade.get("pnl_pct", 0))
        sym = trade.get("symbol", "?")
        reason = trade.get("reason", "")
        emoji = "W" if pnl >= 0 else "L"
        await self.send(
            f"[{emoji}] <b>POSITION CLOSED</b>\n"
            f"Symbol : <code>{sym}</code>\n"
            f"PnL    : <b>{pnl:+.4f} USDT ({pnl_pct:+.2f}%)</b>\n"
            f"Reason : {reason}"
        )

    async def alert_error(self, message: str) -> None:
        await self.send(f"[ERROR] <b>BOT ERROR</b>\n{message[:500]}")

    # ── Command polling ───────────────────────────────────────────

    def _sync_poll_commands(self) -> List[Dict]:
        """
        Poll Telegram for new messages and parse them as bot commands.
        Returns list of parsed command dicts.

        Supported formats (case-insensitive):
          close SIRENUSDT 25       → partial close 25%
          close SIRENUSDT 25%      → partial close 25%
          close SIRENUSDT          → ask for % (not yet implemented)
          positions / pos          → list open positions
          status                   → alias for positions
          help                     → show available commands
        """
        updates = self._sync_get_updates(timeout=1)
        commands = []
        for upd in updates:
            msg  = upd.get("message", {})
            chat = str(msg.get("chat", {}).get("id", ""))
            text = (msg.get("text") or "").strip()

            if chat != str(self.chat_id) or not text:
                continue

            cmd = self._parse_command(text)
            if cmd:
                logger.info(f"[Telegram] Command received: {text!r} → {cmd}")
                commands.append(cmd)
        return commands

    @staticmethod
    def _parse_command(text: str) -> Optional[Dict]:
        """
        Parse a raw Telegram message into a command dict.

        Returns e.g.:
          {"type": "close", "symbol": "SIRENUSDT", "pct": 25.0}
          {"type": "positions"}
          {"type": "help"}
        or None if not a recognized command.
        """
        t = text.strip().lower()

        # ── close SYMBOL PCT ──────────────────────────────────────
        # Accepts: close sirenusdt 25 / close sirenusdt 25% / close sirenusdt50%
        m = re.match(
            r'^close\s+([a-z0-9]+(?:usdt|btc|eth)?)\s*([\d.]+)\s*%?$', t
        )
        if m:
            sym = m.group(1).upper()
            if not sym.endswith("USDT"):
                sym += "USDT"
            pct = float(m.group(2))
            pct = max(1.0, min(100.0, pct))
            return {"type": "close", "symbol": sym, "pct": pct}

        # ── positions / pos / status ──────────────────────────────
        if t in ("positions", "pos", "status", "p"):
            return {"type": "positions"}

        # ── help ──────────────────────────────────────────────────
        if t in ("help", "?", "h"):
            return {"type": "help"}

        # ── perf — performance breakdown ─────────────────────────
        if t in ("perf", "performance", "stats", "report"):
            return {"type": "perf"}

        return None

    async def check_commands(self) -> List[Dict]:
        """Async wrapper — call each scan cycle to collect pending commands."""
        if not self.enabled:
            return []
        return await asyncio.to_thread(self._sync_poll_commands)

    async def send_help(self) -> None:
        text = (
            "<b>Bot Commands</b>\n\n"
            "<code>close SYMBOL PCT</code> — close % of position\n"
            "  e.g. <code>close sirenusdt 25</code>\n"
            "  e.g. <code>close btcusdt 100</code> (full close)\n\n"
            "<code>positions</code> — show open positions (alias: pos, status, p)\n"
            "<code>perf</code> — performance breakdown by AI tier + symbol\n"
            "<code>help</code> — show this message"
        )
        await self.send(text)

    async def send_positions(self, open_positions: Dict) -> None:
        if not open_positions:
            await self.send("No open positions.")
            return
        lines = ["<b>Open Positions</b>\n"]
        for sym, pos in open_positions.items():
            act   = pos.get("action", "?")
            entry = pos.get("entry_price", 0)
            sl    = pos.get("stop_loss", 0)
            lev   = pos.get("leverage", 1)
            dca   = pos.get("dca_count", 0)
            side_icon = "L" if act == "LONG" else "S"
            sl_txt = f"{sl:.6f}" if sl > 0 else "NO SL"
            lines.append(
                f"<code>{sym}</code> [{side_icon}] {lev}x\n"
                f"  Entry: {entry:.6f}  SL: {sl_txt}"
                + (f"  DCA:{dca}" if dca else "")
            )
        await self.send("\n".join(lines))
