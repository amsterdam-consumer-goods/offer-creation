# interface/app.py
"""
Offer Creation Tool - Main Application

Clean, modular Streamlit interface for converting supplier offers.
"""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from components import (
    render_department_selector,
    render_download_buttons,
    render_file_uploader,
    render_header,
    render_process_button,
    render_product_image_uploader,
    render_reset_button,
    render_selectable_table,
    render_success_message,
)
from processor import process_uploaded_file
from styles import get_custom_css


def _force_availability_ints(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure availability columns are displayed as integers in the UI (no .0),
    using CEIL for any non-integer numeric values.
    """
    if df is None or df.empty:
        return df

    cols = ["Availability/Cartons", "Availability/Pieces", "Availability/Pallets"]
    for c in cols:
        if c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce")
            s = np.ceil(s)
            df[c] = s.astype("Int64")  # nullable integer
    return df


# ============================================================================
# PAGE CONFIG
# ============================================================================
st.set_page_config(
    page_title="Offer Creator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================================
# APPLY STYLES
# ============================================================================
st.markdown(get_custom_css(), unsafe_allow_html=True)

# ============================================================================
# SESSION STATE INITIALIZATION
# ============================================================================
if "processed" not in st.session_state:
    st.session_state.processed = False
if "output_path" not in st.session_state:
    st.session_state.output_path = None
if "df" not in st.session_state:
    st.session_state.df = None
if "selected_df" not in st.session_state:
    st.session_state.selected_df = None
if "row_selected" not in st.session_state:
    st.session_state.row_selected = None
if "double_stackable" not in st.session_state:
    st.session_state.double_stackable = False
if "extract_price" not in st.session_state:
    st.session_state.extract_price = False
if "product_images" not in st.session_state:
    st.session_state.product_images = {}
if "uploaded_file_data" not in st.session_state:
    st.session_state.uploaded_file_data = None
if "dept_type" not in st.session_state:
    st.session_state.dept_type = None

# ============================================================================
# MAIN APP FLOW
# ============================================================================
render_header()

dept_type, double_stackable, extract_price = render_department_selector()
st.session_state.double_stackable = double_stackable
st.session_state.extract_price = extract_price
st.session_state.dept_type = dept_type

if dept_type:
    uploaded_file = render_file_uploader()

    if uploaded_file:
        st.session_state.uploaded_file_data = uploaded_file

        process_btn = render_process_button()

        if process_btn:
            with st.spinner("🔄 Processing your offer..."):
                success, output_path, df, error = process_uploaded_file(
                    uploaded_file=uploaded_file,
                    dept_type=dept_type,
                    double_stackable=st.session_state.double_stackable,
                    extract_price=st.session_state.extract_price,
                    product_images=None,
                )

                if success:
                    df = _force_availability_ints(df)

                    st.session_state.processed = True
                    st.session_state.output_path = output_path
                    st.session_state.df = df
                    st.session_state.selected_df = None
                    st.session_state.product_images = {}
                    st.session_state.row_selected = None
                else:
                    st.error(f"❌ Error: {error}")

# ============================================================================
# RESULTS SECTION
# ============================================================================
if st.session_state.processed and st.session_state.df is not None:
    render_success_message()

    edited_df = render_selectable_table(st.session_state.df)

    if edited_df is not None and len(edited_df) > 0 and "Include" in edited_df.columns:
        selected_df = edited_df[edited_df["Include"] == True].copy()  # noqa: E712
        selected_df.drop(columns=["Include"], inplace=True, errors="ignore")
        selected_df.reset_index(drop=True, inplace=True)
    else:
        selected_df = edited_df.copy() if edited_df is not None else None
        if selected_df is not None:
            selected_df.reset_index(drop=True, inplace=True)

    if selected_df is not None:
        selected_df = _force_availability_ints(selected_df)

    st.session_state.selected_df = selected_df

    product_images = render_product_image_uploader(selected_df)
    st.session_state.product_images = product_images

    action, images_to_use = render_download_buttons(
        selected_df,
        product_images,
        dept_type=st.session_state.dept_type,
        base_filename=st.session_state.output_path.name,
    )

    # ------------------------------------------------------------------------
    # NO-IMAGES DOWNLOAD
    # ------------------------------------------------------------------------
    if action == "no_images" and selected_df is not None and len(selected_df) > 0:
        with st.spinner("📄 Generating Excel (no images)..."):
            from domain.schemas import FOOD_HEADERS, HPC_HEADERS
            from writers.excel_writer import write_rows_to_xlsx

            headers = FOOD_HEADERS if st.session_state.dept_type == "food" else HPC_HEADERS
            rows = selected_df.to_dict(orient="records")

            base = st.session_state.output_path.name if st.session_state.output_path else "offer.xlsx"
            output_no_images = Path(tempfile.gettempdir()) / f"no_images_{base}"

            write_rows_to_xlsx(
                output_path=output_no_images,
                sheet_name=st.session_state.dept_type.upper(),
                headers=headers,
                rows=rows,
                product_images=None,
            )

            st.success("✅ Excel (no images) generated!")

            with open(output_no_images, "rb") as f:
                st.download_button(
                    label="📥 Download Excel (No Images)",
                    data=f,
                    file_name=output_no_images.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="secondary",
                    width="stretch",  # replaced use_container_width
                    key="download_no_images",
                )

    # ------------------------------------------------------------------------
    # WITH-IMAGES DOWNLOAD
    # ------------------------------------------------------------------------
    if action == "with_images" and images_to_use and selected_df is not None and len(selected_df) > 0:
        with st.spinner("🎨 Generating Excel with images..."):
            from domain.schemas import FOOD_HEADERS, HPC_HEADERS
            from writers.excel_writer import write_rows_to_xlsx

            headers = FOOD_HEADERS if st.session_state.dept_type == "food" else HPC_HEADERS

            temp_dir = Path(tempfile.gettempdir()) / "offer_images"
            temp_dir.mkdir(exist_ok=True)

            image_paths: list[Path | None] = []
            for idx in range(len(selected_df)):
                if idx in images_to_use:
                    img_file = images_to_use[idx]
                    img_ext = Path(img_file.name).suffix
                    img_path = temp_dir / f"product_{idx}{img_ext}"

                    with open(img_path, "wb") as f:
                        f.write(img_file.getbuffer())

                    image_paths.append(img_path)
                else:
                    image_paths.append(None)

            rows = selected_df.to_dict(orient="records")

            base = st.session_state.output_path.name if st.session_state.output_path else "offer.xlsx"
            output_with_images = Path(tempfile.gettempdir()) / f"with_images_{base}"

            write_rows_to_xlsx(
                output_path=output_with_images,
                sheet_name=st.session_state.dept_type.upper(),
                headers=headers,
                rows=rows,
                product_images=image_paths,
            )

            st.success("✅ Excel with images generated!")

            with open(output_with_images, "rb") as f:
                st.download_button(
                    label="📥 Download Excel with Images",
                    data=f,
                    file_name=output_with_images.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                    width="stretch",  # replaced use_container_width
                    key="final_download",
                )

    if render_reset_button():
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()
