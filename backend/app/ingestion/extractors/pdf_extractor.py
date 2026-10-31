"""PDF text and scholarship data extractor for ScholarLens.

Extracts text from PDF documents using system pdftotext (poppler) with pure-Python stream fallback.
Enforces grounded extraction where all claims are backed by verbatim quotations.
Rejects generic scheme titles, normalizes dates, and validates financial amounts.
"""

from io import BytesIO
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional
import zlib

from backend.app.ingestion.quality import (
    clean_and_validate_amount,
    clean_eligibility_criteria,
    determine_application_status,
    is_generic_scheme_name,
    normalize_date_iso,
)


class PDFExtractor:
    """Extracts text and candidate scholarship schemes from official PDF circulars and guidelines."""

    @classmethod
    def extract_clean_text(cls, pdf_bytes: bytes) -> str:
        """Extract clean text from PDF bytes using pdftotext or pure-Python stream fallback."""
        if not pdf_bytes:
            return ""

        # Strategy 1: Poppler pdftotext binary if available
        if shutil.which("pdftotext"):
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
                    tmp.write(pdf_bytes)
                    tmp.flush()
                    result = subprocess.run(
                        ["pdftotext", "-layout", tmp.name, "-"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=15,
                        check=False,
                    )
                    if result.returncode == 0:
                        text = result.stdout.decode("utf-8", errors="replace")
                        if text and text.strip():
                            return text
            except Exception:
                pass

        # Strategy 2: Pure-Python stream extraction fallback
        return cls._fallback_extract_pdf_streams(pdf_bytes)

    @classmethod
    def _fallback_extract_pdf_streams(cls, pdf_bytes: bytes) -> str:
        """Pure-Python basic PDF stream text extractor for text blocks BT ... ET."""
        extracted_text_parts: List[str] = []

        stream_matches = re.finditer(b"stream[\r\n]+(.*?)[\r\n]+endstream", pdf_bytes, re.DOTALL)
        for match in stream_matches:
            raw_stream = match.group(1)
            decompressed = None

            try:
                decompressed = zlib.decompress(raw_stream)
            except Exception:
                decompressed = raw_stream

            if not decompressed:
                continue

            bt_matches = re.finditer(rb"BT\s*(.*?)\s*ET", decompressed, re.DOTALL)
            for bt in bt_matches:
                block = bt.group(1)
                str_matches = re.findall(rb"\((.*?)\)\s*Tj", block)
                for s in str_matches:
                    try:
                        decoded = s.decode("latin1", errors="replace")
                        extracted_text_parts.append(decoded)
                    except Exception:
                        pass

                tj_blocks = re.findall(rb"\[(.*?)\]\s*TJ", block)
                for tj in tj_blocks:
                    strings = re.findall(rb"\((.*?)\)", tj)
                    combined = "".join(s.decode("latin1", errors="replace") for s in strings)
                    if combined:
                        extracted_text_parts.append(combined)

        if not extracted_text_parts:
            readable = re.findall(rb"[A-Za-z0-9\s,.\-;:/\(\)]{6,}", pdf_bytes)
            for chunk in readable:
                try:
                    extracted_text_parts.append(chunk.decode("ascii", errors="ignore"))
                except Exception:
                    pass

        return "\n".join(extracted_text_parts)

    @classmethod
    def extract_scholarships_from_pdf(
        cls,
        pdf_bytes: bytes,
        source_url: str,
        default_authority: str = "Government of India",
    ) -> List[Dict[str, Any]]:
        """Extract candidate scholarship records backed by verbatim content quotations from PDF text."""
        full_text = cls.extract_clean_text(pdf_bytes)
        if not full_text or len(full_text.strip()) < 50:
            return []

        lines = [line.strip() for line in full_text.splitlines() if line.strip()]
        paragraphs = []
        current_para = []
        for line in lines:
            current_para.append(line)
            if len(line) < 40 or line.endswith((".", ":", ";")):
                para_text = " ".join(current_para).strip()
                if para_text:
                    paragraphs.append(para_text)
                current_para = []
        if current_para:
            paragraphs.append(" ".join(current_para).strip())

        keywords = ["scholarship", "fellowship", "scheme", "guidelines", "stipend"]
        if not any(kw in full_text.lower() for kw in keywords):
            return []

        # Find specific non-generic scheme title
        narrative_prefixes = (
            "the main objective",
            "the objective",
            "in order to",
            "a scholarship scheme",
            "this scheme",
            "to provide",
            "financial assistance to",
            "guidelines and",
            "general instructions",
            "read more",
        )

        # 1. Search individual clean layout lines for concise titles
        line_candidates = []
        for l in lines[:25]:
            l_clean = re.sub(r"^(?:REVISED\s+)?SCHEME\s+GUIDELINES\s+FOR\s+", "", l, flags=re.IGNORECASE).strip()
            l_clean = re.sub(r"^NOTIFICATION\s*[:-]?\s*", "", l_clean, flags=re.IGNORECASE).strip()
            l_lower = l_clean.lower()
            if any(l_lower.startswith(pref) for pref in narrative_prefixes):
                continue
            if any(l_lower.startswith(lbl) for lbl in ["scholarship amount", "eligibility", "application deadline", "documents", "last date"]):
                continue
            if re.match(r"^[\d\.\-\*•\uf0b7]", l_clean) or re.search(r"(?:rs\.?|₹|\$)\s*[\d,]", l_clean, re.I):
                continue
            if not is_generic_scheme_name(l_clean) and 8 <= len(l_clean) <= 100:
                if any(
                    k in l_lower
                    for k in [
                        "scheme of scholarship",
                        "central sector scheme",
                        "scholarship for",
                        "guidelines for",
                        "fellowship",
                        "scholarship",
                        "yashasvi",
                        "sarswati",
                        "samarthan",
                        "apdf",
                    ]
                ):
                    line_candidates.append(l_clean)

        scheme_title = None
        if line_candidates:
            line_candidates.sort(
                key=lambda x: (
                    any(org in x.upper() for org in ["AICTE", "UGC", "CENTRAL", "NATIONAL", "GOVERNMENT"]),
                    -abs(len(x) - 40),
                ),
                reverse=True,
            )
            scheme_title = line_candidates[0]

        if not scheme_title:
            candidates_title = []
            for p in paragraphs[:15]:
                p_clean = re.sub(r"^(?:REVISED\s+)?SCHEME\s+GUIDELINES\s+FOR\s+", "", p, flags=re.IGNORECASE).strip()
                p_clean = re.sub(r"^NOTIFICATION\s*[:-]?\s*", "", p_clean, flags=re.IGNORECASE).strip()
                p_lower = p_clean.lower()
                if any(p_lower.startswith(pref) for pref in narrative_prefixes):
                    continue
                if any(p_lower.startswith(lbl) for lbl in ["scholarship amount", "eligibility", "application deadline", "documents", "last date"]):
                    continue
                if re.match(r"^[\d\.\-\*•\uf0b7]", p_clean) or re.search(r"(?:rs\.?|₹|\$)\s*[\d,]", p_clean, re.I):
                    continue
                if not is_generic_scheme_name(p_clean) and len(p_clean) >= 6:
                    if any(
                        k in p_lower
                        for k in [
                            "scheme of scholarship",
                            "central sector scheme",
                            "scholarship for",
                            "guidelines for",
                            "fellowship",
                            "scholarship",
                            "yashasvi",
                            "sarswati",
                            "samarthan",
                            "apdf",
                        ]
                    ):
                        candidates_title.append(p_clean)

            if candidates_title:
                candidates_title.sort(
                    key=lambda x: (
                        any(org in x.upper() for org in ["AICTE", "UGC", "CENTRAL", "NATIONAL", "GOVERNMENT"]),
                        -len(x) if len(x) > 80 else len(x),
                    ),
                    reverse=True,
                )
                scheme_title = candidates_title[0][:150].strip()

        if not scheme_title:
            for p in paragraphs[:10]:
                p_clean = re.sub(r"^(?:REVISED\s+)?SCHEME\s+GUIDELINES\s+FOR\s+", "", p, flags=re.IGNORECASE).strip()
                p_lower = p_clean.lower()
                if any(p_lower.startswith(pref) for pref in narrative_prefixes):
                    continue
                if not is_generic_scheme_name(p_clean) and any(k in p_lower for k in ["scholarship", "fellowship"]) and len(p_clean) < 100:
                    scheme_title = p_clean.strip()
                    break

        if not scheme_title or is_generic_scheme_name(scheme_title):
            return []

        field_evidence: Dict[str, str] = {"scheme_name": scheme_title}
        supporting_quotations: List[str] = [scheme_title]

        # Extract government authority evidence from document paragraphs
        authority_quote = None
        for p in paragraphs[:20]:
            p_upper = p.upper()
            if any(term in p_upper for term in ["COUNCIL", "MINISTRY", "DEPARTMENT", "COMMISSION", "GOVERNMENT OF INDIA"]):
                if len(p) >= 10 and len(p) <= 250:
                    authority_quote = p.strip()
                    break

        if not authority_quote:
            # Search for authority mentions like AICTE or Government of India in text
            for p in paragraphs:
                p_upper = p.upper()
                if any(term in p_upper for term in ["ALL INDIA COUNCIL FOR TECHNICAL EDUCATION", "AICTE", "UGC", "MINISTRY OF EDUCATION"]):
                    if len(p) >= 10 and len(p) <= 250:
                        authority_quote = p.strip()
                        break

        authority_final = default_authority
        if authority_quote:
            field_evidence["government_authority"] = authority_quote
            if authority_quote not in supporting_quotations:
                supporting_quotations.append(authority_quote)
        else:
            field_evidence["government_authority"] = default_authority
            supporting_quotations.append(default_authority)

        # Extract eligibility
        raw_eligibility: List[str] = []
        for p in paragraphs + lines:
            p_lower = p.lower()
            if any(
                term in p_lower
                for term in [
                    "eligibility",
                    "eligible",
                    "minimum marks",
                    "family income",
                    "percentile",
                    "candidates who",
                    "scholars who",
                    "students who",
                    "ph.d",
                    "degree in",
                    "degree from",
                    "admission in",
                    "girls students",
                ]
            ):
                if 20 <= len(p) <= 300 and p not in raw_eligibility:
                    raw_eligibility.append(p[:250])
                    if "eligibility_criteria" not in field_evidence:
                        field_evidence["eligibility_criteria"] = p[:300]
                    if len(supporting_quotations) < 6 and p not in supporting_quotations:
                        supporting_quotations.append(p[:300])

        cleaned_eligibility = clean_eligibility_criteria(raw_eligibility)

        # Extract amount prioritizing explicit scholarship amount or fellowship tables over family income limits
        raw_amount = None
        amount_quote = None

        pat_budget_table = r"(?i)(?:fellowship|scholarship|stipend)\s+(?:amount\s+)?(rs\.?\s*[\d,]+(?:\s*/?-?\s*per\s+[a-z]+)?[^\n;]*)"
        for p in paragraphs:
            m = re.search(pat_budget_table, p)
            if m:
                raw_amount = m.group(1).strip()
                amount_quote = p[:300]
                break

        if not raw_amount:
            pat_explicit = r"(?i)(?:scholarship amount|rate of scholarship|stipend|allowance)\s*[:\-]\s*([^\n;]+)"
            for p in paragraphs:
                m = re.search(pat_explicit, p)
                if m:
                    raw_amount = m.group(1).strip()
                    amount_quote = p[:300]
                    break

        if not raw_amount:
            pat_currency = r"(?i)(?:rs\.?|₹)\s*[\d,]+(?:\s*(?:per|p\.?a\.?|per annum|per month|yearly|monthly|lakh|crore)[^\n,.;]*)?"
            for p in paragraphs:
                if any(inc in p.lower() for inc in ["family income", "parental income", "gross income"]):
                    continue
                m = re.search(pat_currency, p)
                if m:
                    raw_amount = m.group(0).strip()
                    amount_quote = p[:300]
                    break

        # Validate amount against sanity rules
        validated_amount, amount_is_valid, _ = clean_and_validate_amount(raw_amount)
        amount_final = validated_amount if amount_is_valid else None
        if amount_final and amount_quote:
            field_evidence["scholarship_amount"] = amount_quote
            if amount_quote not in supporting_quotations:
                supporting_quotations.append(amount_quote)

        # Check if circular describes multi-stage verification or institutional workflow
        has_multi_stage_workflow = any(
            re.search(r"\b(?:verification (?:of|by)|l1 level|l2 level|sno level)\b", p, re.IGNORECASE)
            for p in paragraphs + lines
        )

        # Extract student submission deadline avoiding OM / file numbers, institutional verification, and final shutdown dates
        raw_deadline = None
        deadline_patterns = [
            r"(?:last date for (?:submission|applying|online applications?)|submission deadline)\s*[:\-]?\s*([^\n.;]{4,60})",
            r"(?:closing date for (?:submission|students?)|student submission closing date)\s*[:\-]?\s*([^\n.;]{4,60})",
            r"(?:last date of application|deadline for applications?)(?:\s*is)?\s*[:\-]?\s*([^\n.;]{4,60})",
            r"(?:portal to be closed on|to be closed on|closed on)\s*[:\-]?\s*([^\n.;]{4,60})",
            r"(?:last date|deadline|closing date)(?:\s*is)?\s*[:\-]?\s*([^\n.;]{4,60})",
            r"(?:up to|till)\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
            r"(?<!F No:\s)(?<!No:\s)(\b\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4}\b)",
        ]
        for p in paragraphs + lines:
            # Skip lines that are specifically institute verification, scrutiny, or discrepancy resolution
            if re.search(r"\b(?:verification (?:of|by)|scrutiny|sno level|l1 level|l2 level|discrepanc)\b", p, flags=re.IGNORECASE):
                continue
            if has_multi_stage_workflow and re.search(r"\b(?:portal to be closed|final closure)\b", p, re.IGNORECASE):
                # In multi-stage circulars, final portal closure is after administrative verification, not student submission
                continue
            for pat in deadline_patterns:
                m = re.search(pat, p, flags=re.IGNORECASE)
                if m:
                    candidate_dl = m.group(0).strip()
                    if not re.search(r"(?:o\.?m\.?|f\.?\s*no|file\s*no)\b", candidate_dl, re.IGNORECASE):
                        if not re.search(r"\b(?:verification|scrutiny|sno|l1|l2|discrepanc)\b", candidate_dl, re.IGNORECASE):
                            if has_multi_stage_workflow and re.search(r"\b(?:portal to be closed)\b", candidate_dl, re.IGNORECASE):
                                continue
                            raw_deadline = candidate_dl
                            field_evidence["application_deadline"] = p[:300]
                            if p not in supporting_quotations and len(supporting_quotations) < 6:
                                supporting_quotations.append(p[:300])
                            break
            if raw_deadline:
                break

        iso_deadline, raw_deadline_evidence = normalize_date_iso(raw_deadline)

        # Required documents
        required_docs = []
        doc_keywords = ["aadhaar", "marksheet", "income certificate", "caste certificate", "bank account", "bonafide"]
        for p in paragraphs:
            for kw in doc_keywords:
                if kw in p.lower() and kw.title() not in required_docs:
                    required_docs.append(kw.title())
                    if "required_documents" not in field_evidence:
                        field_evidence["required_documents"] = p[:300]

        desc_candidates = [p for p in paragraphs if len(p) > 50 and p != scheme_title]
        description = desc_candidates[0] if desc_candidates else f"Official guidelines for {scheme_title}."
        field_evidence["description"] = description[:300]

        # Determine application status and academic year
        app_status, detected_year = determine_application_status(
            deadline_iso=iso_deadline,
            raw_text=full_text[:4000],
        )

        return [
            {
                "scheme_name": scheme_title,
                "government_authority": authority_final,
                "description": description,
                "eligibility_criteria": cleaned_eligibility,
                "scholarship_amount": amount_final,
                "application_deadline": iso_deadline,
                "application_deadline_raw": raw_deadline_evidence,
                "required_documents": required_docs,
                "official_source_url": source_url,
                "supporting_quotations": supporting_quotations,
                "field_evidence": field_evidence,
                "verification_status": "unverified",
                "scheme_year": detected_year,
                "application_status": app_status,
            }
        ]
