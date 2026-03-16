"""Unified server — single public URL for the Streamlit UI and FastAPI backend.

Routing:
    /           → Streamlit UI  (reverse-proxied from an internal port)
    /api/...    → FastAPI backend endpoints

Start:
    python server.py

The Streamlit process is launched automatically as a subprocess.  Its
``BACKEND_URL`` environment variable is set to ``http://localhost:{PORT}/api``
so ``BackendClient`` calls resolve to the same gateway without any manual
configuration.
"""

import asyncio
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import Optional

import httpx
import uvicorn
import websockets as _ws_lib
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.requests import Request
from starlette.responses import Response

from db.models import initialize_database
from bot.strategy_manager import StrategyManager
from backend.api.strategy_routes import create_router
from backend.api.key_routes import router as key_router

logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8000))
_SL_PORT = int(os.environ.get("STREAMLIT_PORT", 8501))
_SL_BASE = f"http://localhost:{_SL_PORT}"

# ── Core services (shared by all /api routes) ────────────────────────────
initialize_database()
_manager = StrategyManager()
_manager.recover_session()

# ── Subprocess / client handles ──────────────────────────────────────────
_sl_proc: Optional[subprocess.Popen] = None
_http: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _sl_proc, _http

    _http = httpx.AsyncClient(base_url=_SL_BASE, timeout=30.0)

    env = os.environ.copy()
    env["BACKEND_URL"] = f"http://localhost:{PORT}/api"

    _sl_proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "ui.py",
            f"--server.port={_SL_PORT}",
            "--server.address=127.0.0.1",
            "--server.headless=true",
            "--server.enableCORS=false",
            "--server.enableXsrfProtection=false",
        ],
        env=env,
    )

    for _ in range(60):
        try:
            r = await _http.get("/_stcore/health")
            if r.status_code == 200:
                logger.info("Streamlit ready on internal port %d", _SL_PORT)
                break
        except httpx.ConnectError:
            pass
        await asyncio.sleep(0.5)
    else:
        logger.warning("Streamlit did not become healthy within 30 s")

    yield

    if _http:
        await _http.aclose()
    if _sl_proc:
        _sl_proc.terminate()
        try:
            _sl_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _sl_proc.kill()


# ── Gateway application ──────────────────────────────────────────────────
app = FastAPI(title="CoinDCX Strategy Terminal", lifespan=_lifespan)

app.include_router(key_router, prefix="/api")
app.include_router(create_router(_manager), prefix="/api")


@app.get("/api/health")
def api_health():
    return {"status": "ok"}


# ── WebSocket reverse proxy (Streamlit /_stcore/stream) ──────────────────
@app.websocket("/{path:path}")
async def _ws_proxy(ws: WebSocket, path: str):
    await ws.accept()

    qs = ws.scope.get("query_string", b"").decode()
    target = f"ws://localhost:{_SL_PORT}/{path}"
    if qs:
        target += f"?{qs}"

    extra_headers = {}
    cookie = ws.headers.get("cookie")
    if cookie:
        extra_headers["Cookie"] = cookie

    try:
        async with _ws_lib.connect(
            target, additional_headers=extra_headers or None,
        ) as upstream:

            async def _client_to_upstream():
                try:
                    while True:
                        msg = await ws.receive()
                        if msg.get("text") is not None:
                            await upstream.send(msg["text"])
                        elif msg.get("bytes") is not None:
                            await upstream.send(msg["bytes"])
                except (WebSocketDisconnect, Exception):
                    pass

            async def _upstream_to_client():
                try:
                    async for msg in upstream:
                        if isinstance(msg, str):
                            await ws.send_text(msg)
                        else:
                            await ws.send_bytes(msg)
                except Exception:
                    pass

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(_client_to_upstream()),
                    asyncio.create_task(_upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
    except Exception as exc:
        logger.debug("WebSocket proxy error: %s", exc)


# ── HTTP reverse proxy (catch-all → Streamlit) ──────────────────────────
@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def _http_proxy(request: Request, path: str = ""):
    url = f"/{path}"
    if request.url.query:
        url += f"?{request.url.query}"

    fwd_headers = dict(request.headers)
    fwd_headers.pop("host", None)

    resp = await _http.request(
        method=request.method,
        url=url,
        headers=fwd_headers,
        content=await request.body(),
    )

    skip = {"content-encoding", "transfer-encoding", "content-length"}
    hdrs = {k: v for k, v in resp.headers.items() if k.lower() not in skip}
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=hdrs,
    )


# ── Entrypoint ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, log_level="info")
