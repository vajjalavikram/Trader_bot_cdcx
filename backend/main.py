"""FastAPI backend for the CoinDCX Strategy Terminal.

Owns the single ``StrategyManager`` instance.  The Streamlit UI talks to
this server exclusively via ``BackendClient`` HTTP calls.

Start locally::

    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

Railway::

    uvicorn backend.main:app --host 0.0.0.0 --port $PORT
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db.models import initialize_database
from bot.strategy_manager import StrategyManager
from backend.api.strategy_routes import create_router
from backend.api.key_routes import router as key_router

logger = logging.getLogger(__name__)

# ── Database + StrategyManager (single instance) ─────────────────────────
initialize_database()

manager = StrategyManager()
manager.recover_session()

# ── FastAPI application ──────────────────────────────────────────────────
app = FastAPI(title="CoinDCX Strategy Terminal — Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(key_router, prefix="/api")
app.include_router(create_router(manager), prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ── Entrypoint ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
