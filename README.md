# CoinDCX Strategy Terminal

A rule-based automated futures trading bot for CoinDCX that lets retail traders define simple strategies (buy the dip, ride the momentum) and execute them automatically — without writing code.

Everything is controlled through a Streamlit dashboard that works in both **Simulation** and **Live** mode.

---

## The Problem

Retail crypto traders face a set of recurring frustrations:

- **Manual chart monitoring** — staring at screens for hours waiting for the right moment.
- **Delayed reactions** — by the time you open the app, the dip is already over.
- **Emotional decisions** — panic selling or FOMO buying at the worst time.
- **No easy automation** — existing bots require coding knowledge or expensive subscriptions.

This project solves all four.  You set your rules once, click Start, and the bot watches the market 24/7.

---

## Core Features

| Feature | Description |
|---|---|
| **Strategy Automation** | Define a dip/rise threshold, comparison window, and check frequency — the bot monitors the market and enters when conditions are met. |
| **Futures Execution** | Places real leveraged orders on CoinDCX Futures via their REST API. |
| **Risk Management** | Configurable Take-Profit and Stop-Loss percentages, enforced both server-side (CoinDCX TP/SL API) and client-side (safety-net monitor). |
| **Precision Engine** | All prices and quantities are snapped to exchange tick-size rules before submission, preventing HTTP 422 rejection errors. |
| **Simulation Mode** | Paper-trade with a fake wallet to test strategies risk-free before committing real funds. |
| **Live Trading** | Executes real CoinDCX Futures orders with your API keys and margin. |
| **Streamlit Dashboard** | Dark-themed fintech UI for configuration, live monitoring, PnL tracking, and log viewing. |
| **Wallet Validation** | Checks available margin before every trade and blocks execution if funds are insufficient. |
| **INR + USDT Margin** | Supports both INR-margined and USDT-margined futures with automatic currency conversion. |
| **Multi-Pair** | Trade BTC, ETH, or SOL futures from the UI dropdown. |

---

## System Architecture

```
                    ┌──────────────────┐
                    │  Streamlit UI    │◄── standalone (default)
                    │  (ui.py)         │     or HTTP client
                    └────────┬─────────┘
                             │  (direct import OR HTTP)
                    ┌────────▼─────────┐
                    │  FastAPI Backend  │◄── optional, owns StrategyManager
                    │  (backend/)      │     when BACKEND_URL is set
                    └────────┬─────────┘
                             │
          ┌──────────────────▼───────────────────┐
          │          StrategyManager              │
          │  (strategy threads, persistence,      │
          │   heartbeat monitor, portfolio guard) │
          └──────────────────┬───────────────────┘
                             │
          ┌──────────────────▼───────────────────┐
          │      Strategy Engine (strategy.py)    │
          │  momentum / reversal entry evaluation │
          └──────────────────┬───────────────────┘
                             │
          ┌──────────────────▼───────────────────┐
          │   Execution Engine (execution.py)     │
          │   orders, leverage, TP/SL, wallet     │
          └──────────────────┬───────────────────┘
                             │
          ┌──────────────────▼───────────────────┐
          │   Precision Layer (exchange_precision)│
          │   snap prices & quantities            │
          └──────────────────┬───────────────────┘
                             │
          ┌──────────────────▼───────────────────┐
          │          CoinDCX REST API             │
          └──────────────────────────────────────┘

          ┌──────────────────────────────────────┐
          │  Position Monitor (position_monitor)  │
          └──────────────────────────────────────┘

          ┌──────────────────────────────────────┐
          │  Simulation Engine (sim_wallet)       │
          └──────────────────────────────────────┘
```

---

## Project Structure

