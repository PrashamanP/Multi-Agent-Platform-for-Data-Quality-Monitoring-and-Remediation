import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# -------------------------------------------------------------------
# PATH SETUP: make project root importable (so src/ and scripts/ work)
# -------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# -------------------------------------------------------------------
# HELPER FUNCTION: Switch to a specific tab
# -------------------------------------------------------------------
def switch_tab(tab_index: int):
    """
    Inject JavaScript to switch to a specific tab.
    tab_index: 0-based index of the tab to switch to
    """
    js = f"""
    <script>
        var tabs = window.parent.document.querySelectorAll('button[data-baseweb="tab"]');
        if (tabs.length > {tab_index}) {{
            tabs[{tab_index}].click();
        }}
    </script>
    """
    components.html(js, height=0)


# -------------------------------------------------------------------
# PAGE CONFIG
# -------------------------------------------------------------------
st.set_page_config(
    page_title="NPI Data Quality Management System",
    layout="wide",
)

# -------------------------------------------------------------------
# GLOBAL STYLES (tabs, buttons, sliders, etc.)
# -------------------------------------------------------------------
st.markdown(
    """
<style>
/* Overall page background */
[data-testid="stAppViewContainer"] {
    background-color: #f5f7fb;
}

/* Remove top padding */
main.block-container {
    padding-top: 1.5rem;
}

/* Top title spacing */
h1 {
    margin-bottom: 0.25rem;
}

/* Subtitle styling */
.app-subtitle {
    font-size: 0.95rem;
    color: #4b5563;
}

/* ------------------------------------------------------------ */
/* TAB BAR — blue themed, professional                          */
/* ------------------------------------------------------------ */
.stTabs [role="tablist"] {
    border-bottom: 1px solid #E5E7EB !important;
    gap: 1.75rem !important;
}

/* Individual tab label */
.stTabs [role="tab"] {
    font-size: 0.90rem !important;
    padding: 0.75rem 0.25rem !important;
    font-weight: 500 !important;
    color: #4B5563 !important;
    border: none !important;
    background: transparent !important;
}

/* Hover state */
.stTabs [role="tab"]:hover {
    color: #0f65d4 !important;
    opacity: 0.9;
}

/* Active tab label */
.stTabs [role="tab"][aria-selected="true"] {
    color: #0f65d4 !important;
    font-weight: 600 !important;
    border-bottom: 3px solid #0f65d4 !important;
}

/* Remove Streamlit's default colored indicator */
.stTabs [data-baseweb="tab-highlight"] {
    background-color: transparent !important;
    border-bottom: 3px solid #0f65d4 !important;
}

/* Remove focus halo */
.stTabs [role="tab"]:focus {
    outline: none !important;
    box-shadow: none !important;
}

/* ------------------------------------------------------------ */
/* CARDS / SECTIONS                                             */
/* ------------------------------------------------------------ */
.section-card {
    background-color: #ffffff;
    border-radius: 8px;
    padding: 1.25rem 1.5rem;
    border: 1px solid #E5E7EB;
}

.section-title {
    font-size: 1.0rem;
    font-weight: 600;
    color: #111827;
    margin-bottom: 0.75rem;
}

.section-caption {
    font-size: 0.85rem;
    color: #6B7280;
}

/* Nice labels for metadata */
.meta-label {
    font-size: 0.8rem;
    font-weight: 500;
    color: #6B7280;
    text-transform: uppercase;
}

.meta-value {
    font-size: 0.9rem;
    font-weight: 500;
    color: #111827;
}

/* Primary button alignment wrapper */
.button-row {
    margin-top: 1rem;
}

/* ------------------------------------------------------------ */
/* PRIMARY BUTTON — blue                                        */
/* ------------------------------------------------------------ */
div.stButton > button:first-child {
    background-color: #1E6AFF !important;
    color: white !important;
    border-radius: 6px !important;
    border: none !important;
    padding: 0.6rem 1.2rem !important;
    font-size: 14px !important;
}
div.stButton > button:first-child:hover {
    background-color: #1554cc !important;
}

/* ------------------------------------------------------------ */
/* SLIDERS — clean, minimal design                              */
/* ------------------------------------------------------------ */

/* Hide the red current value display above slider */
.stSlider [data-testid="stThumbValue"] {
    display: none !important;
}

/* Hide min/max tick labels below slider */
.stSlider [data-testid="stTickBarMin"],
.stSlider [data-testid="stTickBarMax"] {
    display: none !important;
}

/* Slider track (background) */
.stSlider > div > div > div > div {
    background: #e5e7eb !important;
}

/* Slider track (filled/active part) */
.stSlider [data-testid="stSliderTrack"] > div:first-child {
    background: #0f65d4 !important;
}

/* Slider thumb */
.stSlider [data-testid="stSliderThumb"] {
    background: #0f65d4 !important;
    border: 2px solid #0f65d4 !important;
    width: 16px !important;
    height: 16px !important;
}

/* Slider thumb focus state */
.stSlider [data-testid="stSliderThumb"]:focus {
    box-shadow: 0 0 0 3px rgba(15, 101, 212, 0.25) !important;
}

/* Slider label styling */
.stSlider label {
    font-size: 0.875rem !important;
    font-weight: 500 !important;
    color: #374151 !important;
    margin-bottom: 0.5rem !important;
}

/* ------------------------------------------------------------ */
/* NUMBER INPUT — clean styling                                 */
/* ------------------------------------------------------------ */
.stNumberInput label {
    font-size: 0.875rem !important;
    font-weight: 500 !important;
    color: #374151 !important;
}

.stNumberInput input {
    border-color: #d1d5db !important;
    border-radius: 6px !important;
}

.stNumberInput input:focus {
    outline: none !important;
    box-shadow: 0 0 0 2px rgba(15, 101, 212, 0.25) !important;
    border-color: #0f65d4 !important;
}

/* ------------------------------------------------------------ */
/* CHECKBOX — clean styling                                     */
/* ------------------------------------------------------------ */
.stCheckbox label {
    font-size: 0.875rem !important;
    color: #374151 !important;
}

.stCheckbox [data-testid="stCheckbox"] > label > span:first-child {
    border-color: #d1d5db !important;
}

.stCheckbox [data-testid="stCheckbox"] input:checked + div {
    background-color: #0f65d4 !important;
    border-color: #0f65d4 !important;
}
</style>
""",
    unsafe_allow_html=True,
)

