"""Project configuration, paths, and processing limits."""

from __future__ import annotations

from pathlib import Path

# ============================================================================
# PROJECT PATHS
# ============================================================================

# Project root: .../offer_creation
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Input / Output directories
INPUT_ROOT = PROJECT_ROOT / "input_offers"
OUTPUT_ROOT = PROJECT_ROOT / "offer_outputs"

FOOD_INPUT_DIR = INPUT_ROOT / "food"
HPC_INPUT_DIR = INPUT_ROOT / "hpc"

# DEV: Keep input files in place (don't move to processed)
MOVE_INPUT_TO_PROCESSED = False

# ============================================================================
# FILE UPLOAD LIMITS
# ============================================================================

# Maximum file size in MB (quick filter before processing)
MAX_FILE_SIZE_MB = 50

# Excel content limits (per sheet)
MAX_SHEET_ROWS = 100_000  # 100k rows per sheet
MAX_SHEET_COLS = 300      # 300 columns per sheet
MAX_SHEETS = 20           # Maximum number of sheets to display in UI

# Hard block extremely wide sheets (these will crash openpyxl/pandas)
EXTREME_COLS_LIMIT = 1000  # Block sheets with >1000 columns

# ============================================================================
# LLM PROCESSING LIMITS
# ============================================================================

# Maximum text characters before sending to LLM
# Roughly 120k chars ≈ 30k tokens (conservative estimate)
MAX_TEXT_CHARS_BEFORE_LLM = 120_000

# JSON retry/repair attempts when LLM returns invalid JSON
JSON_RETRY_ATTEMPTS = 3

# Chunk size for large files (rows per chunk)
CHUNK_SIZE = 50

# ============================================================================
# LLM SETTINGS
# ============================================================================

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0

# ============================================================================
# USAGE NOTES
# ============================================================================

# These limits are designed to:
# 1. Prevent out-of-memory crashes on Streamlit Cloud (RAM limits)
# 2. Prevent LLM token limit errors (context window)
# 3. Provide clear user feedback before processing starts
# 4. Allow processing of most real-world supplier offers

# Typical file sizes that work:
# ✅ 100-500 rows, 10-50 columns → processes in seconds
# ✅ 1,000-5,000 rows, 20-100 columns → processes in 1-2 minutes
# ⚠️ 10,000+ rows → may need chunking
# 🚫 100,000+ rows or 1,000+ columns → too large, user must filter/split

# To adjust limits:
# - Increase MAX_SHEET_ROWS if you have more RAM (e.g., self-hosted)
# - Decrease CHUNK_SIZE if hitting token limits
# - Increase JSON_RETRY_ATTEMPTS if dealing with unreliable LLM output