```
Trading Bot/
├── ui.py                     Streamlit trading dashboard (dark fintech theme)
├── run.py                    CLI entry-point (loads .env → runs bot headless)
├── share_dashboard.py        Expose dashboard via ngrok public URL
├── requirements.txt          Python dependencies
├── .env.example              Template for environment variables
├── .gitignore                Excludes .env, runtime_config.json, __pycache__
│
├── backend/                  FastAPI backend (optional — decoupled engine)
│   ├── __init__.py
│   ├── main.py               App entrypoint, creates StrategyManager singleton
│   ├── client.py             HTTP client matching StrategyManager interface
│   └── api/
│       ├── __init__.py
│       └── strategy_routes.py  REST endpoints for strategy lifecycle & queries
│
└── bot/
    ├── __init__.py           Package docstring and module index
    ├── config.py             All strategy parameters, API keys, runtime loading
    ├── market_data.py        Price polling, candlestick cache, USDT/INR rate, instrument rules
    ├── strategy.py           Momentum / Reversal entry condition evaluation
    ├── execution.py          CoinDCX API: orders, leverage, TP/SL, positions, wallet
    ├── exchange_precision.py Price and quantity snapping to exchange tick sizes
    ├── position_monitor.py   Live TP/SL monitoring loop, shared hit-detection helper
    ├── sim_wallet.py         Thread-safe simulated wallet for paper trading
    ├── persistence.py        Session-state persistence and recovery
    ├── strategy_config.py    Per-strategy configuration dataclass
    ├── strategy_manager.py   Multi-strategy registry, lifecycle, portfolio guardrails
    └── main.py               Orchestration: validate → scan → order → monitor → done
```

---

## How the Bot Works

```
User configures strategy (UI or .env)
        │
        ▼
Bot validates inputs (API keys, direction, margins)
        │
        ▼
Market data monitored every N seconds
        │
        ▼
Price change compared to threshold (momentum / reversal)
        │
        ▼
Entry condition triggers → order placed on CoinDCX
        │
        ▼
TP/SL created on the open position (server-side)
        │
        ▼
Position monitored tick-by-tick (client-side safety net)
        │
        ▼
Exit condition hit (TP, SL, or manual stop)
        │
        ▼
Position closed — PnL logged — bot exits
```

### Entry Logic

Every `CHECK_FREQUENCY_SECONDS` the bot:

1. Fetches the **current market price**.
2. Fetches the price from **COMPARISON_WINDOW_MINUTES ago** (cache or candlestick API).
3. Computes: `price_change = (current - past) / past * 100`

| Mode | LONG trigger | SHORT trigger |
|---|---|---|
| Momentum | `price_change >= DIP_PERCENT` | `price_change <= -DIP_PERCENT` |
| Reversal | `price_change <= -DIP_PERCENT` | `price_change >= DIP_PERCENT` |

### Exit Logic

| Direction | Take-Profit | Stop-Loss |
|---|---|---|
| LONG | `entry * (1 + TP%)` | `entry * (1 - SL%)` |
| SHORT | `entry * (1 - TP%)` | `entry * (1 + SL%)` |

TP/SL is set **server-side** (CoinDCX TP/SL API) and monitored **client-side** as a safety net.

---

## Running the Project

### Prerequisites

