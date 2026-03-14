"""WSGI entrypoint for production runtimes."""

from app import create_app


app = create_app()
