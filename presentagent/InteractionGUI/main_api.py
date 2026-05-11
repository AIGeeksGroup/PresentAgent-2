"""
PresentAgent FastAPI Server Entry Point.

Run with: uvicorn main_api:app --reload --port 8000
Or: python main_api.py
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.presenter import router as presenter_router


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PresentAgent API",
    description="Interactive Q&A API for PresentAgent presentations",
    version="1.0.0"
)

# ---------------------------------------------------------------------------
# CORS Configuration
# ---------------------------------------------------------------------------

# Allow all origins for development
# In production, restrict to specific frontend origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Register Routers
# ---------------------------------------------------------------------------

app.include_router(presenter_router, prefix="/api/presenter", tags=["Presenter"])

# ---------------------------------------------------------------------------
# Root Endpoint
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "PresentAgent API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/presenter/health"
    }


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main_api:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