- Python 3.10+
- A CoinDCX account with Futures enabled (for Live mode)
- API key & secret from [CoinDCX API Dashboard](https://coindcx.com/api-dashboard)

### Install

```bash
cd "Trading Bot"
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env with your API credentials (optional for Simulation mode)
```

### Launch the Dashboard

```bash
streamlit run ui.py
```

The UI opens at `http://localhost:8501`.  Select **Simulation** or **Live** mode, configure your strategy, and click **Start Bot**.

### Headless CLI Mode

```bash
python run.py
```

Reads parameters from `.env` and `runtime_config.json`, then runs the bot without a UI.

### Share via Public URL

```bash
python share_dashboard.py
```

Uses [ngrok](https://ngrok.com/) to expose the Streamlit dashboard with a public URL you can share on Slack.

> Requires a free ngrok auth token: `ngrok config add-authtoken <TOKEN>` or set `NGROK_AUTHTOKEN`.

### Backend API (optional)

The project supports a decoupled architecture where a FastAPI backend owns the `StrategyManager` and the Streamlit UI communicates via HTTP.  This is optional — the default standalone mode still works exactly as before.

**Terminal 1 — start the backend:**

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

**Terminal 2 — start the UI pointing at the backend:**

```bash
BACKEND_URL=http://localhost:8000 streamlit run ui.py
```

When `BACKEND_URL` is **not** set (Railway, Streamlit Cloud, or plain `streamlit run ui.py`), the UI falls back to running the `StrategyManager` locally — no backend required.

| Endpoint | Method | Description |
|---|---|---|
| `/strategy/start` | POST | Register + start a strategy |
| `/strategy/stop` | POST | Stop a running strategy |
| `/strategy/{id}` | DELETE | Remove a finished strategy |
| `/strategy/margin-check` | POST | Check margin availability |
| `/strategies` | GET | List all strategies |
| `/strategies/active/ids` | GET | Active strategy IDs |
| `/portfolio` | GET | Portfolio summary |
| `/logs` | GET | Recent log entries |
| `/settings` | GET | Current settings |
| `/settings/max-margin` | PUT | Update max portfolio margin |

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `COINDCX_API_KEY` | — | CoinDCX API key |
| `COINDCX_API_SECRET` | — | CoinDCX API secret |
| `PAIR` | `B-BTC_USDT` | Futures instrument pair |
| `DIP_PERCENT` | `5` | % price change to trigger entry |
| `COMPARISON_WINDOW_MINUTES` | `60` | Compare current price to price X min ago |
| `CHECK_FREQUENCY_SECONDS` | `30` | How often the bot checks conditions |
| `STRATEGY_EXPIRY_MINUTES` | `1440` | Max wait time for entry (24 h) |
| `NOTIONAL` | `0` | Total position value in margin currency |
| `LEVERAGE` | `10` | Position leverage multiplier |
| `ORDER_TYPE` | `market` | `market` or `limit` |
| `TAKE_PROFIT_PERCENT` | `3` | TP distance from entry |
| `STOP_LOSS_PERCENT` | `2` | SL distance from entry |
| `DIRECTION` | `LONG` | `LONG` or `SHORT` |
| `STRATEGY_MODE` | `momentum` | `momentum` or `reversal` |
| `MARGIN_CURRENCY` | `INR` | `INR` or `USDT` |
| `TRADING_MODE` | `simulation` | `simulation` or `live` |
| `SIM_BALANCE` | `10000` | Starting balance for simulation |

---

## Railway Deployment

This project can be deployed to [Railway](https://railway.app/).

### Steps

1. Push the repository to GitHub (ensure `.env` is in `.gitignore`).
2. Create a new project on Railway and connect the GitHub repository.
3. Railway will detect the `Procfile` and install dependencies from `requirements.txt`.
4. Add your API credentials as environment variables in the Railway dashboard (never commit them).
5. The app runs with:

```
streamlit run ui.py --server.port=$PORT --server.address=0.0.0.0
```

The deployed app will be accessible via the Railway public URL.

### Deployment Files

| File | Purpose |
|---|---|
| `Procfile` | Tells Railway how to start the Streamlit app |
| `runtime.txt` | Pins the Python version to 3.11 |
| `.streamlit/config.toml` | Headless server config for cloud environments |
| `requirements.txt` | Python package dependencies |

---

## Deployment — Streamlit Cloud

1. Push the repository to GitHub (ensure `.env` is in `.gitignore`).
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect your GitHub account.
3. Select the repo and set **Main file path** to `ui.py`.
4. Add API credentials as **Secrets** in the Streamlit Cloud dashboard (never commit them).
5. Deploy.

---

## Example Scenarios

### Momentum LONG on BTC — buy when BTC pumps 5 % in 1 hour

```env
PAIR=B-BTC_USDT
DIRECTION=LONG
STRATEGY_MODE=momentum
DIP_PERCENT=5
COMPARISON_WINDOW_MINUTES=60
```

### Reversal SHORT on ETH — short when ETH pumps 3 % in 30 min

```env
PAIR=B-ETH_USDT
DIRECTION=SHORT
STRATEGY_MODE=reversal
DIP_PERCENT=3
COMPARISON_WINDOW_MINUTES=30
```

### Reversal LONG on BTC — buy the dip after a 5 % crash in 2 hours

```env
PAIR=B-BTC_USDT
DIRECTION=LONG
STRATEGY_MODE=reversal
DIP_PERCENT=5
COMPARISON_WINDOW_MINUTES=120
```

---

## Safety Notes

- **Never commit API keys.** The `.env` file is gitignored by default.
- **Start with Simulation mode.** Test your strategy risk-free before switching to Live.
- **Use small notional values first.** Even in Live mode, start small until you trust the setup.
- **Trading carries risk.** Cryptocurrency futures are highly volatile and leveraged — you can lose more than your margin.  This tool is provided for educational purposes; use at your own risk.
- **Server-side TP/SL is your safety net.** Even if the bot crashes, CoinDCX's server-side orders protect the position.

---

## Disclaimer

This project is provided **as-is** for educational and personal-use purposes.  The authors are not responsible for any financial losses incurred through the use of this software.  Always do your own research and never trade with money you cannot afford to lose.
