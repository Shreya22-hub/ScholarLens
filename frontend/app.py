"""ScholarLens Streamlit Frontend.

Provides user interface to explore verified Indian government scholarships,
view configured sources and their verification status, trigger polite ingestion,
and inspect recent fetch telemetry.
"""

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
import streamlit as st

from frontend.helpers import (
    categorize_match_results,
    evaluate_student_matching,
    format_amount_display,
    format_application_status_display,
    format_deadline_display,
    validate_and_build_profile,
)

# Configure page
st.set_page_config(
    page_title="ScholarLens | Verified Indian Scholarships",
    page_icon="🎓",
    layout="wide",
)

# App Title & Description
st.title("🎓 ScholarLens")
st.caption("Evidence-Backed Scholarship Discovery for Indian Students Grounded in Official Government Portals")

# Configuration
BACKEND_URL = "http://127.0.0.1:8000"


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
# Sidebar: Controls & Ingestion Trigger
# =============================================================================

st.sidebar.header("⚙️ Ingestion Controls")

sources_data = fetch_backend_data("/api/sources") or []

source_options = {"All Enabled Sources": None}
for s in sources_data:
    source_options[f"{s.get('name')} ({s.get('id')})"] = s.get("id")

selected_source_label = st.sidebar.selectbox("Select Target Source", list(source_options.keys()))
selected_source_id = source_options[selected_source_label]

if st.sidebar.button("🚀 Run Polite Ingestion", use_container_width=True):
    with st.spinner("Executing polite ingestion pipeline (checking robots.txt & official portals)..."):
        ingest_result = trigger_ingestion(selected_source_id)
        if "error" in ingest_result:
            st.sidebar.error(ingest_result["error"])
        else:
            st.sidebar.success(ingest_result.get("summary", "Ingestion completed!"))
            # Rerun to refresh views
            st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### 🛡️ Grounding Principles")
st.sidebar.markdown(
    """
    - **No Hallucinations**: Information is strictly derived from retrieved text.
    - **Verbatim Quotations**: Supporting quotes must match source text.
    - **Polite Crawling**: Respects robots.txt, uses timeouts, and prevents arbitrary crawling.
    - **Transparent Status**: Unverified or inaccessible sources are explicitly flagged.
    """
)


# =============================================================================
# Main Content Tabs
# =============================================================================

tab_matcher, tab_scholarships, tab_sources, tab_history = st.tabs(
    ["🎯 Eligibility Matcher", "📜 Stored Scholarship Records", "🏛️ Official Sources Registry", "📊 Ingestion Telemetry"]
)

