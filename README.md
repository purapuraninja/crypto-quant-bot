# AI Quant Trading Bot

Automated crypto futures perpetual trading bot with a clean two-layer architecture:

```
┌──────────────────────────────────────────────────────┐
│  DATA LAYER        MarketFeed (ccxt)                  │
│    fetches OHLCV → computes EMA / RSI / vol / struct  │
├──────────────────────────────────────────────────────┤
│  ANALYZER LAYER    SignalEngine (Claude AI)           │
│    market snapshot → quant rules → JSON signal        │
├──────────────────────────────────────────────────────┤
│  EXECUTION LAYER   CEXExecutor | DEXExecutor          │
│    entry order → SL order → TP order                  │
└──────────────────────────────────────────────────────┘
```

---

## Supported Exchanges

| Mode | Exchange | Type |
|------|----------|------|
| `CEX` | Binance USDM Futures | Centralised perpetuals |
| `DEX` | Hyperliquid | Decentralised perpetuals |

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

### 3. Run in dry-run mode (safe, no real orders)

```bash
# Ensure DRY_RUN=true in .env
python main.py
```

### 4. Go live

```bash
# Set DRY_RUN=false in .env
# Ensure all keys are correct
python main.py
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required.** Claude API key |
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | Claude model to use |
| `EXCHANGE_MODE` | `CEX` | `CEX` (Binance) or `DEX` (Hyperliquid) |
| `DRY_RUN` | `true` | `true` = simulate only, no real orders |
| `BINANCE_API_KEY` | — | Binance API key (CEX live mode) |
| `BINANCE_SECRET` | — | Binance secret (CEX live mode) |
| `BINANCE_TESTNET` | `true` | Use Binance testnet |
| `HL_WALLET_ADDRESS` | — | Hyperliquid wallet address (DEX) |
| `HL_PRIVATE_KEY` | — | Ethereum private key (DEX live mode) |
| `HL_TESTNET` | `true` | Use Hyperliquid testnet |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (optional alerts) |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID (optional alerts) |
| `SYMBOLS` | `BTC/USDT:USDT` | Comma-separated ccxt symbols |
| `ACCOUNT_BALANCE` | `10000` | Account balance in USDT |
| `SCAN_INTERVAL_SECONDS` | `300` | Seconds between scans (5 min default) |
| `MAX_OPEN_POSITIONS` | `3` | Max concurrent positions |

---

## Symbol Formats

**CEX (Binance futures):** Use ccxt unified format
```
BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT
```

**DEX (Hyperliquid):** Bot auto-converts — you can use either format
```
BTC/USDT:USDT,ETH/USDT:USDT
# or
BTC,ETH,SOL
```

---

## Project Structure

```
trading_bot/
├── .env.example          # Environment template
├── .env                  # Your config (gitignored)
├── requirements.txt
├── main.py               # Agent loop entry point
├── config.py             # Config loader
├── analyzer/
│   └── signal_engine.py  # Claude AI signal analysis
├── data/
│   └── market_feed.py    # OHLCV + indicator computation
├── execution/
│   ├── base_executor.py  # Abstract interface
│   ├── cex_executor.py   # Binance futures execution
│   └── dex_executor.py   # Hyperliquid execution
├── utils/
│   ├── logger.py
│   └── telegram_notifier.py
└── logs/                 # Auto-created, daily log files
```

---

## Risk Warning

This bot trades real money when `DRY_RUN=false`. Always:
- Test thoroughly with `DRY_RUN=true` first
- Start with a small balance
- Verify stop-loss orders are placed correctly
- Monitor the first few live trades manually

**Not financial advice. Use at your own risk.**
