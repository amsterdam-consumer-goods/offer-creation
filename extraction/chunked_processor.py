"""Chunked processing for large Excel files.

Splits large Excel files into smaller chunks to avoid LLM token limits
and JSON parsing errors. Each chunk is processed independently and results
are merged.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from .llm_client import get_client
from .prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_prompt

# Import config
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import CHUNK_SIZE, MAX_TEXT_CHARS_BEFORE_LLM, JSON_RETRY_ATTEMPTS


def _sanitize_for_json(obj: Any) -> Any:
    """Convert non-JSON-serializable objects to JSON-safe types.
    
    Handles datetime, date, and other problematic types from Excel.
    """
    from datetime import datetime, date
    
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_json(item) for item in obj]
    elif pd.isna(obj):  # Handle pandas NA/NaT
        return None
    else:
        return obj


def _extract_json_from_text(text: str) -> str:
    """Extract JSON object from text that may contain markdown or extra text.
    
    Handles:
    - Markdown code blocks (```json ... ```)
    - Text before/after JSON
    - Multiple JSON objects (takes first)
    """
    text = text.strip()
    
    # Remove markdown code blocks
    if "```" in text:
        # Match ```json ... ``` or ``` ... ```
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if match:
            return match.group(1)
        else:
            # Remove ``` markers
            text = re.sub(r"```(?:json)?", "", text).strip()
    
    # Find first complete JSON object
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        return match.group(0)
    
    # If no JSON found, return original (will fail in json.loads)
    return text


def _parse_llm_response(raw_response: str, retry_count: int = 0) -> Dict[str, Any]:
    """Parse LLM response with robust error handling and retry logic.
    
    Args:
        raw_response: Raw LLM output
        retry_count: Current retry attempt (for logging)
    
    Returns:
        Parsed JSON dict
        
    Raises:
        ValueError: If JSON cannot be parsed after all attempts
    """
    if not raw_response or not raw_response.strip():
        raise ValueError("LLM returned empty response")
    
    # Step 1: Extract JSON from markdown/text
    json_text = _extract_json_from_text(raw_response)
    
    # Step 2: Try parsing
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        error_details = f"Position {e.pos}: {e.msg}"
        
        # Step 3: Try common fixes
        
        # Fix 1: Remove trailing commas (common JSON error)
        json_text_fixed = re.sub(r',\s*([}\]])', r'\1', json_text)
        try:
            return json.loads(json_text_fixed)
        except json.JSONDecodeError:
            pass
        
        # Fix 2: Try to fix unescaped quotes in strings
        # This is tricky and might break valid JSON, so only try if previous failed
        try:
            # Replace unescaped quotes in values (rough heuristic)
            json_text_fixed = re.sub(r'(":\s*")([^"]*)"([^,}\]]*)"', r'\1\2\"\3\"', json_text)
            return json.loads(json_text_fixed)
        except json.JSONDecodeError:
            pass
        
        # If all fixes failed, raise with context
        raise ValueError(
            f"Failed to parse LLM JSON response (attempt {retry_count + 1}).\n"
            f"Error: {error_details}\n"
            f"First 500 chars: {raw_response[:500]}"
        )


def _call_llm_extraction_for_chunk(
    chunk_data: str,
    model: str = "gpt-4o-mini",
    extract_price: bool = False,
    attempt: int = 1
) -> List[Dict[str, Any]]:
    """Call LLM to extract structured data for a single chunk.
    
    Includes retry logic for JSON parsing errors.
    
    Args:
        chunk_data: JSON string of Excel rows
        model: LLM model to use
        extract_price: If True, extract price from supplier offer
        attempt: Current attempt number (for retry logic)
    """
    client = get_client()
    
    user_prompt = build_extraction_prompt(chunk_data, "excel", extract_price)
    
    # Add extra JSON strictness for retries
    if attempt > 1:
        user_prompt += "\n\n⚠️ CRITICAL: Your previous response had invalid JSON. Please ensure:\n"
        user_prompt += "1. Output ONLY valid JSON, no markdown, no extra text\n"
        user_prompt += "2. Escape all special characters in strings\n"
        user_prompt += "3. No trailing commas\n"
        user_prompt += "4. All quotes properly closed"
    
    try:
        # Try using JSON mode if available (OpenAI GPT-4/GPT-3.5-turbo)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0,
            response_format={"type": "json_object"},  # Force JSON output
        )
    except Exception:
        # Fallback: Some models don't support response_format
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0,
        )
    
    raw_output = response.choices[0].message.content
    if not raw_output:
        raise ValueError("LLM returned empty response")
    
    # Parse with retry logic
    for retry in range(JSON_RETRY_ATTEMPTS):
        try:
            parsed = _parse_llm_response(raw_output, retry_count=retry)
            
            # Extract products array
            if "products" in parsed:
                return parsed["products"]
            else:
                return [parsed]
                
        except ValueError as e:
            if retry < JSON_RETRY_ATTEMPTS - 1:
                # Retry: Ask LLM to fix its own JSON
                print(f"⚠️ JSON parse failed (attempt {retry + 1}), asking LLM to fix...")
                
                fix_prompt = (
                    f"The following JSON is invalid:\n\n{raw_output}\n\n"
                    f"Error: {str(e)}\n\n"
                    f"Please output ONLY the corrected valid JSON with no additional text."
                )
                
                fix_response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a JSON validator. Fix invalid JSON."},
                        {"role": "user", "content": fix_prompt}
                    ],
                    temperature=0,
                )
                
                raw_output = fix_response.choices[0].message.content
            else:
                # Final attempt failed
                raise ValueError(
                    f"Failed to parse LLM response after {JSON_RETRY_ATTEMPTS} attempts.\n"
                    f"Last error: {str(e)}\n"
                    f"Please try with a smaller file or contact support."
                )
    
    # Should never reach here
    raise ValueError("Unexpected error in JSON parsing retry loop")


def process_excel_in_chunks(
    rows: List[Dict],
    model: str = "gpt-4o-mini",
    extract_price: bool = False,
    chunk_size: int = CHUNK_SIZE
) -> List[Dict[str, Any]]:
    """Process large Excel files in chunks to avoid token limits.
    
    Args:
        rows: Raw Excel rows (from read_excel)
        model: LLM model to use
        extract_price: If True, extract price from supplier offer
        chunk_size: Number of rows per chunk (default: from config)
        
    Returns:
        List of extracted products (merged from all chunks)
        
    Raises:
        ValueError: If file exceeds maximum text size limit
    """
    import pandas as pd
    
    total_rows = len(rows)
    
    if total_rows == 0:
        return []
    
    # Sanitize all rows first
    sanitized_rows = _sanitize_for_json(rows)
    
    # Check total text size BEFORE processing
    full_json = json.dumps(sanitized_rows, indent=2, ensure_ascii=False)
    total_chars = len(full_json)
    
    if total_chars > MAX_TEXT_CHARS_BEFORE_LLM:
        raise ValueError(
            f"File content ({total_chars:,} characters) exceeds processing limit "
            f"({MAX_TEXT_CHARS_BEFORE_LLM:,} characters).\n"
            f"Please reduce the file size by:\n"
            f"- Filtering to fewer rows\n"
            f"- Removing unnecessary columns\n"
            f"- Splitting into multiple files"
        )
    
    # If small enough, process in one go
    if total_rows <= chunk_size:
        return _call_llm_extraction_for_chunk(full_json, model, extract_price)
    
    # Process in chunks
    all_products = []
    num_chunks = (total_rows + chunk_size - 1) // chunk_size  # Ceiling division
    
    print(f"📊 Processing {total_rows} rows in {num_chunks} chunks...")
    
    for i in range(num_chunks):
        start_idx = i * chunk_size
        end_idx = min(start_idx + chunk_size, total_rows)
        
        chunk = sanitized_rows[start_idx:end_idx]
        chunk_data = json.dumps(chunk, indent=2, ensure_ascii=False)
        
        print(f"  Processing chunk {i+1}/{num_chunks} (rows {start_idx+1}-{end_idx})...")
        
        # Process chunk with retry logic
        chunk_products = _call_llm_extraction_for_chunk(chunk_data, model, extract_price)
        
        # Merge results
        all_products.extend(chunk_products)
    
    print(f"✅ Extracted {len(all_products)} products from {num_chunks} chunks")
    
    return all_products


# Import pandas at module level for _sanitize_for_json
import pandas as pd