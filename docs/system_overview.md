# CoinDCX Strategy Terminal — System Overview

## 1. System Purpose

The CoinDCX Strategy Terminal is a multi-user automated futures trading platform for the CoinDCX exchange. It enables users to define dip/rise trading strategies that continuously monitor cryptocurrency prices and execute trades when configurable conditions are met.

Core goals:

- **Automated entry detection** — scan for price dips or rises within a time window and trigger trades without manual intervention.
- **Multi-strategy execution** — run up to 20 concurrent strategies in parallel, each in its own thread with isolated configuration.
- **Dual mode** — support both simulation (paper trading) and live trading against CoinDCX Futures.
- **Persistence and recovery** — survive backend restarts by persisting strategy state to the database and automatically restarting active strategies on boot.
- **Multi-user isolation** — multiple users share a single backend, with each user only able to see and control their own strategies.

---

## 2. High-Level Architecture

```
┌─────────────────────┐          HTTP/JSON           ┌──────────────────────────┐
│                     │  ───────────────────────────→ │                          │
│   Streamlit UI      │  BackendClient (/api/*)       │   FastAPI Backend         │
│   (port 8501)       │  ←─────────────────────────── │   (port 8000)            │
│                     │          X-API-Key header      │                          │
└─────────────────────┘                                │  ┌──────────────────┐   │
                                                       │  │ StrategyManager  │   │
                                                       │  │  ├ Thread pool   │   │
                                                       │  │  ├ Heartbeat mon │   │
                                                       │  │  └ Queue proc    │   │
                                                       │  └──────────────────┘   │
                                                       │         │               │
                                                       └─────────┼───────────────┘
                                                                 │
                                              ┌──────────────────┼──────────────────┐
                                              │                  │                  │
                                              ▼                  ▼                  ▼
                                     ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
                                     │  PostgreSQL   │   │  CoinDCX API │   │  CoinDCX     │
                                     │  (Railway)    │   │  (REST)      │   │  Public API  │
                                     │  or SQLite    │   │              │   │  (market     │
                                     │  (local)      │   │              │   │   data)      │
                                     └──────────────┘   └──────────────┘   └──────────────┘
```

### Components

| Component | Technology | Role |
|-----------|-----------|------|
| **UI** | Streamlit | Interactive web dashboard for strategy configuration, monitoring, market data |
| **Backend API** | FastAPI + Uvicorn | Stateless HTTP API that owns the `StrategyManager` singleton |
| **Strategy Engine** | Python `threading` | Runs each strategy as an independent daemon thread |
| **Database** | PostgreSQL (prod) / SQLite (dev) | Persists users, strategies, API keys, trades |
| **Exchange** | CoinDCX REST API | Market data, order placement, position management |
| **HTTP Client** | `BackendClient` (`requests`) | Drop-in replacement for `StrategyManager` used by the UI |

---

## 3. Service Deployment Architecture

The system deploys as **two independent Railway services** plus a managed database:

```
Railway
  ├── Backend Service
  │     Start: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
  │     Env:   DATABASE_URL, COINDCX_API_KEY, COINDCX_API_SECRET, ENCRYPTION_KEY
  │
  ├── UI Service
  │     Start: streamlit run ui.py --server.port $PORT --server.address 0.0.0.0
  │     Env:   BACKEND_URL=https://<backend-service>.up.railway.app
  │
  └── PostgreSQL Service (managed by Railway)
        Provided via DATABASE_URL environment variable
```

### Why separate services

- **Independent scaling** — the backend runs 24/7 with active strategy threads; the UI can be restarted or scaled without interrupting running strategies.
- **No shared state** — the UI communicates exclusively via HTTP. If the Streamlit process crashes, no strategies are lost.
- **Clean separation** — the backend owns all trading logic and database access. The UI is a pure presentation layer.

### Local development

```bash
# Terminal 1 — backend
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — UI
streamlit run ui.py
```

The UI defaults `BACKEND_URL` to `http://localhost:8000` when the environment variable is not set.

---

## 4. User Identity Model

The system uses **CoinDCX API key hashing** instead of traditional email/password authentication.

### How it works

