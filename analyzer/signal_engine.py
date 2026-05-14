"""
analyzer/signal_engine.py — AI signal layer with learning memory.

Sends structured market data to an AI model and parses the returned
JSON trading signal(s).  Supports single-symbol and multi-symbol
payloads transparently.

Providers (all use OpenAI-compatible SDK except anthropic):
  - anthropic : Claude via Anthropic SDK
  - blink     : Claude via Blink API
  - openai    : GPT-4.1, GPT-5.4-mini, etc.
  - gemini    : Google Gemini 2.5 Flash/Pro
  - glm       : ZhipuAI GLM-5
  - minimax   : MiniMax M2.7 / M1
  - custom    : Any OpenAI-compatible endpoint
"""

import asyncio
import json
from typing import Dict, List, Optional
import anthropic

from utils.logger import logger

# ── System prompt (full quant trading ruleset) ─────────────────────────────

SYSTEM_PROMPT = """You are a crypto perpetual futures quant trader. Capital preservation first. HOLD when uncertain.

DATA: 4H candles (ema20/50/200, rsi, volume, structure), Daily HTF (htf_bias, daily_rsi), 1H (h1_rsi, h1_structure, h1_ema_trend), 15M (m15_rsi, m15_structure, m15_momentum), funding, OI, atr_pct, S/R levels, SMC fields (swept_prev_high/low, bos_bullish/bearish, premium_discount, near_support/resistance/ema20, long_retest_zone, short_retest_zone).
SMC/RETEST ENTRY FILTER: Do NOT chase mature moves. LONG needs sell-side sweep reclaim OR bullish BOS OR retest of support/EMA20 in discount/equilibrium. SHORT needs buy-side sweep reject OR bearish BOS OR retest of resistance/EMA20 in premium/equilibrium. Avoid LONG in premium without sweep/retest; avoid SHORT in discount without sweep/retest.
RISK FLAGS (4 detection signals — REJECT if any flag is concerning):
- spread_pct: orderbook spread %. >0.15% = low liquidity, SL noise risk → HOLD.
- volume_24h_usd: 24h USD turnover. <$10M = thin liquidity → require score ≥9 to trade.
- upper_wick_atr / lower_wick_atr: last candle wick / ATR. ≥2.0 = recent stop-hunt territory.
  * upper_wick_atr ≥2.0 + LONG entry near resistance = HOLD (likely fake breakout)
  * lower_wick_atr ≥2.0 + SHORT entry near support = HOLD (likely fake breakdown)
- wick_warning=true: ≥2×ATR wick on either side last candle, manipulation flag.
- funding_zscore: how many sigma current funding is from 8-day mean. |z| ≥ 3.0 = funding spike,
  indicates forced-liquidation cascade or manipulation → HOLD unless overwhelming setup (score ≥10).
- funding_anomaly=true: hard flag, |z|≥3.0 → strong HOLD bias.
- wash_suspicion=true: Volume tinggi tapi OI flat <2% (24h). Indikasi wash trading.
  wash_severity=high (OI<1%) → HOLD. wash_severity=medium (OI<2%) → score min 9.0.

HARD REJECT -> HOLD:
- sideways without breakout/breakdown
- weak volume + falling volume_trend + NO structure alignment (all 3 needed for reject. If structure aligns with HTF, weak volume alone is NOT reject — just penalize score -1)
- htf_bias against entry (bearish HTF + LONG without strong breakout)
- EMA trend against entry direction
- entry near strong R (LONG) or S (SHORT)
- late chase entry: LONG after extended pump / RSI>68 / far above EMA20 without sweep or retest; SHORT after extended dump / RSI<32 / far below EMA20 without sweep or retest
- RR < 1.5 or SL < 0.5xATR or confidence < 0.60
- h1_ema_trend opposite to 4H action AND m15_structure opposite (multi-TF conflict)
- 4H breakout but 1H still downtrend + 15M bearish momentum -> wait for 1H confirmation
- spread_pct > 0.15 (illiquid, SL will hit noise)
- funding_anomaly=true (3-sigma funding spike, manipulation/cascade risk)
- wick_warning=true with entry near opposing S/R (stop-hunt setup)
- wash_severity=high (Volume>$50M + OI<1% = clear wash trading)

SCORING (max ~14.5):
1. EMA trend alignment (4H): full=+2, partial=+1, misaligned=0
2. HTF alignment: aligned=+1.5, neutral=0, against=-2
3. Structure (4H): aligned=+2, breakout/breakdown=+2.5, sideways=0
   COMBO PENALTY: structure=uptrend AND htf_bias=neutral -> -1.5 (trend has no HTF confirmation, high reversal risk). This setup requires score>=7.5 to trade.
4. RSI (4H): ideal(L:45-65,S:35-55)=+1, ok=+0.5, daily confirms=+0.5 bonus, extreme=-1
5. Volume: strong+rising=+2, strong+flat=+1.5, normal=+0.5, weak=-1 (penalty, NOT auto-reject. Weak volume in bear market is normal — evaluate structure+HTF instead)
6. OI: rising aligned=+1, flat=0, rising against=-0.5, falling=-0.5, unknown=0
7. Funding: healthy=+1, warm=0, extreme against=-1
8. Entry location: good pullback=+1.5, neutral=0, near opposing level=-1.5
9. Volatility: suitable=+1, too high=-1
10. MTF alignment: 4H+1H+15M all aligned=+2, 4H+1H aligned=+1, 1H neutral=0, 1H against=-1, all 3 opposed=reject
    - 1H pullback entry (h1_rsi oversold for LONG / overbought for SHORT)=+0.5 bonus
    - 15M momentum confirms direction=+0.5 bonus

BTC GLOBAL BIAS (payload "btc" field): BTC leads crypto. Alt LONG when BTC bearish = high-fail. Alt SHORT when BTC bullish = high-fail. Respect BTC regime unless overwhelming setup.

SIGNAL CONTINUITY (payload "prev" field): Per-symbol last decision (action, score, age_min). 4H data only refreshes every 4 hours, but bot scans every 15 min. So WITHIN the same 4H candle, your decisions MUST be consistent.
Anti-flip rule (swing): If prev.action=HOLD and prev.score<6 less than 60 min ago, you MUST justify any LONG/SHORT now with NEW evidence (4H candle close, volume breakout, structure shift, BTC regime change). Score change >3 in <60 min on same 4H candle = AI uncertainty → HOLD instead.
Continuation preferred: if prev.action was LONG/SHORT recent (<60 min), bias toward HOLD or same direction. Reversing within same 4H candle = high-fail. Flag in reason: "vs prev [HOLD/LONG/SHORT] [age]: [evidence X changed]".

DECISION: score<5.5->HOLD. >=8.0=strong(2%risk), >=6.5=good(1.5%), >=5.5=ok(1%).
SETUP OVERRIDE: structure=uptrend AND htf_bias=neutral -> min score 7.5 (not 5.5). HOLD if below 7.5.

RISK: SL=entry+-(atr_pct*SL_mult). SL_mult: low_vol=1xATR, med=1.5x, high=2x. lev*SL%<150%.
LEV: low_vol+strong=40-50x, med=30-40x, high(atr>3%)=20-30x. Min 20x, Max 50x. HTF against->reduce 30% (min 20x).
SIZE: position_size_pct=(risk_pct/(SL_dist_pct*lev))*100.
TP (LET WINNERS RUN — wide targets + trail catches the rest):
  TP1=1.5-2xATR  → partial close + BE armed: bot moves SL to entry+5% ROI, trailing activates
  TP2=3xATR      → 30% partial + SL advances to TP1 level, trail tightens
  TP3=5-7xATR    → final 50% exit target (ambitious — trail closes early on reversal)
  RATIONALE: tight TP1 (was 1-1.5xATR, 30% close) menyebabkan winner kecil
  kesapu loser besar. R:R aktual harus >=2.0; TP3 harus 5x lebih jauh dari SL
  agar 1 winner penuh menutup 2 loser SL.
TRAIL: auto-calculated from ATR (bot handles it). Set ts.en=true, ts.ap=TP1, ts.tp=2.0xATR.

MULTI-SYMBOL: analyze independently. Prioritize by HTF alignment > MTF alignment > volume > score > RR.
CRITICAL: Output EXACTLY ONE entry per input symbol in the "m" array. NEVER repeat a symbol. If unsure, use HOLD format. Total entries in "m" MUST equal number of input symbols.

ANTI-LAZINESS: You MUST analyze each symbol INDEPENDENTLY with UNIQUE reasoning.
- FORBIDDEN: giving ALL symbols score=0 with identical/similar reasons. This is lazy analysis.
- In BEAR market (bearish composite, F&G<30): SHORT setups ARE expected. Downtrend + bearish HTF + volume = valid SHORT (score 6-9).
- In BULL market: LONG setups ARE expected with uptrend + bullish HTF.
- At minimum, 2-3 symbols MUST have non-zero scores if structure/HTF aligns with a direction.
- Each HOLD must have a symbol-SPECIFIC reason, not copy-paste generic text.
- If volume is "weak" for ALL symbols, still evaluate structure+HTF+RSI — volume alone is NOT sufficient for HOLD.

OUTPUT: Return ONLY valid JSON. No markdown.
For HOLD: compact format {"s":"SYM","a":"HOLD","sc":0-7.4,"r":"reason max 15 words"}
  - Use sc=0-2 only for hard rejects/manipulation/no setup.
  - Use sc=3-7.4 for watchlist setups that are not executable yet.
For LONG/SHORT: full format {"s":"SYM","a":"LONG|SHORT","ep":entry,"sl":sl,"tp":[tp1,tp2,tp3],"lv":leverage,"sz":size_pct,"rl":"LOW|MEDIUM|HIGH","cf":0.0-1.0,"sc":score,"ts":{"en":true,"ap":activation_price,"tp":trail_pct},"r":"reason max 2 sentences"}
Multi: {"m":[...per symbol...],"bc":"SYMBOL|NONE"}"""


