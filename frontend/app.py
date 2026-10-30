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


def evaluate_student_matching(profile_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Submit student profile to /api/matching/evaluate with direct service fallback."""
    try:
        res = requests.post(f"{BACKEND_URL}/api/matching/evaluate", json=profile_data, timeout=10.0)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass

    # Direct fallback if backend API service is offline
    try:
        from backend.app.db.database import get_db
        from backend.app.matching.service import MatchingService
        from backend.app.schemas.matching import StudentProfile

        profile = StudentProfile(**profile_data)
        service = MatchingService(db=get_db())
        resp = service.evaluate_profile(profile)
        return resp.model_dump(mode="json")
    except Exception:
        pass

    return None


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
        # Build clean StudentProfile payload
        level_map = {
            "Undergraduate (Degree / B.Tech / MBBS / B.Sc)": "undergraduate",
            "Class 12 / Higher Secondary (+2)": "class_12",
            "Class 10 / Secondary School": "class_10",
            "Polytechnic Diploma": "diploma",
            "Postgraduate (Master's / M.Tech / MBA)": "postgraduate",
            "Doctorate / Ph.D": "doctorate",
        }
        gender_map = {
            "Female (Girl Student)": "female",
            "Male": "male",
            "Other": "other",
        }
        cat_map = {
            "General": "general",
            "OBC (Other Backward Classes)": "obc",
            "SC (Scheduled Caste)": "sc",
            "ST (Scheduled Tribe)": "st",
            "EWS (Economically Weaker Section)": "ews",
            "Minority Community": "minority",
        }

        profile_payload: Dict[str, Any] = {}
        if ed_choice in level_map:
            profile_payload["education_level"] = level_map[ed_choice]
        if course_input and course_input.strip():
            profile_payload["course"] = course_input.strip()
        if income_num > 0:
            profile_payload["annual_family_income"] = float(income_num)
        if marks_num > 0.0:
            profile_payload["academic_percentage"] = float(marks_num)
        if gender_choice in gender_map:
            profile_payload["gender"] = gender_map[gender_choice]
        if cat_choice in cat_map:
            profile_payload["category"] = cat_map[cat_choice]
        if state_input and state_input.strip():
            profile_payload["state_of_residence"] = state_input.strip()
        if is_pwd:
            profile_payload["is_differently_abled"] = True

        with st.spinner("Evaluating profile against official scholarship criteria..."):
            match_res = evaluate_student_matching(profile_payload)

        if not match_res:
            st.error("Failed to connect to matching service.")
        elif match_res.get("total_evaluated", 0) == 0:
            st.info("📌 **No scholarship records found in database.** Run ingestion from the sidebar to populate schemes.")
        else:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("🟢 Confirmed Eligible", match_res.get("eligible_count", 0))
            m2.metric("🟡 Potentially Eligible", match_res.get("potentially_eligible_count", 0))
            m3.metric("🔴 Not Eligible", match_res.get("not_eligible_count", 0))
            m4.metric("⚠️ Unverified / Provisional", match_res.get("unverified_count", 0))

            results = match_res.get("results", [])
            for r in results:
                outcome = r.get("match_outcome")
                is_provisional = r.get("is_provisional", False)
                ver_status = r.get("verification_status", "unverified")

                if outcome == "eligible":
                    badge_color = "green"
                    badge_label = "CONFIRMED ELIGIBLE"
                elif outcome == "potentially_eligible":
                    badge_color = "orange"
                    badge_label = "POTENTIALLY ELIGIBLE (PROVISIONAL)" if is_provisional else "POTENTIALLY ELIGIBLE"
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
                    st.info(f"💡 **Evaluation Explanation**: {r.get('explanation')}")

                    # Detailed Passed / Failed / Unknown Breakdowns
                    c_pass, c_fail, c_unk = st.columns(3)
                    with c_pass:
                        passed = r.get("passed_criteria", [])
                        if passed:
                            st.markdown("✅ **Passed Conditions:**")
                            for p in passed:
                                st.markdown(f"- `{p.get('criterion_type').upper()}`: {p.get('reason')}")
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

                    # Meta info
                    col_amt, col_dead, col_url = st.columns(3)
                    with col_amt:
                        amt = r.get("scholarship_amount")
                        st.write(f"💰 **Amount**: `{amt}`" if amt else "💰 **Amount**: *Unverified / Unknown*")
                    with col_dead:
                        dline = r.get("application_deadline")
                        st.write(f"📅 **Deadline**: `{dline}`" if dline else "📅 **Deadline**: *To be announced*")
                    with col_url:
                        url = r.get("official_source_url")
                        st.write(f"🔗 **Source**: [{url}]({url})")

                    evidence = r.get("evidence_references", {})
                    if evidence:
                        with st.expander("🔍 Grounded Source Citations"):
                            for k, v in evidence.items():
                                st.markdown(f"- **{k.replace('_', ' ').title()}**: *\"{v}\"*")

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
                    if amount:
                        st.write(f"💰 **Scholarship Amount**: `{amount}`")
                    else:
                        st.write("💰 **Scholarship Amount**: *Not verified / Pending official circular*")

                    if deadline_iso:
                        st.write(f"📅 **Application Deadline**: `{deadline_iso}` (ISO)")
                        if deadline_raw and deadline_raw != deadline_iso:
                            st.caption(f"Source text: \"{deadline_raw}\"")
                    elif deadline_raw:
                        st.write(f"📅 **Application Deadline**: `{deadline_raw}` *(Date unverified)*")
                    else:
                        st.write("📅 **Application Deadline**: *To be announced / Not established*")

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