# -------------------------------------------------------------------
# HEADER
# -------------------------------------------------------------------
st.markdown(
    """
# NPI Data Quality Management System
<p class="app-subtitle">
Manage data profiling, anomaly detection, and remediation for NPI datasets.
</p>
""",
    unsafe_allow_html=True,
)


# -------------------------------------------------------------------
# TABS
# -------------------------------------------------------------------
tab_labels = [
    "Upload Data",
    "Profiling",
    "Anomaly Detection",
    "Recommendations",
    "Execute Fixes",
    "Executive Summary"
]
tabs = st.tabs(tab_labels)


# -------------------------------------------------------------------
# TAB 1 — UPLOAD DATA
# -------------------------------------------------------------------
with tabs[0]:
    center = st.container()
    with center:
        st.markdown(
            """
            <div style="max-width: 1100px; margin-left: auto; margin-right: auto;">
            """,
            unsafe_allow_html=True,
        )

        uploaded_file = st.file_uploader(
            "Choose file",
            type=["csv", "xlsx", "xls", "parquet"],
            label_visibility="collapsed",
        )

        df_preview = None
        saved_path = None

        if uploaded_file is not None:
            suffix = Path(uploaded_file.name).suffix.lower()
            try:
                if suffix == ".csv":
                    df_preview = pd.read_csv(uploaded_file)
                elif suffix in (".xlsx", ".xls"):
                    df_preview = pd.read_excel(uploaded_file)
                elif suffix == ".parquet":
                    df_preview = pd.read_parquet(uploaded_file)
                else:
                    st.error("Unsupported file type.")
            except Exception as exc:
                st.error(f"Could not read file: {exc}")

            if df_preview is not None:
                st.markdown("**Dataset preview**")
                st.dataframe(df_preview.head(20), use_container_width=True)

                # Save to data/input so the back-end pipeline can consume it
                input_dir = ROOT_DIR / "data" / "input"
                input_dir.mkdir(parents=True, exist_ok=True)
                saved_path = input_dir / f"uploaded_{uploaded_file.name}"
                try:
                    if suffix == ".csv":
                        df_preview.to_csv(saved_path, index=False)
                    elif suffix in (".xlsx", ".xls"):
                        df_preview.to_excel(saved_path, index=False)
                    elif suffix == ".parquet":
                        df_preview.to_parquet(saved_path, index=False)
                except Exception as exc:
                    st.warning(
                        "File was loaded but could not be saved for the pipeline: "
                        f"{exc}"
                    )
                    saved_path = None

                st.session_state["uploaded_dataset_path"] = (
                    str(saved_path) if saved_path is not None else None
                )

        # Start profiling button
        st.markdown('<div class="button-row">', unsafe_allow_html=True)
        if st.button("Start Profiling", type="primary", key="start_profiling_btn"):
            dataset_path = st.session_state.get("uploaded_dataset_path")
            if not dataset_path:
                st.warning("Please upload a dataset before starting profiling.")
            else:
                st.session_state["navigate_to_tab"] = 1  # Profiling tab
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

        # Handle navigation
        if st.session_state.get("navigate_to_tab") == 1:
            switch_tab(1)
            st.session_state["navigate_to_tab"] = None