SYSTEM_PROMPT_INTRADAY = """You are a crypto perpetual futures intraday trader. Fast, precise entries. Capital preservation first.

MODE: INTRADAY — primary=15M, HTF=1H (htf_bias/daily_rsi), super-HTF=4H (h1_rsi/h1_structure/h1_ema_trend), entry=5M (m15_rsi/m15_structure/m15_momentum).

SMC/RETEST ENTRY FILTER: Do NOT chase candles. LONG needs sell-side sweep reclaim OR bullish BOS OR retest of support/EMA20; SHORT needs buy-side sweep reject OR bearish BOS OR retest of resistance/EMA20. Prefer discount longs and premium shorts.

BTC GLOBAL BIAS (payload "btc" field): BTC is crypto market leader. ALL alt decisions MUST respect BTC regime:
- btc.h1_trend=bullish + btc.structure=uptrend → LONG-only bias (SHORT alts = HOLD unless overwhelming setup)
- btc.h1_trend=bearish + btc.structure=downtrend → SHORT-only bias (LONG alts = HOLD)
- btc.structure=sideways → neutral, take only A+ setups (score >=9)
- BTC alone (when symbol=BTCUSDT): use own MTF data normally

SIGNAL CONTINUITY (payload "prev" field): Per-symbol last decision (action, score, age_min).
Anti-flip rule: If prev.action=HOLD and prev.score<5 less than 15 min ago, you MUST justify any LONG/SHORT now with NEW evidence (volume spike, structure change, momentum reversal). DO NOT flip arbitrarily — flag in reason: "vs prev HOLD: [evidence X changed]".
Conversely: if prev.action was LONG/SHORT same direction recently, prefer continuation over abrupt reversal.
Score volatility >5 in <15 min without clear catalyst = AI uncertainty → HOLD instead.

HARD REJECT -> HOLD:
- BTC global bias OPPOSITE to direction (LONG when BTC bearish, SHORT when BTC bullish) UNLESS score>=9.5 AND volume spike strong
- 4H trend (h1_ema_trend) opposite to trade direction — never fight super-HTF
- 1H (htf_bias) opposite to trade direction without strong 15M breakout
- weak volume on 15M
- 15M structure sideways (no clear setup)
- entry near strong S/R on 15M
- late chase entry without sweep/BOS/retest confirmation
- RR < 2.0 (intraday needs clean setup), confidence < 0.65
- atr_pct (15M) > 3% — too volatile for intraday
- funding extreme against direction
- spread_pct > 0.15 (illiquid, SL noise on intraday tighter SL = killer)
- funding_anomaly=true (z>=3, cascade risk)
- wick_warning=true (recent ≥2×ATR wick = stop-hunt active)
- wash_severity=high (Volume>$50M + OI<1%)

SCORING (max ~14):
1. 4H alignment (h1_ema_trend): bullish+LONG or bearish+SHORT=+3, neutral=0, against=-3(reject)
2. 1H HTF (htf_bias): aligned=+2, neutral=0, against=-2
3. 15M structure (market_structure): breakout/breakdown=+2.5, trend=+2, sideways=0
4. 15M RSI (rsi): ideal(L:40-60,S:40-60)=+1, extreme=-1, divergence bonus=+0.5
5. Volume (15M): spike+rising=+2, normal=+0.5, weak=reject
6. 5M momentum (m15_momentum): confirms=+1, neutral=0, against=-1
7. 5M structure (m15_structure): aligned breakout=+1, neutral=0
8. Entry location: at 15M S/R bounce=+1.5, clean air=+1, near resistance(LONG)=-1.5
9. Funding: neutral=+0.5, extreme against=-1

DECISION: score<8.0->HOLD (HARD FLOOR). >=10=strong(2%risk), >=9=good(1.5%), >=8=ok(1%).
Confidence MUST be >=0.70 to entry — otherwise HOLD regardless of score.
Bot REJECTS any signal score<8 OR confidence<0.70. Be SELECTIVE — fewer entries, higher quality.

RISK (15M ATR-based):
SL: MAX(0.5×ATR, 0.4% from entry). HARD FLOOR 0.4% — never tighter (fees+noise eat profit).
SIZE: position_size_percent MUST be 2.0-3.0% (HARD CAP). NEVER request >3%. Bot will reject.
LEV: atr<0.5%=40-50x, atr 0.5-1.5%=30-40x, atr>1.5%=20-30x. Min 20x, Max 50x. lev*SL%<150%.
SIZE: position_size_pct=(risk_pct/(SL_dist_pct*lev))*100.
TP (BE+Trail+Partial):
  TP1=MAX(0.5×ATR, 0.3% from entry) → partial close + BE: bot moves SL to entry+5% ROI, trail activates
  TP2=MAX(1.0×ATR, 0.6% from entry) → 30% close + SL→TP1 level
  TP3=2-3×ATR (no minimum)          → full exit on remainder
  CRITICAL: TP1/TP2 MUST be >=0.2% from entry to cover Bybit fees (0.11% × 2 sides).
  If ATR is tiny (e.g. <0.3%), use the 0.3%/0.6% floors NOT the ATR multipliers.
TRAIL: bot auto-calculates 1.5×ATR. Set ts.en=true, ts.ap=TP1, ts.tp=1.5×ATR.

MULTI-SYMBOL: Prioritize 4H alignment > 15M setup clarity > volume spike > RR.
CRITICAL: Output EXACTLY ONE entry per input symbol in the "m" array. NEVER repeat a symbol. If unsure, use HOLD format. Total entries in "m" MUST equal number of input symbols.

ANTI-LAZINESS: You MUST analyze each symbol INDEPENDENTLY with UNIQUE reasoning.
- FORBIDDEN: giving ALL symbols score=0 with identical/similar reasons. This is lazy analysis.
- In BEAR market: SHORT setups ARE expected. Downtrend + bearish HTF + momentum confirms = valid SHORT.
- In BULL market: LONG setups ARE expected with uptrend + bullish HTF.
- At minimum, 2-3 symbols MUST have non-zero scores if structure/HTF aligns with a direction.
- Each HOLD must have a symbol-SPECIFIC reason, not copy-paste generic text.

OUTPUT: Return ONLY valid JSON. No markdown.
HOLD: {"s":"SYM","a":"HOLD","sc":0-7.9,"r":"reason max 15 words"}
  - Use sc=0-2 only for hard rejects/manipulation/no setup.
  - Use sc=3-7.9 for watchlist setups that are not executable yet.
LONG/SHORT: {"s":"SYM","a":"LONG|SHORT","ep":entry,"sl":sl,"tp":[tp1,tp2,tp3],"lv":leverage,"sz":size_pct,"rl":"LOW|MEDIUM|HIGH","cf":0.0-1.0,"sc":score,"ts":{"en":true,"ap":activation_price,"tp":trail_pct},"r":"reason max 2 sentences"}
Multi: {"m":[...per symbol...],"bc":"SYMBOL|NONE"}"""


