"""ScholarLens Streamlit Frontend.

Provides user interface to explore verified Indian government scholarships,
view configured sources and their verification status, trigger polite ingestion,
and inspect recent fetch telemetry.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import requests
import streamlit as st

# Ensure repository root is on sys.path regardless of execution directory
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from frontend.helpers import (
    categorize_match_results,
    evaluate_student_matching,
    filter_match_results,
    format_amount_display,
    format_application_status_display,
    format_deadline_display,
    humanize_criterion_type,
    validate_and_build_profile,
)
from frontend.styles import (
    inject_custom_styles,
    render_hero_section,
    render_kpi_dashboard,
)

# Configure page
st.set_page_config(
    page_title="ScholarLens | Verified Indian Scholarships",
    layout="wide",
)

# Inject centralized styling system
inject_custom_styles()

# Executive Hero Section
render_hero_section()

# Configuration
BACKEND_URL = os.getenv("BACKEND_API_URL", "http://127.0.0.1:8000").rstrip("/")


def fetch_backend_data(endpoint: str) -> Optional[Any]:
    """Fetch data from FastAPI backend with fallback to direct database access."""
    try:
        res = requests.get(f"{BACKEND_URL}{endpoint}", timeout=3.0)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass

    # Direct database fallback if backend service is not running
    try:
        from backend.app.db.database import get_db
        from backend.app.ingestion.registry import SourceRegistry

        db = get_db()
        registry = SourceRegistry(db=db)

        if endpoint.startswith("/api/sources") or endpoint == "/sources":
            sources = registry.get_all_sources()
            return [s.model_dump(mode="json") for s in sources]
        elif endpoint.startswith("/api/scholarships") or endpoint == "/scholarships":
            records = db.get_scholarship_records(limit=200)
            return [r.model_dump(mode="json") for r in records]
        elif endpoint.startswith("/api/ingestion/runs"):
            runs = db.get_recent_ingestion_runs(limit=50)
            return [r.model_dump(mode="json") for r in runs]
    except Exception:
        pass

    return None


def trigger_ingestion(source_id: Optional[str] = None) -> Dict[str, Any]:
    """Trigger ingestion via backend API with direct pipeline fallback."""
    payload = {"source_id": source_id} if source_id else {}
    try:
        res = requests.post(f"{BACKEND_URL}/api/ingest", json=payload, timeout=60.0)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass

    # Direct pipeline execution fallback
    try:
        from backend.app.db.database import get_db
        from backend.app.ingestion.pipeline import IngestionPipeline

        db = get_db()
        pipeline = IngestionPipeline(db=db)
        if source_id:
            result = pipeline.ingest_source(source_id)
            return {"summary": "Single source ingestion executed locally", "results": [result]}
        else:
            results = pipeline.ingest_all_enabled_sources()
            return {"summary": f"Ingested {len(results)} source(s) locally", "results": results}
    except Exception as e:
        return {"error": f"Failed to execute ingestion: {str(e)}"}


# =============================================================================
# Sidebar: Platform Information & Ingestion Controls
# =============================================================================

sources_data = fetch_backend_data("/api/sources") or []

st.sidebar.markdown(
    """
    <div class="sl-sidebar-brand">
        <h2>ScholarLens</h2>
        <p>Scholarships from official Indian sources</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Core Platform Statistics & Overview
st.sidebar.markdown(
    f'<div class="sl-sidebar-stat"><strong>Official sources</strong> · {len(sources_data)} registered</div>'
    '<div class="sl-sidebar-stat"><strong>Evidence</strong> · Published pages and circulars</div>',
    unsafe_allow_html=True,
)

st.sidebar.markdown("---")
st.sidebar.markdown("### How information is checked")
st.sidebar.markdown(
    """
    <div class="sl-principle-card">
        <h4>Eligibility</h4>
        <p>Your profile is compared with the criteria recorded for each scheme.</p>
    </div>
    <div class="sl-principle-card">
        <h4>Source evidence</h4>
        <p>Open the linked official page and review the quoted evidence.</p>
    </div>
    <div class="sl-principle-card">
        <h4>Respectful access</h4>
        <p>Only registered sources are checked, following their published access rules.</p>
    </div>
    <div class="sl-principle-card">
        <h4>Verification status</h4>
        <p>Unverified information is labelled and is not shown as confirmed eligibility.</p>
    </div>
    """,
    unsafe_allow_html=True,
)
st.sidebar.caption(
    "*Independent discovery tool. References publicly available government circulars; "
    "not an officially endorsed government portal.*"
)