# -------------------------------------------------------------------
# TAB 2 — PROFILING
# -------------------------------------------------------------------
with tabs[1]:
    st.title("Data Profiling")

    # Custom CSS for professional styling
    st.markdown(
        """
        <style>
        .profile-intro {
            color: #64748b;
            font-size: 0.95rem;
            margin-bottom: 24px;
        }
        .profile-section-header {
            font-size: 1.1rem;
            font-weight: 600;
            color: #1e293b;
            margin-bottom: 16px;
            margin-top: 24px;
            padding-bottom: 8px;
            border-bottom: 1px solid #e2e8f0;
        }
        .profile-metric-card {
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
        }
        .profile-metric-value {
            font-size: 2rem;
            font-weight: 700;
            color: #1e3a5f;
        }
        .profile-metric-label {
            font-size: 0.85rem;
            color: #64748b;
            margin-top: 4px;
        }
        .profile-metric-value.success { color: #065f46; }
        .profile-metric-value.warning { color: #92400e; }
        .profile-metric-value.danger { color: #991b1b; }
        .legend-container {
            display: flex;
            gap: 24px;
            flex-wrap: wrap;
            padding: 12px 0;
            margin-bottom: 16px;
        }
        .legend-item {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.85rem;
            color: #64748b;
        }
        .legend-dot {
            width: 12px;
            height: 12px;
            border-radius: 3px;
        }
        .legend-dot.excellent { background-color: #d1fae5; border: 1px solid #a7f3d0; }
        .legend-dot.attention { background-color: #fef3c7; border: 1px solid #fde68a; }
        .legend-dot.critical { background-color: #fee2e2; border: 1px solid #fecaca; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    dataset_path = st.session_state.get("uploaded_dataset_path")

    if not dataset_path:
        st.warning("Please upload a dataset in the Upload Data tab before running profiling.")
    else:
        st.markdown(
            '<p class="profile-intro">Analyze the dataset for completeness and conformity issues. This identifies structural and rule-based problems used by downstream agents.</p>',
            unsafe_allow_html=True,
        )

        run_clicked = st.button("Run Profiling", type="primary", key="run_profiling_btn")

        if run_clicked:
            import requests

            try:
                backend_url = "http://localhost:8000"

                with st.spinner("Running profiling agents..."):
                    response = requests.post(
                        f"{backend_url}/profiling/profile",
                        json={"dataset_path": dataset_path},
                    )

                    if response.status_code == 200:
                        results = response.json()
                        st.session_state.profile_results = results
                        st.success("Profiling complete.")
                    else:
                        st.error(f"Profiling failed: {response.status_code}")

            except requests.exceptions.ConnectionError:
                st.error("Could not connect to backend. Ensure FastAPI server is running on http://localhost:8000")
            except Exception as e:
                st.error(f"Profiling failed: {e}")

        # Show results if they exist
        if "profile_results" in st.session_state:
            results = st.session_state.profile_results

            # Handle both direct profile object and nested structure
            profile = results.get("dataset_profile", results)
            issues = results.get("issues", [])
            column_profiles = results.get("column_profiles", {})

            # ─────────────────────────────────────────────────────────────
            # Dataset Summary
            # ─────────────────────────────────────────────────────────────
            st.markdown('<p class="profile-section-header">Dataset Summary</p>', unsafe_allow_html=True)

            total_rows = profile.get('total_rows', 0)
            total_cols = profile.get('total_columns', 0)
            issue_count = len(issues)

            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(
                    f"""
                    <div class="profile-metric-card">
                        <div class="profile-metric-value">{total_rows:,}</div>
                        <div class="profile-metric-label">Total Rows</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with col2:
                st.markdown(
                    f"""
                    <div class="profile-metric-card">
                        <div class="profile-metric-value">{total_cols}</div>
                        <div class="profile-metric-label">Total Columns</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with col3:
                issue_class = "danger" if issue_count > 0 else "success"
                st.markdown(
                    f"""
                    <div class="profile-metric-card">
                        <div class="profile-metric-value {issue_class}">{issue_count}</div>
                        <div class="profile-metric-label">Issues Found</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # ─────────────────────────────────────────────────────────────
            # Column Details
            # ─────────────────────────────────────────────────────────────
            st.markdown('<p class="profile-section-header">Column Details</p>', unsafe_allow_html=True)

            # Compact legend
            st.markdown(
                """
                <div class="legend-container">
                    <div class="legend-item">
                        <div class="legend-dot excellent"></div>
                        <span>Excellent (100%)</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-dot attention"></div>
                        <span>Needs Attention (90-99%)</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-dot critical"></div>
                        <span>Critical (&lt;90%)</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            col_df = None

            def highlight_quality(val):
                """Color code based on quality thresholds."""
                if pd.isna(val) or val is None:
                    return "background-color: #f8fafc; color: #94a3b8"
                elif val == 100.0:
                    return "background-color: #d1fae5; color: #065f46"
                elif val >= 90.0:
                    return "background-color: #fef3c7; color: #92400e"
                else:
                    return "background-color: #fee2e2; color: #991b1b"

            # Dict format
            if column_profiles and isinstance(column_profiles, dict):
                column_records = []
                for column_name, metrics in column_profiles.items():
                    if isinstance(metrics, dict):
                        completeness_val = metrics.get("completeness_score", metrics.get("completeness"))
                        if completeness_val is not None and completeness_val <= 1.0:
                            completeness_pct = completeness_val * 100.0
                        else:
                            completeness_pct = completeness_val

                        conformity_val = metrics.get("conformity_score", metrics.get("conformity"))
                        if conformity_val is not None and conformity_val <= 1.0:
                            conformity_pct = conformity_val * 100.0
                        else:
                            conformity_pct = conformity_val

                        column_records.append({
                            "Column Name": column_name,
                            "Completeness": completeness_pct,
                            "Conformity": conformity_pct,
                        })

                if column_records:
                    col_df = pd.DataFrame(column_records)

            # List format fallback
            elif column_profiles and isinstance(column_profiles, list):
                column_records = []
                for cp in column_profiles:
                    name = cp.get("column_name", cp.get("name", ""))

                    completeness_val = cp.get("completeness_score", cp.get("completeness"))
                    if completeness_val is not None and completeness_val <= 1.0:
                        completeness_pct = completeness_val * 100.0
                    else:
                        completeness_pct = completeness_val

                    conformity_val = cp.get("conformity_score", cp.get("conformity"))
                    if conformity_val is not None and conformity_val <= 1.0:
                        conformity_pct = conformity_val * 100.0
                    else:
                        conformity_pct = conformity_val

                    column_records.append({
                        "Column Name": name,
                        "Completeness": completeness_pct,
                        "Conformity": conformity_pct,
                    })

                if column_records:
                    col_df = pd.DataFrame(column_records)

            if col_df is not None:
                styled_df = (
                    col_df.style.applymap(
                        highlight_quality, subset=["Completeness", "Conformity"]
                    )
                    .format({
                        "Completeness": lambda x: f"{x:.1f}%" if pd.notna(x) else "N/A",
                        "Conformity": lambda x: f"{x:.1f}%" if pd.notna(x) else "N/A",
                    })
                )

                st.dataframe(styled_df, use_container_width=True, hide_index=True)

                # Download button
                download_df = col_df.copy()
                download_df["Completeness"] = download_df["Completeness"].apply(
                    lambda x: f"{x:.1f}%" if pd.notna(x) else "N/A"
                )
                download_df["Conformity"] = download_df["Conformity"].apply(
                    lambda x: f"{x:.1f}%" if pd.notna(x) else "N/A"
                )
                csv = download_df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="Download Column Details",
                    data=csv,
                    file_name="column_details.csv",
                    mime="text/csv",
                )
            else:
                st.info("No column-level details were returned from the profiler.")

            # ─────────────────────────────────────────────────────────────
            # Issues
            # ─────────────────────────────────────────────────────────────
            if issues:
                st.markdown('<p class="profile-section-header">Issues</p>', unsafe_allow_html=True)

                issues_df = pd.DataFrame(issues)

                # Select and rename relevant columns if they exist
                display_columns = []
                column_mapping = {}

                if "column_name" in issues_df.columns:
                    display_columns.append("column_name")
                    column_mapping["column_name"] = "Column"
                if "issue_type" in issues_df.columns:
                    display_columns.append("issue_type")
                    column_mapping["issue_type"] = "Issue Type"
                if "count" in issues_df.columns:
                    display_columns.append("count")
                    column_mapping["count"] = "Affected Values"
                if "percentage" in issues_df.columns:
                    display_columns.append("percentage")
                    column_mapping["percentage"] = "Percentage"

                if display_columns:
                    display_df = issues_df[display_columns].copy()
                    display_df = display_df.rename(columns=column_mapping)

                    # Format percentage
                    if "Percentage" in display_df.columns:
                        display_df["Percentage"] = display_df["Percentage"].apply(
                            lambda x: f"{x:.1f}%" if pd.notna(x) else "N/A"
                        )

                    # Format issue type
                    if "Issue Type" in display_df.columns:
                        display_df["Issue Type"] = display_df["Issue Type"].str.title()

                    st.dataframe(display_df.head(20), use_container_width=True, hide_index=True)

                    if len(issues_df) > 20:
                        st.caption(f"Showing 20 of {len(issues_df)} issues")

                    # Download all issues
                    csv = issues_df.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        label="Download All Issues",
                        data=csv,
                        file_name="profiling_issues.csv",
                        mime="text/csv",
                    )
                else:
                    st.dataframe(issues_df.head(20), use_container_width=True)
            else:
                st.info("No issues were found during profiling.")

            # ─────────────────────────────────────────────────────────────
            # Next Button
            # ─────────────────────────────────────────────────────────────
            st.markdown("---")
            col1, col2, col3 = st.columns([2, 1, 1])
            with col3:
                if st.button("Next: Anomaly Detection", type="primary", key="profiling_next_btn"):
                    st.session_state["navigate_to_tab"] = 2
                    st.rerun()

            # Handle navigation
            if st.session_state.get("navigate_to_tab") == 2:
                switch_tab(2)
                st.session_state["navigate_to_tab"] = None


# -------------------------------------------------------------------
# TAB 3 — ANOMALY DETECTION
# -------------------------------------------------------------------
with tabs[2]:
    st.title("Anomaly Detection")

    st.markdown(
        """
        <p style="color: #64748b; font-size: 0.95rem; margin-bottom: 24px;">
        Compare the current profiling run against historical baselines to detect statistical anomalies and distribution shifts.
        </p>
        """,
        unsafe_allow_html=True,
    )

    if "profile_results" not in st.session_state:
        st.warning("Please run profiling first before detecting anomalies.")
    else:
        # Configuration section
        st.markdown("### Detection Settings")

        col1, col2, col3 = st.columns(3)

        with col1:
            min_history = st.number_input(
                "Min History Points",
                min_value=2,
                max_value=10,
                value=3,
                help="Minimum number of historical runs needed for detection",
            )

        with col2:
            delta_threshold = st.slider(
                "Delta Threshold (%)",
                min_value=1.0,
                max_value=30.0,
                value=10.0,
                step=1.0,
                help="Percentage change that triggers an anomaly",
            )

        with col3:
            zscore_threshold = st.slider(
                "Z-Score Threshold",
                min_value=1.0,
                max_value=5.0,
                value=2.5,
                step=0.1,
                help="Statistical significance threshold (std deviations)",
            )

        flag_degradations_only = st.checkbox(
            "Flag only degradations (ignore improvements)",
            value=True,
            help="When checked, only quality decreases trigger anomalies",
        )

        st.markdown("---")

        # Run anomaly detection button
        run_anomaly_clicked = st.button("Run Anomaly Detection", type="primary", key="run_anomaly_btn")

        if run_anomaly_clicked:
            import requests

            try:
                backend_url = "http://localhost:8000"
                dataset_path = st.session_state.get("uploaded_dataset_path")

                with st.spinner("Running anomaly detection against historical baselines..."):
                    response = requests.post(
                        f"{backend_url}/anomaly/detect",
                        json={
                            "dataset_path": dataset_path,
                            "min_history_points": min_history,
                            "delta_threshold": delta_threshold,
                            "zscore_threshold": zscore_threshold,
                            "flag_only_degradations": flag_degradations_only,
                        },
                    )

                    if response.status_code == 200:
                        results = response.json()
                        st.session_state.anomaly_results = results

                        if results.get("status") == "skipped":
                            st.warning(results.get("message"))
                        elif results.get("status") == "error":
                            st.error(results.get("message"))
                        else:
                            st.success("Anomaly detection complete.")
                    else:
                        st.error(f"Anomaly detection failed: {response.status_code}")

            except requests.exceptions.ConnectionError:
                st.error("Could not connect to backend. Make sure your FastAPI server is running on http://localhost:8000")
            except Exception as e:
                st.error(f"Anomaly detection failed: {e}")

        # Display results if available
        if "anomaly_results" in st.session_state:
            results = st.session_state.anomaly_results

            if results.get("status") == "skipped":
                st.info("Anomaly detection was skipped. You need at least 3 historical runs in DuckDB.")
            else:
                anomalies = results.get("anomalies", [])

                # Detection Summary
                st.markdown("### Detection Summary")

                cols = st.columns(4)
                cols[0].metric("Total Anomalies", len(anomalies))

                high_count = sum(1 for a in anomalies if a.get("severity") == "high")
                medium_count = sum(1 for a in anomalies if a.get("severity") == "medium")
                low_count = sum(1 for a in anomalies if a.get("severity") == "low")

                cols[1].metric("High Severity", high_count)
                cols[2].metric("Medium Severity", medium_count)
                cols[3].metric("Low Severity", low_count)

                # Anomalies table
                if anomalies:
                    st.markdown("### Detected Anomalies")

                    anomaly_records = []
                    for anomaly in anomalies:
                        anomaly_records.append({
                            "Column": anomaly.get("column_name", "N/A"),
                            "Metric": anomaly.get("metric", "N/A"),
                            "Current Value": f"{anomaly.get('current_value', 0):.2f}%",
                            "Baseline": f"{anomaly.get('baseline_value', 0):.2f}%",
                            "Delta": f"{anomaly.get('delta', 0):.2f}%",
                            "Z-Score": f"{anomaly.get('z_score', 0):.2f}",
                            "Severity": anomaly.get("severity", "unknown").upper(),
                        })

                    anomaly_df = pd.DataFrame(anomaly_records)
                    st.dataframe(anomaly_df, use_container_width=True, hide_index=True)
                else:
                    st.success("No anomalies detected. Your data quality is consistent with historical baselines.")

        # Next button
        st.markdown("---")
        col1, col2, col3 = st.columns([2, 1, 1])
        with col3:
            if st.button("Next: Recommendations", type="primary", key="anomaly_next_btn"):
                st.session_state["navigate_to_tab"] = 3
                st.rerun()

        # Handle navigation
        if st.session_state.get("navigate_to_tab") == 3:
            switch_tab(3)
            st.session_state["navigate_to_tab"] = None


# -------------------------------------------------------------------
# TAB 4 — RECOMMENDATIONS
# -------------------------------------------------------------------
with tabs[3]:
    st.title("Fix Recommendations")

    # Custom CSS
    st.markdown(
        """
        <style>
        .section-header {
            font-size: 1.15rem;
            font-weight: 600;
            color: #1e293b;
            margin-bottom: 16px;
            margin-top: 8px;
            padding-bottom: 8px;
            border-bottom: 1px solid #e2e8f0;
        }
        .intro-text {
            color: #64748b;
            font-size: 0.95rem;
            margin-bottom: 24px;
        }
        .stat-box {
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 16px;
            text-align: center;
        }
        .stat-value {
            font-size: 1.5rem;
            font-weight: 700;
            color: #1e3a5f;
        }
        .stat-label {
            font-size: 0.8rem;
            color: #64748b;
            margin-top: 4px;
        }
        .rec-card {
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 16px;
            background-color: #ffffff;
        }
        .rec-card:hover {
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        .rec-title {
            font-weight: 600;
            font-size: 1.1rem;
            color: #1e293b;
        }
        .rec-subtitle {
            font-size: 0.85rem;
            color: #64748b;
            margin-top: 2px;
        }
        .badge {
            padding: 4px 12px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 500;
        }
        .badge-high { background-color: #fee2e2; color: #991b1b; }
        .badge-medium { background-color: #fef3c7; color: #92400e; }
        .badge-low { background-color: #f1f5f9; color: #475569; }
        .badge-auto { background-color: #d1fae5; color: #065f46; }
        .badge-review { background-color: #f1f5f9; color: #475569; }
        .confidence-box {
            text-align: center;
            padding: 12px 16px;
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            min-width: 90px;
        }
        .confidence-label { font-size: 0.75rem; color: #64748b; }
        .confidence-value { font-size: 1.4rem; font-weight: 700; color: #1e3a5f; }
        .confidence-high { color: #065f46; }
        .confidence-medium { color: #1e3a5f; }
        .confidence-low { color: #64748b; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<p class="intro-text">Review recommendations for fixing data quality issues. Each recommendation includes a confidence score and indicates whether it can be auto-fixed.</p>',
        unsafe_allow_html=True,
    )

    if "profile_results" not in st.session_state:
        st.warning("Please run profiling first before generating recommendations.")
    else:
        # Generate button
        generate_clicked = st.button("Generate Recommendations", type="primary", use_container_width=True, key="gen_rec_btn")

        if generate_clicked:
            import requests

            try:
                backend_url = "http://localhost:8000"
                dataset_path = st.session_state.get("uploaded_dataset_path")

                with st.spinner("Analyzing issues and generating recommendations..."):
                    response = requests.post(
                        f"{backend_url}/recommendations/generate",
                        json={"dataset_path": dataset_path},
                    )

                    if response.status_code == 200:
                        results = response.json()
                        st.session_state.recommendation_results = results

                        rec_count = len(results.get("recommendations", []))
                        if rec_count > 0:
                            st.success(f"Generated {rec_count} recommendations.")
                        else:
                            st.info("No issues found. Your data quality meets the required standards.")
                    else:
                        st.error(f"Failed to generate recommendations: {response.status_code}")

            except requests.exceptions.ConnectionError:
                st.error("Could not connect to backend. Please ensure FastAPI is running.")
            except Exception as e:
                st.error(f"An error occurred: {e}")

        # Display recommendations
        if "recommendation_results" in st.session_state:
            results = st.session_state.recommendation_results
            recommendations = results.get("recommendations", [])

            if recommendations:
                st.markdown("---")

                # Statistics row
                total_recs = len(recommendations)
                auto_fixable = sum(1 for r in recommendations if r.get("actionable", False))
                manual_review = total_recs - auto_fixable
                high_impact = sum(1 for r in recommendations if r.get("estimated_impact") == "high")

                stat1, stat2, stat3, stat4 = st.columns(4)
                with stat1:
                    st.markdown(f'<div class="stat-box"><div class="stat-value">{total_recs}</div><div class="stat-label">Total Recommendations</div></div>', unsafe_allow_html=True)
                with stat2:
                    st.markdown(f'<div class="stat-box"><div class="stat-value" style="color: #065f46;">{auto_fixable}</div><div class="stat-label">Auto-Fixable</div></div>', unsafe_allow_html=True)
                with stat3:
                    st.markdown(f'<div class="stat-box"><div class="stat-value" style="color: #64748b;">{manual_review}</div><div class="stat-label">Manual Review</div></div>', unsafe_allow_html=True)
                with stat4:
                    st.markdown(f'<div class="stat-box"><div class="stat-value" style="color: #991b1b;">{high_impact}</div><div class="stat-label">High Impact</div></div>', unsafe_allow_html=True)

                st.markdown("<br>", unsafe_allow_html=True)

                # Filters
                st.markdown('<p class="section-header">Filters</p>', unsafe_allow_html=True)

                col1, col2, col3 = st.columns([2, 2, 1])

                with col1:
                    issue_types = list(set(r.get("issue_type", "unknown") for r in recommendations))
                    issue_type_filter = st.multiselect(
                        "Issue Type",
                        options=issue_types,
                        default=issue_types,
                        key="rec_issue_filter"
                    )

                with col2:
                    impact_options = ["high", "medium", "low"]
                    impact_filter = st.multiselect(
                        "Impact Level",
                        options=impact_options,
                        default=impact_options,
                        key="rec_impact_filter"
                    )

                with col3:
                    actionable_filter = st.selectbox(
                        "Fixable",
                        options=["All", "Auto-Fixable", "Manual Review"],
                        key="rec_actionable_filter"
                    )

                # Apply filters
                filtered_recs = [
                    r for r in recommendations
                    if r.get("estimated_impact") in impact_filter
                    and r.get("issue_type") in issue_type_filter
                ]

                if actionable_filter == "Auto-Fixable":
                    filtered_recs = [r for r in filtered_recs if r.get("actionable", False)]
                elif actionable_filter == "Manual Review":
                    filtered_recs = [r for r in filtered_recs if not r.get("actionable", False)]

                st.caption(f"Showing {len(filtered_recs)} of {len(recommendations)} recommendations")

                st.markdown("<br>", unsafe_allow_html=True)

                # Recommendation cards
                st.markdown('<p class="section-header">Recommendations</p>', unsafe_allow_html=True)

                for i, rec in enumerate(filtered_recs):
                    impact = rec.get("estimated_impact", "medium")
                    actionable = rec.get("actionable", False)
                    confidence = rec.get("confidence_score", 0.5)
                    issue_type = rec.get("issue_type", "unknown")

                    impact_class = f"badge-{impact}"
                    action_class = "badge-auto" if actionable else "badge-review"
                    action_label = "Auto-Fix" if actionable else "Review"

                    if confidence >= 0.8:
                        conf_class = "confidence-high"
                    elif confidence >= 0.6:
                        conf_class = "confidence-medium"
                    else:
                        conf_class = "confidence-low"

                    st.markdown(
                        f"""
                        <div class="rec-card">
                            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
                                <div>
                                    <div class="rec-title">{rec.get('column_name', 'Unknown')}</div>
                                    <div class="rec-subtitle">{issue_type.replace('_', ' ').title()} Issue</div>
                                </div>
                                <div style="display: flex; gap: 8px;">
                                    <span class="badge {impact_class}">{impact.upper()}</span>
                                    <span class="badge {action_class}">{action_label}</span>
                                </div>
                            </div>
                            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                                <div style="flex: 1;">
                                    <div style="font-size: 0.9rem; color: #1e293b; margin-bottom: 4px;"><strong>Strategy:</strong> {rec.get('fix_strategy', 'N/A').replace('_', ' ').title()}</div>
                                    <div style="font-size: 0.9rem; color: #475569; margin-bottom: 8px;"><strong>Action:</strong> {rec.get('fix_description', 'N/A')}</div>
                                    <div style="font-size: 0.85rem; color: #64748b; font-style: italic;">{rec.get('rationale', '') or 'No additional details'}</div>
                                </div>
                                <div class="confidence-box">
                                    <div class="confidence-label">Confidence</div>
                                    <div class="confidence-value {conf_class}">{confidence:.0%}</div>
                                </div>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                # Next button
                st.markdown("---")
                col1, col2, col3 = st.columns([2, 1, 1])
                with col3:
                    if st.button("Next: Execute Fixes", type="primary", key="rec_next_btn"):
                        st.session_state["navigate_to_tab"] = 4
                        st.rerun()

                # Handle navigation
                if st.session_state.get("navigate_to_tab") == 4:
                    switch_tab(4)
                    st.session_state["navigate_to_tab"] = None


# -------------------------------------------------------------------
# TAB 5 — EXECUTE FIXES
# -------------------------------------------------------------------
with tabs[4]:
    st.title("Execute Fixes")

    # Custom CSS
    st.markdown(
        """
        <style>
        .summary-bar-container {
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 16px 20px;
            margin-bottom: 16px;
        }
        .metric-box {
            text-align: center;
            padding: 8px 20px;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            background-color: #ffffff;
            min-width: 85px;
        }
        .metric-value { font-size: 1.4rem; font-weight: 700; }
        .metric-value.accepted { color: #059669; }
        .metric-value.rejected { color: #dc2626; }
        .metric-value.pending { color: #6b7280; }
        .metric-label { font-size: 0.75rem; color: #64748b; text-transform: uppercase; }
        .recommendation-card {
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 12px;
        }
        .card-accepted { background-color: #f0fdf4; border-color: #86efac; }
        .card-rejected { background-color: #fafafa; border-color: #d4d4d4; }
        .card-pending { background-color: #ffffff; border-color: #e2e8f0; }
        .status-badge { display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 0.8rem; font-weight: 500; }
        .status-accepted { background-color: #d1fae5; color: #065f46; }
        .status-rejected { background-color: #fee2e2; color: #991b1b; }
        .status-pending { background-color: #f3f4f6; color: #4b5563; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if "recommendation_results" not in st.session_state:
        st.warning("Please generate recommendations first in the Recommendations tab.")
    else:
        results = st.session_state.recommendation_results
        recommendations = results.get("recommendations", [])

        if not recommendations:
            st.info("No recommendations to review. Your data quality meets the required standards.")
        else:
            # Initialize decisions state
            if "recommendation_decisions" not in st.session_state:
                st.session_state.recommendation_decisions = {
                    rec.get("recommendation_id", f"rec_{i}"): "pending"
                    for i, rec in enumerate(recommendations)
                }

            decisions = st.session_state.recommendation_decisions
            accepted_count = sum(1 for v in decisions.values() if v == "accepted")
            rejected_count = sum(1 for v in decisions.values() if v == "rejected")
            pending_count = sum(1 for v in decisions.values() if v == "pending")

            total = len(recommendations)
            reviewed = accepted_count + rejected_count
            progress_pct = (reviewed / total * 100) if total > 0 else 0

            # Summary bar
            st.markdown(
                f"""
                <div class="summary-bar-container">
                    <div style="display: flex; align-items: center; justify-content: space-between; gap: 24px; flex-wrap: wrap;">
                        <div style="flex: 1; min-width: 200px; max-width: 400px;">
                            <div style="font-size: 0.85rem; color: #475569; margin-bottom: 8px;">Reviewed {reviewed} of {total} recommendations</div>
                            <div style="background-color: #e2e8f0; border-radius: 4px; height: 8px; overflow: hidden;">
                                <div style="background: linear-gradient(90deg, #3b82f6 0%, #2563eb 100%); height: 100%; width: {progress_pct}%;"></div>
                            </div>
                        </div>
                        <div style="display: flex; gap: 12px;">
                            <div class="metric-box">
                                <div class="metric-value accepted">{accepted_count}</div>
                                <div class="metric-label">Accepted</div>
                            </div>
                            <div class="metric-box">
                                <div class="metric-value rejected">{rejected_count}</div>
                                <div class="metric-label">Rejected</div>
                            </div>
                            <div class="metric-box">
                                <div class="metric-value pending">{pending_count}</div>
                                <div class="metric-label">Pending</div>
                            </div>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Bulk actions
            col1, col2, col3, col4 = st.columns([1, 1, 1, 3])
            with col1:
                if st.button("Accept All", use_container_width=True, key="accept_all_btn"):
                    for rec_id in st.session_state.recommendation_decisions:
                        st.session_state.recommendation_decisions[rec_id] = "accepted"
                    st.rerun()
            with col2:
                if st.button("Reject All", use_container_width=True, key="reject_all_btn"):
                    for rec_id in st.session_state.recommendation_decisions:
                        st.session_state.recommendation_decisions[rec_id] = "rejected"
                    st.rerun()
            with col3:
                if st.button("Reset All", use_container_width=True, key="reset_all_btn"):
                    for rec_id in st.session_state.recommendation_decisions:
                        st.session_state.recommendation_decisions[rec_id] = "pending"
                    st.rerun()

            st.markdown("")

            # Scrollable recommendations
            with st.container(height=450):
                for i, rec in enumerate(recommendations):
                    rec_id = rec.get("recommendation_id", f"rec_{i}")
                    current_status = st.session_state.recommendation_decisions.get(rec_id, "pending")
                    issue_type = rec.get("issue_type", "unknown")
                    confidence = rec.get("confidence_score", 0.5)

                    if current_status == "accepted":
                        card_class = "card-accepted"
                        status_class = "status-accepted"
                        status_text = "Accepted"
                    elif current_status == "rejected":
                        card_class = "card-rejected"
                        status_class = "status-rejected"
                        status_text = "Rejected"
                    else:
                        card_class = "card-pending"
                        status_class = "status-pending"
                        status_text = "Pending Review"

                    st.markdown(
                        f"""
                        <div class="recommendation-card {card_class}">
                            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                                <div>
                                    <span style="font-weight: 600; color: #1e293b; font-size: 1rem;">{rec.get('column_name', 'Unknown Column')}</span>
                                    <span style="color: #64748b; font-size: 0.85rem; margin-left: 8px;">| {issue_type.replace('_', ' ').title()}</span>
                                </div>
                                <div style="display: flex; align-items: center; gap: 16px;">
                                    <span style="color: #475569; font-size: 0.85rem;">Confidence: {confidence:.0%}</span>
                                    <span class="status-badge {status_class}">{status_text}</span>
                                </div>
                            </div>
                            <div style="color: #475569; font-size: 0.9rem; margin-top: 10px;">{rec.get('fix_description', 'No description available')}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    col1, col2, col3 = st.columns([1, 1, 6])
                    with col1:
                        if st.button("Accept", key=f"acc_{rec_id}", use_container_width=True, disabled=current_status == "accepted"):
                            st.session_state.recommendation_decisions[rec_id] = "accepted"
                            st.rerun()
                    with col2:
                        if st.button("Reject", key=f"rej_{rec_id}", use_container_width=True, disabled=current_status == "rejected"):
                            st.session_state.recommendation_decisions[rec_id] = "rejected"
                            st.rerun()

            # Execute section
            st.markdown("<br>", unsafe_allow_html=True)

            accepted_recs = [
                rec for i, rec in enumerate(recommendations)
                if st.session_state.recommendation_decisions.get(rec.get("recommendation_id", f"rec_{i}"), "pending") == "accepted"
            ]

            if not accepted_recs:
                st.info("Select at least one recommendation to continue.")
            else:
                st.markdown(f"**{len(accepted_recs)}** fix(es) ready to apply")

                if st.button("Execute Fixes", type="primary", use_container_width=True, key="execute_fixes_btn"):
                    import requests
                    from datetime import datetime

                    try:
                        backend_url = "http://localhost:8000"
                        dataset_path = st.session_state.get("uploaded_dataset_path")

                        original_name = Path(dataset_path).stem if dataset_path else "data"
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
                        st.session_state.output_filename = f"{original_name}_fixed_{timestamp}"

                        with st.spinner("Applying fixes to dataset..."):
                            response = requests.post(
                                f"{backend_url}/fixes/apply",
                                json={
                                    "dataset_path": dataset_path,
                                    "accepted_recommendations": accepted_recs
                                },
                            )

                            if response.status_code == 200:
                                fix_results = response.json()
                                st.session_state.fix_results = fix_results
                                st.success("Fixes applied successfully.")
                            else:
                                st.error(f"Operation failed: {response.status_code}")

                    except requests.exceptions.ConnectionError:
                        st.error("Unable to connect to backend service.")
                    except Exception as e:
                        st.error(f"An error occurred: {e}")

            # Results & Download
            if "fix_results" in st.session_state:
                fix_results = st.session_state.fix_results

                st.markdown("---")

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Rows Modified", fix_results.get("total_rows_fixed", 0))
                with col2:
                    st.metric("Total Rows", fix_results.get("row_count", 0))
                with col3:
                    st.metric("Fixes Applied", len(fix_results.get("fixes_applied", [])))

                fixed_file_path = fix_results.get("fixed_file_path", "")

                if fixed_file_path:
                    try:
                        fixed_df = pd.read_csv(fixed_file_path)
                        csv_data = fixed_df.to_csv(index=False).encode("utf-8")
                        output_filename = st.session_state.get("output_filename", "fixed_data")

                        st.download_button(
                            label="Download Fixed Dataset",
                            data=csv_data,
                            file_name=f"{output_filename}.csv",
                            mime="text/csv",
                            use_container_width=True
                        )
                    except Exception as e:
                        st.error(f"Unable to prepare download: {e}")

                # Next button to Executive Summary
                st.markdown("---")
                col1, col2, col3 = st.columns([2, 1, 1])
                with col3:
                    if st.button("Next: Executive Summary", type="primary", key="exec_next_btn"):
                        st.session_state["navigate_to_tab"] = 5
                        st.rerun()

                if st.session_state.get("navigate_to_tab") == 5:
                    switch_tab(5)
                    st.session_state["navigate_to_tab"] = None


# -------------------------------------------------------------------
# TAB 6 — EXECUTIVE SUMMARY
# -------------------------------------------------------------------
with tabs[5]:
    st.title("Executive Summary")

    st.markdown(
        """
        <style>
        .exec-section-header {
            font-size: 1.1rem;
            font-weight: 600;
            color: #1e293b;
            margin-bottom: 16px;
            margin-top: 24px;
            padding-bottom: 8px;
            border-bottom: 1px solid #e2e8f0;
        }
        .exec-intro {
            color: #64748b;
            font-size: 0.95rem;
            margin-bottom: 24px;
        }
        .metric-card {
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
        }
        .metric-value {
            font-size: 2rem;
            font-weight: 700;
            color: #1e3a5f;
        }
        .metric-label {
            font-size: 0.85rem;
            color: #64748b;
            margin-top: 4px;
        }
        .metric-value.success { color: #065f46; }
        .metric-value.warning { color: #92400e; }
        .metric-value.danger { color: #991b1b; }
        .metric-value.info { color: #1e40af; }
        .log-entry {
            padding: 12px 16px;
            border-left: 3px solid #e2e8f0;
            margin-bottom: 8px;
            background-color: #f8fafc;
            border-radius: 0 6px 6px 0;
        }
        .log-entry.success { border-left-color: #10b981; }
        .log-entry.warning { border-left-color: #f59e0b; }
        .log-entry.info { border-left-color: #3b82f6; }
        .log-entry.error { border-left-color: #ef4444; }
        .log-timestamp { font-size: 0.75rem; color: #94a3b8; margin-bottom: 4px; }
        .log-message { font-size: 0.9rem; color: #334155; }
        .log-details { font-size: 0.8rem; color: #64748b; margin-top: 4px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<p class="exec-intro">Overview of data quality analysis, recommendations, and fixes applied to your dataset.</p>', unsafe_allow_html=True)

    has_profile = "profile_results" in st.session_state
    has_recommendations = "recommendation_results" in st.session_state
    has_fixes = "fix_results" in st.session_state

    if not has_profile:
        st.info("Run data profiling to generate the executive summary.")
    else:
        from datetime import datetime

        profile_results = st.session_state.profile_results
        dataset_profile = profile_results.get("dataset_profile", {})
        issues = profile_results.get("issues", [])
        column_profiles = profile_results.get("column_profiles", {})

        # Get dataset info
        dataset_name = dataset_profile.get("dataset_name") or profile_results.get("dataset_name") or "Unknown"
        row_count = dataset_profile.get("total_rows") or profile_results.get("total_rows") or 0
        column_count = dataset_profile.get("total_columns") or profile_results.get("total_columns") or len(column_profiles) or 0

        # Dataset Overview
        st.markdown('<p class="exec-section-header">Dataset Overview</p>', unsafe_allow_html=True)

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f'<div class="metric-card"><div class="metric-value info">{row_count:,}</div><div class="metric-label">Total Rows</div></div>', unsafe_allow_html=True)
        with col2:
            st.markdown(f'<div class="metric-card"><div class="metric-value info">{column_count}</div><div class="metric-label">Total Columns</div></div>', unsafe_allow_html=True)
        with col3:
            st.markdown(f'<div class="metric-card"><div class="metric-value danger">{len(issues)}</div><div class="metric-label">Issues Found</div></div>', unsafe_allow_html=True)
        with col4:
            if column_profiles:
                avg_completeness = sum(cp.get("completeness_score", 100) for cp in column_profiles.values()) / len(column_profiles)
            else:
                avg_completeness = 100
            quality_class = "success" if avg_completeness >= 90 else ("warning" if avg_completeness >= 70 else "danger")
            st.markdown(f'<div class="metric-card"><div class="metric-value {quality_class}">{avg_completeness:.1f}%</div><div class="metric-label">Avg. Completeness</div></div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        info_col1, info_col2 = st.columns(2)
        with info_col1:
            st.markdown(f"**Dataset:** {dataset_name}")
        with info_col2:
            profile_timestamp = profile_results.get("execution_metadata", {}).get("timestamp", "N/A")
            st.markdown(f"**Profiled at:** {profile_timestamp}")

        # Issues Summary
        if issues:
            st.markdown('<p class="exec-section-header">Issues Summary</p>', unsafe_allow_html=True)

            issue_counts = {}
            for issue in issues:
                issue_type = issue.get("issue_type", "unknown")
                issue_counts[issue_type] = issue_counts.get(issue_type, 0) + 1

            issue_cols = st.columns(len(issue_counts))
            type_colors = {"completeness": "warning", "conformity": "danger", "uniqueness": "info", "anomaly": "danger"}
            for idx, (issue_type, count) in enumerate(issue_counts.items()):
                with issue_cols[idx]:
                    color_class = type_colors.get(issue_type, "info")
                    st.markdown(f'<div class="metric-card"><div class="metric-value {color_class}">{count}</div><div class="metric-label">{issue_type.title()} Issues</div></div>', unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            issues_df = pd.DataFrame([{
                "Column": issue.get("column_name", "N/A"),
                "Issue Type": issue.get("issue_type", "N/A").title(),
                "Affected Values": issue.get("count", 0),
                "Percentage": f"{issue.get('percentage', 0):.1f}%"
            } for issue in issues[:10]])

            st.dataframe(issues_df, use_container_width=True, hide_index=True)

            if len(issues) > 10:
                st.caption(f"Showing 10 of {len(issues)} issues")

        # Recommendations & Fixes
        if has_recommendations or has_fixes:
            st.markdown('<p class="exec-section-header">Recommendations & Fixes</p>', unsafe_allow_html=True)

            total_recs = 0
            total_fixes = 0

            if has_recommendations:
                rec_results = st.session_state.recommendation_results
                recommendations = rec_results.get("recommendations", [])
                total_recs = len(recommendations)

            if has_fixes:
                fix_results = st.session_state.fix_results
                fixes_applied = fix_results.get("fixes_applied", [])
                total_fixes = len(fixes_applied)

            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f'<div class="metric-card"><div class="metric-value info">{total_recs}</div><div class="metric-label">Recommendations Generated</div></div>', unsafe_allow_html=True)
            with col2:
                st.markdown(f'<div class="metric-card"><div class="metric-value success">{total_fixes}</div><div class="metric-label">Fixes Applied</div></div>', unsafe_allow_html=True)

        # Activity Log
        st.markdown('<p class="exec-section-header">Activity Log</p>', unsafe_allow_html=True)

        activity_log = []

        if has_profile:
            profile_meta = profile_results.get("execution_metadata", {})
            profile_time = profile_meta.get("timestamp", "N/A")
            profile_duration = profile_meta.get("execution_time_seconds", 0)
            activity_log.append({
                "timestamp": profile_time,
                "type": "info",
                "message": "Data Profiling Completed",
                "details": f"Analyzed {column_count} columns, found {len(issues)} issues in {profile_duration:.2f}s"
            })

        if "anomaly_results" in st.session_state:
            anomaly_results = st.session_state.anomaly_results
            anomaly_meta = anomaly_results.get("execution_metadata", {})
            anomaly_time = anomaly_meta.get("timestamp", "N/A")
            anomaly_count = len(anomaly_results.get("anomalies", []))
            activity_log.append({
                "timestamp": anomaly_time,
                "type": "warning" if anomaly_count > 0 else "success",
                "message": "Anomaly Detection Completed",
                "details": f"Detected {anomaly_count} anomalies"
            })

        if has_recommendations:
            rec_results = st.session_state.recommendation_results
            rec_meta = rec_results.get("execution_metadata", {})
            rec_time = rec_meta.get("timestamp", "N/A")
            rec_duration = rec_meta.get("execution_time_seconds", 0)
            recs_list = rec_results.get("recommendations", [])
            total_recs_log = len(recs_list)
            auto_fixable_log = sum(1 for r in recs_list if r.get("actionable", False))
            activity_log.append({
                "timestamp": rec_time,
                "type": "info",
                "message": "Recommendations Generated",
                "details": f"Generated {total_recs_log} recommendations ({auto_fixable_log} auto-fixable) in {rec_duration:.2f}s"
            })

        if has_fixes:
            fix_results = st.session_state.fix_results
            fixes_list = fix_results.get("fixes_applied", [])
            fix_timestamp = fix_results.get("timestamp", "N/A")
            successful = sum(1 for f in fixes_list if f.get("status") == "success")
            failed = sum(1 for f in fixes_list if f.get("status") == "failed")
            skipped = sum(1 for f in fixes_list if f.get("status") == "skipped")
            activity_log.append({
                "timestamp": fix_timestamp,
                "type": "success" if failed == 0 else "warning",
                "message": "Fixes Executed",
                "details": f"Applied {successful} fixes, {failed} failed, {skipped} skipped"
            })

        activity_log.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        if activity_log:
            for entry in activity_log:
                log_type = entry.get("type", "info")
                st.markdown(
                    f"""
                    <div class="log-entry {log_type}">
                        <div class="log-timestamp">{entry.get('timestamp', 'N/A')}</div>
                        <div class="log-message">{entry.get('message', '')}</div>
                        <div class="log-details">{entry.get('details', '')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.markdown('<p style="color: #64748b; font-style: italic;">No activity recorded yet.</p>', unsafe_allow_html=True)