"""
LLM-based extraction into the CanonicalRow format.

This module provides a single normalized interface to convert supplier offers from:
- Excel (tabular rows)
- PDF (extracted text)
- Images (vision input)

into a list of CanonicalRow dictionaries.

Core responsibilities:
- Build LLM prompts and call the OpenAI client.
- Parse model output into JSON reliably (including markdown-wrapped JSON).
- Convert extracted dicts into CanonicalRow with type normalization.
- For Excel, optionally chunk large inputs and pre-extract simple content patterns as a fallback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from domain.canonical import CanonicalRow
from input_readers import read_excel, read_image_as_data_url, read_pdf

from .chunked_processor import process_excel_in_chunks
from .llm_client import get_client
from .prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_prompt
from fields.normalization import to_float, to_int


def _extract_content_from_text(text: str) -> str | None:
    """Extract a simple content pattern like 187GR, 1.5KG, 330ML from arbitrary text."""
    if not text:
        return None

    pattern = r"\b(\d+(?:[.,]\d+)?)\s*(GR|KG|ML|L)\b"
    match = re.search(pattern, text.upper())
    if not match:
        return None

    number = match.group(1).replace(",", ".")
    unit = match.group(2)
    return f"{number}{unit}"


def _pre_extract_content_from_rows(rows: List[Dict]) -> List[str | None]:
    """Pre-extract content patterns from raw Excel rows as a fallback if the LLM misses it."""
    extracted: List[str | None] = []

    for row in rows:
        content = None
        for value in row.values():
            if value:
                content = _extract_content_from_text(str(value))
                if content:
                    break
        extracted.append(content)

    return extracted


def _parse_llm_response(raw_response: str) -> Dict[str, Any]:
    """Parse model output into JSON, handling possible markdown code fences."""
    raw = (raw_response or "").strip()

    if raw.startswith("```"):
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL)
        if match:
            raw = match.group(1)
        else:
            raw = re.sub(r"```(?:json)?", "", raw).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError(f"LLM did not return valid JSON. Error: {e}\nGot: {raw[:500]}")
        return json.loads(match.group(0))


def _call_llm_extraction(
    raw_data: str,
    file_type: str,
    model: str = "gpt-4o-mini",
    extract_price: bool = False,
) -> List[Dict[str, Any]]:
    """Call the LLM for extraction and return a list of product dictionaries."""
    client = get_client()
    user_prompt = build_extraction_prompt(raw_data, file_type, extract_price)

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

    parsed = _parse_llm_response(raw_output)

    if isinstance(parsed, dict) and "products" in parsed:
        return parsed["products"]
    return [parsed]


def _dict_to_canonical(product: Dict[str, Any], source_file: str, source_row: int) -> CanonicalRow:
    """Convert a model-produced product dict into a typed CanonicalRow with normalization."""
    return CanonicalRow(
        ean=product.get("ean"),
        product_description=product.get("product_description"),
        content=product.get("content"),
        languages=product.get("languages"),
        piece_per_case=to_int(product.get("piece_per_case")),
        case_per_pallet=to_int(product.get("case_per_pallet")),
        pieces_per_pallet=to_int(product.get("pieces_per_pallet")),
        bbd=product.get("bbd"),
        availability_pieces=to_int(product.get("availability_pieces")),
        availability_cartons=to_int(product.get("availability_cartons")),
        availability_pallets=to_int(product.get("availability_pallets")),
        price_unit_eur=to_float(product.get("price_unit_eur")),
        source_file=source_file,
        source_row=source_row,
    )


def excel_to_canonical(
    xlsx_path: Path,
    model: str = "gpt-4o-mini",
    extract_price: bool = False,
    sheet_name: str | None = None,
) -> List[CanonicalRow]:
    """Convert an Excel file into CanonicalRow items (chunked when needed)."""
    rows = read_excel(xlsx_path, sheet_name=sheet_name)

    pre_extracted_content = _pre_extract_content_from_rows(rows)
    products = process_excel_in_chunks(rows, model, extract_price)

    canonical_rows = [
        _dict_to_canonical(p, str(xlsx_path), idx) for idx, p in enumerate(products, start=1)
    ]

    for i, row in enumerate(canonical_rows):
        if not row.get("content") and i < len(pre_extracted_content) and pre_extracted_content[i]:
            row["content"] = pre_extracted_content[i]

    return canonical_rows


def pdf_to_canonical(
    pdf_path: Path,
    model: str = "gpt-4o-mini",
    extract_price: bool = False,
) -> List[CanonicalRow]:
    """Convert a PDF file into CanonicalRow items using extracted text + LLM."""
    raw_text = read_pdf(pdf_path)
    products = _call_llm_extraction(raw_text, "pdf", model, extract_price)

    return [_dict_to_canonical(p, str(pdf_path), idx) for idx, p in enumerate(products, start=1)]


def image_to_canonical(
    image_path: Path,
    model: str = "gpt-4o-mini",
    extract_price: bool = False,
) -> List[CanonicalRow]:
    """Convert an image file into CanonicalRow items using LLM vision extraction."""
    data_url = read_image_as_data_url(image_path)
    client = get_client()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_extraction_prompt("See image below", "image", extract_price)},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        temperature=0,
    )

    raw_output = response.choices[0].message.content
    if not raw_output:
        raise ValueError("LLM returned empty response")

    parsed = _parse_llm_response(raw_output)
    products = parsed.get("products", [parsed]) if isinstance(parsed, dict) else [parsed]

    return [_dict_to_canonical(p, str(image_path), idx) for idx, p in enumerate(products, start=1)]