# -----------------------------------------------------------------------------
# Tab 1: Eligibility Matcher (Phase 4)
# -----------------------------------------------------------------------------
with tab_matcher:
    st.subheader("🎯 Explainable Scholarship Matching Engine")
    st.caption("Enter your academic and profile details to receive transparent, rule-based eligibility evaluations.")

    with st.form("student_profile_matching_form"):
        st.markdown("#### 👤 Student Profile")
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
            )
        with col_course:
            course_input = st.text_input(
                "Degree / Stream / Discipline (Optional)",
                placeholder="e.g. B.Tech Computer Science, MBBS, B.Sc Mathematics",
            )

        col_income, col_marks = st.columns(2)
        with col_income:
            income_num = st.number_input(
                "Annual Family Income (₹ INR)",
                min_value=0,
                max_value=10000000,
                value=0,
                step=25000,
                help="Leave 0 if unspecified or prefer not to disclose.",
            )
        with col_marks:
            marks_num = st.number_input(
                "Academic Marks / Percentage (%)",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                step=0.5,
                help="Leave 0.0 if unspecified.",
            )

        col_gen, col_cat = st.columns(2)
        with col_gen:
            gender_choice = st.selectbox(
                "Gender",
                ["Not Specified", "Female (Girl Student)", "Male", "Other"],
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
            )

        col_state, col_pwd = st.columns(2)
        with col_state:
            state_input = st.text_input(
                "State of Residence / Domicile (Optional)",
                placeholder="e.g. Maharashtra, Uttar Pradesh, Delhi",
            )
        with col_pwd:
            st.write("")
            st.write("")
            is_pwd = st.checkbox("Person with Benchmark Disability (PwD)")

        run_match = st.form_submit_button("🔍 Find Matching Scholarships", use_container_width=True)

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
            st.error(f"⚠️ {err_msg}")
        else:
            with st.spinner("Evaluating profile against official scholarship criteria..."):
                success, err_api, match_res = evaluate_student_matching(profile_payload)

            if not success:
                st.error(f"⚠️ {err_api}")
            elif not match_res or match_res.get("total_evaluated", 0) == 0:
                st.info(
                    "📌 **No scholarship records found in local database.**\n\n"
                    "Official government scholarship portals (such as National Scholarship Portal, UGC, and AICTE) "
                    "enforce access restrictions (such as robots.txt disallowances, SSL certificate requirements, or dynamic dashboards) "
                    "which our polite crawler strictly respects without unauthorized bypass.\n\n"
                    "Use the sidebar controls to trigger polite ingestion and inspect portal responses."
                )
            else:
                if match_res.get("is_fallback"):
                    st.warning(
                        "⚠️ **Notice: Local Fallback Engine Active** — The backend API server is unreachable. "
                        "These results were computed using the local in-process fallback engine, not verified through the live backend API service."
                    )

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("🟢 Confirmed Eligible", match_res.get("eligible_count", 0))
                m2.metric("🟡 Potentially Eligible", match_res.get("potentially_eligible_count", 0))
                m3.metric("🔴 Not Eligible", match_res.get("not_eligible_count", 0))
                m4.metric("⚠️ Unverified / Provisional", match_res.get("unverified_count", 0))

                st.caption(
                    "📊 **Result Categorization**: Total evaluated scholarships are partitioned into "
                    "**Confirmed Eligible**, **Potentially Eligible**, and **Not Eligible** (sum equals total evaluated). "
                    "**Provisional / Unverified** provides a dedicated audit view of schemes lacking primary source verification."
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

                st.markdown("---")
                subtab_open, subtab_hist, subtab_pot, subtab_not, subtab_prov = st.tabs([
                    f"🟢 Open Opportunities ({len(open_confirmed)})",
                    f"🏛️ Historical & Unconfirmed Matches ({len(historical_closed) + len(unknown_cycle)})",
                    f"🟡 Potentially Eligible ({len(potentially_eligible)})",
                    f"🔴 Not Eligible ({len(not_eligible)})",
                    f"⚠️ Provisional / Unverified ({len(unverified_provisional)})",
                ])

                def render_match_card(r: Dict[str, Any], show_provisional_alert: bool = False):
                    outcome = r.get("match_outcome")
                    is_prov = r.get("is_provisional", False)
                    ver_status = r.get("verification_status", "unverified")
                    app_status = (r.get("application_status") or "unknown").strip().lower()
                    scheme_year = r.get("scheme_year")

                    if outcome == "eligible" and not is_prov and ver_status == "verified":
                        if app_status == "open":
                            badge_color = "green"
                            badge_label = "CONFIRMED ELIGIBLE & OPEN"
                        elif app_status in ("closed", "expired"):
                            badge_color = "#b91c1c"
                            badge_label = "ELIGIBLE (HISTORICAL — NOT CURRENTLY OPEN)"
                        else:
                            badge_color = "#475569"
                            badge_label = "ELIGIBLE (CYCLE STATUS UNCONFIRMED)"
                    elif outcome == "potentially_eligible":
                        badge_color = "orange"
                        badge_label = "POTENTIALLY ELIGIBLE (PROVISIONAL)" if is_prov else "POTENTIALLY ELIGIBLE"
                    else:
                        badge_color = "red"
                        badge_label = "NOT ELIGIBLE"

                    with st.container(border=True):
                        st.markdown(
                            f"### {r.get('scheme_name')} "
                            f"<span style='font-size: 13px; padding: 3px 8px; border-radius: 4px; "
                            f"background-color: {badge_color}; color: white;'>{badge_label}</span>",
                            unsafe_allow_html=True,
                        )
                        st.write(f"**Authority**: {r.get('government_authority')}")

                        # Explicit status callouts based on application availability & verification
                        if is_prov or show_provisional_alert:
                            st.warning(
                                "⚠️ **Provisional Evaluation Notice**: This scholarship record is UNVERIFIED against official "
                                "circulars. Eligibility cannot be confirmed until primary documents are validated."
                            )
                        elif outcome == "eligible" and not is_prov and ver_status == "verified":
                            if app_status == "open":
                                st.success("🟢 **Confirmed Open Opportunity**: Verified against official circulars and currently accepting student applications.")
                            elif app_status in ("closed", "expired"):
                                st.info(
                                    "🔴 **Historical Scholarship — Not Currently Open**: Your profile matches the published criteria for this scheme, "
                                    "but this application cycle is closed. Documented historically for your reference. Do not attempt to apply for past cycles."
                                )
                            else:
                                st.warning(
                                    "⚠️ **Application status has not been confirmed. Check the official source before applying.** "
                                    "Your profile matches the published guideline criteria, but active application dates have not been established by primary evidence."
                                )

                        # Clear 3-Pillar Status Distinction (Eligibility vs Verification vs Application Availability)
                        c_elig, c_veri, c_avail = st.columns(3)
                        with c_elig:
                            if outcome == "eligible":
                                st.markdown("🎯 **Eligibility Match**\n\n`✅ Criteria Satisfied`")
                            elif outcome == "potentially_eligible":
                                st.markdown("🎯 **Eligibility Match**\n\n`🟡 Pending Information`")
                            else:
                                st.markdown("🎯 **Eligibility Match**\n\n`❌ Criteria Failed`")
                        with c_veri:
                            if ver_status == "verified" and not is_prov:
                                st.markdown("🛡️ **Evidence Verification**\n\n`🛡️ Official Source Verified`")
                            else:
                                st.markdown("🛡️ **Evidence Verification**\n\n`⚠️ Unverified / Provisional`")
                        with c_avail:
                            app_badge, app_help = format_application_status_display(app_status, scheme_year)
                            st.markdown(f"⏱️ **Application Availability**\n\n`{app_badge}`")
                            if app_help:
                                st.caption(app_help)

                        st.info(f"💡 **Evaluation Explanation**: {r.get('explanation')}")

                        # Detailed Passed / Failed / Unknown Breakdowns (Criterion-level explanations preserved)
                        c_pass, c_fail, c_unk = st.columns(3)
                        with c_pass:
                            passed = r.get("passed_criteria", [])
                            if passed:
                                st.markdown("✅ **Passed Conditions:**")
                                for p in passed:
                                    st.markdown(f"- `{p.get('criterion_type').upper()}`: {p.get('reason')}")
                            else:
                                st.caption("No criteria confirmed passed yet.")
                        with c_fail:
                            failed = r.get("failed_criteria", [])
                            if failed:
                                st.markdown("❌ **Failed Conditions:**")
                                for f in failed:
                                    st.markdown(f"- `{f.get('criterion_type').upper()}`: {f.get('reason')}")
                        with c_unk:
                            unknown = r.get("unknown_criteria", [])
                            if unknown:
                                st.markdown("❓ **Unknown / Pending:**")
                                for u in unknown:
                                    st.markdown(f"- `{u.get('criterion_type').upper()}`: {u.get('reason')}")

                        # Financial Benefit and Deadline display
                        col_amt, col_dead = st.columns(2)
                        with col_amt:
                            amt_display = format_amount_display(
                                r.get("scholarship_amount"),
                                verification_status=ver_status,
                                verification_notes=r.get("explanation"),
                            )
                            st.write(f"💰 **Amount**: `{amt_display}`")
                        with col_dead:
                            dead_disp, dead_caption = format_deadline_display(
                                r.get("application_deadline"),
                                r.get("application_deadline_raw"),
                                verification_status=ver_status,
                            )
                            st.write(f"📅 **Deadline**: `{dead_disp}`")
                            if dead_caption:
                                st.caption(dead_caption)

                        url = r.get("official_source_url")
                        st.write(f"🔗 **Official Source Link**: [{url}]({url})")

                        evidence = r.get("evidence_references", {})
                        if evidence:
                            with st.expander("🔍 Grounded Source Citations"):
                                for k, v in evidence.items():
                                    st.markdown(f"- **{k.replace('_', ' ').title()}**: *\"{v}\"*")

                with subtab_open:
                    if open_confirmed:
                        for r in open_confirmed:
                            render_match_card(r)
                    else:
                        st.info(
                            "ℹ️ **No currently open verified opportunities confirmed.**\n\n"
                            "No scholarship with verified official evidence is currently confirmed open for student applications. "
                            "Review the **Historical & Unconfirmed Matches** tab for past scholarship benchmarks and schemes with unconfirmed application cycles."
                        )

                with subtab_hist:
                    if unknown_cycle:
                        st.markdown("### ⚪ Verified Guidelines — Application Status Unconfirmed")
                        st.warning(
                            "⚠️ **Application status has not been confirmed. Check the official source before applying.**\n\n"
                            "Your profile matches the published eligibility criteria verified from official guidelines, "
                            "but active portal submission dates are not established in the circular."
                        )
                        for r in unknown_cycle:
                            render_match_card(r)

                    if historical_closed:
                        st.markdown("### 🔴 Historical Scholarships — Not Currently Open")
                        st.info(
                            "ℹ️ **Historical Scholarship — Not Currently Open**: Your profile matches the published criteria "
                            "for these past scholarship cycles. Documented historically from official records for your reference. "
                            "Do not attempt to apply for closed cycles."
                        )
                        for r in historical_closed:
                            render_match_card(r)

                    if not unknown_cycle and not historical_closed:
                        st.info("ℹ️ **No historical or unconfirmed eligible scholarships match your profile.**")

                with subtab_pot:
                    if potentially_eligible:
                        for r in potentially_eligible:
                            render_match_card(r, show_provisional_alert=r.get("is_provisional", False))
                    else:
                        st.info("ℹ️ **No potentially eligible scholarships pending information.**")

                with subtab_not:
                    if not_eligible:
                        for r in not_eligible:
                            render_match_card(r)
                    else:
                        st.info("ℹ️ **No scholarships marked not eligible.**")

                with subtab_prov:
                    if unverified_provisional:
                        st.warning(
                            "⚠️ **Provisional Matching Section**: The scholarships below are stored in the local database "
                            "but remain UNVERIFIED against primary source circulars. They are never presented as confirmed eligible."
                        )
                        for r in unverified_provisional:
                            render_match_card(r, show_provisional_alert=True)
                    else:
                        st.info("ℹ️ **No provisional or unverified scholarship records in database.**")

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
            "📌 **No scholarship records found matching criteria.**\n\n"
            "To populate records from official government sources, click **Run Polite Ingestion** "
            "in the sidebar or use the `/api/ingest` endpoint."
        )
    else:
        for item in filtered_scholarships:
            status = item.get("verification_status", "unverified")
            status_color = "green" if status == "verified" else ("orange" if status == "unverified" else "red")
            notes = item.get("verification_notes")
            amount = item.get("scholarship_amount")
            deadline_iso = item.get("application_deadline")
            deadline_raw = item.get("application_deadline_raw")

            with st.container(border=True):
                st.markdown(
                    f"### {item.get('scheme_name')} "
                    f"<span style='font-size: 14px; padding: 3px 8px; border-radius: 4px; "
                    f"background-color: {status_color}; color: white;'>{status.upper()}</span>",
                    unsafe_allow_html=True,
                )
                st.write(f"**Authority**: {item.get('government_authority')}")
                st.write(f"**Description**: {item.get('description')}")

                if notes:
                    if status == "verified":
                        st.success(f"🛡️ **Verification Status**: {notes}")
                    elif status == "unverified":
                        st.warning(f"⚠️ **Verification Warning**: {notes}")
                    else:
                        st.error(f"🚨 **Verification Flag**: {notes}")

                c1, c2 = st.columns(2)
                with c1:
                    amt_disp = format_amount_display(amount, verification_status=status, verification_notes=notes)
                    st.write(f"💰 **Scholarship Amount**: `{amt_disp}`")

                    dead_disp, dead_cap = format_deadline_display(deadline_iso, deadline_raw, verification_status=status)
                    st.write(f"📅 **Application Deadline**: `{dead_disp}`")
                    if dead_cap:
                        st.caption(dead_cap)

                    app_badge, app_help = format_application_status_display(item.get("application_status"), item.get("scheme_year"))
                    st.write(f"⏱️ **Application Cycle**: `{app_badge}`")
                    st.caption(app_help)

                with c2:
                    st.write(f"🔗 **Official Source**: [{item.get('official_source_url')}]({item.get('official_source_url')})")
                    if item.get("last_verified_timestamp"):
                        st.write(f"⏱️ **Last Verified**: `{item.get('last_verified_timestamp')}`")
                    else:
                        st.write("⏱️ **Last Verified**: *Unverified against primary source circular*")

                # Eligibility & Documents
                criteria = item.get("eligibility_criteria", [])
                if criteria:
                    st.markdown("**Eligibility Criteria:**")
                    for crit in criteria:
                        st.markdown(f"- {crit}")
                else:
                    st.markdown("**Eligibility Criteria:** *No verified criteria extracted from retrieved content.*")

                docs = item.get("required_documents", [])
                if docs:
                    st.markdown("**Required Documents:** " + ", ".join([f"`{d}`" for d in docs]))

                # Verbatim supporting quotes and field-level evidence
                quotes = item.get("supporting_quotations", [])
                evidence = item.get("field_evidence", {})
                if quotes or evidence:
                    with st.expander("🔍 Grounded Source Evidence & Citations"):
                        if evidence:
                            st.markdown("**Field-Level Evidence:**")
                            for k, v in evidence.items():
                                st.markdown(f"- **{k.replace('_', ' ').title()}**: *\"{v}\"*")
                        if quotes:
                            st.markdown("**Source Text Excerpts:**")
                            for q in quotes:
                                st.markdown(f"> *\"{q}\"*")


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

        with st.expander(f"🏛️ {s.get('name')} [{src_type}] — Status: {v_status.upper()}"):
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
                st.error(f"⚠️ **Last Fetch Error**: {s.get('last_error')}")

            if s.get("notes"):
                st.info(f"ℹ️ **Notes**: {s.get('notes')}")


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
            badge = "✅ SUCCESS" if success else "❌ FAILED"
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