# Collapsed Admin & Pipeline Controls Expander
with st.sidebar.expander("Admin & Pipeline Controls", expanded=False):
    st.caption("Authorized ingestion pipeline for registered government scholarship portals.")

    source_options = {"All Enabled Sources": None}
    for s in sources_data:
        source_options[f"{s.get('name')} ({s.get('id')})"] = s.get("id")

    selected_source_label = st.selectbox(
        "Select Target Source",
        list(source_options.keys()),
        key="sidebar_target_source",
    )
    selected_source_id = source_options[selected_source_label]

    if st.button("Run polite ingestion", use_container_width=True, key="sidebar_run_ingest"):
        with st.spinner("Executing polite ingestion pipeline (checking robots.txt & official portals)..."):
            ingest_result = trigger_ingestion(selected_source_id)
            if "error" in ingest_result:
                st.error(ingest_result["error"])
            else:
                st.success(ingest_result.get("summary", "Ingestion completed!"))
                st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption("ScholarLens v1.2 • FORGE'26 Hackathon\n\nOfficial Portal Grounding: AICTE • UGC • NSP")


# =============================================================================
# Main Content Tabs
# =============================================================================

tab_matcher, tab_scholarships, tab_sources, tab_history = st.tabs(
    ["Eligibility", "Records", "Sources", "History"]
)

