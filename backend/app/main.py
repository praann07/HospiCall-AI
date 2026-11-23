"""ASGI entry point: `uvicorn app.main:app` (run from the backend/ directory)."""
from .api import create_app

app = create_app()