1. User enters their CoinDCX API key in the UI.
2. The UI calls `POST /api/session/load` with the raw API key.
3. The backend computes `user_id = SHA-256(api_key)` — a deterministic, non-reversible 64-character hex string.
4. A `users` row is created (or found) for that `user_id`.
5. All subsequent API calls include the raw API key in the `X-API-Key` HTTP header.
6. The backend hashes it on every request to derive the `user_id`.

### Implementation

- `backend/security/identity.py` — `get_user_id_from_api_key()` computes SHA-256; `get_caller_id()` is a FastAPI `Depends` that reads the header.
- The raw API key is never stored in the database. Only the hash is used as `user_id`.

### Rationale

- No registration flow, no password management.
- The API key is already a secret the user possesses.
- The hash is deterministic — the same key always produces the same `user_id`, enabling session recovery.

---

## 5. Security Model

### API key handling

- The CoinDCX **API key** (public identifier) is stored in the `api_keys` table when the user opts into "Remember API Secret."
- The CoinDCX **API secret** is encrypted with Fernet symmetric encryption before storage (`backend/security/crypto.py`).
- The encryption key is read from the `ENCRYPTION_KEY` environment variable — it is never committed to source control.
- API secrets are **never** written to the strategies table. On recovery, credentials are read from environment variables.

### Request authentication

Every backend endpoint (except `/api/health` and `POST /api/session/load`) requires the `X-API-Key` header. The FastAPI dependency `get_caller_id` extracts and hashes it. Missing or empty keys return HTTP 401.

### User isolation

The API routing layer (`strategy_routes.py`) maintains an in-memory **ownership registry** (`strategy_id → user_id`). Every query and mutation checks ownership:

- `GET /api/strategies` — returns only strategies owned by the caller.
- `POST /api/strategy/stop` — returns HTTP 403 if the strategy belongs to another user.
- `GET /api/logs` — filters log entries to only show lines tagged with the caller's strategy IDs.

### CORS

The backend allows all origins (`allow_origins=["*"]`) via FastAPI's `CORSMiddleware`. This enables the Streamlit UI (running on a different port or domain) to make API calls.

---

## 6. Strategy Lifecycle

```
User configures params in UI
        │
        ▼
POST /api/strategy/start
        │
        ▼
StrategyManager.register_strategy()
  ├─ Generates ID (S001, S002, ...)
  ├─ Stores in memory dict
  ├─ Persists to database via save_session_state()
  └─ Calls start_strategy()
        │
        ▼
Thread launched (_run_strategy)
  ├─ Builds StrategyConfig from params
  ├─ Creates SimWallet (if simulation)
  └─ Calls bot.main.run()
        │
        ▼
Entry scanning loop
  ├─ Fetches current price + price from X minutes ago
  ├─ Evaluates strategy (momentum or reversal)
  ├─ Sleeps check_frequency_seconds between checks
  └─ Exits on: entry triggered, expiry reached, or stop signal
        │
        ▼ (entry triggered)
Order execution
  ├─ Simulation: SimWallet.open_position()
  └─ Live: place_order() via CoinDCX REST API
        │
        ▼
Position monitoring
  ├─ Polls price at check_frequency_seconds
  ├─ Computes unrealized PnL
  ├─ Checks TP/SL conditions
  └─ Live: creates server-side TP/SL orders on CoinDCX
        │
        ▼
Strategy ends
  ├─ TP hit, SL hit, expired, stopped by user, or error
  ├─ Trade event logged to trades table
  └─ Final state persisted to database
```

### Key details

- **Thread per strategy**: each strategy gets its own `threading.Thread` with an independent `StrategyConfig` instance. No shared mutable state between strategies.
- **Stop event**: each strategy has a `threading.Event`. Setting it causes the bot to exit gracefully at the next sleep boundary.
- **Status callback**: the bot pushes live telemetry (price, PnL, phase) to `StrategyManager.update_strategy_state()`, which the UI polls via API.
- **Concurrency limit**: max 20 concurrent threads. Excess strategies are queued and launched as capacity frees.

---

## 7. Persistence Model

### Database schema (5 tables)

