"""FastAPI backend server for the CoinDCX Strategy Terminal.

Owns the single ``StrategyManager`` instance and exposes HTTP endpoints
so that the Streamlit UI (or any other client) can drive strategy
lifecycle, query portfolio state, and stream logs — without embedding
the engine in the UI process.

Start with::

    uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI

from db.models import initialize_database
from bot.strategy_manager import StrategyManager
from backend.api.strategy_routes import create_router
from backend.api.key_routes import router as key_router

app = FastAPI(title="CoinDCX Strategy Terminal — Backend")

initialize_database()

manager = StrategyManager()
manager.recover_session()

app.include_router(key_router)
app.include_router(create_router(manager))


@app.get("/health")
def health():
    return {"status": "ok"}