# -----------------------------------------------------------------------------
# Tab 1: Eligibility Matcher (Phase 4)
# -----------------------------------------------------------------------------
with tab_matcher:
    st.subheader("Find scholarships that may fit")
    st.caption("Add the details you want considered. Each result shows which criteria match and what still needs checking.")

    with st.form("student_profile_matching_form"):
        # Section 1: Academic Details
        st.markdown(
            """
            <div class="sl-form-section-header">
                <span class="sl-section-badge">1</span>
                <div>
                    <h4 class="sl-section-title">Academic Details</h4>
                    <p class="sl-section-subtitle">Current educational tier, degree/stream, and qualifying academic marks</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        col_ed, col_course = st.columns(2)
        with col_ed:
            ed_choice = st.selectbox(
                "Current Education Level",
                [
                    "Not Specified",
                    "Undergraduate (Degree / B.Tech / MBBS / B.Sc)",
                    "Class 12 / Higher Secondary (+2)",
                    "Class 10 / Secondary School",
                    "Polytechnic Diploma",
                    "Postgraduate (Master's / M.Tech / MBA)",
                    "Doctorate / Ph.D",
                ],
                help="Select your current enrolled tier or qualifying qualification.",
            )
        with col_course:
            course_input = st.text_input(
                "Degree / Stream / Discipline (Optional)",
                placeholder="e.g. B.Tech Computer Science, MBBS, B.Sc Mathematics",
                help="Specific degree or branch evaluated against course-restricted schemes.",
            )

        col_marks, col_marks_hint = st.columns(2)
        with col_marks:
            marks_num = st.number_input(
                "Academic Marks / Percentage (%)",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                step=0.5,
                help="Aggregate percentage in previous qualifying exam. Leave 0.0 if unspecified.",
            )
        with col_marks_hint:
            st.caption("**Academic eligibility guidance**")
            st.caption("Many government schemes require minimum academic thresholds (e.g., ≥ 50% or 60%). Leave 0.0 to evaluate schemes without marks restrictions.")

        st.markdown("---")

        # Section 2: Personal Details
        st.markdown(
            """
            <div class="sl-form-section-header">
                <span class="sl-section-badge">2</span>
                <div>
                    <h4 class="sl-section-title">Personal Details</h4>
                    <p class="sl-section-subtitle">Demographics, social inclusion categories, disability benchmark, and domicile</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        col_gen, col_cat = st.columns(2)
        with col_gen:
            gender_choice = st.selectbox(
                "Gender",
                ["Not Specified", "Female (Girl Student)", "Male", "Other"],
                help="Select to evaluate schemes reserved for female students (e.g. AICTE Pragati).",
            )
        with col_cat:
            cat_choice = st.selectbox(
                "Social Category",
                [
                    "Not Specified",
                    "General",
                    "OBC (Other Backward Classes)",
                    "SC (Scheduled Caste)",
                    "ST (Scheduled Tribe)",
                    "EWS (Economically Weaker Section)",
                    "Minority Community",
                ],
                help="Select your category to match affirmative action and reserved opportunities.",
            )

        col_state, col_pwd = st.columns(2)
        with col_state:
            state_input = st.text_input(
                "State of Residence / Domicile (Optional)",
                placeholder="e.g. Maharashtra, Uttar Pradesh, Delhi",
                help="State of domicile for state-specific or regional quotas.",
            )
        with col_pwd:
            st.markdown("<div style='font-size: 0.85rem; font-weight: 500; color: #334155; margin-bottom: 0.35rem;'>Inclusion Benchmark</div>", unsafe_allow_html=True)
            is_pwd = st.checkbox(
                "Person with Benchmark Disability (PwD)",
                help="Check if certified with ≥ 40% benchmark disability for inclusion schemes (e.g. AICTE Saksham).",
            )
            st.caption("Qualifies for reserved disability scholarship schemes.")

        st.markdown("---")

        # Section 3: Financial Details
        st.markdown(
            """
            <div class="sl-form-section-header">
                <span class="sl-section-badge">3</span>
                <div>
                    <h4 class="sl-section-title">Financial Details</h4>
                    <p class="sl-section-subtitle">Parental/household annual income limits in Indian Rupees (INR)</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        col_income, col_income_hint = st.columns(2)
        with col_income:
            income_num = st.number_input(
                "Annual Family Income (₹ INR)",
                min_value=0,
                max_value=10000000,
                value=0,
                step=25000,
                help="Gross annual parental income from all sources. Leave 0 if unspecified.",
            )
        with col_income_hint:
            st.caption("**Income ceiling guidance**")
            st.caption("Government schemes commonly cap income at **₹ 2.5 Lakh**, **₹ 6 Lakh**, or **₹ 8 Lakh** per annum. Leaving 0 permits matching schemes without income caps.")

        st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
        run_match = st.form_submit_button("Find matching scholarships", use_container_width=True)

    if run_match:
        is_valid, err_msg, profile_payload = validate_and_build_profile(
            ed_choice=ed_choice,
            course_input=course_input,
            income_num=income_num,
            marks_num=marks_num,
            gender_choice=gender_choice,
            cat_choice=cat_choice,
            state_input=state_input,
            is_pwd=is_pwd,
        )

        if not is_valid:
            st.error(f"**Validation notice**: {err_msg}")
            st.session_state.pop("match_res", None)
        else:
            with st.spinner("Evaluating profile against official scholarship criteria..."):
                success, err_api, match_res = evaluate_student_matching(profile_payload)

            if not success:
                st.error(err_api)
                st.session_state.pop("match_res", None)
            else:
                st.session_state["match_res"] = match_res
                st.session_state["match_search_query"] = ""
                st.session_state["match_filter_category"] = "All"
                st.session_state["match_filter_status"] = "All"
                st.session_state["match_filter_verification"] = "All"
                st.session_state["match_filter_authority"] = "All"

    match_res = st.session_state.get("match_res")
    if match_res is not None:
        if not match_res or match_res.get("total_evaluated", 0) == 0:
            st.info(
                "**No scholarship records found in local database.**\n\n"
                "Official government scholarship portals (such as National Scholarship Portal, UGC, and AICTE) "
                "enforce access restrictions (such as robots.txt disallowances, SSL certificate requirements, or dynamic dashboards) "
                "which our polite crawler strictly respects without unauthorized bypass.\n\n"
                "Use the sidebar controls to trigger polite ingestion and inspect portal responses."
            )
        else:
            if match_res.get("is_fallback"):
                st.warning(
                    "**Notice: Local fallback engine active** — The backend API server is unreachable. "
                    "These results were computed using the local in-process fallback engine, not verified through the live backend API service."
                )

            results = match_res.get("results", [])
            categorized = categorize_match_results(results)
            confirmed_eligible = categorized["confirmed_eligible"]
            open_confirmed = categorized["open_confirmed_eligible"]
            historical_closed = categorized["historical_closed_eligible"]
            unknown_cycle = categorized["unknown_cycle_eligible"]
            potentially_eligible = categorized["potentially_eligible"]
            not_eligible = categorized["not_eligible"]
            unverified_provisional = categorized["unverified_provisional"]

            # Executive 4-Card KPI Dashboard & Separated Audit Indicator
            render_kpi_dashboard(
                open_count=len(open_confirmed),
                historical_count=len(historical_closed) + len(unknown_cycle),
                potentially_count=len(potentially_eligible),
                not_eligible_count=len(not_eligible),
                unverified_count=len(unverified_provisional),
            )

            st.markdown("---")

            # -------------------------------------------------------------
            # Helper: Render Modern Scholarship Match Card
            # -------------------------------------------------------------
            def render_match_card(r: Dict[str, Any], show_provisional_alert: bool = False):
                outcome = r.get("match_outcome")
                is_prov = r.get("is_provisional", False)
                ver_status = r.get("verification_status", "unverified")
                app_status = (r.get("application_status") or "unknown").strip().lower()
                scheme_year = r.get("scheme_year")
                scheme_name = r.get("scheme_name", "Unknown Scheme")
                authority = r.get("government_authority", "Official Authority")
                url = r.get("official_source_url", "")
                passed = r.get("passed_criteria", [])
                failed = r.get("failed_criteria", [])
                unknown = r.get("unknown_criteria", [])
                evidence = r.get("evidence_references", {})

                # 1. Eligibility badge chip
                if outcome == "eligible" and not is_prov and ver_status == "verified":
                    elig_badge = "Criteria satisfied"
                    elig_chip_class = "sl-pill-open"
                elif outcome == "potentially_eligible":
                    elig_badge = "Pending information"
                    elig_chip_class = "sl-pill-pending"
                else:
                    elig_badge = "Criteria not met"
                    elig_chip_class = "sl-pill-closed"

                # 2. Evidence verification badge chip
                if ver_status == "verified" and not is_prov:
                    veri_badge = "Verified circular"
                    veri_chip_class = "sl-pill-open"
                else:
                    veri_badge = "Unverified / provisional"
                    veri_chip_class = "sl-pill-unverified"

                # 3. Application cycle badge chip
                app_badge, app_help = format_application_status_display(app_status, scheme_year)
                if app_status == "open":
                    avail_chip_class = "sl-pill-open"
                elif app_status in ("closed", "expired"):
                    avail_chip_class = "sl-pill-closed"
                else:
                    avail_chip_class = "sl-pill-slate"

                # Financial benefit and deadline display
                amt_display = format_amount_display(
                    r.get("scholarship_amount"),
                    verification_status=ver_status,
                    verification_notes=r.get("explanation"),
                )
                dead_disp, dead_caption = format_deadline_display(
                    r.get("application_deadline"),
                    r.get("application_deadline_raw"),
                    verification_status=ver_status,
                )

                with st.container(border=True):
                    # Header Row: Authority tag and Scheme Title
                    st.markdown(
                        f"""
                        <div class="sl-card-authority-badge">{authority}</div>
                        <div class="sl-card-title">{scheme_name}</div>
                        """,
                        unsafe_allow_html=True,
                    )

                    # 3-Pillar Status Chip Row
                    st.markdown(
                        f"""
                        <div class="sl-pillar-row">
                            <span class="sl-status-chip {elig_chip_class}">{elig_badge}</span>
                            <span class="sl-status-chip {veri_chip_class}">{veri_badge}</span>
                            <span class="sl-status-chip {avail_chip_class}">{app_badge}</span>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    # Contextual Status Callouts
                    if is_prov or show_provisional_alert:
                        st.warning(
                            "**Provisional evaluation notice**: This scholarship record is UNVERIFIED against primary official circulars. "
                            "Eligibility cannot be confirmed until primary documents are validated."
                        )
                    elif outcome == "eligible" and not is_prov and ver_status == "verified":
                        if app_status == "open":
                            st.success("**Confirmed open opportunity**: Verified against official circulars and currently accepting student applications.")
                        elif app_status in ("closed", "expired"):
                            st.info(
                                f"**Historical scholarship — not currently open ({scheme_year or 'Past cycle'})**: "
                                "Your profile matches the published criteria for this scheme, but this application cycle is closed. "
                                "Documented historically for benchmark reference. Do not attempt to apply for past cycles."
                            )
                        else:
                            st.warning(
                                "**Application status has not been confirmed. Check the official source before applying.** "
                                "Your profile matches the published guideline criteria, but active application dates have not been established by primary evidence."
                            )

                    # Key Facts Box: Financial Benefit, Deadline, Governing Body
                    st.markdown(
                        f"""
                        <div class="sl-key-facts-box">
                            <div class="sl-fact-unit">
                                <span class="sl-fact-title">Scholarship benefit</span>
                                <span class="sl-fact-content">{amt_display}</span>
                            </div>
                            <div class="sl-fact-unit">
                                <span class="sl-fact-title">Application deadline</span>
                                <span class="sl-fact-content">{dead_disp}</span>
                                <span class="sl-fact-sub">{dead_caption or ''}</span>
                            </div>
                            <div class="sl-fact-unit">
                                <span class="sl-fact-title">Governing body</span>
                                <span class="sl-fact-content">{authority}</span>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    # Concise Result Summary & Criteria Pill Counts
                    st.markdown(f"**Evaluation Summary**: {r.get('explanation')}")
                    st.markdown(
                        f"<div style='margin-bottom: 0.5rem;'>"
                        f"<span class='sl-criteria-pill sl-pill-open'>{len(passed)} criteria satisfied</span>"
                        f"<span class='sl-criteria-pill sl-pill-closed'>{len(failed)} failed</span>"
                        f"<span class='sl-criteria-pill sl-pill-pending'>{len(unknown)} pending information</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                    # Expandable Detailed Criteria Breakdown
                    with st.expander("View eligibility details and source evidence", expanded=False):
                        if passed:
                            st.markdown("##### Eligibility criteria satisfied")
                            for p in passed:
                                c_name = humanize_criterion_type(p.get("criterion_type"))
                                st.markdown(
                                    f"<div class='sl-criterion-item sl-criterion-pass'>"
                                    f"<b>{c_name}</b>: {p.get('reason')}"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )

                        if failed:
                            st.markdown("##### Mandatory criteria not satisfied")
                            for f in failed:
                                c_name = humanize_criterion_type(f.get("criterion_type"))
                                st.markdown(
                                    f"<div class='sl-criterion-item sl-criterion-fail'>"
                                    f"<b>{c_name}</b>: {f.get('reason')}"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )

                        if unknown:
                            st.markdown("##### Missing information / pending review")
                            for u in unknown:
                                c_name = humanize_criterion_type(u.get("criterion_type"))
                                st.markdown(
                                    f"<div class='sl-criterion-item sl-criterion-unknown'>"
                                    f"<b>{c_name}</b>: {u.get('reason')}"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )

                        if evidence:
                            st.markdown("##### Source citations")
                            for k, v in evidence.items():
                                st.markdown(
                                    f"<div class='sl-citation-quote'>"
                                    f"<b>{k.replace('_', ' ').title()}</b>: &ldquo;{v}&rdquo;"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )

                    # Card Footer Action Link
                    if url:
                        st.markdown(
                            f"<div style='margin-top: 0.5rem;'>"
                            f"<a href='{url}' target='_blank' class='sl-portal-btn'>"
                            f"Visit official portal ({authority})"
                            f"</a>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

            # -------------------------------------------------------------
            # Stage 6: Client-Side Instant Search & Quick Filters
            # -------------------------------------------------------------
            if "match_search_query" not in st.session_state:
                st.session_state["match_search_query"] = ""
            if "match_filter_category" not in st.session_state:
                st.session_state["match_filter_category"] = "All"
            if "match_filter_status" not in st.session_state:
                st.session_state["match_filter_status"] = "All"
            if "match_filter_verification" not in st.session_state:
                st.session_state["match_filter_verification"] = "All"
            if "match_filter_authority" not in st.session_state:
                st.session_state["match_filter_authority"] = "All"

            def reset_filter_state():
                st.session_state["match_search_query"] = ""
                st.session_state["match_filter_category"] = "All"
                st.session_state["match_filter_status"] = "All"
                st.session_state["match_filter_verification"] = "All"
                st.session_state["match_filter_authority"] = "All"

            extracted_authorities = sorted(list({str(r.get("government_authority") or "").strip() for r in results if r.get("government_authority")}))
            authority_options = ["All"] + extracted_authorities
            if st.session_state.get("match_filter_authority") not in authority_options:
                st.session_state["match_filter_authority"] = "All"

            with st.container(border=True):
                col_filter_header, col_filter_reset = st.columns([3, 1])
                with col_filter_header:
                    st.markdown(
                        """
                        <div class="sl-filter-title">
                            <span>Search and quick filters</span>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with col_filter_reset:
                    st.button(
                        "Reset filters",
                        key="reset_filters_top",
                        use_container_width=True,
                        on_click=reset_filter_state,
                    )

                search_query = st.text_input(
                    "Search Opportunities",
                    placeholder="Search by scholarship name, authority, description, discipline...",
                    key="match_search_query",
                    label_visibility="collapsed",
                )

                col_cat, col_status, col_ver, col_auth = st.columns(4)
                with col_cat:
                    cat_filter = st.selectbox(
                        "Eligibility Category",
                        [
                            "All",
                            "Confirmed Open",
                            "Historical / Unconfirmed",
                            "Potentially Eligible",
                            "Not Eligible",
                            "Provisional / Unverified",
                        ],
                        key="match_filter_category",
                    )
                with col_status:
                    status_filter = st.selectbox(
                        "Application Status",
                        [
                            "All",
                            "Open",
                            "Closed / Past Cycle",
                            "Unknown / Unconfirmed",
                        ],
                        key="match_filter_status",
                    )
                with col_ver:
                    ver_filter = st.selectbox(
                        "Verification",
                        [
                            "All",
                            "Verified",
                            "Unverified / Provisional",
                        ],
                        key="match_filter_verification",
                    )
                with col_auth:
                    auth_filter = st.selectbox(
                        "Issuing Authority",
                        authority_options,
                        key="match_filter_authority",
                    )

                # Compute filtered slices for each subtab
                filtered_all = filter_match_results(
                    results,
                    query=search_query,
                    authority=auth_filter,
                    status=status_filter,
                    verification=ver_filter,
                    category=cat_filter,
                )
                filtered_open = filter_match_results(
                    open_confirmed,
                    query=search_query,
                    authority=auth_filter,
                    status=status_filter,
                    verification=ver_filter,
                    category=cat_filter,
                )
                filtered_hist = filter_match_results(
                    historical_closed + unknown_cycle,
                    query=search_query,
                    authority=auth_filter,
                    status=status_filter,
                    verification=ver_filter,
                    category=cat_filter,
                )
                filtered_pot = filter_match_results(
                    potentially_eligible,
                    query=search_query,
                    authority=auth_filter,
                    status=status_filter,
                    verification=ver_filter,
                    category=cat_filter,
                )
                filtered_not = filter_match_results(
                    not_eligible,
                    query=search_query,
                    authority=auth_filter,
                    status=status_filter,
                    verification=ver_filter,
                    category=cat_filter,
                )
                filtered_prov = filter_match_results(
                    unverified_provisional,
                    query=search_query,
                    authority=auth_filter,
                    status=status_filter,
                    verification=ver_filter,
                    category=cat_filter,
                )

                total_evaluated = len(results)
                total_filtered = len(filtered_all)

                # Compute active filters summary tags
                active_filters = []
                if search_query.strip():
                    active_filters.append(f'Search: "{search_query.strip()}"')
                if cat_filter != "All":
                    active_filters.append(f"Category: {cat_filter}")
                if status_filter != "All":
                    active_filters.append(f"Status: {status_filter}")
                if ver_filter != "All":
                    active_filters.append(f"Verification: {ver_filter}")
                if auth_filter != "All":
                    active_filters.append(f"Authority: {auth_filter}")

                if active_filters:
                    filter_tags_html = " ".join(f'<span class="sl-active-filter-tag">{f}</span>' for f in active_filters)
                    st.markdown(
                        f"""
                        <div class="sl-active-filters-row">
                            <span>Showing <b>{total_filtered} of {total_evaluated}</b> scholarships:</span>
                            {filter_tags_html}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"""
                        <div class="sl-active-filters-row">
                            <span>Showing all <b>{total_evaluated}</b> evaluated scholarships across categories.</span>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

            if total_filtered == 0:
                st.warning(
                    "**No scholarships match your active search and filter criteria.** "
                    "Try broadening your search term or click **Reset Filters** above to restore all evaluated scholarships."
                )
                st.markdown(
                    """
                    <div class="sl-empty-search-state">
                        <div class="sl-empty-search-title">No Matching Opportunities Found</div>
                        <div class="sl-empty-search-desc">
                            We could not find any scholarship records matching your search query and filter combination.
                            You can reset your filters or change your search keywords to view other available schemes.
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

            subtab_open, subtab_hist, subtab_pot, subtab_not, subtab_prov = st.tabs([
                f"Open opportunities ({len(filtered_open)})",
                f"Historical & unconfirmed ({len(filtered_hist)})",
                f"Potentially eligible ({len(filtered_pot)})",
                f"Not eligible ({len(filtered_not)})",
                f"Provisional / unverified ({len(filtered_prov)})",
            ])

            with subtab_open:
                if filtered_open:
                    st.markdown(f"Displaying **{len(filtered_open)}** open and confirmed eligible scheme(s).")
                    for r in filtered_open:
                        render_match_card(r)
                else:
                    if not open_confirmed:
                        st.info(
                            "**No currently open verified opportunities confirmed.**\n\n"
                            "No scholarship with verified official evidence is currently confirmed open for student applications. "
                            "Review the **Historical & Unconfirmed Matches** tab for past scholarship benchmarks and schemes with unconfirmed application cycles."
                        )
                    else:
                        st.info("**No open opportunities match the active search and filter criteria.**")

            with subtab_hist:
                hist_unknown = [r for r in filtered_hist if (r.get("application_status") or "").strip().lower() not in ("open", "closed", "expired")]
                hist_closed = [r for r in filtered_hist if (r.get("application_status") or "").strip().lower() in ("closed", "expired")]

                if hist_unknown:
                    st.markdown("### Verified guidelines — application status unconfirmed")
                    st.warning(
                        "**Application status has not been confirmed. Check the official source before applying.**\n\n"
                        "Your profile matches the published eligibility criteria verified from official guidelines, "
                        "but active portal submission dates are not established in the circular."
                    )
                    for r in hist_unknown:
                        render_match_card(r)

                if hist_closed:
                    st.markdown("### Historical scholarships — not currently open")
                    st.info(
                        "**Historical scholarship — not currently open**: Your profile matches the published criteria "
                        "for these past scholarship cycles. Documented historically from official records for your reference. "
                        "Do not attempt to apply for closed cycles."
                    )
                    for r in hist_closed:
                        render_match_card(r)

                if not hist_unknown and not hist_closed:
                    st.info("**No historical or unconfirmed eligible scholarships match the active search and filter criteria.**")

            with subtab_pot:
                if filtered_pot:
                    st.markdown(f"Displaying **{len(filtered_pot)}** potentially eligible scheme(s) pending verification.")
                    for r in filtered_pot:
                        render_match_card(r, show_provisional_alert=r.get("is_provisional", False))
                else:
                    st.info("**No potentially eligible scholarships match the active search and filter criteria.**")

            with subtab_not:
                if filtered_not:
                    st.markdown(f"Displaying **{len(filtered_not)}** scheme(s) where mandatory criteria were not satisfied.")
                    for r in filtered_not:
                        render_match_card(r)
                else:
                    st.info("**No ineligible scholarships match the active search and filter criteria.**")

            with subtab_prov:
                if filtered_prov:
                    st.warning(
                        "**Provisional matching section**: The scholarships below are stored in the local database "
                        "but remain UNVERIFIED against primary source circulars. They are never presented as confirmed eligible."
                    )
                    for r in filtered_prov:
                        render_match_card(r, show_provisional_alert=True)
                else:
                    st.info("**No provisional or unverified scholarship records match the active search and filter criteria.**")

# -----------------------------------------------------------------------------
# Tab 2: Stored Scholarship Records
# -----------------------------------------------------------------------------
with tab_scholarships:
    scholarships_data = fetch_backend_data("/api/scholarships") or []

    # Filter controls
    col_filter, col_count = st.columns([3, 1])
    with col_filter:
        status_filter = st.selectbox(
            "Filter by Verification Status",
            ["All Records", "Verified Only", "Unverified Only", "Flagged Only"],
        )

    filtered_scholarships = scholarships_data
    if status_filter == "Verified Only":
        filtered_scholarships = [s for s in scholarships_data if s.get("verification_status") == "verified"]
    elif status_filter == "Unverified Only":
        filtered_scholarships = [s for s in scholarships_data if s.get("verification_status") == "unverified"]
    elif status_filter == "Flagged Only":
        filtered_scholarships = [s for s in scholarships_data if s.get("verification_status") == "flagged"]

    with col_count:
        st.metric("Total Displayed", len(filtered_scholarships))

    if not filtered_scholarships:
        st.info(
            "**No scholarship records found matching criteria.**\n\n"
            "To populate records from official government sources, click **Run Polite Ingestion** "
            "in the sidebar or use the `/api/ingest` endpoint."
        )
    else:
        for item in filtered_scholarships:
            status = item.get("verification_status", "unverified")
            notes = item.get("verification_notes")
            amount = item.get("scholarship_amount")
            deadline_iso = item.get("application_deadline")
            deadline_raw = item.get("application_deadline_raw")
            authority = item.get("government_authority", "Official Authority")
            scheme_name = item.get("scheme_name", "Unknown Scheme")
            url = item.get("official_source_url", "")
            app_status = (item.get("application_status") or "unknown").strip().lower()
            scheme_year = item.get("scheme_year")

            status_chip_class = "sl-pill-open" if status == "verified" else ("sl-pill-unverified" if status == "unverified" else "sl-pill-closed")
            app_badge, app_help = format_application_status_display(app_status, scheme_year)
            amt_disp = format_amount_display(amount, verification_status=status, verification_notes=notes)
            dead_disp, dead_cap = format_deadline_display(deadline_iso, deadline_raw, verification_status=status)

            with st.container(border=True):
                st.markdown(
                    f"""
                    <div class="sl-card-authority-badge">{authority}</div>
                    <div class="sl-card-title">{scheme_name}</div>
                    <div class="sl-pillar-row">
                        <span class="sl-status-chip {status_chip_class}">{status.upper()}</span>
                        <span class="sl-status-chip sl-pill-slate">{app_badge}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                st.write(f"**Description**: {item.get('description')}")

                if notes:
                    if status == "verified":
                        st.success(f"**Verification status**: {notes}")
                    elif status == "unverified":
                        st.warning(f"**Verification warning**: {notes}")
                    else:
                        st.error(f"**Verification flag**: {notes}")

                st.markdown(
                    f"""
                    <div class="sl-key-facts-box">
                        <div class="sl-fact-unit">
                            <span class="sl-fact-title">Benefit amount</span>
                            <span class="sl-fact-content">{amt_disp}</span>
                        </div>
                        <div class="sl-fact-unit">
                            <span class="sl-fact-title">Application deadline</span>
                            <span class="sl-fact-content">{dead_disp}</span>
                            <span class="sl-fact-sub">{dead_cap or ''}</span>
                        </div>
                        <div class="sl-fact-unit">
                            <span class="sl-fact-title">Verification record</span>
                            <span class="sl-fact-content">{'Verified' if item.get('last_verified_timestamp') else 'Unverified'}</span>
                            <span class="sl-fact-sub">{item.get('last_verified_timestamp') or 'Pending official circular'}</span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                # Eligibility & Documents in an expander
                criteria = item.get("eligibility_criteria", [])
                docs = item.get("required_documents", [])
                quotes = item.get("supporting_quotations", [])
                evidence = item.get("field_evidence", {})

                with st.expander("View official criteria, required documents and evidence", expanded=False):
                    if criteria:
                        st.markdown("##### Official eligibility criteria")
                        for crit in criteria:
                            st.markdown(f"- {crit}")
                    else:
                        st.caption("No verified criteria extracted from retrieved content.")

                    if docs:
                        st.markdown("##### Required documents")
                        st.markdown(", ".join([f"`{d}`" for d in docs]))

                    if evidence or quotes:
                        st.markdown("##### Source citations")
                        if evidence:
                            for k, v in evidence.items():
                                st.markdown(f"<div class='sl-citation-quote'><b>{k.replace('_', ' ').title()}</b>: &ldquo;{v}&rdquo;</div>", unsafe_allow_html=True)
                        if quotes:
                            for q in quotes:
                                st.markdown(f"> *\"{q}\"*")

                if url:
                    st.markdown(
                        f"<div style='margin-top: 0.5rem;'>"
                        f"<a href='{url}' target='_blank' class='sl-portal-btn'>"
                        f"View official portal ({authority})"
                        f"</a>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )


# -----------------------------------------------------------------------------
# Tab 2: Sources Registry
# -----------------------------------------------------------------------------
with tab_sources:
    st.subheader(f"Configured Official Government Portals ({len(sources_data)})")
    st.write(
        "Only portals explicitly defined in the source registry can be fetched. "
        "Arbitrary crawling is disabled by design."
    )

    for s in sources_data:
        v_status = s.get("verification_status", "unverified")
        v_color = "green" if v_status == "verified" else ("orange" if v_status == "unverified" else "red")
        src_type = s.get("source_type", "html").upper()

        with st.expander(f"{s.get('name')} [{src_type}] — Status: {v_status.upper()}"):
            col_a, col_b = st.columns(2)
            with col_a:
                st.write(f"**Authority**: {s.get('authority')}")
                st.write(f"**Official URL**: [{s.get('url')}]({s.get('url')})")
                st.write(f"**Format**: `{src_type}`")
                st.write(f"**Category**: {s.get('category')}")
                st.write(f"**Enabled**: `{'Yes' if s.get('is_active') else 'No'}`")
            with col_b:
                st.markdown(
                    f"**Verification Status**: <span style='color: {v_color}; font-weight: bold;'>{v_status.upper()}</span>",
                    unsafe_allow_html=True,
                )
                st.write(f"**Last Checked**: `{s.get('last_checked_timestamp') or 'Never'}`")
                st.write(f"**Last Verified**: `{s.get('last_verified_timestamp') or 'Never'}`")

            if s.get("last_error"):
                st.error(f"**Last fetch error**: {s.get('last_error')}")

            if s.get("notes"):
                st.info(f"**Notes**: {s.get('notes')}")


# -----------------------------------------------------------------------------
# Tab 3: Ingestion Telemetry
# -----------------------------------------------------------------------------
with tab_history:
    st.subheader("Recent Ingestion Telemetry & Logs")
    runs_data = fetch_backend_data("/api/ingestion/runs") or []

    if not runs_data:
        st.write("No ingestion runs recorded yet.")
    else:
        for run in runs_data:
            success = run.get("success", False)
            badge = "SUCCESS" if success else "FAILED"
            color = "green" if success else "red"

            with st.container(border=True):
                st.markdown(
                    f"**Source**: `{run.get('source_id')}` | **Result**: <span style='color: {color}; font-weight: bold;'>{badge}</span> "
                    f"| **Time**: `{run.get('fetched_at')}`",
                    unsafe_allow_html=True,
                )
                st.write(f"**URL**: [{run.get('url')}]({run.get('url')})")
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.write(f"**HTTP Status**: `{run.get('status_code') or 'None'}`")
                with c2:
                    st.write(f"**Content-Type**: `{run.get('content_type') or 'N/A'}`")
                with c3:
                    st.write(f"**Records Extracted**: `{run.get('records_extracted', 0)}`")

                if run.get("error_message"):
                    st.warning(f"**Error Details**: {run.get('error_message')}")
