"""
OpenAI client factory.

This module initializes environment variables (via dotenv) and exposes a single
shared OpenAI client instance for the application. The client reads credentials
(e.g., OPENAI_API_KEY) from the environment.
"""

from __future__ import annotations

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """Return a singleton OpenAI client instance."""
    global _client
    if _client is None:
        _client = OpenAI()
    return _client