| Table | Purpose |
|-------|---------|
| `users` | User identity; `user_id` (SHA-256 hash) is the primary key |
| `api_keys` | Encrypted exchange credentials; FK to `users` with `ON DELETE CASCADE` |
| `strategies` | Full strategy state snapshot (29 columns); FK to `users` |
| `positions` | Position records; FK to `strategies` with `ON DELETE CASCADE` |
| `trades` | ENTRY/EXIT audit trail; FK to `strategies` with `ON DELETE CASCADE` |

### How persistence works

- **`StrategyManager._persist()`** is called after every state change (register, start, stop, phase update, remove).
- It exports the full in-memory strategy dict, strips secrets and non-serializable fields (`thread`, `stop_event`), and calls `save_session_state()`.
- `save_session_state()` performs an atomic upsert of every strategy using `INSERT ... ON CONFLICT (strategy_id) DO UPDATE SET ...` — compatible with both SQLite and PostgreSQL.
- Strategies deleted from the manager are also deleted from the database in the same transaction.

### Database abstraction (`db/database.py`)

- `DATABASE_URL` is read from the environment, defaulting to `sqlite:///trading_bot.db`.
- A SQLAlchemy `create_engine()` is created for URL parsing and future ORM use.
- The actual connections use raw DBAPI drivers for performance:
  - **SQLite**: direct `sqlite3.connect()` with WAL mode, singleton across threads.
  - **PostgreSQL**: `psycopg2.connect()` wrapped in `_PgConnection` which auto-converts `?` placeholders to `%s` and uses `RealDictCursor` for dict-like row access. Thread-local connections.
- All SQL in the codebase uses `?` placeholders. The wrapper transparently handles the conversion.

### Why PostgreSQL over SQLite

- SQLite works for local development and single-server deployments.
- Railway's ephemeral filesystem means SQLite data is lost on every deploy.
- PostgreSQL provides durable storage independent of the application container.
- The codebase supports both via `DATABASE_URL` — no code changes needed to switch.

---

## 8. Strategy Recovery

When `backend/main.py` starts, it calls `manager.recover_session()` which:

1. Calls `load_session_state()` to read all rows from the `strategies` table.
2. Rebuilds each strategy's in-memory dict (params, status, prices, PnL, timestamps).
3. Re-injects API credentials from environment variables (`COINDCX_API_KEY`, `COINDCX_API_SECRET`) since secrets are never persisted.
4. For strategies that were in an active status (`starting`, `queued`, `scanning`, `order_placed`, `position_open`), calls `start_strategy()` to relaunch their threads.
5. Suppresses persistence writes during recovery to avoid flushing the half-rebuilt registry.

### Heartbeat monitor

A background daemon thread sweeps every 10 seconds looking for strategies that:
- Have an active status but a dead thread (crashed silently).
- Have a stale heartbeat (>30 seconds since last update).

These are automatically restarted via `_restart_stale_strategy()`.

### Limitations

- Recovery re-enters the scanning loop from scratch. If a strategy had an open position on CoinDCX, the recovery does not reconnect to that position — it starts a new scanning cycle.
- Simulation wallet state is lost on restart (it exists only in memory).

---

## 9. Multi-User Design

### Isolation layers

1. **API layer** (`strategy_routes.py`): the ownership registry maps `strategy_id → user_id`. Every endpoint filters results to the caller's strategies. Mutations on others' strategies return HTTP 403.
2. **Database layer**: each strategy row has a `user_id` foreign key. Ownership is written to the DB when a strategy is created (`_set_owner`) and loaded into the in-memory registry on startup (`_load_ownership`).
3. **Log filtering**: the `/api/logs` endpoint filters the shared log deque by strategy ID, only returning entries tagged with IDs the caller owns.

### Shared resources

- All users share a single `StrategyManager` instance and its thread pool.
- Portfolio margin limits are global (not per-user) — `max_portfolio_margin` applies across all users.

---

## 10. API Structure

All routes are prefixed with `/api`. Authentication is via the `X-API-Key` header.

### Session

| Method | Path | Auth | Purpose |
|--------|------|:----:|---------|
| `POST` | `/api/session/load` | No | Establish identity, optionally store encrypted secret, return owned strategies |
| `GET` | `/api/health` | No | Backend health check |

