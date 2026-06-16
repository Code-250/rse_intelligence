"""
Financial Document Analyzer — FastAPI entry point.

Run locally:
    uvicorn main:app --reload --port 8001

Environment:
    Copy .env.example to .env and fill in values before running.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Financial Document Analyzer API",
    description="Upload financial PDFs — get AI-powered analysis, ratios, and plain-English insight.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten in production via env var
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", summary="Health check", tags=["infra"])
async def health():
    """Returns 200 if the service is running. Used by smoke tests and uptime monitoring."""
    return {"status": "ok", "service": "financial-doc-analyzer"}


# Routers registered here as they are built (Backend/AI Agent adds these)
# from routers import auth, documents
# app.include_router(auth.router,      prefix="/api/v1/auth",      tags=["auth"])
# app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
