"""
config.py — Central configuration loader.
Reads all settings from .env via python-dotenv.

Supported AI Providers:
  - anthropic : Claude models via Anthropic SDK
  - blink     : Claude models via Blink API (OpenAI-compatible)
  - openai    : GPT models via OpenAI API
  - gemini    : Google Gemini via OpenAI-compatible endpoint
  - glm       : GLM models via ZhipuAI (OpenAI-compatible)
  - minimax   : MiniMax models via OpenAI-compatible endpoint
  - custom    : Any OpenAI-compatible API
"""

import os
from typing import List
from dotenv import load_dotenv

load_dotenv()


# ── Provider presets (base_url defaults) ──────────────────────────
_PROVIDER_PRESETS = {
    "openai":     {"base_url": "https://api.openai.com/v1",            "default_model": "gpt-4.1-mini"},
    "gemini":     {"base_url": "https://generativelanguage.googleapis.com/v1beta", "default_model": "gemini-2.0-flash"},
    "glm":        {"base_url": "https://api.z.ai/api/paas/v4",         "default_model": "glm-5"},
    "minimax":    {"base_url": "https://api.minimaxi.chat/v1",         "default_model": "MiniMax-M1-80k"},
    "blink":      {"base_url": "https://core.blink.new/api/v1/ai",     "default_model": "anthropic/claude-sonnet-4.5"},
    "qwen":       {"base_url": "https://openrouter.ai/api/v1",         "default_model": "qwen/qwen3.5-flash-02-23"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1",         "default_model": "deepseek/deepseek-v4-flash"},
    "deepseek":   {"base_url": "https://api.deepseek.com/v1",          "default_model": "deepseek-v4-flash"},
}

# All providers that use OpenAI-compatible SDK
_OPENAI_COMPATIBLE = {"openai", "gemini", "glm", "minimax", "blink", "qwen", "openrouter", "deepseek", "custom"}


class Config:
    # ── AI Provider ───────────────────────────────────────────
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "blink").lower()

    # Anthropic native SDK
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    # Blink
    BLINK_API_KEY: str = os.getenv("BLINK_API_KEY", "")
    BLINK_BASE_URL: str = os.getenv("BLINK_BASE_URL", _PROVIDER_PRESETS["blink"]["base_url"])
    BLINK_MODEL: str = os.getenv("BLINK_MODEL", _PROVIDER_PRESETS["blink"]["default_model"])

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "")

    # Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_BASE_URL: str = os.getenv("GEMINI_BASE_URL", _PROVIDER_PRESETS["gemini"]["base_url"])
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", _PROVIDER_PRESETS["gemini"]["default_model"])

    # GLM (ZhipuAI)
    GLM_API_KEY: str = os.getenv("GLM_API_KEY", "")
    GLM_BASE_URL: str = os.getenv("GLM_BASE_URL", _PROVIDER_PRESETS["glm"]["base_url"])
    GLM_MODEL: str = os.getenv("GLM_MODEL", _PROVIDER_PRESETS["glm"]["default_model"])

    # MiniMax
    MINIMAX_API_KEY: str = os.getenv("MINIMAX_API_KEY", "")
    MINIMAX_BASE_URL: str = os.getenv("MINIMAX_BASE_URL", _PROVIDER_PRESETS["minimax"]["base_url"])
    MINIMAX_MODEL: str = os.getenv("MINIMAX_MODEL", _PROVIDER_PRESETS["minimax"]["default_model"])

    # Custom (any OpenAI-compatible)
    CUSTOM_API_KEY: str = os.getenv("CUSTOM_API_KEY", "")
    CUSTOM_BASE_URL: str = os.getenv("CUSTOM_BASE_URL", "")
    CUSTOM_MODEL: str = os.getenv("CUSTOM_MODEL", "")

    # Qwen via OpenRouter (OpenAI-compatible)
    QWEN_API_KEY: str = os.getenv("QWEN_API_KEY", "")
    QWEN_BASE_URL: str = os.getenv("QWEN_BASE_URL", _PROVIDER_PRESETS["qwen"]["base_url"])
    QWEN_MODEL: str = os.getenv("QWEN_MODEL", _PROVIDER_PRESETS["qwen"]["default_model"])

    # OpenRouter (proper slot — generic alias untuk semua model di OpenRouter,
    # mis. deepseek/deepseek-v4-flash. Sebelumnya ditumpangkan di slot qwen)
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", _PROVIDER_PRESETS["openrouter"]["base_url"])
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", _PROVIDER_PRESETS["openrouter"]["default_model"])

    # DeepSeek native API (api.deepseek.com) — punya prompt caching $0.0028/M
    # input untuk cache hit. Auto cache sisi server, tidak perlu code change.
    # Model: deepseek-chat (= V4-Flash), deepseek-reasoner (= V4-Pro reasoning).
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", _PROVIDER_PRESETS["deepseek"]["base_url"])
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", _PROVIDER_PRESETS["deepseek"]["default_model"])

    # ── AI Fallback (Tier 2) ──────────────────────────────────
    # When primary AI fails (timeout / API error / parse failure / empty response),
    # bot retries the same scan with this fallback provider. Empty = no fallback.
    AI_PROVIDER_FALLBACK: str = os.getenv("AI_PROVIDER_FALLBACK", "").lower()

    # ── AI Tier-2 Override (optional) ─────────────────────────
    # If set, Tier-2 uses this instead of pulling defaults from
    # AI_PROVIDER_FALLBACK's standard slot. Useful when Tier-2 reuses the
    # same provider as Tier-1 (e.g. both via OpenRouter) but with a different model.
    AI_TIER2_MODEL: str = os.getenv("AI_TIER2_MODEL", "")
    AI_TIER2_API_KEY: str = os.getenv("AI_TIER2_API_KEY", "")
    AI_TIER2_BASE_URL: str = os.getenv("AI_TIER2_BASE_URL", "")

    # ── AI Tier-3 (optional 2nd fallback) ─────────────────────
    # Triggered when both Tier-1 and Tier-2 fail.
    AI_PROVIDER_TIER3: str = os.getenv("AI_PROVIDER_TIER3", "").lower()
    AI_TIER3_MODEL: str = os.getenv("AI_TIER3_MODEL", "")
    AI_TIER3_API_KEY: str = os.getenv("AI_TIER3_API_KEY", "")
    AI_TIER3_BASE_URL: str = os.getenv("AI_TIER3_BASE_URL", "")

    # ── Exchange ─────────────────────────────────────────────
    EXCHANGE_MODE: str = os.getenv("EXCHANGE_MODE", "CEX").upper()
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    # CEX — Bybit
    BYBIT_API_KEY: str = os.getenv("BYBIT_API_KEY", "")
    BYBIT_SECRET: str = os.getenv("BYBIT_SECRET", "")
    BYBIT_TESTNET: bool = os.getenv("BYBIT_TESTNET", "true").lower() == "true"

    # DEX — Hyperliquid
    HL_WALLET_ADDRESS: str = os.getenv("HL_WALLET_ADDRESS", "")
    HL_PRIVATE_KEY: str = os.getenv("HL_PRIVATE_KEY", "")
    HL_TESTNET: bool = os.getenv("HL_TESTNET", "true").lower() == "true"

    # Binance (on-chain/regime data — public endpoints only, key optional)
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")

    # ── Telegram ──────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # ── Trading ───────────────────────────────────────────────
    _raw_symbols: str = os.getenv("SYMBOLS", "BTC/USDT:USDT")
    SYMBOLS: List[str] = [s.strip() for s in _raw_symbols.split(",") if s.strip()]
    ACCOUNT_BALANCE: float = float(os.getenv("ACCOUNT_BALANCE", "10000"))
    SCAN_INTERVAL: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "2"))

    # ── Symbol blacklist (HARD reject) ────────────────────────
    # Bot REFUSE entry pada simbol di list ini, meski:
    #   - lolos quality score
    #   - di-pick AUTO_SYMBOLS auto-discovery
    #   - manual ada di SYMBOLS list
    # Tujuan: kunci simbol yang historis rugi konsisten supaya tidak nyangkut lagi.
    _raw_blacklist: str = os.getenv("SYMBOL_BLACKLIST", "")
    SYMBOL_BLACKLIST: List[str] = [
        s.strip().upper() for s in _raw_blacklist.split(",") if s.strip()
    ]

    # ── Data-only symbols (fetch but don't trade) ─────────────
    # Simbol yang di-fetch market datanya untuk konteks AI (mis. BTC sebagai
    # global bias anchor di prompt, lihat signal_engine.py BTC GLOBAL BIAS),
    # TAPI bot tidak boleh masuk posisi di simbol ini.
    # Default: BTCUSDT — dipakai sebagai "leader" untuk menentukan
    # alt LONG/SHORT bias, tapi bot punya WR rendah trading BTC sendiri.
    _raw_data_only: str = os.getenv("DATA_ONLY_SYMBOLS", "BTCUSDT")
    DATA_ONLY_SYMBOLS: List[str] = [
        s.strip().upper() for s in _raw_data_only.split(",") if s.strip()
    ]

    # ── Detection thresholds (4 risk filters) ─────────────────
    # Reject signal kalau spread > MAX_SPREAD_PCT (orderbook ask-bid / mid)
    # Default 0.15% — coin mainstream tipikal <0.05%, low-cap >0.2%.
    MAX_SPREAD_PCT: float = float(os.getenv("MAX_SPREAD_PCT", "0.15"))

    # Reject signal kalau wick (high-close atau close-low) > N × ATR pada
    # 1 candle terakhir. Indikasi stop-hunt / manipulation.
    MAX_WICK_ATR_RATIO: float = float(os.getenv("MAX_WICK_ATR_RATIO", "2.5"))

    # Reject signal kalau funding rate berubah > N × stdev dari 24h history.
    # 3.0 = 3-sigma anomaly, indikasi funding spike / manipulation.
    FUNDING_ANOMALY_SIGMA: float = float(os.getenv("FUNDING_ANOMALY_SIGMA", "3.0"))

    # ── Auto symbol discovery ──────────────────────────────────
    # If AUTO_SYMBOLS=true, SYMBOLS list above is ignored at startup.
    # Bot fetches top AUTO_SYMBOLS_COUNT USDT-perps by 24h volume from Bybit.
    # Symbol list is refreshed every AUTO_SYMBOLS_REFRESH_H hours.
    AUTO_SYMBOLS: bool = os.getenv("AUTO_SYMBOLS", "false").lower() == "true"
    AUTO_SYMBOLS_COUNT: int = int(os.getenv("AUTO_SYMBOLS_COUNT", "30"))
    AUTO_SYMBOLS_REFRESH_H: float = float(os.getenv("AUTO_SYMBOLS_REFRESH_H", "4"))
    # Min 24h USD turnover utk masuk auto pool — filter wash + low-cap
    AUTO_SYMBOLS_MIN_TURNOVER_USD: float = float(
        os.getenv("AUTO_SYMBOLS_MIN_TURNOVER_USD", "30000000")
    )

    # Position sizing limits (% of balance)
    MIN_POSITION_SIZE_PCT: float = float(os.getenv("MIN_POSITION_SIZE_PCT", "1.0"))
    MAX_POSITION_SIZE_PCT: float = float(os.getenv("MAX_POSITION_SIZE_PCT", "5.0"))

    # ── SL mode ───────────────────────────────────────────────
    # "atr"  : SL di level ATR dari AI (default lama)
    # "roi"  : SL di -100% ROI (near liquidation). DCA di -75% ROI, SL otomatis
    #          update ke avg entry baru setelah DCA
    # "none" : tidak place SL sama sekali
    SL_MODE: str = os.getenv("SL_MODE", "roi").lower()

    # ── DCA (Dollar Cost Averaging) ──────────────────────────
    DCA_ENABLED: bool = os.getenv("DCA_ENABLED", "true").lower() == "true"
    DCA_SIZE_PCT: float = float(os.getenv("DCA_SIZE_PCT", "1.0"))  # % of balance per DCA
    DCA_TIMEOUT: int = int(os.getenv("DCA_TIMEOUT", "120"))  # seconds to wait for user reply
    # ROI threshold to trigger DCA alert (leveraged %, negative = losing)
    # e.g. -75 = ask DCA when position is down 75% of margin
    DCA_ROI_TRIGGER: float = float(os.getenv("DCA_ROI_TRIGGER", "-75.0"))

    # ── Risk/Reward filter ────────────────────────────────────
    # Reject signal if weighted TP reward / SL risk < MIN_RR.
    # Weighted reward follows the planned exits: TP1, TP2, then TP3 remainder.
    MIN_RR: float = float(os.getenv("MIN_RR", "1.2"))

    # ── Hard quality floor (overrides adaptive min) ───────────
    # AI-rated score and confidence are subjective — bot's own filter floor
    # provides backstop. Adaptive min_score/min_conf may go lower in normal
    # mode but bot will still reject anything below MIN_SCORE_HARD/MIN_CONF_HARD.
    MIN_SCORE_HARD: float = float(os.getenv("MIN_SCORE_HARD", "7.0"))
    MIN_CONFIDENCE_HARD: float = float(os.getenv("MIN_CONFIDENCE_HARD", "0.65"))

    # Minimum SL distance from entry (% price). Tighter SL = noise stops out.
    # Bybit fee × 2 = 0.11%, plus need buffer for normal wick.
    # 0.4% minimum = SL fires only on real movement, not random tick.
    MIN_SL_DISTANCE_PCT: float = float(os.getenv("MIN_SL_DISTANCE_PCT", "0.40"))

    # Adaptive leverage guardrail (applied in Phase-3 post-filter)
    LEVERAGE_FLOOR: int = int(os.getenv("LEVERAGE_FLOOR", "30"))
    LEVERAGE_CAP_DEFENSIVE: int = int(os.getenv("LEVERAGE_CAP_DEFENSIVE", "30"))
    LEVERAGE_CAP_CONSERVATIVE: int = int(os.getenv("LEVERAGE_CAP_CONSERVATIVE", "40"))
    LEVERAGE_CAP_NORMAL: int = int(os.getenv("LEVERAGE_CAP_NORMAL", "45"))
    LEVERAGE_CAP_AGGRESSIVE: int = int(os.getenv("LEVERAGE_CAP_AGGRESSIVE", "50"))

    # ICT/SMC retest gate:
    # strict = legacy hard gate, soft = reject obvious chase only,
    # off = disable this gate entirely.
    SMC_FILTER_MODE: str = os.getenv("SMC_FILTER_MODE", "soft").lower()
    SMC_SOFT_BYPASS_SCORE: float = float(os.getenv("SMC_SOFT_BYPASS_SCORE", "8.2"))
    SMC_SOFT_BYPASS_CONFIDENCE: float = float(os.getenv("SMC_SOFT_BYPASS_CONFIDENCE", "0.72"))

    # ── Anti-overtrading ──────────────────────────────────────
    # Max number of trades (open+closed today) per symbol per day
    MAX_TRADES_PER_SYMBOL_DAY: int = int(os.getenv("MAX_TRADES_PER_SYMBOL_DAY", "3"))
    # Hours to wait before re-entering a symbol after a losing trade
    SYMBOL_LOSS_COOLDOWN_H: float = float(os.getenv("SYMBOL_LOSS_COOLDOWN_H", "4.0"))

    # ── Daily circuit breaker ─────────────────────────────────
    # Stop opening NEW positions if daily realized loss exceeds this (USDT)
    # 0 = disabled. e.g. 3.0 = halt new entries if today lost > $3
    DAILY_LOSS_LIMIT_USDT: float = float(os.getenv("DAILY_LOSS_LIMIT_USDT", "8.0"))

    # ── SL price cap ─────────────────────────────────────────
    # Max % price move allowed from entry before SL fires (regardless of leverage)
    # Prevents "SL_MODE=roi" from allowing -20% move on 5x positions
    # e.g. 6.0 = SL placed at most 6% from entry price
    SL_MAX_PRICE_MOVE_PCT: float = float(os.getenv("SL_MAX_PRICE_MOVE_PCT", "15.0"))

    # ── AI Learning ───────────────────────────────────────────
    AI_LEARNING: bool = os.getenv("AI_LEARNING", "true").lower() == "true"

    # ── Trading Mode ──────────────────────────────────────────
    # "swing"    : 4H primary, Daily HTF, 1H + 15M confirmation
    # "intraday" : 15M primary, 1H HTF, 4H super-HTF, 5M entry timing
    TRADING_MODE: str = os.getenv("TRADING_MODE", "swing").lower()

    # ── Time-based Auto-Close (P1 — prevent intraday → swing drift) ───
    # Force close if intraday position has been held too long with no progress.
    # Stagnant exit: > STAGNANT_HOLD_HOURS AND |ROI| < STAGNANT_ROI_PCT
    # Hard exit  : > MAX_HOLD_HOURS_INTRADAY (regardless of state)
    STAGNANT_HOLD_HOURS: float = float(os.getenv("STAGNANT_HOLD_HOURS", "6.0"))
    STAGNANT_ROI_PCT: float = float(os.getenv("STAGNANT_ROI_PCT", "5.0"))
    MAX_HOLD_HOURS_INTRADAY: float = float(os.getenv("MAX_HOLD_HOURS_INTRADAY", "12.0"))
    MAX_HOLD_HOURS_SWING: float = float(os.getenv("MAX_HOLD_HOURS_SWING", "72.0"))

    # ── Partial Close at TP (P2) ──────────────────────────────
    # Close X% of position when TP1/TP2 hit (instead of just moving SL)
    # TP3 closes the remainder.
    PARTIAL_CLOSE_ENABLED: bool = os.getenv("PARTIAL_CLOSE_ENABLED", "true").lower() == "true"
    TP1_CLOSE_PCT: float = float(os.getenv("TP1_CLOSE_PCT", "30.0"))
    TP2_CLOSE_PCT: float = float(os.getenv("TP2_CLOSE_PCT", "30.0"))
    # TP3 closes whatever remains (typically 40%)

    # Minimum % price move from entry required to actually do a partial close.
    # Bybit taker fee = 0.055% × 2 sides = 0.11% breakeven. If TP is set tighter
    # than this, partial close becomes net negative after fees. Default 0.2% =
    # break fees + small net profit margin.
    PARTIAL_CLOSE_MIN_MOVE_PCT: float = float(os.getenv("PARTIAL_CLOSE_MIN_MOVE_PCT", "0.20"))

    # After TP1 hit, SL moves to a profit lock. ROI mode is leverage-aware:
    # 5% ROI on 30x = 0.1667% price move from entry. Legacy price-percent lock
    # remains available as a fallback when BE_PROFIT_LOCK_ROI_PCT is disabled.
    BE_PROFIT_LOCK_ROI_PCT: float = float(os.getenv("BE_PROFIT_LOCK_ROI_PCT", "5.0"))
    BE_PROFIT_LOCK_PCT: float = float(os.getenv("BE_PROFIT_LOCK_PCT", "0.30"))

    # ── Quick-Exit on Reversal (P3) ────────────────────────────
    # Force close if technical signals reverse before SL/TP hit.
    # Triggers: structure flip + RSI extreme + momentum reverse
    QUICK_EXIT_ENABLED: bool = os.getenv("QUICK_EXIT_ENABLED", "true").lower() == "true"
    # RSI threshold: LONG exits if rsi < this; SHORT exits if rsi > (100-this)
    QUICK_EXIT_RSI_LONG: float = float(os.getenv("QUICK_EXIT_RSI_LONG", "35.0"))
    QUICK_EXIT_RSI_SHORT: float = float(os.getenv("QUICK_EXIT_RSI_SHORT", "65.0"))
    # Only trigger if position is at least N hours old (avoid premature exits)
    QUICK_EXIT_MIN_HOURS: float = float(os.getenv("QUICK_EXIT_MIN_HOURS", "1.0"))

    # Fast failed-setup exit:
    # If a new position quickly moves against entry and the market structure no
    # longer supports the trade direction, close before waiting for full SL.
    FAILED_SETUP_EXIT_ENABLED: bool = os.getenv("FAILED_SETUP_EXIT_ENABLED", "true").lower() == "true"
    FAILED_SETUP_MIN_MINUTES: float = float(os.getenv("FAILED_SETUP_MIN_MINUTES", "5.0"))
    FAILED_SETUP_MAX_MINUTES: float = float(os.getenv("FAILED_SETUP_MAX_MINUTES", "30.0"))
    FAILED_SETUP_MAX_ROI_LOSS_PCT: float = float(os.getenv("FAILED_SETUP_MAX_ROI_LOSS_PCT", "-10.0"))

    # ── Per-Mode Auto-Tuning ─────────────────────────────────
    # Intraday mode needs different defaults than swing for many shared
    # parameters. Instead of requiring manual .env edits on every mode
    # switch, we auto-adjust here.
    #
    # Priority: INTRADAY_<PARAM> env var > intraday default > swing value
    # User can set e.g. INTRADAY_SCAN_INTERVAL_SECONDS=180 to customize.
    #
    # Swing defaults are the class-level defaults above — no override needed.
    # fmt: off
    # ┌─────────────────────────────┬─────────┬─────────┬──────────────────────────────────┐
    # │ Parameter                   │ Swing   │Intraday │ Why                              │
    # ├─────────────────────────────┼─────────┼─────────┼──────────────────────────────────┤
    # │ SCAN_INTERVAL               │ 900s    │ 300s    │ 15M candle, need faster scan     │
    # │ MAX_OPEN_POSITIONS          │ 4       │ 3       │ Less concurrent, small account   │
    # │ SYMBOL_LOSS_COOLDOWN_H      │ 4.0h    │ 1.5h   │ Setups change fast in intraday   │
    # │ MAX_TRADES_PER_SYMBOL_DAY   │ 3       │ 5       │ More opportunities intraday      │
    # │ BE_PROFIT_LOCK_PCT          │ 0.50%   │ 0.15%  │ TP1=0.3%, lock must be < TP1     │
    # │ DCA_ROI_TRIGGER             │ -75%    │ -40%   │ Intraday SL cap = -50% ROI       │
    # │ SL_MAX_PRICE_MOVE_PCT       │ 15%     │ 5%     │ Tighter for faster timeframe     │
    # │ DAILY_LOSS_LIMIT_USDT       │ 8.0     │ 5.0    │ Tighter daily limit intraday     │
    # │ STAGNANT_HOLD_HOURS         │ 6.0h    │ 3.0h   │ Faster stagnant detection        │
    # └─────────────────────────────┴─────────┴─────────┴──────────────────────────────────┘
    # fmt: on
    if TRADING_MODE == "intraday":
        SCAN_INTERVAL           = int(os.getenv("INTRADAY_SCAN_INTERVAL_SECONDS", "300"))
        MAX_OPEN_POSITIONS      = int(os.getenv("INTRADAY_MAX_OPEN_POSITIONS", "3"))
        SYMBOL_LOSS_COOLDOWN_H  = float(os.getenv("INTRADAY_SYMBOL_LOSS_COOLDOWN_H", "1.5"))
        MAX_TRADES_PER_SYMBOL_DAY = int(os.getenv("INTRADAY_MAX_TRADES_PER_SYMBOL_DAY", "5"))
        BE_PROFIT_LOCK_ROI_PCT  = float(os.getenv("INTRADAY_BE_PROFIT_LOCK_ROI_PCT", "5.0"))
        BE_PROFIT_LOCK_PCT      = float(os.getenv("INTRADAY_BE_PROFIT_LOCK_PCT", "0.15"))
        DCA_ROI_TRIGGER         = float(os.getenv("INTRADAY_DCA_ROI_TRIGGER", "-40.0"))
        SL_MAX_PRICE_MOVE_PCT   = float(os.getenv("INTRADAY_SL_MAX_PRICE_MOVE_PCT", "5.0"))
        DAILY_LOSS_LIMIT_USDT   = float(os.getenv("INTRADAY_DAILY_LOSS_LIMIT_USDT", "5.0"))
        STAGNANT_HOLD_HOURS     = float(os.getenv("INTRADAY_STAGNANT_HOLD_HOURS", "3.0"))

    # ── Helper: resolve provider config ───────────────────────

    @classmethod
    def get_ai_config(cls, provider: str = None) -> dict:
        """Return {api_key, model, base_url, provider} for the given provider
        (defaults to AI_PROVIDER). Used by SignalEngine for both tier-1 and tier-2."""
        p = (provider or cls.AI_PROVIDER).lower()

        if p == "anthropic":
            return {
                "provider": "anthropic",
                "api_key": cls.ANTHROPIC_API_KEY,
                "model": cls.CLAUDE_MODEL,
                "base_url": "",
            }
        elif p == "blink":
            return {
                "provider": "blink",
                "api_key": cls.BLINK_API_KEY,
                "model": cls.BLINK_MODEL,
                "base_url": cls.BLINK_BASE_URL,
            }
        elif p == "openai":
            return {
                "provider": "openai",
                "api_key": cls.OPENAI_API_KEY,
                "model": cls.OPENAI_MODEL or _PROVIDER_PRESETS["openai"]["default_model"],
                "base_url": cls.OPENAI_BASE_URL or _PROVIDER_PRESETS["openai"]["base_url"],
            }
        elif p == "gemini":
            return {
                "provider": "gemini",
                "api_key": cls.GEMINI_API_KEY,
                "model": cls.GEMINI_MODEL,
                "base_url": cls.GEMINI_BASE_URL,
            }
        elif p == "glm":
            return {
                "provider": "glm",
                "api_key": cls.GLM_API_KEY,
                "model": cls.GLM_MODEL,
                "base_url": cls.GLM_BASE_URL,
            }
        elif p == "minimax":
            return {
                "provider": "minimax",
                "api_key": cls.MINIMAX_API_KEY,
                "model": cls.MINIMAX_MODEL,
                "base_url": cls.MINIMAX_BASE_URL,
            }
        elif p == "custom":
            return {
                "provider": "custom",
                "api_key": cls.CUSTOM_API_KEY,
                "model": cls.CUSTOM_MODEL,
                "base_url": cls.CUSTOM_BASE_URL,
            }
        elif p == "qwen":
            return {
                "provider": "qwen",
                "api_key": cls.QWEN_API_KEY,
                "model": cls.QWEN_MODEL,
                "base_url": cls.QWEN_BASE_URL,
            }
        elif p == "openrouter":
            return {
                "provider": "openrouter",
                "api_key": cls.OPENROUTER_API_KEY,
                "model": cls.OPENROUTER_MODEL,
                "base_url": cls.OPENROUTER_BASE_URL,
            }
        elif p == "deepseek":
            return {
                "provider": "deepseek",
                "api_key": cls.DEEPSEEK_API_KEY,
                "model": cls.DEEPSEEK_MODEL,
                "base_url": cls.DEEPSEEK_BASE_URL,
            }
        else:
            raise ValueError(f"Unknown AI_PROVIDER: {p}")

    @classmethod
    def get_tier_chain(cls) -> List[dict]:
        """
        Build the ordered fallback chain AFTER tier-1.
        Returns a list of {provider, model, api_key, base_url} dicts.
        Empty list = no fallback. Each tier may apply per-tier overrides.
        """
        chain: List[dict] = []

        # ── TIER 2 ────────────────────────────────────────────
        if cls.AI_PROVIDER_FALLBACK:
            try:
                t2 = cls.get_ai_config(cls.AI_PROVIDER_FALLBACK)
                # Per-tier overrides (model/key/url) — useful when reusing
                # the same provider with a different model
                if cls.AI_TIER2_MODEL:
                    t2["model"] = cls.AI_TIER2_MODEL
                if cls.AI_TIER2_API_KEY:
                    t2["api_key"] = cls.AI_TIER2_API_KEY
                if cls.AI_TIER2_BASE_URL:
                    t2["base_url"] = cls.AI_TIER2_BASE_URL
                if t2.get("api_key"):
                    chain.append(t2)
            except Exception:
                pass

        # ── TIER 3 ────────────────────────────────────────────
        if cls.AI_PROVIDER_TIER3:
            try:
                t3 = cls.get_ai_config(cls.AI_PROVIDER_TIER3)
                if cls.AI_TIER3_MODEL:
                    t3["model"] = cls.AI_TIER3_MODEL
                if cls.AI_TIER3_API_KEY:
                    t3["api_key"] = cls.AI_TIER3_API_KEY
                if cls.AI_TIER3_BASE_URL:
                    t3["base_url"] = cls.AI_TIER3_BASE_URL
                if t3.get("api_key"):
                    chain.append(t3)
            except Exception:
                pass

        return chain

    @classmethod
    def validate(cls) -> None:
        errors: List[str] = []
        valid_providers = {"anthropic", "blink", "openai", "gemini", "glm", "minimax", "custom", "qwen", "openrouter", "deepseek"}

        if cls.AI_PROVIDER not in valid_providers:
            errors.append(
                f"Unknown AI_PROVIDER '{cls.AI_PROVIDER}' "
                f"(use: {' | '.join(sorted(valid_providers))})"
            )
        else:
            try:
                cfg = cls.get_ai_config()
                if not cfg["api_key"]:
                    key_name = {
                        "anthropic": "ANTHROPIC_API_KEY",
                        "blink": "BLINK_API_KEY",
                        "openai": "OPENAI_API_KEY",
                        "gemini": "GEMINI_API_KEY",
                        "glm": "GLM_API_KEY",
                        "minimax": "MINIMAX_API_KEY",
                        "custom": "CUSTOM_API_KEY",
                        "qwen": "QWEN_API_KEY",
                        "openrouter": "OPENROUTER_API_KEY",
                        "deepseek": "DEEPSEEK_API_KEY",
                    }.get(cls.AI_PROVIDER, "API_KEY")
                    errors.append(f"{key_name} is required for provider={cls.AI_PROVIDER}")
                if not cfg["model"]:
                    errors.append(f"Model is required for provider={cls.AI_PROVIDER}")
                if cls.AI_PROVIDER == "custom" and not cfg["base_url"]:
                    errors.append("CUSTOM_BASE_URL is required for provider=custom")
            except ValueError as e:
                errors.append(str(e))

        # Validate fallback provider (optional). Same provider is allowed
        # as long as a per-tier model override (AI_TIER2_MODEL) is set.
        if cls.AI_PROVIDER_FALLBACK:
            if cls.AI_PROVIDER_FALLBACK not in valid_providers:
                errors.append(
                    f"Unknown AI_PROVIDER_FALLBACK '{cls.AI_PROVIDER_FALLBACK}' "
                    f"(use: {' | '.join(sorted(valid_providers))})"
                )
            else:
                try:
                    fb_cfg = cls.get_ai_config(cls.AI_PROVIDER_FALLBACK)
                    fb_model = cls.AI_TIER2_MODEL or fb_cfg["model"]
                    fb_key = cls.AI_TIER2_API_KEY or fb_cfg["api_key"]
                    if not fb_key:
                        errors.append(
                            f"API key missing for fallback provider "
                            f"'{cls.AI_PROVIDER_FALLBACK}'"
                        )
                    # Reject only if same provider AND same model (no actual override)
                    if (
                        cls.AI_PROVIDER_FALLBACK == cls.AI_PROVIDER
                        and fb_model == cls.get_ai_config()["model"]
                    ):
                        errors.append(
                            "Tier-2 must differ from Tier-1: same provider AND same model. "
                            "Set AI_TIER2_MODEL to override the model."
                        )
                except Exception as e:
                    errors.append(f"Fallback provider error: {e}")

        # Validate Tier-3 (optional, similar rules)
        if cls.AI_PROVIDER_TIER3:
            if cls.AI_PROVIDER_TIER3 not in valid_providers:
                errors.append(
                    f"Unknown AI_PROVIDER_TIER3 '{cls.AI_PROVIDER_TIER3}' "
                    f"(use: {' | '.join(sorted(valid_providers))})"
                )
            else:
                try:
                    t3_cfg = cls.get_ai_config(cls.AI_PROVIDER_TIER3)
                    t3_key = cls.AI_TIER3_API_KEY or t3_cfg["api_key"]
                    if not t3_key:
                        errors.append(
                            f"API key missing for tier-3 provider "
                            f"'{cls.AI_PROVIDER_TIER3}'"
                        )
                except Exception as e:
                    errors.append(f"Tier-3 provider error: {e}")

        if not cls.DRY_RUN:
            if cls.EXCHANGE_MODE == "CEX":
                if not cls.BYBIT_API_KEY:
                    errors.append("BYBIT_API_KEY required for live CEX trading")
                if not cls.BYBIT_SECRET:
                    errors.append("BYBIT_SECRET required for live CEX trading")
            elif cls.EXCHANGE_MODE == "DEX":
                if not cls.HL_PRIVATE_KEY:
                    errors.append("HL_PRIVATE_KEY required for live DEX trading")
                if not cls.HL_WALLET_ADDRESS:
                    errors.append("HL_WALLET_ADDRESS required for live DEX trading")

        if cls.ACCOUNT_BALANCE <= 0:
            errors.append("ACCOUNT_BALANCE must be > 0")

        if not cls.SYMBOLS:
            errors.append("SYMBOLS list cannot be empty")

        if errors:
            raise ValueError("Config validation failed:\n" + "\n".join(f"  x {e}" for e in errors))

    @classmethod
    def summary(cls) -> str:
        try:
            cfg = cls.get_ai_config()
            model_info = cfg["model"]
            if cfg["base_url"]:
                model_info += f" (via {cfg['base_url']})"
        except Exception:
            model_info = "unknown"

        lines = [
            f"  Exchange Mode : {cls.EXCHANGE_MODE}",
            f"  Trading Mode  : {cls.TRADING_MODE.upper()}",
            f"  Dry Run       : {cls.DRY_RUN}",
            f"  Symbols       : {cls.SYMBOLS}",
            f"  Balance       : {cls.ACCOUNT_BALANCE} USDT",
            f"  Scan Interval : {cls.SCAN_INTERVAL}s",
            f"  Max Positions : {cls.MAX_OPEN_POSITIONS}",
            f"  Position Size : {cls.MIN_POSITION_SIZE_PCT}-{cls.MAX_POSITION_SIZE_PCT}% of balance",
            f"  DCA           : {'ON' if cls.DCA_ENABLED else 'OFF'} ({cls.DCA_SIZE_PCT}% per DCA)",
            f"  AI Provider   : {cls.AI_PROVIDER}"
            + (f"  -> t2: {cls.AI_PROVIDER_FALLBACK}" if cls.AI_PROVIDER_FALLBACK else "")
            + (f"  -> t3: {cls.AI_PROVIDER_TIER3}" if cls.AI_PROVIDER_TIER3 else ""),
            f"  AI Model      : {model_info}",
            f"  AI Learning   : {cls.AI_LEARNING}",
        ]
        if cls.EXCHANGE_MODE == "CEX":
            lines.append(f"  Testnet       : {cls.BYBIT_TESTNET}")
        elif cls.EXCHANGE_MODE == "DEX":
            lines.append(f"  HL Testnet    : {cls.HL_TESTNET}")
        return "\n".join(lines)