### Strategy lifecycle

| Method | Path | Auth | Purpose |
|--------|------|:----:|---------|
| `POST` | `/api/strategy/start` | Yes | Register + start a strategy |
| `POST` | `/api/strategy/stop` | Yes | Stop a running strategy |
| `DELETE` | `/api/strategy/{id}` | Yes | Remove a finished strategy |
| `POST` | `/api/strategy/margin-check` | Yes | Check if margin is available |

### Queries

| Method | Path | Auth | Purpose |
|--------|------|:----:|---------|
| `GET` | `/api/strategies` | Yes | List caller's strategies |
| `GET` | `/api/strategies/active/ids` | Yes | Active strategy IDs |
| `GET` | `/api/portfolio` | Yes | Aggregated PnL, margin, win/loss |
| `GET` | `/api/logs` | Yes | Filtered log entries |

### Settings

| Method | Path | Auth | Purpose |
|--------|------|:----:|---------|
| `GET` | `/api/settings` | Yes | Read max portfolio margin |
| `PUT` | `/api/settings/max-margin` | Yes | Update max portfolio margin |

### API keys

| Method | Path | Auth | Purpose |
|--------|------|:----:|---------|
| `POST` | `/api/keys/add` | Yes | Store encrypted exchange key |
| `GET` | `/api/keys` | Yes | List keys (secret excluded) |
| `DELETE` | `/api/keys/{id}` | Yes | Delete a key |

---

## 11. Data Flow

### End-to-end: user starts a strategy

```
1. User fills parameters in Streamlit sidebar + right panel.
2. User clicks "Start Strategy".
3. UI calls BackendClient.register_strategy(params).
4. BackendClient sends POST /api/strategy/start with X-API-Key header.
5. Backend hashes API key → user_id.
6. Backend calls StrategyManager.register_strategy(params) → returns strategy ID.
7. Backend records ownership (strategy_id → user_id) in memory + DB.
8. Backend calls StrategyManager.start_strategy(id).
9. StrategyManager launches a daemon thread running bot.main.run().
10. Thread begins scanning CoinDCX market data at configured frequency.
11. StrategyManager._persist() writes state to strategies table.
```

### End-to-end: UI polls for updates

```
1. Streamlit @st.fragment(run_every=10s) timer fires.
2. BackendClient sends GET /api/strategies with X-API-Key.
3. Backend filters StrategyManager.get_all_strategies() by ownership.
4. UI renders strategy table with status, PnL, prices.
5. Separately, GET /api/portfolio returns aggregated metrics.
6. Separately, GET /api/logs returns filtered log entries.
```

### End-to-end: strategy executes a trade

```
1. Bot thread detects price dip/rise condition.
2. Simulation: SimWallet.open_position() records virtual trade.
   Live: place_order() sends signed request to CoinDCX REST API.
3. status_callback pushes "Positioned" phase to StrategyManager.
4. StrategyManager writes ENTRY trade to trades table.
5. Bot enters position monitoring loop.
6. On TP/SL hit: position is closed, EXIT trade logged, state persisted.
```

---

## 12. Design Decisions

### API key identity vs. login system

**Decision**: Use SHA-256 hash of CoinDCX API key as `user_id`.

**Reasoning**: Eliminates registration flow, password storage, and session tokens. The API key is already a secret the user must have to trade. The hash is deterministic, enabling stateless authentication on every request.

### Streamlit for UI

**Decision**: Use Streamlit instead of React/Vue.

**Reasoning**: Rapid prototyping, built-in auto-refresh fragments, server-side rendering (no build step), native support for DataFrames and Plotly charts. Tradeoff is limited control over UI behavior and client-side state.

### Thread-based strategy execution

**Decision**: One `threading.Thread` per strategy, max 20 concurrent.

**Reasoning**: Each strategy has its own polling loop with independent timing. Threads provide true concurrency for I/O-bound operations (HTTP calls to CoinDCX). The GIL is not a bottleneck since the work is network-bound. Excess strategies are queued and auto-launched when capacity frees.

### FastAPI backend

**Decision**: FastAPI with sync endpoint handlers.