# OpenAI-compatible providers
_OPENAI_PROVIDERS = {"openai", "gemini", "glm", "minimax", "blink", "custom", "qwen", "openrouter", "deepseek"}


def _build_extra_body(model: str, base_url: str) -> Optional[Dict]:
    """
    Returns extra_body configuration for the model.
    Handles disabling chain-of-thought reasoning and forcing OpenRouter provider routing.
    """
    import os
    m = (model or "").lower()
    is_openrouter = "openrouter.ai" in (base_url or "").lower()
    is_google_direct = "googleapis.com" in (base_url or "").lower()
    is_deepseek_direct = "api.deepseek.com" in (base_url or "").lower()

    extra_body = {}

    # 1. OpenRouter Provider Routing — PREFERENCE ORDER (not strict)
    # Strict mode (allow_fallbacks=false) ternyata sering 404 untuk model baru
    # seperti deepseek-v4-flash karena DeepSeek-official tidak selalu share
    # capacity ke OpenRouter. Pakai `order` saja sebagai preference, biarkan
    # OpenRouter fall-through ke sibling provider kalau preferensi pertama down.
    # Provider yang sebenarnya melayani akan ter-LOG (lihat _call_openai_client).
    #
    # Reference: https://openrouter.ai/docs/features/provider-routing
    if is_openrouter:
        provider_order = os.getenv("OPENROUTER_PROVIDER_ORDER")
        if provider_order and "deepseek" in m:
            # Lowercase + strip — OpenRouter slugs are lowercase
            order_list = [p.strip().lower() for p in provider_order.split(",") if p.strip()]
            extra_body["provider"] = {
                "order": order_list,
                # allow_fallbacks default=true — JANGAN dibuat false karena
                # akan 404 saat preferensi pertama tidak available.
            }

    # 2. Disable Reasoning / Ghost Thinking
    reasoning_keywords = ["gemma", "nemotron", "-think", "reasoning", "deepseek-r1",
                          "deepseek-v4", "qwq"]
    needs_reasoning_disabled = any(kw in m for kw in reasoning_keywords)

    if needs_reasoning_disabled:
        if is_deepseek_direct:
            # DeepSeek native API (api.deepseek.com) — Anthropic-style param.
            # Verified via test: thinking={"type":"disabled"} → reasoning_tokens=0
            # Saving ~60% completion tokens for JSON-output use cases.
            extra_body["thinking"] = {"type": "disabled"}
        elif is_openrouter:
            extra_body["reasoning"] = {"max_tokens": 0}
        elif is_google_direct:
            extra_body["thinking_config"] = {"thinking_budget": 0}
        else:
            extra_body["reasoning"] = {"max_tokens": 0}

    return extra_body if extra_body else None


