from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from backend.routers import chat, admin, webhooks
from backend.tracing.setup import init_tracing

init_tracing()

app = FastAPI(
    title="AI Customer Support Agent",
    description="Multi-agent AI customer support system for e-commerce.",
    version="0.1.0",
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(webhooks.router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "ai-support-agent"}


# Serve React frontend static files (built in Phase 4)
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA for all non-API routes."""
        index_path = os.path.join(frontend_dist, "index.html")
        return FileResponse(index_path)