**Reasoning**: FastAPI provides automatic OpenAPI docs, Pydantic validation, and dependency injection (used for `get_caller_id`). Sync handlers run in uvicorn's thread pool, which is adequate for the current scale. Async handlers are not needed since the bottleneck is the CoinDCX API, not the backend.

### Raw SQL over ORM

**Decision**: Use raw SQL with a thin compatibility wrapper instead of SQLAlchemy ORM.

**Reasoning**: The schema is simple (5 tables), the queries are straightforward, and the wrapper allows the same SQL to run on both SQLite and PostgreSQL with zero caller changes. The SQLAlchemy engine is still created for URL parsing and is available for future ORM migration.

### Two-service Railway deployment

**Decision**: Separate backend and UI into independent Railway services.

**Reasoning**: The backend must run continuously (strategies execute in threads). The UI is stateless and can be restarted freely. Separating them means a UI deploy doesn't kill running strategies.

---

## 13. Tradeoffs

| Decision | Benefit | Cost |
|----------|---------|------|
| API key identity | Zero-friction auth, no passwords | Users without CoinDCX API keys cannot use the platform |
| Thread per strategy | Simple isolation, no async complexity | Max ~20 concurrent strategies per process; no horizontal scaling |
| Raw SQL | Full control, dual-DB compatibility | No migration framework, manual schema evolution via `_add_column` |
| In-memory strategy state | Fast reads for UI polling | State is duplicated between memory and DB; potential consistency drift |
| Global portfolio margin limit | Simple risk control | Not per-user; one user's strategies consume shared margin budget |
| Polling-based UI | Works with Streamlit's architecture | 10-second latency on updates; higher backend load than WebSocket push |
| Singleton `StrategyManager` | Single source of truth | Cannot horizontally scale the backend (all strategies must live in one process) |

---

## 14. Limitations

1. **Single-process backend** — all strategies run in one Python process. If the process crashes, all strategies stop until recovery runs on restart.
2. **No horizontal scaling** — the `StrategyManager` is a singleton with in-memory state. Running multiple backend instances would create duplicate managers.
3. **Recovery does not reconnect positions** — after a restart, strategies re-enter the scanning loop. Open positions on CoinDCX are not automatically reconnected; they remain open on the exchange until manually managed.
4. **Simulation state is volatile** — `SimWallet` exists only in memory. Simulated positions and balances are lost on restart.
5. **Global margin limit** — `max_portfolio_margin` applies across all users, not per-user.
6. **No WebSocket push** — the UI polls the backend every 10 seconds. Real-time updates require manual refresh or waiting for the polling interval.
7. **No migration framework** — schema changes require manual `ALTER TABLE` statements wrapped in try/except (`_add_column` pattern). No version tracking.
8. **Log filtering is tag-based** — logs are filtered by scanning for `[strategy_id]` substrings. Untagged log lines are invisible to all users.
9. **Thread ceiling** — the 20-thread limit is hardcoded. Beyond this, strategies queue but there is no backpressure to the UI.

---

## 15. Future Improvements

1. **Per-user margin limits** — store `max_portfolio_margin` per user in the database instead of a global setting.
2. **Position reconnection on recovery** — query CoinDCX for open positions at startup and attach them to the correct strategies instead of restarting from scratch.
3. **WebSocket push** — replace UI polling with server-sent events or WebSocket connections for real-time updates.
4. **Celery/Redis task queue** — replace threads with distributed task workers to enable horizontal scaling across multiple backend instances.
5. **Database migrations** — adopt Alembic for versioned schema migrations instead of the `_add_column` try/except pattern.
6. **SQLAlchemy ORM** — migrate from raw SQL to ORM models. The engine is already created; models can be layered on incrementally.
7. **Comprehensive trade history** — expose the `trades` table through the API so users can view their complete ENTRY/EXIT audit trail in the UI.
8. **Rate limiting** — add per-user rate limits on API endpoints to prevent abuse in a shared deployment.
9. **Monitoring and alerting** — add structured logging, health metrics, and alerts for stale strategies or database connectivity issues.
10. **Multi-exchange support** — the `api_keys` table already has an `exchange` column. The bot modules could be extended to support exchanges beyond CoinDCX.