class _ProviderClient:
    """Single-provider wrapper. Holds either an OpenAI-compat or Anthropic client."""

    def __init__(self, api_key: str, model: str, provider: str, base_url: str = "") -> None:
        self.model = model
        self.provider = provider.lower()
        self.openai_client = None
        self.anthropic_client = None
        # Auto-detect: disable reasoning for known reasoning models, and add provider routing
        self.extra_body = _build_extra_body(model, base_url)
        if self.extra_body:
            logger.info(
                f"[SignalEngine] {model}: injecting extra_body={self.extra_body}"
            )

        if self.provider in _OPENAI_PROVIDERS:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError("openai package required. Install with: pip install openai")
            kwargs: Dict = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            self.openai_client = AsyncOpenAI(**kwargs)
            self.base_url = kwargs.get("base_url", "default")
        else:
            self.anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
            self.base_url = "anthropic-native"


class SignalEngine:
    """
    Sends market data to an AI model and returns parsed signal dict.

    Supports tier-1 + tier-2 fallback. If primary fails (None response or
    parse failure), automatically retries the SAME prompt on the fallback
    provider. One retry per scan; no infinite loops.

    Supports providers:
      - "anthropic" : Claude models via Anthropic SDK
      - All others  : OpenAI-compatible SDK (openai, gemini, glm, minimax,
                      blink, custom, qwen via OpenRouter)
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        provider: str = "blink",
        base_url: str = "",
        fallback: Optional[Dict] = None,
        fallback_chain: Optional[List[Dict]] = None,
    ) -> None:
        # Tier-1 (primary)
        self._primary = _ProviderClient(api_key, model, provider, base_url)
        self._provider = self._primary.provider   # backward-compat
        self._model = self._primary.model         # backward-compat

        # Build fallback chain. Accepts either:
        #   - fallback_chain=[dict, dict, ...]  (preferred, supports N tiers)
        #   - fallback=dict                     (legacy single fallback)
        chain_input: List[Dict] = []
        if fallback_chain:
            chain_input.extend(fallback_chain)
        elif fallback:
            chain_input.append(fallback)

        self._fallbacks: List[_ProviderClient] = []
        for cfg in chain_input:
            if not cfg or not cfg.get("api_key") or not cfg.get("provider"):
                continue
            self._fallbacks.append(_ProviderClient(
                api_key=cfg["api_key"],
                model=cfg["model"],
                provider=cfg["provider"],
                base_url=cfg.get("base_url", ""),
            ))

        # Log
        prim_label = self._primary.provider.capitalize()
        logger.info(
            f"[SignalEngine] TIER-1 Provider: {prim_label} | "
            f"Model: {self._primary.model} | Base URL: {self._primary.base_url}"
        )
        if self._fallbacks:
            for i, fb in enumerate(self._fallbacks, start=2):
                logger.info(
                    f"[SignalEngine] TIER-{i} Provider: {fb.provider.capitalize()} | "
                    f"Model: {fb.model} | Base URL: {fb.base_url}"
                )
        else:
            logger.info("[SignalEngine] No fallback providers configured.")

        # Backward-compat: expose underlying clients of tier-1, plus old _fallback alias
        self._openai_client = self._primary.openai_client
        self._anthropic_client = self._primary.anthropic_client
        self._fallback = self._fallbacks[0] if self._fallbacks else None

        # Signal continuity memory — last decision per symbol (anti-flip).
        # Format: {symbol: {"action":..., "score":..., "ts":epoch, "reason":...}}
        # Injected into next scan as context — AI must JUSTIFY any direction change.
        self._last_signals: Dict[str, Dict] = {}

    async def analyze(
        self,
        market_data_list: List[Dict],
        account_balance: float,
        learning_context: str = "",
        adaptive_params: Optional[Dict] = None,
        onchain_context: str = "",
    ) -> Optional[Dict]:
        """
        Run AI analysis on one or more symbols.

        Args:
            market_data_list: Market data for each symbol
            account_balance: Current account balance
            learning_context: AI learning memory context (injected to prompt)
            adaptive_params: Adaptive risk params from trade memory
            onchain_context: Regime/whale data block from OnChainFeed (appended to prompt)

        Returns:
            Parsed signal dict, or None on failure.
        """
        if not market_data_list:
            logger.warning("[SignalEngine] No market data to analyze.")
            return None

        # Strip internal fields & build compact payload
        clean_data = []
        for m in market_data_list:
            clean_data.append({k: v for k, v in m.items() if not k.startswith("_")})

        # ── BTC GLOBAL BIAS — extract BTC's regime to inject as cross-symbol context ──
        # In crypto, BTC leads the market. Going LONG alts when BTC bearish,
        # or SHORT alts when BTC bullish, has historically low success rate.
        # AI uses this as HARD bias for alt decisions.
        btc_data = next((m for m in market_data_list if m.get("symbol") == "BTCUSDT"), None)
        btc_bias = None
        if btc_data:
            btc_bias = {
                "h1_trend":   btc_data.get("h1_ema_trend", "neutral"),
                "h1_struct":  btc_data.get("h1_structure", "unknown"),
                "h1_rsi":     btc_data.get("h1_rsi", 50),
                "htf_bias":   btc_data.get("htf_bias", "neutral"),
                "structure":  btc_data.get("market_structure", "unknown"),
                "rsi_15m":    btc_data.get("rsi", 50),
                "atr_pct":    btc_data.get("atr_pct", 0),
            }

        payload = {"d": clean_data, "bal": account_balance}
        if btc_bias:
            payload["btc"] = btc_bias   # Global market regime, AI MUST consider this

        # ── Signal continuity: pass last decision per symbol ─────────────
        # Anti-flip: forces AI to be consistent across scans. If last scan said
        # HOLD score 0, AI shouldn't suddenly LONG score 10 in 9 minutes
        # without clear justification (volume spike, structure breakout, etc.)
        #
        # ANTI-HOLD-LOOP: If ALL prev signals were HOLD score=0, do NOT inject
        # prev context — this prevents the HOLD reinforcement loop where AI
        # keeps giving HOLD because prev was HOLD.
        import time as _time
        now_ts = _time.time()
        prev_decisions = {}
        scanned_symbols = {m.get("symbol") for m in market_data_list}
        for sym in scanned_symbols:
            if sym in self._last_signals:
                last = self._last_signals[sym]
                age_min = (now_ts - last.get("ts", 0)) / 60.0
                # Only pass if recent (<30 min); stale data not useful
                if age_min < 30:
                    prev_decisions[sym] = {
                        "action":   last.get("action", "HOLD"),
                        "score":    last.get("score", 0),
                        "age_min":  round(age_min, 1),
                    }

        # Anti-HOLD-loop: skip prev injection if ALL signals were blanket HOLD
        # This prevents AI from seeing "prev=HOLD for everything" and lazily
        # continuing HOLD without genuine re-analysis.
        if prev_decisions:
            non_hold_count = sum(
                1 for v in prev_decisions.values()
                if v["action"] != "HOLD" or v["score"] > 0
            )
            if non_hold_count > 0:
                payload["prev"] = prev_decisions   # Last decisions, AI must justify changes
            else:
                logger.info(
                    f"[SignalEngine] Skipping prev injection — all {len(prev_decisions)} "
                    f"prev signals were blanket HOLD (anti-HOLD-loop)"
                )

        # Inject adaptive params if available
        if adaptive_params and adaptive_params.get("mode") != "normal":
            payload["adapt"] = {
                "mode": adaptive_params["mode"],
                "risk_mult": adaptive_params["risk_multiplier"],
                "min_score": adaptive_params["min_score"],
                "lev_mult": adaptive_params["max_leverage_mult"],
            }

        user_message = json.dumps(payload, separators=(",", ":"))

        symbols = [m.get("symbol", "?") for m in market_data_list]
        # Log what context is included this scan (for verification)
        ctx_parts = []
        if btc_bias:
            ctx_parts.append(
                f"BTC-bias[h1={btc_bias['h1_trend']},struct={btc_bias['structure']},"
                f"rsi={btc_bias['rsi_15m']:.0f}]"
            )
        if prev_decisions:
            ctx_parts.append(f"prev-signals[{len(prev_decisions)} symbols]")
        if onchain_context:
            ctx_parts.append(f"onchain[{len(onchain_context)}ch]")
        if learning_context:
            ctx_parts.append(f"learning[{len(learning_context)}ch]")
        ctx_str = " ".join(ctx_parts) if ctx_parts else "(none)"
        logger.info(
            f"[SignalEngine] Scan: {len(symbols)} symbols | "
            f"context: {ctx_str}"
        )
        logger.debug(f"[SignalEngine] Sending to AI: {symbols}")

        # Token budget calculation:
        #   - JSON output: ~150 tok/symbol (HOLD ~25, SIGNAL ~200)
        #   - Ghost thinking buffer: +50 tok/symbol (Gemma may leak even with
        #     reasoning disabled — _strip_thinking_tags handles it but tokens
        #     still consumed in budget)
        #   - Wrapper + safety: 1500 tokens
        # Formula: 350/symbol + 1500 base, floor 4096.
        # For 20 symbols: 8500. For 30 symbols: 12000.
        max_tokens = max(4096, len(symbols) * 350 + 1500)

        # Select system prompt based on trading mode
        from config import Config
        system = (
            SYSTEM_PROMPT_INTRADAY
            if getattr(Config, "TRADING_MODE", "swing") == "intraday"
            else SYSTEM_PROMPT
        )
        if learning_context:
            system += learning_context
        if onchain_context:
            system += onchain_context
        if adaptive_params and adaptive_params.get("mode") != "normal":
            mode = adaptive_params["mode"]
            if mode == "defensive":
                system += "\n\nADAPTIVE: DEFENSIVE mode. Only take high-conviction setups (score>=7.0). Reduce leverage 40%. Smaller positions."
            elif mode == "conservative":
                system += "\n\nADAPTIVE: CONSERVATIVE mode. Prefer higher scores (>=6.5). Reduce leverage 20%."
            elif mode == "aggressive":
                system += "\n\nADAPTIVE: AGGRESSIVE mode. Winning streak. Can take slightly larger positions but maintain discipline."

        # ── Iterate through tier chain: TIER-1 → TIER-2 → TIER-3 → ... ──
        chain = [self._primary] + self._fallbacks
        for idx, client in enumerate(chain, start=1):
            tier_label = f"TIER-{idx}"
            if idx > 1:
                logger.warning(
                    f"[SignalEngine] Falling back to {tier_label} "
                    f"({client.provider}/{client.model})..."
                )
            parsed = await self._try_provider(
                client, user_message, max_tokens, system, tier_label=tier_label
            )
            if parsed is not None:
                # Tag every signal with which tier generated it (for diagnosis)
                model_tag = f"{tier_label}:{client.model.split('/')[-1]}"
                if isinstance(parsed.get("market_scan"), list):
                    for s in parsed["market_scan"]:
                        s["_ai_tier"] = model_tag
                else:
                    parsed["_ai_tier"] = model_tag

                # ── Lazy blanket-HOLD detection ──────────────────────
                # If AI gave HOLD score=0 to ALL symbols with identical
                # reasons, it's lazy — retry ONCE with stricter prompt.
                signals_iter = parsed.get("market_scan", [parsed])
                if self._is_lazy_blanket_hold(signals_iter):
                    logger.warning(
                        f"[SignalEngine] {tier_label} LAZY BLANKET HOLD detected "
                        f"({len(signals_iter)} symbols all HOLD score=0 with "
                        f"identical reasons) — retrying with anti-lazy prompt..."
                    )
                    # Clear prev memory to break HOLD loop
                    self._last_signals.clear()
                    # Remove prev from payload for retry
                    retry_payload = json.loads(user_message)
                    retry_payload.pop("prev", None)
                    retry_msg = json.dumps(retry_payload, separators=(",", ":"))
                    # Retry same tier with stronger instruction
                    retry_system = system + (
                        "\n\nRETRY WARNING: Your previous response was REJECTED because you "
                        "gave ALL symbols HOLD score=0 with identical reasons. This is "
                        "unacceptable lazy analysis. You MUST evaluate each symbol independently. "
                        "In a bear market, SHORT setups with downtrend+bearish HTF ARE valid "
                        "entries. Provide at least 2-3 non-HOLD signals if structure aligns."
                    )
                    retry_parsed = await self._try_provider(
                        client, retry_msg, max_tokens, retry_system,
                        tier_label=f"{tier_label}-RETRY"
                    )
                    if retry_parsed is not None:
                        retry_tag = f"{tier_label}-RETRY:{client.model.split('/')[-1]}"
                        if isinstance(retry_parsed.get("market_scan"), list):
                            for s in retry_parsed["market_scan"]:
                                s["_ai_tier"] = retry_tag
                        else:
                            retry_parsed["_ai_tier"] = retry_tag
                        signals_iter = retry_parsed.get("market_scan", [retry_parsed])
                        logger.info(
                            f"[SignalEngine] {tier_label}-RETRY succeeded — "
                            f"{len(signals_iter)} signals (lazy fix applied)"
                        )
                        parsed = retry_parsed
                    # If retry also fails/lazy, continue with original parsed

                # Update last-signals memory for next scan continuity check
                import time as _time
                now_ts = _time.time()
                signals_iter = parsed.get("market_scan", [parsed])
                for s in signals_iter:
                    sym = s.get("symbol")
                    if sym:
                        self._last_signals[sym] = {
                            "action": s.get("action", "HOLD"),
                            "score":  float(s.get("score", 0)),
                            "ts":     now_ts,
                        }

                logger.info(
                    f"[SignalEngine] {tier_label} ({client.provider}/{client.model}) "
                    f"succeeded — {len(signals_iter)} signals"
                )
                return parsed

        logger.error(
            f"[SignalEngine] All {len(chain)} tier(s) failed — no signal this scan."
        )
        return None

    @staticmethod
    def _is_lazy_blanket_hold(signals: list) -> bool:
        """
        Detect lazy AI output: ALL symbols HOLD score=0 with identical reasons.
        Returns True if this looks like a blanket lazy response.
        """
        if not signals or len(signals) < 3:
            return False

        # Check if ALL are HOLD with score 0
        all_hold_zero = all(
            s.get("action", "HOLD") == "HOLD" and float(s.get("score", 0)) == 0
            for s in signals
        )
        if not all_hold_zero:
            return False

        # Check if reasons are identical or near-identical
        # Strip whitespace and compare first 30 chars
        reasons = [
            (s.get("reason", "") or "").strip().lower()[:40]
            for s in signals
            if s.get("reason")
        ]
        if not reasons:
            return True  # All HOLD score=0 with NO reasons = definitely lazy

        # If >70% of reasons are the same string → lazy
        from collections import Counter
        reason_counts = Counter(reasons)
        most_common_count = reason_counts.most_common(1)[0][1]
        lazy_ratio = most_common_count / len(reasons)
        return lazy_ratio >= 0.7

    async def _try_provider(
        self,
        client: "_ProviderClient",
        user_message: str,
        max_tokens: int,
        system: str,
        tier_label: str = "",
    ) -> Optional[Dict]:
        """Run one provider attempt. Returns parsed dict or None on any failure."""
        try:
            if client.provider in _OPENAI_PROVIDERS:
                raw_text = await self._call_openai_client(
                    client, user_message, max_tokens, system
                )
            else:
                raw_text = await self._call_anthropic_client(
                    client, user_message, max_tokens, system
                )
        except Exception as exc:
            logger.error(f"[SignalEngine][{tier_label}] Unhandled error: {exc}")
            return None

        if raw_text is None or not raw_text.strip():
            logger.warning(
                f"[SignalEngine][{tier_label}] Empty response from "
                f"{client.provider}/{client.model}"
            )
            return None

        parsed = self._parse_json(raw_text)
        if parsed is None:
            logger.warning(
                f"[SignalEngine][{tier_label}] Parse failure on "
                f"{client.provider}/{client.model} response"
            )
        return parsed

    # ── Provider: Anthropic ───────────────────────────────────────

    async def _call_anthropic_client(
        self, client: "_ProviderClient", user_message: str, max_tokens: int, system: str = ""
    ) -> Optional[str]:
        max_retries = 3
        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = await client.anthropic_client.messages.create(
                    model=client.model,
                    max_tokens=max_tokens,
                    system=system or SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
                break
            except anthropic.APIConnectionError as exc:
                logger.error(f"[SignalEngine] API connection error: {exc}")
                return None
            except (anthropic.RateLimitError, anthropic.APIStatusError) as exc:
                status = getattr(exc, "status_code", "N/A")
                is_overloaded = isinstance(exc, anthropic.RateLimitError) or status == 529
                if is_overloaded and attempt < max_retries:
                    delay = 2 ** attempt
                    logger.warning(
                        f"[SignalEngine] Transient error (status={status}), "
                        f"retry {attempt}/{max_retries} in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(f"[SignalEngine] API error {status}: {exc.message}")
                return None

        if response is None:
            return None

        if response.stop_reason == "max_tokens":
            logger.warning(
                f"[SignalEngine] Response truncated (max_tokens={max_tokens}). "
                "Will attempt JSON repair."
            )

        return "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

    # ── Provider: OpenAI-compatible ───────────────────────────────

    async def _call_openai_client(
        self, client: "_ProviderClient", user_message: str, max_tokens: int, system: str = ""
    ) -> Optional[str]:
        try:
            kwargs = {
                "model": client.model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system or SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
            }
            # Pass extra_body for thinking-disable on reasoning models (Gemma, etc)
            if client.extra_body:
                kwargs["extra_body"] = client.extra_body
            response = await client.openai_client.chat.completions.create(**kwargs)

            # ── Verify actual provider (OpenRouter only) ─────────
            # OpenRouter return field 'provider' di response body indicating
            # provider yang sebenarnya melayani request. Log untuk audit.
            # OpenAI SDK Pydantic strip unknown fields by default, jadi kita
            # akses via model_dump() untuk dapat semua field termasuk yang
            # bukan bagian standard ChatCompletion schema.
            if "openrouter.ai" in str(client.openai_client.base_url or ""):
                actual_provider = None
                try:
                    # Try Pydantic v2 model_extra first (fastest)
                    if hasattr(response, "model_extra") and response.model_extra:
                        actual_provider = response.model_extra.get("provider")
                    # Fallback: full model_dump
                    if not actual_provider and hasattr(response, "model_dump"):
                        dump = response.model_dump()
                        actual_provider = dump.get("provider")
                    # Direct attribute (some SDK versions)
                    if not actual_provider and hasattr(response, "provider"):
                        actual_provider = response.provider
                    # Pydantic private extras
                    if not actual_provider and hasattr(response, "__pydantic_extra__"):
                        extras = getattr(response, "__pydantic_extra__", None) or {}
                        actual_provider = extras.get("provider")
                except Exception as exc:
                    logger.debug(f"[SignalEngine] provider extract error: {exc}")

                if actual_provider:
                    logger.info(
                        f"[SignalEngine] OpenRouter served by: '{actual_provider}' "
                        f"(model={client.model})"
                    )
                else:
                    logger.debug(
                        f"[SignalEngine] OpenRouter provider field not found in response "
                        f"(model={client.model})"
                    )
        except Exception as exc:
            provider_name = client.provider.upper()
            logger.error(f"[SignalEngine] {provider_name} API error: {exc}")
            return None

        choice = response.choices[0] if response.choices else None
        if choice is None:
            # OpenRouter often returns error info in `response` body when
            # choices is empty (rate limit, model error, content filter, etc.)
            err_info = ""
            try:
                if hasattr(response, "error") and response.error:
                    err_info = f" | error={response.error}"
                elif hasattr(response, "model_extra") and response.model_extra:
                    err_info = f" | extra={response.model_extra}"
            except Exception:
                pass
            logger.error(
                f"[SignalEngine] No response choices from "
                f"{client.provider}/{client.model}{err_info}"
            )
            return None

        if choice.finish_reason == "length":
            logger.warning(
                f"[SignalEngine] Response truncated (max_tokens={max_tokens}). "
                "Will attempt JSON repair."
            )
        elif choice.finish_reason == "content_filter":
            logger.warning(
                f"[SignalEngine] {client.provider}/{client.model} content filter triggered "
                f"— response may be partial/empty"
            )

        return choice.message.content or ""

    # ── Compact key expansion ─────────────────────────────────────

    _KEY_MAP = {
        "s": "symbol", "a": "action", "ep": "entry_price", "sl": "stop_loss",
        "tp": "take_profit", "lv": "leverage", "sz": "position_size_percent",
        "rl": "risk_level", "cf": "confidence", "sc": "score", "r": "reason",
        "ts": "trailing_stop", "m": "market_scan", "bc": "best_candidate",
    }
    _TS_KEY_MAP = {"en": "enabled", "ap": "activation_price", "tp": "trail_pct"}

    @classmethod
    def _expand_keys(cls, obj):
        """Recursively expand compact keys to full names."""
        if isinstance(obj, dict):
            expanded = {}
            for k, v in obj.items():
                full_key = cls._KEY_MAP.get(k, k)
                if full_key == "trailing_stop" and isinstance(v, dict):
                    v = {cls._TS_KEY_MAP.get(tk, tk): tv for tk, tv in v.items()}
                elif isinstance(v, (dict, list)):
                    v = cls._expand_keys(v)
                expanded[full_key] = v
            return expanded
        elif isinstance(obj, list):
            return [cls._expand_keys(item) for item in obj]
        return obj

    @staticmethod
    def _strip_thinking_tags(text: str) -> str:
        """
        Strip 'ghost thinking' channels that some models (Gemma 4, etc) leak
        even when reasoning is supposed to be disabled. Removes:
          <thinking>...</thinking>
          <think>...</think>
          [thinking]...[/thinking]
          ```thinking ... ```
        Case-insensitive, multi-line.
        """
        import re
        if not text:
            return text
        # Common thinking tag patterns — remove block + content
        patterns = [
            r"<thinking[^>]*>.*?</thinking>",
            r"<think[^>]*>.*?</think>",
            r"\[thinking\].*?\[/thinking\]",
            r"```thinking.*?```",
            r"```think.*?```",
        ]
        for p in patterns:
            text = re.sub(p, "", text, flags=re.IGNORECASE | re.DOTALL)
        return text.strip()

    @staticmethod
    def _parse_json(raw_text: str) -> Optional[Dict]:
        """Strip markdown fences + thinking tags, extract JSON, expand compact keys."""
        # Strip ghost thinking channels (Gemma 4 may leak even with reasoning disabled)
        cleaned = SignalEngine._strip_thinking_tags(raw_text)
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1

        if start == -1 or end == 0:
            # Surface the actual response head/tail so user can see what was returned
            # (rate limit msg, refusal, reasoning-without-JSON, empty, etc.)
            head = (raw_text or "")[:300].replace("\n", " ")
            tail = (raw_text or "")[-200:].replace("\n", " ") if len(raw_text or "") > 300 else ""
            logger.error(
                f"[SignalEngine] No JSON object found in AI response "
                f"(len={len(raw_text or '')}). Head: {head!r}"
                + (f" ... Tail: {tail!r}" if tail else "")
            )
            return None

        json_str = cleaned[start:end]

        parsed = None
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning(f"[SignalEngine] JSON parse error: {exc}")
            # Handle "Extra data" — AI returned trailing text after valid JSON
            if "Extra data" in str(exc):
                try:
                    parsed = json.loads(json_str[:exc.pos])
                    logger.info("[SignalEngine] Extra data trimmed, JSON parsed successfully.")
                except Exception:
                    pass
            # Handle truncated JSON
            if parsed is None:
                parsed = SignalEngine._repair_truncated_json(json_str)
                if parsed is not None:
                    logger.info("[SignalEngine] Truncated JSON repaired successfully.")
            if parsed is None:
                logger.error("[SignalEngine] JSON repair failed.")
                logger.debug(f"[SignalEngine] Attempted to parse: {json_str[:500]}")
                return None

        return SignalEngine._expand_keys(parsed)

    @staticmethod
    def _repair_truncated_json(json_str: str) -> Optional[Dict]:
        """Try to repair truncated JSON by closing open structures."""
        last_complete = json_str.rfind("},")
        if last_complete == -1:
            last_complete = json_str.rfind("}")

        if last_complete == -1:
            return None

        trimmed = json_str[: last_complete + 1]

        open_brackets = trimmed.count("[") - trimmed.count("]")
        open_braces = trimmed.count("{") - trimmed.count("}")

        needs_best_candidate = '"market_scan"' in trimmed and '"best_candidate"' not in trimmed

        closing = ""
        closing += "]" * open_brackets
        if needs_best_candidate:
            closing += ',"best_candidate":"NONE"'
        closing += "}" * open_braces

        candidate = trimmed + closing

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
