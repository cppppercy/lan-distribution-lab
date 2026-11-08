"""HTTP application for the current project baseline."""

from fastapi import FastAPI

app = FastAPI(title="LAN Distribution Lab", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Return a public, stable liveness response without operational secrets."""
    return {"status": "ok", "service": "lan-distribution-lab"}
