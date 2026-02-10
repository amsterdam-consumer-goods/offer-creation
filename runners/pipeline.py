"""Main pipeline for processing offer files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional
import pandas as pd

from domain.canonical import CanonicalRow
from domain.schemas import FOOD_HEADERS, HPC_HEADERS
from extraction import excel_to_canonical, image_to_canonical, pdf_to_canonical
from fields import allocate
from fields.normalization import (
    extract_content_from_description,
    clean_description_from_content,
    force_clean_description,
    normalize_content,
    normalize_languages,
    extract_ca_cse,
)
from fields.packaging_math import apply_packaging_math, apply_double_stackable
from mapping import canonical_to_food_row, canonical_to_hpc_row
from writers import write_rows_to_xlsx


def clean_and_normalize_row(row: CanonicalRow) -> CanonicalRow:
    """Clean and normalize a canonical row.

    This function ALWAYS:
    1. Cleans description from any content pattern (even if content field exists)
    2. Ensures content field is populated (from field or extracted from description)
    3. Normalizes content format (110G → 110 GR)
    4. Normalizes languages format (DE,FR → DE / FR)
    5. Extracts CA/CSE if present in description

    Args:
        row: CanonicalRow from LLM extraction

    Returns:
        CanonicalRow with cleaned description and normalized content
    
    Examples:
        Input: {description: "MILKA 120G COW", content: "120G", languages: "DE,FR"}
        Output: {description: "MILKA COW", content: "120 GR", languages: "DE / FR"}
        
        Input: {description: "OREO 154G BROWNIE", content: None, languages: "DE/FR/NL"}
        Output: {description: "OREO BROWNIE", content: "154 GR", languages: "DE / FR / NL"}
        
        Input: {description: "MKA 110G CHOCO 10CA", content: "110G"}
        Output: {description: "MKA CHOCO", content: "110 GR", piece_per_case: 10}
    """
    description = row.get("product_description")
    content = row.get("content")
    languages = row.get("languages")
    piece_per_case = row.get("piece_per_case")

    # Step 1: FORCE clean description and extract content
    # This runs REGARDLESS of whether content field exists
    cleaned_desc, extracted_content = force_clean_description(description)

    if cleaned_desc:
        row["product_description"] = cleaned_desc

    # Step 2: Ensure content field is populated
    # Priority: existing content > extracted content
    final_content = content or extracted_content

    if final_content:
        # Step 3: Normalize content format (110G → 110 GR)
        normalized_content = normalize_content(final_content)
        row["content"] = normalized_content

    # Step 4: Normalize languages format (DE,FR → DE / FR)
    if languages:
        normalized_languages = normalize_languages(languages)
        if normalized_languages:
            row["languages"] = normalized_languages

    # Step 5: Extract CA/CSE if not already set
    if not piece_per_case and description:
        extracted_ppc = extract_ca_cse(description)
        if extracted_ppc:
            row["piece_per_case"] = extracted_ppc

    return row


def extract_missing_content(row: CanonicalRow) -> CanonicalRow:
    """Extract content from product description if content field is empty.

    DEPRECATED: Use clean_and_normalize_row instead.
    This function is kept for backward compatibility.

    Args:
        row: CanonicalRow potentially missing content

    Returns:
        CanonicalRow with extracted content and cleaned description
    """
    content = row.get("content")
    description = row.get("product_description")

    # If content is already present, do nothing
    if content:
        return row

    # Try to extract content from description
    extracted_content = extract_content_from_description(description)

    if extracted_content:
        # Set the extracted content
        row["content"] = extracted_content

        # Clean description by removing the content
        cleaned_description = clean_description_from_content(description, extracted_content)
        if cleaned_description:
            row["product_description"] = cleaned_description

    return row


def process_file(
    input_path: Path,
    category: Literal["food", "hpc"],
    output_dir: Path,
    double_stackable: bool = False,
    extract_price: bool = False,
    product_images: Optional[List[Optional[Path]]] = None,
    sheet_name: Optional[str] = None,  # NEW: Sheet name to process
) -> tuple[Path, pd.DataFrame]:
    """Process a single offer file through complete pipeline.

    Pipeline:
    1. Read file (Excel/PDF/Image) → Raw data
    2. Extract with LLM → Canonical (with content pre-extraction for Excel)
    3. 🆕 ALWAYS clean description and normalize content (NEW LAYER!)
    4. Apply packaging math (compute missing fields)
    5. Apply double stackable if enabled (2x availability)
    6. Map to category schema (Food/HPC)
    7. Generate article numbers
    8. Write to Excel with images

    Args:
        input_path: Path to input file (Excel/PDF/PNG)
        category: "food" or "hpc"
        output_dir: Where to save output Excel
        double_stackable: If True, multiply all availability values by 2
        extract_price: If True, extract price from supplier offer
        product_images: Optional list of image paths (one per product, None for missing)
        sheet_name: Optional sheet name to process (Excel only, None = first sheet)

    Returns:
        tuple: (output_path, dataframe)
    """
    print(f"\n📄 Processing: {input_path.name}")

    # Step 1 & 2: Read + Extract → Canonical
    suffix = input_path.suffix.lower()

    if suffix in [".xlsx", ".xls"]:
        # NEW: Pass sheet_name to Excel extraction
        canonical_rows = excel_to_canonical(
            input_path, 
            extract_price=extract_price,
            sheet_name=sheet_name  # NEW parameter
        )
        if sheet_name:
            print(f"ℹ️  Processing sheet: '{sheet_name}'")
    elif suffix == ".pdf":
        canonical_rows = pdf_to_canonical(input_path, extract_price=extract_price)
    elif suffix in [".png", ".jpg", ".jpeg"]:
        canonical_rows = image_to_canonical(input_path, extract_price=extract_price)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    print(f"✓ Extracted {len(canonical_rows)} products")

    # Step 3: 🆕 ALWAYS clean description and normalize content
    # This runs for ALL file types to ensure consistency
    canonical_rows = [clean_and_normalize_row(row) for row in canonical_rows]
    print(f"✓ Cleaned descriptions and normalized content")

    # Step 4: Apply packaging mathematics
    canonical_rows = [apply_packaging_math(row) for row in canonical_rows]
    print(f"✓ Applied packaging math")

    # Step 5: Apply double stackable if enabled
    if double_stackable:
        canonical_rows = [apply_double_stackable(row) for row in canonical_rows]
        print(f"✓ Applied double stackable (2x availability)")

    # Step 6: Map to category schema
    if category == "food":
        headers = FOOD_HEADERS
        mapped_rows = [canonical_to_food_row(row) for row in canonical_rows]
    else:
        headers = HPC_HEADERS
        mapped_rows = [canonical_to_hpc_row(row) for row in canonical_rows]

    # Step 7: Generate article numbers
    article_numbers = allocate(len(mapped_rows))
    for row, article_no in zip(mapped_rows, article_numbers):
        row["Article Number"] = article_no

    print(f"✓ Generated article numbers: {article_numbers[0]} - {article_numbers[-1]}")

    # Create DataFrame BEFORE writing Excel
    import pandas as pd
    df = pd.DataFrame(mapped_rows, columns=headers)
    
    # ✅ CRITICAL FIX: Convert datetime columns to string (especially BBD)
    # This prevents "Object of type datetime is not JSON serializable" error
    # when processor.py calls df.to_dict("records")
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            # Convert datetime to string, replace NaT/nan with empty string
            df[col] = df[col].astype(str).replace('NaT', '').replace('nan', '')
            print(f"ℹ️  Converted {col} from datetime to string for JSON compatibility")

    # Step 8: Write to Excel with images
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{category.upper()}_{input_path.stem}_{timestamp}.xlsx"
    output_path = output_dir / output_filename

    write_rows_to_xlsx(
        output_path=output_path,
        sheet_name=category.upper(),
        headers=headers,
        rows=mapped_rows,
        product_images=product_images,
    )

    if product_images:
        num_images = sum(1 for p in product_images if p)
        print(f"✅ Created: {output_path.name} (with {num_images} product images)")
    else:
        print(f"✅ Created: {output_path.name}")
    
    return output_path, df  # RETURN BOTH


def process_batch(
    input_dir: Path,
    category: Literal["food", "hpc"],
    output_dir: Path,
    double_stackable: bool = False,
) -> List[Path]:
    """Process all files in a directory.

    Args:
        input_dir: Directory containing input files
        category: "food" or "hpc"
        output_dir: Where to save output files
        double_stackable: If True, multiply all availability values by 2

    Returns:
        List of created output file paths
    """
    if not input_dir.exists():
        print(f"⚠️  Input directory does not exist: {input_dir}")
        return []

    # Find all supported files
    files = [
        f for f in input_dir.iterdir()
        if f.is_file() and f.suffix.lower() in [".xlsx", ".xls", ".pdf", ".png", ".jpg", ".jpeg"]
    ]

    if not files:
        print(f"ℹ️  No supported files found in: {input_dir}")
        return []

    print(f"\n{'='*60}")
    print(f"📥 Processing {category.upper()} batch: {len(files)} files")
    print(f"{'='*60}")

    output_paths = []
    for file in files:
        try:
            output_path, _ = process_file(  # Unpack tuple
                input_path=file,
                category=category,
                output_dir=output_dir,
                double_stackable=double_stackable,
                product_images=None,  # Batch processing doesn't support images
                sheet_name=None,  # Batch processing uses default (first sheet)
            )
            output_paths.append(output_path)
        except Exception as e:
            print(f"❌ Error processing {file.name}: {e}")
            continue

    return output_paths