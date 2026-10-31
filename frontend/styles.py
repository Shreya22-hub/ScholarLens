"""ScholarLens centralized CSS design system and presentation components.

Provides custom styling, design tokens, hero banner, and reusable presentation
utilities for a modern, executive scholarship discovery dashboard.
"""

import streamlit as st

__all__ = [
    "CUSTOM_CSS",
    "inject_custom_styles",
    "render_hero_section",
    "render_kpi_dashboard",
]


# =============================================================================
# CSS Design System Tokens & Injection
# =============================================================================

CUSTOM_CSS = """
<style>
/* Root Design Tokens */
:root {
    --sl-navy-dark: #0f172a;
    --sl-navy-mid: #1e293b;
    --sl-navy-light: #334155;
    --sl-blue-primary: #2563eb;
    --sl-blue-hover: #1d4ed8;
    --sl-blue-light: #eff6ff;
    --sl-emerald-primary: #059669;
    --sl-emerald-bg: #ecfdf5;
    --sl-emerald-border: #a7f3d0;
    --sl-amber-primary: #d97706;
    --sl-amber-bg: #fffbeb;
    --sl-amber-border: #fde68a;
    --sl-red-primary: #dc2626;
    --sl-red-bg: #fef2f2;
    --sl-red-border: #fecaca;
    --sl-slate-bg: #f8fafc;
    --sl-border-color: #e2e8f0;
    --sl-card-shadow: 0 4px 6px -1px rgba(15, 23, 42, 0.05), 0 2px 4px -2px rgba(15, 23, 42, 0.03);
}

/* Page Layout Refinements */
.main .block-container {
    max-width: 1240px;
    padding-top: 1.5rem;
    padding-bottom: 3.5rem;
}

/* Typography Enhancements */
h1, h2, h3, h4, h5, h6 {
    color: var(--sl-navy-dark);
    font-weight: 700;
    letter-spacing: -0.02em;
}

/* Streamlit Tabs Polish */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    border-bottom: 2px solid var(--sl-border-color);
    padding-bottom: 4px;
}

.stTabs [data-baseweb="tab"] {
    font-weight: 600;
    font-size: 0.95rem;
    padding: 8px 16px;
    border-radius: 8px 8px 0 0;
    color: var(--sl-navy-light);
    border: none;
    transition: all 0.15s ease-in-out;
}

.stTabs [data-baseweb="tab"]:hover {
    color: var(--sl-blue-primary);
    background-color: var(--sl-slate-bg);
}

.stTabs [data-baseweb="tab"][aria-selected="true"] {
    color: var(--sl-blue-primary);
    border-bottom: 3px solid var(--sl-blue-primary);
    background-color: transparent;
}

/* Primary Button Styling */
div.stButton > button[kind="primary"] {
    background-color: var(--sl-blue-primary);
    color: #ffffff;
    font-weight: 600;
    border-radius: 8px;
    border: 1px solid var(--sl-blue-primary);
    padding: 0.6rem 1.25rem;
    box-shadow: 0 2px 4px rgba(37, 99, 235, 0.2);
    transition: all 0.15s ease-in-out;
}

div.stButton > button[kind="primary"]:hover {
    background-color: var(--sl-blue-hover);
    border-color: var(--sl-blue-hover);
    box-shadow: 0 4px 8px rgba(37, 99, 235, 0.3);
}

/* Secondary Button Styling */
div.stButton > button:not([kind="primary"]) {
    border-radius: 8px;
    border: 1px solid var(--sl-border-color);
    font-weight: 500;
    transition: all 0.15s ease;
}

div.stButton > button:not([kind="primary"]):hover {
    border-color: var(--sl-blue-primary);
    color: var(--sl-blue-primary);
}

/* Metric Cards Refinement */
div[data-testid="stMetric"] {
    background-color: #ffffff;
    border: 1px solid var(--sl-border-color);
    border-radius: 10px;
    padding: 1rem 1.2rem;
    box-shadow: var(--sl-card-shadow);
}

div[data-testid="stMetric"] [data-testid="stMetricLabel"] {
    font-weight: 600;
    font-size: 0.85rem;
    color: var(--sl-navy-light);
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

div[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-weight: 800;
    font-size: 1.85rem;
    color: var(--sl-navy-dark);
}

/* Form Container Styling */
div[data-testid="stForm"] {
    border: 1px solid var(--sl-border-color);
    border-radius: 12px;
    padding: 1.75rem 1.85rem;
    background-color: #ffffff;
    box-shadow: var(--sl-card-shadow);
}

/* Form Section Headers & Dividers */
.sl-form-section-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 0.25rem 0 0.85rem 0;
}

.sl-section-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    background: linear-gradient(135deg, var(--sl-blue-primary) 0%, #1d4ed8 100%);
    color: #ffffff;
    border-radius: 50%;
    font-size: 0.85rem;
    font-weight: 700;
    flex-shrink: 0;
    box-shadow: 0 2px 4px rgba(37, 99, 235, 0.25);
}

.sl-section-title {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--sl-navy-dark);
    margin: 0;
    line-height: 1.25;
}

.sl-section-subtitle {
    font-size: 0.82rem;
    color: var(--sl-navy-light);
    margin: 0.2rem 0 0 0;
    line-height: 1.35;
}

/* Expander Polish */
div[data-testid="stExpander"] {
    border: 1px solid var(--sl-border-color);
    border-radius: 10px;
    background-color: #ffffff;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);
}

/* Hero Section Components */
.sl-hero-container {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 60%, #0f172a 100%);
    border-radius: 14px;
    padding: 2rem 2.25rem;
    margin-bottom: 1.75rem;
    color: #ffffff;
    box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.2);
    border: 1px solid rgba(255, 255, 255, 0.12);
}

.sl-hero-tag {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    background-color: rgba(37, 99, 235, 0.25);
    border: 1px solid rgba(96, 165, 250, 0.4);
    border-radius: 9999px;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: #93c5fd;
    text-transform: uppercase;
    margin-bottom: 0.75rem;
}

.sl-hero-title {
    font-size: 2.15rem;
    font-weight: 800;
    letter-spacing: -0.03em;
    color: #ffffff;
    margin: 0 0 0.5rem 0;
    line-height: 1.2;
}

.sl-hero-subtitle {
    font-size: 1.05rem;
    color: #cbd5e1;
    line-height: 1.55;
    margin: 0 0 1.25rem 0;
    max-width: 960px;
}

.sl-trust-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
}

.sl-trust-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 14px;
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 500;
    color: #f8fafc;
    backdrop-filter: blur(4px);
}

.sl-trust-badge b {
    color: #ffffff;
    font-weight: 700;
}

/* Sidebar Custom Styling */
.sl-sidebar-brand {
    padding: 0.5rem 0 1rem 0;
    border-bottom: 1px solid var(--sl-border-color);
    margin-bottom: 1rem;
}

.sl-sidebar-brand h2 {
    font-size: 1.35rem;
    margin: 0;
    color: var(--sl-navy-dark);
}

.sl-sidebar-brand p {
    font-size: 0.85rem;
    color: var(--sl-navy-light);
    margin: 0.25rem 0 0 0;
}

.sl-principle-card {
    background-color: var(--sl-slate-bg);
    border: 1px solid var(--sl-border-color);
    border-radius: 8px;
    padding: 0.75rem 0.9rem;
    margin-bottom: 0.6rem;
}

.sl-principle-card h4 {
    font-size: 0.85rem;
    margin: 0 0 0.25rem 0;
    color: var(--sl-navy-dark);
}

.sl-principle-card p {
    font-size: 0.8rem;
    color: var(--sl-navy-light);
    margin: 0;
    line-height: 1.4;
}

/* Badges for Scholarship Statuses */
.sl-pill-badge {
    display: inline-block;
    padding: 3px 9px;
    border-radius: 6px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.03em;
    text-transform: uppercase;
}

.sl-pill-open {
    background-color: var(--sl-emerald-bg);
    color: var(--sl-emerald-primary);
    border: 1px solid var(--sl-emerald-border);
}

.sl-pill-pending {
    background-color: var(--sl-amber-bg);
    color: var(--sl-amber-primary);
    border: 1px solid var(--sl-amber-border);
}

.sl-pill-closed {
    background-color: var(--sl-red-bg);
    color: var(--sl-red-primary);
    border: 1px solid var(--sl-red-border);
}

.sl-pill-unverified {
    background-color: #fff7ed;
    color: #c2410c;
    border: 1px solid #ffedd5;
}

.sl-pill-slate {
    background-color: #f1f5f9;
    color: #475569;
    border: 1px solid #e2e8f0;
}

/* Executive KPI Dashboard Cards */
.sl-kpi-card {
    background-color: #ffffff;
    border: 1px solid var(--sl-border-color);
    border-radius: 12px;
    padding: 1.25rem 1.25rem 1.15rem 1.25rem;
    box-shadow: var(--sl-card-shadow);
    height: 100%;
    min-height: 155px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
    border-top: 4px solid var(--sl-border-color);
}

.sl-kpi-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 12px rgba(15, 23, 42, 0.08);
}

.sl-kpi-card-open {
    border-top-color: var(--sl-emerald-primary);
    background: linear-gradient(180deg, #f0fdf4 0%, #ffffff 45%);
}

.sl-kpi-card-hist {
    border-top-color: #64748b;
    background: linear-gradient(180deg, #f8fafc 0%, #ffffff 45%);
}

.sl-kpi-card-pot {
    border-top-color: var(--sl-amber-primary);
    background: linear-gradient(180deg, #fffbeb 0%, #ffffff 45%);
}

.sl-kpi-card-not {
    border-top-color: var(--sl-red-primary);
    background: linear-gradient(180deg, #fef2f2 0%, #ffffff 45%);
}

.sl-kpi-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.65rem;
}

.sl-kpi-label {
    font-size: 0.85rem;
    font-weight: 700;
    color: var(--sl-navy-dark);
    letter-spacing: -0.01em;
}

.sl-kpi-badge {
    font-size: 0.7rem;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 9999px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

.sl-kpi-badge-open {
    background-color: var(--sl-emerald-bg);
    color: var(--sl-emerald-primary);
    border: 1px solid var(--sl-emerald-border);
}

.sl-kpi-badge-hist {
    background-color: #f1f5f9;
    color: #475569;
    border: 1px solid #cbd5e1;
}

.sl-kpi-badge-pot {
    background-color: var(--sl-amber-bg);
    color: var(--sl-amber-primary);
    border: 1px solid var(--sl-amber-border);
}

.sl-kpi-badge-not {
    background-color: var(--sl-red-bg);
    color: var(--sl-red-primary);
    border: 1px solid var(--sl-red-border);
}

.sl-kpi-value {
    font-size: 2.25rem;
    font-weight: 800;
    line-height: 1.1;
    color: var(--sl-navy-dark);
    margin-bottom: 0.5rem;
}

.sl-kpi-desc {
    font-size: 0.8rem;
    color: var(--sl-navy-light);
    line-height: 1.4;
    margin: 0;
}

/* Distinct Audit Banner */
.sl-audit-banner {
    background-color: #fff7ed;
    border: 1px solid #fed7aa;
    border-left: 4px solid #ea580c;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    margin: 1.25rem 0 0.85rem 0;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}

.sl-audit-header {
    display: flex;
    align-items: flex-start;
    gap: 12px;
}

.sl-audit-shield {
    font-size: 1.35rem;
    line-height: 1.2;
    flex-shrink: 0;
}

.sl-audit-title {
    font-size: 0.92rem;
    font-weight: 700;
    color: #9a3412;
    margin-bottom: 0.25rem;
}

.sl-audit-desc {
    font-size: 0.82rem;
    color: #7c2d12;
    line-height: 1.45;
}

.sl-result-caption {
    font-size: 0.82rem;
    color: var(--sl-navy-light);
    background-color: #f8fafc;
    border: 1px solid var(--sl-border-color);
    border-radius: 8px;
    padding: 0.65rem 0.95rem;
    margin-bottom: 1.25rem;
    line-height: 1.45;
}

/* Modernized Scholarship Cards */
.sl-card-container {
    background-color: #ffffff;
    border: 1px solid var(--sl-border-color);
    border-radius: 12px;
    padding: 1.25rem 1.4rem;
    margin-bottom: 1.15rem;
    box-shadow: 0 2px 4px rgba(15, 23, 42, 0.04);
}

.sl-card-top-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 0.5rem;
}

.sl-card-authority-badge {
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 700;
    color: var(--sl-blue-primary);
    background-color: var(--sl-blue-light);
    border: 1px solid #bfdbfe;
    padding: 2px 8px;
    border-radius: 4px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 0.35rem;
}

.sl-card-title {
    font-size: 1.18rem;
    font-weight: 700;
    color: var(--sl-navy-dark);
    margin: 0;
    line-height: 1.3;
}

.sl-pillar-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0.65rem 0 0.85rem 0;
}

.sl-pillar-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 0.76rem;
    font-weight: 600;
    border: 1px solid var(--sl-border-color);
    background-color: var(--sl-slate-bg);
    color: var(--sl-navy-dark);
}

.sl-key-facts-box {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 12px;
    background-color: var(--sl-slate-bg);
    border: 1px solid var(--sl-border-color);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin: 0.75rem 0;
}

.sl-fact-unit {
    display: flex;
    flex-direction: column;
}

.sl-fact-title {
    font-size: 0.72rem;
    font-weight: 700;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.03em;
}

.sl-fact-content {
    font-size: 0.92rem;
    font-weight: 700;
    color: var(--sl-navy-dark);
    margin-top: 2px;
}

.sl-fact-sub {
    font-size: 0.74rem;
    color: #64748b;
    margin-top: 1px;
}

.sl-criteria-breakdown {
    margin-top: 0.5rem;
    padding: 0.5rem 0;
}

.sl-criterion-item {
    padding: 0.4rem 0.6rem;
    border-radius: 6px;
    margin-bottom: 0.35rem;
    font-size: 0.82rem;
    line-height: 1.4;
}

.sl-criterion-pass {
    background-color: #f0fdf4;
    border-left: 3px solid var(--sl-emerald-primary);
    color: #14532d;
}

.sl-criterion-fail {
    background-color: #fef2f2;
    border-left: 3px solid var(--sl-red-primary);
    color: #7f1d1d;
}

.sl-criterion-unknown {
    background-color: #fffbeb;
    border-left: 3px solid var(--sl-amber-primary);
    color: #78350f;
}

.sl-citation-quote {
    background-color: #f8fafc;
    border-left: 3px solid var(--sl-blue-primary);
    padding: 0.5rem 0.85rem;
    margin: 0.4rem 0;
    border-radius: 0 6px 6px 0;
    font-style: italic;
    font-size: 0.82rem;
    color: #334155;
}

/* Instant Search & Quick Filter Controls */
.sl-filter-title {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--sl-navy-dark);
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 0.25rem;
}

.sl-filter-count-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 10px;
    background-color: var(--sl-blue-light);
    border: 1px solid #bfdbfe;
    border-radius: 9999px;
    font-size: 0.8rem;
    font-weight: 700;
    color: var(--sl-blue-primary);
}

.sl-active-filters-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    margin-top: 0.65rem;
    padding-top: 0.6rem;
    border-top: 1px solid #f1f5f9;
    font-size: 0.82rem;
    color: var(--sl-navy-light);
}

.sl-active-filter-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background-color: var(--sl-slate-bg);
    border: 1px solid var(--sl-border-color);
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 0.76rem;
    font-weight: 600;
    color: var(--sl-navy-dark);
}

/* Empty Search State Presentation */
.sl-empty-search-state {
    text-align: center;
    padding: 2.25rem 1.5rem 1.25rem 1.5rem;
    background-color: #f8fafc;
    border: 1px dashed #cbd5e1;
    border-radius: 12px;
    margin: 1rem 0;
}

.sl-empty-search-icon {
    font-size: 2.25rem;
    margin-bottom: 0.5rem;
    line-height: 1;
}

.sl-empty-search-title {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--sl-navy-dark);
    margin-bottom: 0.35rem;
}

.sl-empty-search-desc {
    font-size: 0.85rem;
    color: var(--sl-navy-light);
    max-width: 540px;
    margin: 0 auto 0.75rem auto;
    line-height: 1.45;
}
</style>
"""


