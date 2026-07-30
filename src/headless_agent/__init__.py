"""Headless Cortex Code Agent service.

A thin, production-shaped wrapper around the Cortex Code Agent SDK that runs the
agent loop headlessly (no interactive TUI) behind a FastAPI + SSE interface,
suitable for local development and deployment to Snowpark Container Services.
"""

__version__ = "0.1.0"
