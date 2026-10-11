"""ASGI entrypoint for local and conventional Python servers."""

from app.factory import create_app

app = create_app()