def inject_custom_styles() -> None:
    """Inject centralized CSS styles into the Streamlit app DOM."""
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def render_hero_section() -> None:
    """Render the executive ScholarLens Hero Header with value proposition and trust indicators."""
    hero_html = """
    <div class="sl-hero-container">
        <div class="sl-hero-tag">
            <span>Evidence-Based Discovery Platform</span>
        </div>
        <div class="sl-hero-title">ScholarLens</div>
        <div class="sl-hero-subtitle">
            Evidence-backed scholarship discovery tracking official portals and published circulars for Indian students.
            Discover eligible opportunities with deterministic rule matching, verifiable source citations, and human-friendly explanations.
        </div>
        <div class="sl-trust-grid">
            <div class="sl-trust-badge">
                <span>🛡️</span>
                <span><b>Deterministic Evaluation</b> — Strictly Grounded in Circulars</span>
            </div>
            <div class="sl-trust-badge">
                <span>🏛️</span>
                <span><b>Tracked Official Portals</b> — AICTE • UGC • Central Ministries</span>
            </div>
            <div class="sl-trust-badge">
                <span>🎯</span>
                <span><b>Explainable Rules</b> — Criteria Passed & Failed Breakdowns</span>
            </div>
        </div>
    </div>
    """
    st.markdown(hero_html, unsafe_allow_html=True)


