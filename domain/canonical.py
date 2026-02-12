"""
CanonicalRow schema definition.

This TypedDict represents the normalized, model-agnostic structure used
across the entire offer processing pipeline. All extraction layers
(Excel, PDF, image, LLM) must map their outputs into this structure
before further processing (allocation, pricing logic, writing to Excel, etc.).

Fields are optional because different suppliers and categories (Food/HPC)
may provide incomplete data.
"""

from __future__ import annotations

from typing import Optional, TypedDict


class CanonicalRow(TypedDict, total=False):
    ean: Optional[str]
    product_description: Optional[str]
    content: Optional[str]
    languages: Optional[str]

    piece_per_case: Optional[int]
    case_per_pallet: Optional[int]
    pieces_per_pallet: Optional[int]

    bbd: Optional[str]

    availability_cartons: Optional[int]
    availability_pieces: Optional[int]
    availability_pallets: Optional[int]

    price_unit_eur: Optional[float]

    source_file: Optional[str]
    source_row: Optional[int]
