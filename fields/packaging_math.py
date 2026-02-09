"""
Packaging and availability mathematical computations.

CHANGE REQUEST:
- Company wants availability fields as INTEGERS (no 5940.0).
- For any availability values that require division (i.e., produce floats),
  ALWAYS ROUND UP (CEIL).
- This applies both to computed values AND to any numeric-like values coming in.
"""

from __future__ import annotations

import math
from typing import Optional, Union

from domain.canonical import CanonicalRow

Number = Union[int, float]


def _to_number(value) -> Optional[float]:
    """Convert int/float (or numeric-like strings) to float. Return None if not possible."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    try:
        s = str(value).strip()
        if not s:
            return None
        s = s.replace(",", ".")
        return float(s)
    except Exception:
        return None


def _is_valid_positive_number(value) -> bool:
    """Check if value is a valid positive number (int/float)."""
    v = _to_number(value)
    return v is not None and v > 0


def _ceil_int(value) -> Optional[int]:
    """
    Convert numeric-like value to an integer by ALWAYS rounding up (ceil).
    Returns None if not convertible or <= 0.
    """
    v = _to_number(value)
    if v is None or v <= 0:
        return None
    return int(math.ceil(v))


def _finalize_availability_ints(row: CanonicalRow) -> CanonicalRow:
    """
    Enforce integer availability fields (ceil).
    This removes .0 display and guarantees integer outputs.
    """
    for k in ("availability_pieces", "availability_cartons", "availability_pallets"):
        vi = _ceil_int(row.get(k))
        row[k] = vi if vi is not None else row.get(k)
        # If it was non-numeric junk, keep as-is (or you can force to None)
        if _to_number(row.get(k)) is None:
            row[k] = None
    return row


def apply_double_stackable(row: CanonicalRow) -> CanonicalRow:
    """Double stackable = multiply availability values by 2, then force integer (ceil)."""
    for k in ("availability_pieces", "availability_cartons", "availability_pallets"):
        v = _to_number(row.get(k))
        if v is not None and v > 0:
            row[k] = v * 2
    return _finalize_availability_ints(row)


def complete_packaging_triad(row: CanonicalRow) -> CanonicalRow:
    """
    Complete packaging triad using 2-of-3 rule (floats allowed here).
    Packaging triad:
    - A: piece_per_case
    - B: case_per_pallet
    - C: pieces_per_pallet
    """
    a = _to_number(row.get("piece_per_case"))
    b = _to_number(row.get("case_per_pallet"))
    c = _to_number(row.get("pieces_per_pallet"))

    # A and B -> C
    if _is_valid_positive_number(a) and _is_valid_positive_number(b):
        if row.get("pieces_per_pallet") is None:
            row["pieces_per_pallet"] = a * b

    # A and C -> B
    if _is_valid_positive_number(a) and _is_valid_positive_number(c):
        if row.get("case_per_pallet") is None and a != 0:
            row["case_per_pallet"] = c / a

    # B and C -> A
    if _is_valid_positive_number(b) and _is_valid_positive_number(c):
        if row.get("piece_per_case") is None and b != 0:
            row["piece_per_case"] = c / b

    return row


def complete_availability(row: CanonicalRow) -> CanonicalRow:
    """
    Complete availability fields using packaging info.
    IMPORTANT:
    - availability_* must be INTEGERS
    - any division result MUST be rounded UP (ceil)
    - NEVER compute negatives / zeros
    - Supplier-provided values take precedence (we compute only missing fields)
    """
    pieces = _to_number(row.get("availability_pieces"))
    cartons = _to_number(row.get("availability_cartons"))
    pallets = _to_number(row.get("availability_pallets"))

    ppc = _to_number(row.get("piece_per_case"))
    ppp = _to_number(row.get("pieces_per_pallet"))

    # FORWARD (Pieces -> Cartons/Pallets)
    if _is_valid_positive_number(pieces):
        if row.get("availability_cartons") is None and _is_valid_positive_number(ppc) and ppc != 0:
            row["availability_cartons"] = math.ceil(pieces / ppc)

        if row.get("availability_pallets") is None and _is_valid_positive_number(ppp) and ppp != 0:
            row["availability_pallets"] = math.ceil(pieces / ppp)

    # REVERSE (Cartons/Pallets -> Pieces) only if Pieces missing
    if row.get("availability_pieces") is None:
        if _is_valid_positive_number(cartons) and _is_valid_positive_number(ppc):
            # multiplication should be integer-safe, but still ceil+int for safety
            row["availability_pieces"] = math.ceil(cartons * ppc)
            pieces = _to_number(row.get("availability_pieces"))

        elif _is_valid_positive_number(pallets) and _is_valid_positive_number(ppp):
            row["availability_pieces"] = math.ceil(pallets * ppp)
            pieces = _to_number(row.get("availability_pieces"))

    # CROSS-FILL if Pieces is now known
    pieces = _to_number(row.get("availability_pieces"))
    if _is_valid_positive_number(pieces):
        if row.get("availability_cartons") is None and _is_valid_positive_number(ppc) and ppc != 0:
            row["availability_cartons"] = math.ceil(pieces / ppc)

        if row.get("availability_pallets") is None and _is_valid_positive_number(ppp) and ppp != 0:
            row["availability_pallets"] = math.ceil(pieces / ppp)

    # FINAL: force integer availability fields (also removes .0 for provided numbers)
    return _finalize_availability_ints(row)


def apply_packaging_math(row: CanonicalRow, max_iterations: int = 3) -> CanonicalRow:
    """Apply packaging + availability math iteratively."""
    for _ in range(max_iterations):
        before = dict(row)

        row = complete_packaging_triad(row)
        row = complete_availability(row)

        if dict(row) == before:
            break

    return row
