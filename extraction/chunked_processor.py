"""
Chunked Excel extraction via LLM.

This module processes large Excel-derived row data by splitting it into smaller chunks,
sending each chunk to the LLM for structured extraction, and merging the results.

Key features:
- Sanitizes Excel/pandas-native types into JSON-safe values.
- Enforces a maximum serialized text size before any LLM call.
- Robustly extracts/parses JSON from model output (handles markdown/code fences).
- Retries JSON parsing and can ask the model to repair invalid JSON.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .llm_client import get_client
from .prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_prompt

sys.path.append(str(Path(__file__).parent.parent))
from config import CHUNK_SIZE, MAX_TEXT_CHARS_BEFORE_LLM, JSON_RETRY_ATTEMPTS  # noqa: E402


def _sanitize_for_json(obj: Any) -> Any:
    """Convert non-JSON-serializable values (datetime, pandas NA/NaT) into JSON-safe types."""
    from datetime import date, datetime

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(item) for item in obj]
    if pd.isna(obj):
        return None
    return obj


def _extract_json_from_text(text: str) -> str:
    """Extract the first JSON object from a response that may include markdown or extra text."""
    text = (text or "").strip()

    if "```" in text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if match:
            return match.group(1)
        text = re.sub(r"```(?:json)?", "", text).strip()

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    return match.group(0) if match else text


def _parse_llm_response(raw_response: str, retry_count: int = 0) -> Dict[str, Any]:
    """Parse model output into JSON with minimal repair attempts."""
    if not raw_response or not raw_response.strip():
        raise ValueError("LLM returned empty response")

    json_text = _extract_json_from_text(raw_response)

    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        error_details = f"Position {e.pos}: {e.msg}"

        json_text_fixed = re.sub(r",\s*([}\]])", r"\1", json_text)
        try:
            return json.loads(json_text_fixed)
        except json.JSONDecodeError:
            pass

        try:
            json_text_fixed = re.sub(
                r'(":\s*")([^"]*)"([^,}\]]*)"', r"\1\2\\\"\3\\\"", json_text
            )
            return json.loads(json_text_fixed)
        except json.JSONDecodeError:
            pass

        raise ValueError(
            f"Failed to parse LLM JSON response (attempt {retry_count + 1}).\n"
            f"Error: {error_details}\n"
            f"First 500 chars: {raw_response[:500]}"
        )


def _call_llm_extraction_for_chunk(
    chunk_data: str,
    model: str = "gpt-4o-mini",
    extract_price: bool = False,
    attempt: int = 1,
) -> List[Dict[str, Any]]:
    """Extract structured products for a single chunk and return a list of product dicts."""
    client = get_client()
    user_prompt = build_extraction_prompt(chunk_data, "excel", extract_price)

    if attempt > 1:
        user_prompt += (
            "\n\nYour previous response had invalid JSON. Output ONLY valid JSON.\n"
            "- No markdown or extra text\n"
            "- No trailing commas\n"
            "- Properly escaped strings\n"
        )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
    except Exception:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )

    raw_output = response.choices[0].message.content
    if not raw_output:
        raise ValueError("LLM returned empty response")

    for retry in range(JSON_RETRY_ATTEMPTS):
        try:
            parsed = _parse_llm_response(raw_output, retry_count=retry)
            if isinstance(parsed, dict) and "products" in parsed:
                return parsed["products"]
            return [parsed] if isinstance(parsed, dict) else parsed
        except ValueError as e:
            if retry >= JSON_RETRY_ATTEMPTS - 1:
                raise ValueError(
                    f"Failed to parse LLM response after {JSON_RETRY_ATTEMPTS} attempts.\n"
                    f"Last error: {str(e)}\n"
                    f"Try a smaller file or reduce rows/columns."
                ) from e

            fix_prompt = (
                "The following JSON is invalid:\n\n"
                f"{raw_output}\n\n"
                f"Error: {str(e)}\n\n"
                "Output ONLY the corrected valid JSON with no additional text."
            )

            fix_response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a JSON validator. Fix invalid JSON."},
                    {"role": "user", "content": fix_prompt},
                ],
                temperature=0,
            )
            raw_output = fix_response.choices[0].message.content

    raise ValueError("Unexpected error in JSON parsing retry loop")


def process_excel_in_chunks(
    rows: List[Dict],
    model: str = "gpt-4o-mini",
    extract_price: bool = False,
    chunk_size: int = CHUNK_SIZE,
) -> List[Dict[str, Any]]:
    """Process Excel rows through the LLM in chunks and return merged product results."""
    total_rows = len(rows)
    if total_rows == 0:
        return []

    sanitized_rows = _sanitize_for_json(rows)

    full_json = json.dumps(sanitized_rows, indent=2, ensure_ascii=False)
    total_chars = len(full_json)
    if total_chars > MAX_TEXT_CHARS_BEFORE_LLM:
        raise ValueError(
            f"File content ({total_chars:,} characters) exceeds limit ({MAX_TEXT_CHARS_BEFORE_LLM:,}). "
            "Reduce file size by filtering rows/columns or splitting the file."
        )

    if total_rows <= chunk_size:
        return _call_llm_extraction_for_chunk(full_json, model, extract_price)

    all_products: List[Dict[str, Any]] = []
    num_chunks = (total_rows + chunk_size - 1) // chunk_size

    for i in range(num_chunks):
        start_idx = i * chunk_size
        end_idx = min(start_idx + chunk_size, total_rows)

        chunk = sanitized_rows[start_idx:end_idx]
        chunk_data = json.dumps(chunk, indent=2, ensure_ascii=False)

        chunk_products = _call_llm_extraction_for_chunk(chunk_data, model, extract_price)
        all_products.extend(chunk_products)

    return all_products
