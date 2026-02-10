"""
File Processor for Offer Creation Tool
Handles file processing logic with proper image support
"""

import streamlit as st
from pathlib import Path
import tempfile
import pandas as pd
import sys
import shutil

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from runners.pipeline import process_file


def _normalize_text(x) -> str:
    if x is None:
        return ""
    return str(x).strip().lower()


def process_uploaded_file(
    uploaded_file,
    dept_type,
    double_stackable: bool = False,
    extract_price: bool = False,
    product_images: list = None,
    selected_rows_only: pd.DataFrame = None,
    selected_sheet: str = None,  # NEW: Sheet name to process
):
    """Process uploaded file and return results with optional product images.

    Args:
        uploaded_file: Streamlit UploadedFile object
        dept_type: str, "food" or "hpc"
        double_stackable: bool, if True availability values are doubled
        extract_price: bool, if True price is extracted from supplier offer
        product_images: list of Path objects (one per SELECTED product, None for missing)
        selected_rows_only: DataFrame with only selected rows (for regeneration with images)
        selected_sheet: str, name of the sheet to process (None = first sheet)

    Returns:
        tuple: (success: bool, output_path: Path or None, df: DataFrame or None, error: str or None)
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = Path(temp_dir)

        # Save uploaded file
        input_path = temp_dir / uploaded_file.name
        with open(input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        # Create output directory
        output_dir = temp_dir / "output"
        output_dir.mkdir(exist_ok=True)

        # Progress indicators
        progress_bar = st.progress(0)
        status_text = st.empty()

        try:
            status_text.text("📄 Reading file...")
            progress_bar.progress(20)

            status_text.text("🤖 AI is extracting data...")
            progress_bar.progress(40)

            # Process file - GET DATAFRAME DIRECTLY
            output_path, df = process_file(
                input_path=input_path,
                category=dept_type,
                output_dir=output_dir,
                double_stackable=double_stackable,
                extract_price=extract_price,
                product_images=product_images,
                sheet_name=selected_sheet,  # NEW: Pass sheet name to pipeline
            )

            progress_bar.progress(80)
            status_text.text("✅ Finalizing...")

            # ------------------------------------------------------------------
            # IMPORTANT FIX:
            # Do NOT filter by Article Number when re-processing, because
            # article numbers are regenerated on every run -> mismatch -> empty df.
            # ------------------------------------------------------------------
            if selected_rows_only is not None and not selected_rows_only.empty:
                df_filtered = df.copy()

                # 1) Try match by EAN if available
                use_ean = False
                if "EAN code unit" in selected_rows_only.columns and "EAN code unit" in df_filtered.columns:
                    sel_eans = selected_rows_only["EAN code unit"].dropna().astype(str).str.strip()
                    sel_eans = sel_eans[sel_eans != ""]
                    if len(sel_eans) > 0:
                        use_ean = True
                        df_filtered["__ean"] = df_filtered["EAN code unit"].astype(str).str.strip()
                        df_filtered = df_filtered[df_filtered["__ean"].isin(set(sel_eans.tolist()))]
                        df_filtered = df_filtered.drop(columns=["__ean"], errors="ignore")

                # 2) If EAN not usable, match by Product Description (+ Content if exists)
                if not use_ean:
                    if "Product Description" in selected_rows_only.columns and "Product Description" in df_filtered.columns:
                        # Build composite keys
                        sel_desc = selected_rows_only["Product Description"].apply(_normalize_text)

                        if "Content" in selected_rows_only.columns and "Content" in df_filtered.columns:
                            sel_content = selected_rows_only["Content"].apply(_normalize_text)
                            sel_keys = (sel_desc + "||" + sel_content).tolist()

                            df_desc = df_filtered["Product Description"].apply(_normalize_text)
                            df_cont = df_filtered["Content"].apply(_normalize_text)
                            df_filtered["__k"] = df_desc + "||" + df_cont
                        else:
                            sel_keys = sel_desc.tolist()
                            df_filtered["__k"] = df_filtered["Product Description"].apply(_normalize_text)

                        df_filtered = df_filtered[df_filtered["__k"].isin(set(sel_keys))]
                        df_filtered = df_filtered.drop(columns=["__k"], errors="ignore")

                # 3) Fallback: if still empty, take first N rows (better than empty)
                if df_filtered.empty:
                    n = len(selected_rows_only)
                    df_filtered = df.head(n).copy()

                df = df_filtered

                # Rewrite Excel with only selected rows
                sys.path.append(str(Path(__file__).parent.parent))
                from writers.excel_writer import write_rows_to_xlsx
                from domain.schemas import FOOD_HEADERS, HPC_HEADERS

                # ⚠️ CRITICAL FIX: Use correct headers and sheet_name based on dept_type
                if dept_type == "food":
                    headers = FOOD_HEADERS
                    sheet_name = "FOOD"
                else:  # hpc
                    headers = HPC_HEADERS
                    sheet_name = "HPC"
                
                # ✅ FIX: Convert datetime columns to string before to_dict()
                df_for_export = df.copy()
                for col in df_for_export.columns:
                    if pd.api.types.is_datetime64_any_dtype(df_for_export[col]):
                        # Convert datetime to string, replace NaT/nan with empty string
                        df_for_export[col] = df_for_export[col].astype(str).replace('NaT', '').replace('nan', '')
                
                rows = df_for_export.to_dict("records")

                final_output = Path(tempfile.gettempdir()) / f"selected_{output_path.name}"

                write_rows_to_xlsx(
                    output_path=final_output,
                    sheet_name=sheet_name,
                    headers=headers,
                    rows=rows,
                    product_images=product_images,
                )
            else:
                # Use original output
                final_output = Path(tempfile.gettempdir()) / output_path.name
                shutil.copy(output_path, final_output)

            progress_bar.progress(100)

            if product_images:
                num_images = sum(1 for p in product_images if p and Path(p).exists())
                status_text.text(f"🎉 Complete with {num_images} images!")
            else:
                status_text.text("🎉 Complete!")

            return True, final_output, df, None

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            print(f"Error in processor: {error_detail}")
            
            # Provide user-friendly error messages
            error_msg = str(e)
            if "JSON" in error_msg or "json" in error_msg:
                error_msg = "AI returned invalid data format. Please try with a smaller file or contact support."
            elif "Memory" in error_msg or "memory" in error_msg:
                error_msg = "File too large to process. Please reduce file size or split into smaller batches."
            elif "token" in error_msg.lower():
                error_msg = "File content exceeds processing limits. Please reduce data size."
            
            return False, None, None, error_msg