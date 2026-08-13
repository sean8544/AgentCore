"""Run AgentCore API server."""

from __future__ import annotations

import uvicorn


def run() -> None:
    """Start the FastAPI development server."""

    uvicorn.run("agentcore.api:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
