"""
Central configuration for repository paths and safety limits.

This module defines:
- Repository-relative input/output directories used by the pipeline and UI.
- File and sheet limits to prevent memory issues and oversized spreadsheets.
- LLM-related limits (text size, retry count, chunk size) to avoid context/window failures.
- Default LLM model settings.

All values are constants and should be imported where needed (no runtime logic here).
"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_ROOT = PROJECT_ROOT / "input_offers"
OUTPUT_ROOT = PROJECT_ROOT / "offer_outputs"

FOOD_INPUT_DIR = INPUT_ROOT / "food"
HPC_INPUT_DIR = INPUT_ROOT / "hpc"

MOVE_INPUT_TO_PROCESSED = False

MAX_FILE_SIZE_MB = 50

MAX_SHEET_ROWS = 10_000
MAX_SHEET_COLS = 100
MAX_SHEETS = 20
EXTREME_COLS_LIMIT = 500

MAX_TEXT_CHARS_BEFORE_LLM = 120_000
JSON_RETRY_ATTEMPTS = 3
CHUNK_SIZE = 50

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0
