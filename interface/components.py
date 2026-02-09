"""
UI Components for Offer Creation Tool
Reusable UI elements
"""

import base64
import tempfile
import urllib.parse
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st


def render_logo_html() -> str:
    """
    Returns HTML string to render logo as a square.
    """
    logo_dir = Path(__file__).parent / "company_logo"
    if not logo_dir.exists():
        return ""

    candidates = list(logo_dir.glob("*.*"))
    candidates = [p for p in candidates if p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]]
    if not candidates:
        return ""

    logo_path = candidates[0]
    ext = logo_path.suffix.lower().replace(".", "")
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext

    b64 = base64.b64encode(logo_path.read_bytes()).decode("utf-8")
    return f'<img class="acg-logo-square" src="data:image/{mime};base64,{b64}" />'


def render_header():
    """
    Title on the left, logo on the right, single row.
    """
    logo_html = render_logo_html()

    st.markdown(
        f"""
        <div class="header-row">
          <div class="header-left">
            <h1 class="main-title">Offer Creator</h1>
            <p class="subtitle">Convert supplier offers into standardized format</p>
          </div>
          <div class="header-right">
            {logo_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_department_selector():
    """
    Render department selection + Double Stackable + With Price checkboxes.

    Returns:
        tuple[str, bool, bool]: (dept_type, double_stackable, extract_price)
    """
    st.markdown('<div class="department-section">', unsafe_allow_html=True)
    st.markdown('<div class="section-label">Select Department</div>', unsafe_allow_html=True)

    department = st.radio(
        "Department",
        options=["Food", "HPC"],
        horizontal=True,
        label_visibility="collapsed",
        key="department_radio",
    )

    double_stackable = st.checkbox(
        "Double Stackable",
        value=False,
        help="If checked, availability calculations will use 2x pallet capacity (e.g., 33 -> 66).",
        key="double_stackable_checkbox",
    )

    extract_price = st.checkbox(
        "With Price",
        value=False,
        help="If checked, AI will extract prices from supplier offer and fill the Price/Unit column.",
        key="extract_price_checkbox",
    )

    st.markdown("</div>", unsafe_allow_html=True)

    dept_type = "food" if department == "Food" else "hpc"
    return dept_type, double_stackable, extract_price


def render_file_uploader():
    """Render file upload section with clear instructions."""
    st.markdown("### 📤 Upload Offer File")
    st.markdown("Drag and drop your file here, or click to browse")

    uploaded_file = st.file_uploader(
        "Select file",
        type=["xlsx", "xls", "pdf", "png", "jpg", "jpeg"],
        help="Supported formats: Excel (.xlsx, .xls), PDF (.pdf), Images (.png, .jpg)",
        label_visibility="collapsed",
        key="offer_file_uploader",
    )

    if uploaded_file:
        st.success(f"✅ {uploaded_file.name} uploaded successfully!")
    else:
        st.info("📋 Accepted formats: Excel, PDF, PNG, JPG")

    return uploaded_file


def render_process_button():
    left, _ = st.columns([1, 5])
    with left:
        return st.button(
            "🚀 Process Offer",
            type="primary",
            width="stretch",  # use_container_width -> width='stretch'
            key="process_offer_btn",
        )


def render_success_message():
    """Render success message after processing."""
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        """
    <div class="success-box">
        <h3>🎉 Offer Processed Successfully!</h3>
    </div>
    """,
        unsafe_allow_html=True,
    )


def _force_availability_int_display(df: pd.DataFrame) -> pd.DataFrame:
    """
    Streamlit preview fix:
    - Ensure availability columns are integers (nullable Int64)
    - Use CEIL for any floats
    - Keep missing values as <NA>
    """
    if df is None or df.empty:
        return df

    cols = ["Availability/Cartons", "Availability/Pieces", "Availability/Pallets"]
    for c in cols:
        if c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce")
            s = np.ceil(s)
            df[c] = s.astype("Int64")
    return df


def render_selectable_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Show ALL rows and let user select rows via checkbox column + select-all.
    """
    st.markdown("### ✅ Select Products to Include")

    n = len(df)

    if st.session_state.row_selected is None or len(st.session_state.row_selected) != n:
        st.session_state.row_selected = [True] * n

    all_selected = all(st.session_state.row_selected)
    select_all = st.checkbox("Select all", value=all_selected, key="select_all_checkbox")

    if select_all and not all_selected:
        st.session_state.row_selected = [True] * n
    elif (not select_all) and all_selected:
        st.session_state.row_selected = [False] * n

    # IMPORTANT: do NOT format availability as strings (that is what caused floats/decimals)
    # Instead: force proper Int64 dtype for clean display.
    view_df = _force_availability_int_display(df.copy())

    view_df.insert(0, "_selected", st.session_state.row_selected)

    edited = st.data_editor(
        view_df,
        width="stretch",  # use_container_width -> width='stretch'
        hide_index=True,
        column_config={
            "_selected": st.column_config.CheckboxColumn(
                "Include",
                help="Tick to include this row in the download",
                default=True,
            )
        },
        disabled=[c for c in view_df.columns if c != "_selected"],
        height=520,
        key="products_editor",
    )

    st.session_state.row_selected = edited["_selected"].tolist()

    selected_mask = pd.Series(st.session_state.row_selected, index=df.index)
    selected_df = df[selected_mask].copy().reset_index(drop=True)

    # Keep selected_df also clean for subsequent steps
    selected_df = _force_availability_int_display(selected_df)

    st.caption(f"Selected: {len(selected_df)} / {len(df)} products")
    return selected_df


def render_product_image_uploader(selected_df):
    st.markdown("---")
    st.markdown("### 🖼️ Add Product Images")
    st.info("💡 Search for product images on Google using EAN codes. Upload images here to include in Excel file.")

    if selected_df is None or selected_df.empty:
        st.warning("⚠️ Select products above first to add images")
        return {}

    uploaded_images = {}
    num_selected = len(selected_df)

    for idx in range(num_selected):
        row = selected_df.iloc[idx]
        product_desc = row.get("Product Description", "")
        ean_code = row.get("EAN code unit", "")

        if not product_desc:
            continue

        search_term = ean_code if ean_code else product_desc
        search_query = urllib.parse.quote(str(search_term))
        google_images_url = f"https://www.google.com/search?tbm=isch&q={search_query}"

        with st.expander(f"Product {idx + 1}: {str(product_desc)[:60]}..."):
            col1, col2 = st.columns([1, 2])

            with col1:
                st.markdown(f"**Product:** {product_desc}")
                if ean_code:
                    st.markdown(f"**🔍 Searching with EAN:** {ean_code}")
                else:
                    st.markdown("**🔍 Searching with:** Product Description")
                st.markdown(f"[Open Google Images Search]({google_images_url})")

            with col2:
                uploaded_img = st.file_uploader(
                    f"Upload image for: {str(product_desc)[:30]}",
                    type=["png", "jpg", "jpeg"],
                    key=f"product_image_{idx}",
                    label_visibility="collapsed",
                )

                if uploaded_img:
                    st.image(uploaded_img, width=150, caption="Preview")
                    uploaded_images[idx] = uploaded_img

    if uploaded_images:
        st.success(f"✅ {len(uploaded_images)} product image(s) ready to add")

    return uploaded_images


def render_download_buttons(selected_df, product_images, dept_type, base_filename="offer_output.xlsx"):
    st.markdown("---")
    st.markdown("### 💾 Download Result")

    if selected_df is None or selected_df.empty:
        st.warning("No products selected. Select at least 1 product to enable download.")
        return None, None

    # Ensure clean ints before exporting
    selected_df = _force_availability_int_display(selected_df.copy())

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**📸 With Product Images**")
        if product_images:
            if st.button(
                "🖼️ Download Excel with Images",
                type="primary",
                width="stretch",  # use_container_width -> width='stretch'
                key="download_with_images_btn",
            ):
                return "with_images", product_images
            st.caption(f"{len(product_images)} image(s) will be added")
        else:
            st.info("💡 Upload product images above to enable this option")

    with col2:
        st.markdown("**📊 Data Only**")

        filename = base_filename.replace(".xlsx", "") + "_data_only.xlsx"
        out_path = Path(tempfile.gettempdir()) / filename

        from domain.schemas import FOOD_HEADERS, HPC_HEADERS
        from writers.excel_writer import write_rows_to_xlsx

        if dept_type == "food":
            headers = FOOD_HEADERS
            sheet_name = "FOOD"
        else:
            headers = HPC_HEADERS
            sheet_name = "HPC"

        rows = selected_df.to_dict(orient="records")

        write_rows_to_xlsx(
            output_path=out_path,
            sheet_name=sheet_name,
            headers=headers,
            rows=rows,
            product_images=None,
        )

        with open(out_path, "rb") as f:
            st.download_button(
                label="📥 Download Excel Without Images",
                data=f,
                file_name=filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",  # use_container_width -> width='stretch'
                key="download_no_images_btn",
            )

    return None, None


def render_reset_button():
    st.markdown("<br>", unsafe_allow_html=True)
    return st.button("🔄 Process Another Offer", key="reset_btn")
