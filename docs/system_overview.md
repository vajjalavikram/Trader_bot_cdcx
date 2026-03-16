# CoinDCX Strategy Terminal — System Overview

> Audience: Product managers, engineering leads, new developers.
> Last updated: March 2026.

---

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [System Architecture](#2-system-architecture)
3. [Key Modules](#3-key-modules)
4. [Strategy Execution Flow](#4-strategy-execution-flow)
5. [Database Design](#5-database-design)
6. [Security Model](#6-security-model)
7. [Reliability Features](#7-reliability-features)
8. [Concurrency Model](#8-concurrency-model)
9. [Limitations & Bottlenecks](#9-limitations--bottlenecks)
10. [Future Improvements](#10-future-improvements)
11. [Development Phases](#11-development-phases)
12. [Operation Guide](#12-operation-guide)

---

## 1. Product Overview

### What the bot does

The CoinDCX Strategy Terminal is an automated futures trading platform that connects to the CoinDCX exchange. Users define rule-based strategies through a browser dashboard, and the system continuously monitors crypto markets and executes leveraged trades when conditions are met — without any manual intervention.

### The user problem it solves

Retail cryptocurrency traders face four recurring frustrations:

| Problem | How the bot solves it |
|---|---|
| **Manual chart monitoring** — hours spent staring at screens waiting for the right moment. | The bot watches the market 24/7 and triggers automatically. |
| **Delayed reactions** — by the time you open the app the opportunity is gone. | Sub-minute scan loops catch dips and surges in real time. |
| **Emotional decisions** — panic selling or FOMO buying at the worst time. | Pre-configured TP/SL enforces disciplined exits. |
| **No easy automation** — existing bots require coding or expensive subscriptions. | A browser UI replaces all code; just fill in parameters and click "Start". |

### Intended users

- **Retail crypto traders** who want rule-based automation without writing code.
- **Quantitative hobbyists** who want a structured platform for testing dip/rise hypotheses.
- **Small trading desks** that need a lightweight, self-hosted strategy runner.

### Supported strategies

The platform currently supports one strategy family — **Dip / Rise** — with two behavioral modes:

| Mode | LONG trigger | SHORT trigger |
|---|---|---|
| **Momentum** | Price rose ≥ X % over the comparison window. | Price fell ≥ X % over the comparison window. |
| **Reversal** | Price fell ≥ X % (buy the dip). | Price rose ≥ X % (short the top). |

Users configure the threshold (`dip_percent`), the lookback window (`comparison_window_minutes`), and the scan interval (`check_frequency_seconds`). Once an entry is triggered, the bot places an order and monitors the position until Take-Profit or Stop-Loss is hit.

### How users interact with the system

```
User (browser)
  │
  ▼
Streamlit UI   ── configures parameters, starts/stops strategies,
  │                views live PnL, logs, and market data
  │
  ▼
FastAPI Backend (optional)   ── owns the engine when deployed
  │                              in decoupled mode
  ▼
StrategyManager   ── registers strategies, enforces margin limits,
  │                   launches threads, persists state
  ▼
Strategy Engine   ── evaluates entry conditions, places orders,
                     monitors positions, logs trades
```

In **standalone mode** (default) the Streamlit process hosts the `StrategyManager` directly. In **decoupled mode** (when `BACKEND_URL` is set) the UI becomes a pure HTTP client and the engine runs inside a separate FastAPI process.

---

## 2. System Architecture

### Component diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                         User (Browser)                           │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                ┌────────────▼────────────┐
                │      Streamlit UI       │  ◄── ui.py
                │  (tabs: Guide, Market   │      Tabs, forms, charts,
                │   Viewer, Terminal)     │      live metrics, logs
                └────────────┬────────────┘
                             │  direct import (standalone)
                             │  or HTTP (decoupled)
                ┌────────────▼────────────┐
                │    FastAPI Backend       │  ◄── backend/main.py
                │  /auth  /keys           │      JWT auth, key mgmt,
                │  /strategy  /portfolio  │      user isolation
                └────────────┬────────────┘
                             │
          ┌──────────────────▼──────────────────┐
          │          StrategyManager             │  ◄── bot/strategy_manager.py
          │  Thread scheduler, queue processor,  │
          │  heartbeat monitor, portfolio guard  │
          └──────────┬──────────┬───────────────┘
                     │          │
          ┌──────────▼──┐  ┌───▼──────────────┐
          │  Strategy   │  │  Persistence     │  ◄── bot/persistence.py
          │  Thread     │  │  Layer (SQLite)  │      db/database.py
          │  (per strat)│  │                  │      db/models.py
          └──────┬──────┘  └──────────────────┘
                 │
      ┌──────────▼────────────────────────────┐
      │           Strategy Engine              │
      │  ┌────────────┐  ┌─────────────────┐  │
      │  │ strategy.py │  │ execution.py    │  │
      │  │ (evaluate)  │  │ (CoinDCX API)   │  │
      │  └────────────┘  └─────────────────┘  │
      │  ┌──────────────────┐  ┌───────────┐  │
      │  │position_monitor.py│  │sim_wallet │  │
      │  │(TP/SL watcher)    │  │(paper)    │  │
      │  └──────────────────┘  └───────────┘  │
      └───────────────────────────────────────┘
```

### Component responsibilities

| Component | Responsibility |
|---|---|
| **Streamlit UI** (`ui.py`) | Parameter input, strategy control buttons, live portfolio metrics, candlestick charts, orderbook, log viewer. Operates in standalone or decoupled mode. |
| **FastAPI Backend** (`backend/`) | Stateless HTTP API layer. Owns the `StrategyManager` singleton in decoupled mode. Handles JWT authentication, user isolation, and encrypted API-key storage. |
| **StrategyManager** (`bot/strategy_manager.py`) | Central orchestrator. Registers strategies, enforces a portfolio margin ceiling, launches one thread per strategy, monitors heartbeats, queues excess strategies, and persists state to SQLite. |
| **Thread Scheduler + Queue** (inside StrategyManager) | Caps concurrent strategy threads at `MAX_CONCURRENT_STRATEGIES` (20). Overflow strategies enter a FIFO queue and are auto-started when capacity frees. |
| **Strategy Engine** (`bot/main.py`, `strategy.py`, `execution.py`, `position_monitor.py`) | Self-contained per-strategy logic: price scanning, entry evaluation, order placement, TP/SL creation, and position monitoring. Each thread gets its own `StrategyConfig` — no shared global state. |
| **Persistence Layer** (`bot/persistence.py`) | Serialises the full strategy registry to SQLite on every significant state change. On restart, `StrategyManager.recover_session()` rebuilds the registry and restarts active strategies. |
| **SQLite Database** (`db/`) | Stores users, encrypted API keys, strategies, positions, and trade history. Configured with WAL mode, foreign-key enforcement, and a 5-second busy timeout. |

---

## 3. Key Modules

### `backend/` — FastAPI API layer

| File | Purpose |
|---|---|
| `main.py` | Application entry point. Creates `FastAPI`, initialises the database, instantiates `StrategyManager`, mounts all routers, exposes `/health`. |
| `client.py` | Drop-in HTTP replacement for `StrategyManager`. The UI uses this when `BACKEND_URL` is set; every public method mirrors the manager's interface. |
| `api/key_routes.py` | `POST /keys/add`, `GET /keys`, `DELETE /keys/{id}` — Fernet-encrypted exchange credential storage, scoped to the caller. |
| `api/strategy_routes.py` | Strategy lifecycle (start, stop, remove), session loading, margin check, portfolio summary, log streaming, and settings. Includes the `POST /session/load` endpoint. An in-memory ownership map enforces user isolation without modifying `StrategyManager`. |
| `security/identity.py` | `get_user_id_from_api_key()` (SHA-256 hash) and `get_caller_id()` — FastAPI dependency that derives `user_id` from the `X-API-Key` header. |
| `security/crypto.py` | `encrypt_secret()` / `decrypt_secret()` via `cryptography.fernet`. Key read from `ENCRYPTION_KEY`. |

### `bot/` — Trading engine

| File | Purpose |
|---|---|
| `config.py` | Reads environment variables and `runtime_config.json` into module-level constants. Provides `normalize_pair()` and `load_from_dict()`. Used by the CLI entry point (`run.py`). |
| `strategy_config.py` | `StrategyConfig` dataclass with ~25 fields. `from_params()` builds a config from a UI params dict; `from_global_config()` reads from `bot.config`. Each strategy thread receives its own instance, enabling true parallel execution. |
| `strategy.py` | `evaluate(current_price, past_price, cfg)` — returns `"buy"`, `"sell"`, or `None` based on momentum/reversal logic and the configured `dip_percent` threshold. |
| `execution.py` | CoinDCX Futures API integration: HMAC-SHA256 request signing, order placement, leverage setting, position queries, TP/SL creation via `create_tpsl`, wallet balance fetching, and automatic retry on 5xx errors. |
| `market_data.py` | Price cache (in-memory deque), live trade fetching, candlestick retrieval, USDT/INR exchange rate, and instrument rule queries (min quantity, tick size, notional limits). |
| `exchange_precision.py` | `snap_price(price, increment)` and `snap_quantity(qty, step)` — floor-rounds values to exchange tick rules, preventing 422 rejection errors. |
| `position_monitor.py` | `monitor()` blocks until TP/SL is hit, the position is closed server-side, or the stop event is set. Computes unrealized PnL each loop iteration and streams telemetry via the status callback. |
| `sim_wallet.py` | `SimWallet` class — thread-safe simulated futures wallet for paper trading. Tracks balance, open positions, margin deductions, and PnL calculation. |
| `main.py` | `run(strategy_config, stop_event, status_callback, sim_wallet)` — the single-strategy orchestrator. Validates config, sets leverage, seeds the price cache, runs the entry scan loop, places the order (real or simulated), attaches TP/SL, and monitors until exit. |
| `strategy_manager.py` | Multi-strategy orchestrator. Thread-safe registry (RLock), portfolio margin guard, thread-per-strategy launcher with a 20-thread cap and FIFO overflow queue, heartbeat monitor that restarts silently crashed threads, and SQLite persistence on every significant state transition. |
| `persistence.py` | `load_session_state()` / `save_session_state()` — reads and writes the strategy registry to the `strategies` table. Secrets are stripped before writing. The legacy JSON backend is preserved but disabled. |

### `db/` — Database layer

| File | Purpose |
|---|---|
| `database.py` | Lazy singleton `get_connection()`. Opens `data/trading_bot.db` with `check_same_thread=False`, `sqlite3.Row` factory, WAL journal mode, foreign-key enforcement, and a 5-second busy timeout. |
| `models.py` | `initialize_database()` — idempotent schema creation for `users`, `api_keys`, `strategies`, `positions`, and `trades` tables, plus indexes and a default-user seed row. Includes ALTER TABLE migrations for the auth columns. |
| `trade_logger.py` | `log_trade(strategy_id, user_id, side, price, quantity, timestamp)` — atomic INSERT into the `trades` table, called on confirmed position open/close. |

### `ui.py` — Streamlit dashboard

A 980-line single-file Streamlit application with three tabs:

| Tab | Contents |
|---|---|
| **Guide** | Product overview, strategy explanation, step-by-step instructions, live system status (auto-refresh fragment), strategy lifecycle reference, risk guardrails, and dashboard navigation hints. |
| **Market Viewer** | Token/interval selectors, live LTP, price-change metric, Plotly candlestick chart, top-5 orderbook bids/asks, and recent-10 trades table. Auto-refreshes every 5 seconds. |
| **Trading Terminal** | Full strategy configuration sidebar (mode, pair, leverage, notional, TP/SL, dip %, expiry), start/stop/remove buttons, portfolio summary metrics, active-strategies table with PnL colour-coding, and a scrollable event log. |

A compact system status bar at the top of the page (above tabs) shows: system state indicator, trading mode, active strategy count, margin used, and portfolio PnL.

---

## 4. Strategy Execution Flow

Here is the complete lifecycle from the moment a user clicks "Start Strategy" to the final trade log entry.

### Step 1 — UI request

The user fills in strategy parameters in the Streamlit sidebar (pair, leverage, notional, direction, TP/SL, dip %, etc.) and clicks **Start Strategy**. The UI calls `manager.register_strategy(params)` followed by `manager.start_strategy(sid)`.

### Step 2 — API endpoint (decoupled mode)

If running in decoupled mode, the Streamlit `BackendClient` sends `POST /strategy/start` with the params dict. The FastAPI handler injects CoinDCX credentials from environment variables if not provided, calls `register_strategy`, records ownership (`strategy_id → user_id`), then starts the strategy.

### Step 3 — StrategyManager registration

`register_strategy()` assigns a sequential ID (`S001`, `S002`, …), computes margin from notional/leverage, initialises the strategy dict (status, prices, PnL, timestamps), and persists the state to SQLite.

### Step 4 — Thread launch (or queue)

`start_strategy()` counts currently alive threads. If below `MAX_CONCURRENT_STRATEGIES` (20), it calls `_launch_thread()` which creates a daemon thread targeting `_run_strategy()`. If at capacity, the strategy is placed in a FIFO queue with status `"queued"`. A background queue-processor thread polls every 5 seconds and launches queued strategies as slots free up.

### Step 5 — Strategy thread initialisation

Inside `_run_strategy()`:
1. A `_StrategyLogHandler` is attached to the `"bot"` logger so all log output is captured in the shared `logs` deque (visible in the UI).
2. A `StrategyConfig` is built from the params dict — this is the strategy's private, isolated configuration.
3. In simulation mode, a `SimWallet` is created with the configured starting balance.
4. `bot.main.run()` is called with the config, stop event, status callback, and optional sim wallet.

### Step 6 — Entry scan loop

`run()` validates the config, sets leverage on CoinDCX (live mode), fetches instrument rules (min quantity, tick size), seeds the price cache, and enters a loop:

1. Fetch the **current price** (latest trade).
2. Fetch the **past price** from the cache (X minutes ago).
3. Call `strategy.evaluate(current_price, past_price, cfg)`.
4. If `None` — no trigger. Sleep for `check_frequency_seconds` and repeat.
5. If the strategy expiry is reached without a trigger — exit with status `"Expired"`.
6. The `status_callback` is invoked each iteration with the current price, phase, and wallet balance.

### Step 7 — Entry trigger and order placement

When `evaluate()` returns `"buy"` or `"sell"`:

1. Compute `quantity = notional / current_price`, snapped to exchange tick rules.
2. **Live mode**: call `execution.place_order()` which sends `POST /exchange/v1/derivatives/futures/orders/create` to CoinDCX with HMAC-SHA256 signing. Then resolve the position ID by polling `get_position()` up to 10 times (0.5s apart). Finally call `execution.create_tpsl()` to attach server-side TP/SL via `POST /exchange/v1/derivatives/futures/positions/create_tpsl`.
3. **Simulation mode**: call `sim_wallet.open_position()` which deducts margin and records the position locally.
4. The `status_callback` reports phase `"Positioned"` with position info, which triggers an **ENTRY** trade log via `db.trade_logger.log_trade()`.

### Step 8 — Position monitoring

**Live mode**: `position_monitor.monitor()` loops every `check_frequency_seconds`, fetches the current price, computes unrealized PnL, streams telemetry to the UI via the callback, and checks TP/SL:
- LONG: TP hit when `current ≥ tp_price`, SL hit when `current ≤ sl_price`.
- SHORT: TP hit when `current ≤ tp_price`, SL hit when `current ≥ sl_price`.

When TP or SL is hit, it calls `exit_position()` and returns.

**Simulation mode**: `_sim_monitor()` performs the same logic against the `SimWallet`, calling `sim_wallet.close_position()` on exit.

### Step 9 — Exit and trade logging

When the position closes, the `status_callback` reports phase `"Done"`. `StrategyManager.update_strategy_state()` detects this transition and calls `_log_trade_event()` with side `"EXIT"`, which writes a second row to the `trades` table via `db.trade_logger.log_trade()`.

The strategy thread cleans up: removes itself from `_active_threads` (freeing a concurrency slot), removes the log handler, and triggers a final `_persist()`.

---

## 5. Database Design

The application uses SQLite (`data/trading_bot.db`) with WAL journal mode, foreign-key enforcement, and a 5-second busy timeout for concurrent access from strategy threads.

### Entity-Relationship diagram

```
users
  │
  ├──< strategies   (user_id FK)
  │       │
  │       ├──< positions   (strategy_id FK, CASCADE)
  │       │
  │       └──< trades      (strategy_id FK, CASCADE)
  │
  └──< api_keys    (user_id FK, CASCADE)
```

### Table descriptions

#### `users`

| Column | Type | Notes |
|---|---|---|
| `user_id` | TEXT PK | UUID hex, generated at registration. |
| `email` | TEXT UNIQUE | Login identifier. Nullable for the legacy `default_user` seed row. |
| `password_hash` | TEXT | Legacy column (unused — kept for schema compatibility). |
| `created_at` | INTEGER | Unix epoch seconds. |

A `default_user` row is seeded on first startup for backward compatibility with the standalone (non-auth) UI mode.

#### `api_keys`

| Column | Type | Notes |
|---|---|---|
| `key_id` | TEXT PK | UUID hex. |
| `user_id` | TEXT FK → users | Owning user. ON DELETE CASCADE. |
| `exchange` | TEXT | Exchange identifier (e.g. `"coindcx"`). |
| `api_key` | TEXT | Public API key (stored in plaintext). |
| `encrypted_secret` | TEXT | Fernet-encrypted API secret. |
| `created_at` | INTEGER | Unix epoch seconds. |

The encrypted secret can only be decrypted with the `ENCRYPTION_KEY` environment variable. It is never returned in API responses.

#### `strategies`

| Column | Type | Notes |
|---|---|---|
| `strategy_id` | TEXT PK | Sequential ID (`S001`, `S002`, …). |
| `user_id` | TEXT FK → users | Owning user. Defaults to `default_user`. |
| `pair` | TEXT | CoinDCX instrument code (e.g. `B-BTC_USDT`). |
| `pair_label` | TEXT | Human-readable base symbol (e.g. `BTC`). |
| `direction` | TEXT | `LONG` or `SHORT`. |
| `strategy_mode` | TEXT | `momentum` or `reversal`. |
| `trading_mode` | TEXT | `simulation` or `live`. |
| `notional` | REAL | Position notional value. |
| `leverage` | INTEGER | Configured leverage. |
| `margin` | REAL | Computed as `notional / leverage`. |
| `dip_percent` | REAL | Entry threshold percentage. |
| `strategy_expiry_minutes` | INTEGER | Max wait time for entry. |
| `tp_percent` | REAL | Take-profit percentage. |
| `sl_percent` | REAL | Stop-loss percentage. |
| `status` | TEXT | Lifecycle state (see below). |
| `phase` | TEXT | Human-readable phase label. |
| `entry_price` | REAL | Filled after order execution. |
| `tp_price`, `sl_price` | REAL | Computed TP/SL prices. |
| `quantity` | REAL | Order quantity. |
| `current_price` | REAL | Last observed market price. |
| `pnl`, `pnl_percent` | REAL | Unrealized PnL. |
| `wallet_balance` | REAL | Last fetched wallet balance. |
| `error`, `error_detail_json` | TEXT | Error info if the strategy crashed. |
| `params_json` | TEXT | Full params dict as JSON (secrets stripped). |
| `created_at` | REAL | Unix timestamp. |
| `last_heartbeat` | REAL | Last heartbeat from the strategy thread. |

**Strategy status values**: `waiting`, `starting`, `queued`, `scanning`, `order_placed`, `position_open`, `stopping`, `stopped`, `expired`, `closed`, `error`.

#### `positions`

| Column | Type | Notes |
|---|---|---|
| `position_id` | TEXT PK | Exchange position ID or UUID. |
| `strategy_id` | TEXT FK → strategies | ON DELETE CASCADE. |
| `user_id` | TEXT FK → users | Owning user. |
| `entry_price` | REAL | Fill price. |
| `tp_price`, `sl_price` | REAL | TP/SL levels. |
| `quantity` | REAL | Position size. |
| `status` | TEXT | `open` or `closed`. |
| `opened_at`, `closed_at` | INTEGER | Epoch timestamps. |

#### `trades`

| Column | Type | Notes |
|---|---|---|
| `trade_id` | TEXT PK | UUID hex. |
| `strategy_id` | TEXT FK → strategies | ON DELETE CASCADE. |
| `user_id` | TEXT | Owning user. |
| `side` | TEXT | `ENTRY` or `EXIT`. |
| `price` | REAL | Execution price. |
| `quantity` | REAL | Trade size. |
| `timestamp` | INTEGER | Epoch seconds. |

### Foreign key cascades

Deleting a user cascades to `api_keys`. Deleting a strategy cascades to `positions` and `trades`. This ensures orphan data is never left behind.

### Indexes

| Index | Table | Column(s) |
|---|---|---|
| `idx_users_email` | users | `email` (unique, partial — WHERE email IS NOT NULL) |
| `idx_strategy_user` | strategies | `user_id` |
| `idx_position_strategy` | positions | `strategy_id` |
| `idx_trade_strategy` | trades | `strategy_id` |
| `idx_apikeys_user` | api_keys | `user_id` |

---

## 6. Security Model

### API-Key Identity (no email / password)

There is no traditional login system. The CoinDCX **API key** is the user's identity. When a user connects, the backend computes:

```
user_id = SHA-256( api_key )
```

This deterministic, non-reversible hash serves as the `user_id` for database ownership and strategy isolation. A user row is automatically created on first connection.

### Session flow

1. The user opens the Streamlit UI and enters their CoinDCX API key + secret.
2. The UI calls `POST /session/load` with the API key, secret, and an optional `remember_secret` flag.
3. The backend hashes the API key to derive `user_id`, creates the user row if it doesn't exist, optionally encrypts and stores the secret, and returns the user's existing strategies.
4. All subsequent API calls include the API key in the `X-API-Key` HTTP header. The backend hashes it on every request to identify the caller.

### Unprotected endpoints

- `POST /session/load` — initial connection (no header required; the key is in the body).
- `GET /health` — uptime check.

All other endpoints require a valid `X-API-Key` header.

### Encrypted API secret storage

Exchange API secrets are encrypted at rest using `cryptography.fernet` (AES-128-CBC with HMAC-SHA256 authentication):

1. During `POST /session/load`, if `remember_secret` is `true`, the secret is Fernet-encrypted with the `ENCRYPTION_KEY` environment variable and stored in the `api_keys` table.
2. If `remember_secret` is `false`, the secret is used only for the current session and never persisted.
3. `GET /keys` returns key metadata but **never** the `encrypted_secret` column.
4. API secrets are never logged.

### User isolation

Every strategy is tagged with the caller's `user_id` (SHA-256 hash of their API key) at the API routing layer. An in-memory ownership map (`strategy_id → user_id`) is loaded from the database at startup and updated on every create/delete. All query and mutation endpoints filter by the caller:

- `GET /strategies` — returns only the caller's strategies.
- `POST /strategy/stop` — returns HTTP 403 if the strategy belongs to another user.
- `GET /portfolio` — computes metrics only for the caller's strategies.
- `GET /logs` — filters log entries by the caller's strategy IDs.

This isolation is enforced entirely at the API routing layer. `StrategyManager` itself has no concept of users — it simply runs whatever strategies are registered. Entering the same API key from any device loads the same strategies.

---

## 7. Reliability Features

### Heartbeat monitoring

Every strategy thread updates a `last_heartbeat` timestamp on each iteration of its main loop (via `update_strategy_state`). A background monitor thread sweeps every 10 seconds:

- If a strategy is in an active status but its thread is dead **and** the heartbeat is older than 30 seconds, the strategy is considered silently crashed.
- The monitor resets the strategy to `"waiting"` and calls `start_strategy()`, which launches a fresh thread.

This automatically recovers from uncaught exceptions, segfaults, or thread starvation.

### Restart recovery (persistence)

`StrategyManager._persist()` writes the full strategy registry to SQLite after every significant state change (registration, phase transition, position open/close). On startup, `recover_session()`:

1. Sets a `_recovering` flag to suppress persistence writes during rebuild.
2. Loads all strategies from the database.
3. Rebuilds the in-memory registry.
4. Restarts strategies that were in an active status (`scanning`, `order_placed`, `position_open`, etc.).
5. Clears the flag and performs a final persist.

API credentials are **never** persisted — they are re-read from environment variables on recovery.

### Concurrency limits

`MAX_CONCURRENT_STRATEGIES = 20` caps the number of simultaneously running strategy threads. When a strategy is started and all 20 slots are occupied:

1. The strategy is set to status `"queued"` and added to a FIFO deque.
2. A background queue-processor thread polls every 5 seconds.
3. When a running thread finishes (its `_run_strategy` `finally` block pops it from `_active_threads`), the queue processor detects the freed slot and launches the next queued strategy.

This prevents thread explosion under heavy load.

### Database transactions

All SQLite writes use Python's `with conn:` context manager, which wraps the block in a transaction. The `busy_timeout = 5000` pragma tells SQLite to retry for up to 5 seconds before raising "database is locked", accommodating concurrent writes from multiple strategy threads. WAL mode allows readers to proceed without blocking writers.

### Portfolio margin guard

`StrategyManager.can_start_strategy(required_margin)` checks whether adding a new strategy would exceed the configurable `max_portfolio_margin` ceiling (default ₹50,000). The UI calls this before every strategy launch and blocks execution with a clear error message if the limit would be exceeded.

---

## 8. Concurrency Model

### Thread-per-strategy architecture

Each active strategy runs in its own Python `threading.Thread` (daemon). The thread:

1. Builds an isolated `StrategyConfig` from its params dict — no shared global config.
2. Calls `bot.main.run()` which contains the full scan → order → monitor loop.
3. Streams telemetry back to `StrategyManager` via a `status_callback` lambda.
4. On completion, removes itself from `_active_threads` and persists state.

A `threading.RLock` protects the shared strategy registry. Re-entrant locking is necessary because `update_strategy_state()` can be called from within `_persist()` indirectly.

### Queue overflow

When all 20 thread slots are occupied, excess strategies enter a FIFO queue (`deque`). The background `_process_queue()` thread drains the queue as slots free up. Queued strategies that receive a stop signal before launch are moved directly to `"stopped"`.

### Thread cleanup

The `_run_strategy()` method uses a `finally` block to guarantee cleanup:

```
finally:
    bot_logger.removeHandler(handler)
    with self._lock:
        self._active_threads.pop(strategy_id, None)
    self._persist()
```

This ensures dead threads never block the queue and the heartbeat monitor has accurate liveness information.

### Limitations of the thread-based design

| Limitation | Impact |
|---|---|
| **GIL contention** | Python's Global Interpreter Lock means CPU-bound work in one thread can delay others. In practice the bot is I/O-bound (HTTP calls, `time.sleep`), so the GIL is rarely a bottleneck. |
| **Memory overhead** | Each thread carries its own stack (~8 MB default on Linux). At 20 concurrent strategies this is manageable; at 200 it would not be. |
| **No cross-process scaling** | All strategies run in a single process. If the process dies, everything stops (though recovery restarts it). |
| **Thread debugging** | Daemon threads silently swallow exceptions unless they are caught and forwarded. The heartbeat monitor mitigates this by detecting dead threads. |

---

## 9. Limitations & Bottlenecks

### SQLite write concurrency

SQLite allows only one writer at a time (WAL mode allows concurrent readers). Under very high strategy counts, concurrent `_persist()` calls may experience the 5-second busy timeout. In practice, 20 concurrent strategies with writes every few seconds is well within SQLite's capacity.

### Thread scaling limits

The `MAX_CONCURRENT_STRATEGIES = 20` cap is a deliberate safety measure. Python threads are not lightweight coroutines — each consumes a real OS thread with a default 8 MB stack. Scaling to hundreds of strategies would require migrating to an async architecture (see Future Improvements).

### CoinDCX API rate limits

CoinDCX enforces rate limits that are not publicly documented per-endpoint. The bot uses `API_MAX_RETRIES` (default 3) with exponential backoff and retries on 5xx errors, but 429 (Too Many Requests) could still affect strategies during high-frequency scanning.

### No distributed workers

The entire system runs on a single node. There is no message queue, no horizontal scaling, and no failover. If the server goes down, strategies stop until restart recovery kicks in.

### Single-database architecture

All users, strategies, and trade history share one SQLite file. There is no database sharding, read replicas, or connection pooling. This is appropriate for a small to medium user base but would need to be replaced for enterprise scale.

### No backtesting engine

Users cannot test strategies against historical data before deploying them. They must rely on simulation mode with live market data.

---

## 10. Future Improvements

### Short-term (low effort, high impact)

| Improvement | Rationale |
|---|---|
| **Async worker pool** | Replace `threading.Thread` with `asyncio` tasks or a `concurrent.futures.ProcessPoolExecutor` to reduce memory overhead and improve I/O throughput. |
| **WebSocket price streaming** | Replace periodic HTTP polling with CoinDCX WebSocket feeds for lower latency and reduced API call volume. |
| **Strategy analytics dashboard** | Add historical PnL charts, win/loss ratio, Sharpe ratio, and drawdown metrics to the UI. |
| **Backtesting engine** | Allow users to replay strategies against historical candlestick data before going live. |

### Medium-term (moderate effort)

| Improvement | Rationale |
|---|---|
| **Redis task queue** | Replace in-process thread queue with Redis + Celery (or similar) for durable, distributed task scheduling with retry semantics. |
| **PostgreSQL** | Replace SQLite with PostgreSQL for proper multi-writer concurrency, connection pooling, and scalability beyond a single node. |
| **OAuth / SSO** | Add Google or GitHub login as an alternative identity provider for teams. |
| **API key rotation** | Add expiry dates and automatic rotation reminders for stored exchange credentials. |

### Long-term (strategic)

| Improvement | Rationale |
|---|---|
| **Multi-exchange support** | Extend `execution.py` to support Binance, Bybit, or other exchanges alongside CoinDCX. |
| **Mobile app (React Native)** | A companion mobile app for monitoring strategies and receiving push notifications on TP/SL hits. |
| **Strategy marketplace** | Let users share or sell strategy configurations, with performance tracking. |
| **ML-based strategy tuning** | Use historical trade data to suggest optimal `dip_percent`, `comparison_window`, and TP/SL parameters. |

---

## 11. Development Phases

The system was built incrementally. Each phase addressed a specific operational need.

### Phase 1 — Core bot and strategy engine

**What**: Built the foundational trading bot with modular architecture (`config`, `market_data`, `strategy`, `execution`, `position_monitor`, `main`). Implemented the Dip/Rise strategy with momentum and reversal modes, market and limit orders, and TP/SL monitoring.

**Why**: Established the core value proposition — automated entry and exit based on configurable rules.

### Phase 2 — Streamlit UI and simulation mode

**What**: Added the Streamlit web dashboard for parameter input, live metrics, and strategy control. Introduced simulation mode with a fake wallet (`SimWallet`) for risk-free testing. Added INR margin support, multi-pair trading (BTC, ETH, SOL), exchange precision enforcement, and notional-value input.

**Why**: Removed the need for users to edit config files or environment variables. Simulation mode lowered the barrier to entry — users could experiment without risking real funds.

### Phase 3 — Multi-strategy execution

**What**: Refactored from a single global config to per-strategy `StrategyConfig` dataclasses. Built `StrategyManager` for concurrent multi-strategy orchestration with a thread-per-strategy model, portfolio margin guardrails, and a structured strategy table in the UI.

**Why**: Users needed to run multiple strategies simultaneously (e.g. BTC momentum + ETH reversal) without them interfering with each other.

### Phase 4 — Cloud deployment

**What**: Added `Procfile`, `runtime.txt`, `.streamlit/config.toml` for Railway deployment. Added ngrok integration for sharing the dashboard via public URLs.

**Why**: Moving from localhost to cloud meant the bot could run 24/7 without requiring the user's laptop to stay open.

### Phase 5 — Session persistence and recovery

**What**: Created `bot/persistence.py` to save strategy state to disk. Initially backed by JSON, later migrated to SQLite (`db/` package). Added `recover_session()` to `StrategyManager` to rebuild and restart strategies after server restarts.

**Why**: Cloud deployments restart unpredictably (Railway cold starts, deploys, crashes). Without persistence, all running strategies would be lost on every restart.

### Phase 6 — Reliability hardening

**What**: Added heartbeat monitoring (detect and restart silently crashed threads), a recovery guard (`_recovering` flag to prevent persistence during rebuild), a 20-thread concurrency cap with FIFO queue, and SQLite pragmas (busy timeout, foreign keys, WAL).

**Why**: As the number of concurrent strategies grew, silent thread crashes and database lock contention became real risks. These mechanisms make the system self-healing.

### Phase 7 — Backend API and security

**What**: Introduced a FastAPI backend server that owns the `StrategyManager` singleton, decoupling the engine from the UI. Added API-key-based identity (SHA-256 hash), Fernet-encrypted API secret storage, and per-user strategy isolation at the API routing layer.

**Why**: Moving to a multi-user model required authentication and data isolation. Decoupling the engine from the UI also enabled future architectures where multiple UI clients (web, mobile) share a single backend.

---

## 12. Operation Guide

### Prerequisites

- Python 3.9+
- Packages listed in `requirements.txt`

```bash
pip install -r requirements.txt
```

### Environment variables

Create a `.env` file in the project root (see `.env.example`):

```env
# CoinDCX credentials (required for live trading)
COINDCX_API_KEY=your_key
COINDCX_API_SECRET=your_secret

# Security (required for multi-user backend)
ENCRYPTION_KEY=<output of: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Optional
BACKEND_URL=http://localhost:8000   # set to enable decoupled mode
TRADING_MODE=simulation             # simulation (default) or live
```

### Running the backend (decoupled mode)

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

The backend initialises the database, recovers any persisted strategies, and starts listening for HTTP requests. The `/health` endpoint returns `{"status": "ok"}`.

### Running the Streamlit UI

**Standalone mode** (engine runs inside the UI process):

```bash
streamlit run ui.py
```

**Decoupled mode** (UI talks to the FastAPI backend):

```bash
BACKEND_URL=http://localhost:8000 streamlit run ui.py
```

### Connecting (loading a session)

```bash
# Connect with your CoinDCX API key
curl -X POST http://localhost:8000/session/load \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "your-coindcx-api-key",
    "secret": "your-coindcx-api-secret",
    "remember_secret": true
  }'

# Response: {"user_id": "a1b2c3...", "strategies": [...]}
```

No registration or login is required. The API key **is** your identity. Entering the same key from any device loads the same strategies.

### Starting a strategy via API

```bash
API_KEY="your-coindcx-api-key"

curl -X POST http://localhost:8000/strategy/start \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "params": {
      "pair": "B-BTC_USDT",
      "direction": "LONG",
      "strategy_mode": "reversal",
      "trading_mode": "simulation",
      "notional": 5000,
      "leverage": 10,
      "dip_percent": 3,
      "comparison_window_minutes": 60,
      "check_frequency_seconds": 30,
      "strategy_expiry_minutes": 1440,
      "take_profit_percent": 3,
      "stop_loss_percent": 2
    }
  }'

# Response: {"strategy_id": "S001", "started": true}
```

### Inspecting strategies and portfolio

```bash
# List your strategies
curl -H "X-API-Key: $API_KEY" http://localhost:8000/strategies

# Portfolio summary
curl -H "X-API-Key: $API_KEY" http://localhost:8000/portfolio

# Recent logs
curl -H "X-API-Key: $API_KEY" http://localhost:8000/logs?limit=50
```

### Railway deployment

1. Push the repository to GitHub.
2. Connect the repo to Railway.
3. Railway detects the `Procfile` and runs: `streamlit run ui.py --server.port=$PORT --server.address=0.0.0.0`.
4. Set environment variables (`COINDCX_API_KEY`, `ENCRYPTION_KEY`, etc.) in the Railway dashboard.
5. The app is accessible via the Railway public URL.

### Headless CLI mode (no UI)

```bash
python run.py
```

Reads configuration from `.env` and `runtime_config.json`, runs a single strategy, and logs to the console. Useful for cron jobs or minimal deployments.
