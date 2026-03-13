# CoinDCX Futures — Dip / Rise Trading Bot

Automated Python trading bot for CoinDCX Futures that monitors market prices and executes trades when a configurable **Dip/Rise** strategy condition is met, with **Momentum** or **Reversal** behaviour.

---

## Project Structure

```
Trading Bot/
├── run.py                  # Entry-point — loads .env and starts the bot
├── ui.py                   # Streamlit web dashboard
├── share_dashboard.py      # Expose dashboard via public ngrok URL
├── requirements.txt        # Python dependencies
├── .env.example            # Template for environment variables
├── .env                    # Your local config (create from .env.example)
├── runtime_config.json     # Saved parameters from the UI
└── bot/
    ├── __init__.py
    ├── config.py           # Strategy parameters & API key loading
    ├── market_data.py      # Price fetching + in-memory cache
    ├── strategy.py         # Momentum / Reversal entry evaluation
    ├── execution.py        # Order placement & position management via API
    ├── exchange_precision.py # Price & quantity snapping utilities
    ├── position_monitor.py # TP/SL monitoring loop
    ├── sim_wallet.py       # Simulated wallet for paper trading
    └── main.py             # Orchestration — entry scan → order → monitor
```

---

## How It Works

### Entry Logic

Every `CHECK_FREQUENCY_SECONDS` the bot:

1. Fetches the **current market price**.
2. Fetches the price from **COMPARISON_WINDOW_MINUTES ago** (from cache or candlestick API).
3. Computes: `price_change = (current - past) / past * 100`

| Mode       | LONG trigger                  | SHORT trigger                  |
|------------|-------------------------------|--------------------------------|
| Momentum   | `price_change >= DIP_PERCENT` | `price_change <= -DIP_PERCENT` |
| Reversal   | `price_change <= -DIP_PERCENT`| `price_change >= DIP_PERCENT`  |

### Exit Logic

After entry, the bot monitors the position continuously:

| Direction | Take-Profit Price                          | Stop-Loss Price                          |
|-----------|--------------------------------------------|------------------------------------------|
| LONG      | `entry * (1 + TAKE_PROFIT_PERCENT / 100)`  | `entry * (1 - STOP_LOSS_PERCENT / 100)`  |
| SHORT     | `entry * (1 - TAKE_PROFIT_PERCENT / 100)`  | `entry * (1 + STOP_LOSS_PERCENT / 100)`  |

TP/SL is set **both server-side** (via CoinDCX TPSL API) and **client-side** (the bot monitors and calls exit as a safety net).

---

## Setup

### 1. Prerequisites

- Python 3.10+
- A CoinDCX account with Futures enabled
- API key & secret from [CoinDCX API Dashboard](https://coindcx.com/api-dashboard)

### 2. Install Dependencies

```bash
cd "Trading Bot"
pip install -r requirements.txt
```

### 3. Configure

Copy the example environment file and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```env
COINDCX_API_KEY=your_api_key_here
COINDCX_API_SECRET=your_api_secret_here

PAIR=B-BTC_USDT
DIP_PERCENT=5
COMPARISON_WINDOW_MINUTES=60
CHECK_FREQUENCY_SECONDS=30
STRATEGY_EXPIRY_MINUTES=1440

MARGIN_USDT=100
LEVERAGE=10
ORDER_TYPE=market
TAKE_PROFIT_PERCENT=3
STOP_LOSS_PERCENT=2

DIRECTION=LONG
STRATEGY_MODE=momentum
```

### 4. Run

**CLI (headless):**

```bash
python run.py
```

**Streamlit dashboard (local):**

```bash
streamlit run ui.py
```

### 5. Share Dashboard via Public URL

Use ngrok to expose the Streamlit dashboard so teammates can access it remotely (e.g. share on Slack):

```bash
python share_dashboard.py
```

The script will print a public URL like:

```
==================================================
  Public Dashboard URL:
  https://abcd-12-34-56-78.ngrok-free.app
==================================================
```

Share that link with anyone who needs to view the dashboard.

> **Note:** You need a free [ngrok account](https://ngrok.com/) and auth token.
> Configure it once with `ngrok config add-authtoken <YOUR_TOKEN>` or set the
> `NGROK_AUTHTOKEN` environment variable.

---

## Configuration Reference

| Variable                     | Default      | Description                                           |
|------------------------------|--------------|-------------------------------------------------------|
| `COINDCX_API_KEY`           | —            | Your CoinDCX API key                                  |
| `COINDCX_API_SECRET`        | —            | Your CoinDCX API secret                               |
| `PAIR`                       | `B-BTC_USDT` | CoinDCX Futures instrument pair                       |
| `DIP_PERCENT`                | `5`          | % price change required to trigger entry              |
| `COMPARISON_WINDOW_MINUTES`  | `60`         | Compare current price to price X minutes ago          |
| `CHECK_FREQUENCY_SECONDS`    | `30`         | How often the bot checks conditions                   |
| `STRATEGY_EXPIRY_MINUTES`    | `1440`       | Max wait time for entry (default 24h)                 |
| `MARGIN_USDT`                | `100`        | Margin per trade in USDT                              |
| `LEVERAGE`                   | `10`         | Position leverage                                     |
| `ORDER_TYPE`                 | `market`     | `market` or `limit`                                   |
| `TAKE_PROFIT_PERCENT`        | `3`          | TP distance from entry (%)                            |
| `STOP_LOSS_PERCENT`          | `2`          | SL distance from entry (%)                            |
| `DIRECTION`                  | `LONG`       | `LONG` or `SHORT`                                     |
| `STRATEGY_MODE`              | `momentum`   | `momentum` or `reversal`                              |
| `MARGIN_CURRENCY`            | `USDT`       | Margin currency (`USDT` or `INR`)                     |
| `API_MAX_RETRIES`            | `3`          | Retry count for failed API calls                      |
| `API_RETRY_DELAY_SECONDS`    | `2`          | Seconds between retries                               |
| `LOG_LEVEL`                  | `INFO`       | `DEBUG`, `INFO`, `WARNING`, `ERROR`                   |

---

## Example Scenarios

### Momentum LONG on BTC (buy when BTC pumps 5% in 1 hour)

```env
PAIR=B-BTC_USDT
DIRECTION=LONG
STRATEGY_MODE=momentum
DIP_PERCENT=5
COMPARISON_WINDOW_MINUTES=60
```

### Reversal SHORT on ETH (short when ETH pumps 3% in 30 min)

```env
PAIR=B-ETH_USDT
DIRECTION=SHORT
STRATEGY_MODE=reversal
DIP_PERCENT=3
COMPARISON_WINDOW_MINUTES=30
```

### Reversal LONG on BTC (buy the dip after a 5% crash in 2 hours)

```env
PAIR=B-BTC_USDT
DIRECTION=LONG
STRATEGY_MODE=reversal
DIP_PERCENT=5
COMPARISON_WINDOW_MINUTES=120
```

---

## Constraints

- Only **one active position** per strategy run.
- Entry scanning stops immediately after an order is placed.
- The bot exits cleanly if the expiry window passes without triggering.
- Server-side TP/SL orders protect the position even if the bot crashes.

---

## Disclaimer

This bot is provided for **educational purposes only**. Trading cryptocurrency futures involves significant risk of loss. Use at your own risk. Always test with small amounts first.