def render_kpi_dashboard(
    open_count: int,
    historical_count: int,
    potentially_count: int,
    not_eligible_count: int,
    unverified_count: int,
) -> None:
    """Render the executive 4-card KPI dashboard and separated audit indicator banner."""
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown(
            f"""
            <div class="sl-kpi-card sl-kpi-card-open">
                <div class="sl-kpi-header">
                    <span class="sl-kpi-label">Confirmed Open</span>
                    <span class="sl-kpi-badge sl-kpi-badge-open">Verified Open</span>
                </div>
                <div class="sl-kpi-value">{open_count}</div>
                <div class="sl-kpi-desc">Verified open opportunities currently accepting student applications.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""
            <div class="sl-kpi-card sl-kpi-card-hist">
                <div class="sl-kpi-header">
                    <span class="sl-kpi-label">Historical / Unconfirmed</span>
                    <span class="sl-kpi-badge sl-kpi-badge-hist">Past Cycle</span>
                </div>
                <div class="sl-kpi-value">{historical_count}</div>
                <div class="sl-kpi-desc">Matches guideline criteria from past cycles or unconfirmed dates.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            f"""
            <div class="sl-kpi-card sl-kpi-card-pot">
                <div class="sl-kpi-header">
                    <span class="sl-kpi-label">Potentially Eligible</span>
                    <span class="sl-kpi-badge sl-kpi-badge-pot">Pending Info</span>
                </div>
                <div class="sl-kpi-value">{potentially_count}</div>
                <div class="sl-kpi-desc">Matches rules; pending additional profile details or circular review.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col4:
        st.markdown(
            f"""
            <div class="sl-kpi-card sl-kpi-card-not">
                <div class="sl-kpi-header">
                    <span class="sl-kpi-label">Not Eligible</span>
                    <span class="sl-kpi-badge sl-kpi-badge-not">Failed Criteria</span>
                </div>
                <div class="sl-kpi-value">{not_eligible_count}</div>
                <div class="sl-kpi-desc">Profile does not meet one or more mandatory eligibility requirements.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Visually distinct Data-Quality & Provenance Audit Indicator (separated outside the 4 primary cards)
    st.markdown(
        f"""
        <div class="sl-audit-banner">
            <div class="sl-audit-header">
                <span class="sl-audit-shield">🛡️</span>
                <div>
                    <div class="sl-audit-title">Data-Quality & Provenance Audit Indicator</div>
                    <div class="sl-audit-desc">
                        <b>{unverified_count} provisional record(s)</b> with unverified source evidence are isolated in the <b>Provisional / Unverified</b> tab.
                        This audit count identifies data-quality status and is not an additional category to add to the verified outcome totals above.
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="sl-result-caption">
            📊 <b>Result Categorization:</b> Each scholarship appears in exactly one primary subtab below.
            The primary outcome metrics reflect verified scholarships evaluated against your profile.
            A scholarship matching eligibility criteria is never counted as confirmed open unless its application status is verified as open.
        </div>
        """,
        unsafe_allow_html=True,
    )
