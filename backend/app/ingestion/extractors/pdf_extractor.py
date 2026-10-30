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
        scheme_title = None
        for p in paragraphs[:15]:
            p_lower = p.lower()
            if not is_generic_scheme_name(p) and any(
                k in p_lower for k in ["scheme of scholarship", "central sector scheme", "scholarship for", "guidelines for"]
            ):
                scheme_title = p[:150].strip()
                break

        if not scheme_title:
            for p in paragraphs[:10]:
                if not is_generic_scheme_name(p) and any(k in p.lower() for k in ["scholarship", "fellowship"]) and len(p) < 100:
                    scheme_title = p.strip()
                    break

        if not scheme_title or is_generic_scheme_name(scheme_title):
            return []

        field_evidence: Dict[str, str] = {"scheme_name": scheme_title}
        supporting_quotations: List[str] = [scheme_title]

        # Extract eligibility
        raw_eligibility: List[str] = []
        for p in paragraphs:
            p_lower = p.lower()
            if any(term in p_lower for term in ["eligibility", "eligible", "minimum marks", "family income", "percentile"]):
                if len(p) > 20 and p not in raw_eligibility:
                    raw_eligibility.append(p[:250])
                    if "eligibility_criteria" not in field_evidence:
                        field_evidence["eligibility_criteria"] = p[:300]
                    if len(supporting_quotations) < 4 and p not in supporting_quotations:
                        supporting_quotations.append(p[:300])

        cleaned_eligibility = clean_eligibility_criteria(raw_eligibility)

        # Extract amount prioritizing explicit scholarship amount over family income limits
        raw_amount = None
        pat_explicit = r"(?i)(?:scholarship amount|rate of scholarship|stipend|allowance)\s*[:\-]\s*([^\n;]+)"
        for p in paragraphs:
            m = re.search(pat_explicit, p)
            if m:
                raw_amount = m.group(1).strip()
                field_evidence["scholarship_amount"] = p[:300]
                if p not in supporting_quotations and len(supporting_quotations) < 4:
                    supporting_quotations.append(p[:300])
                break

        if not raw_amount:
            pat_currency = r"(?i)(?:rs\.?|₹)\s*[\d,]+(?:\s*(?:per|p\.?a\.?|per annum|per month|yearly|monthly|lakh|crore)[^\n,.;]*)?"
            for p in paragraphs:
                if any(inc in p.lower() for inc in ["family income", "parental income", "gross income"]):
                    continue
                m = re.search(pat_currency, p)
                if m:
                    raw_amount = m.group(0).strip()
                    field_evidence["scholarship_amount"] = p[:300]
                    if p not in supporting_quotations and len(supporting_quotations) < 4:
                        supporting_quotations.append(p[:300])
                    break

        # Validate amount against sanity rules
        validated_amount, amount_is_valid, _ = clean_and_validate_amount(raw_amount)
        amount_final = validated_amount if amount_is_valid else None

        # Extract deadline
        raw_deadline = None
        deadline_patterns = [
            r"(?:last date|deadline|closing date|closing dates)(?:\s*is|\s*for submission)?\s*[:\-]?\s*([^\n.;]{4,60})",
            r"(?:up to|till)\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
            r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})",
        ]
        for p in paragraphs:
            for pat in deadline_patterns:
                m = re.search(pat, p, flags=re.IGNORECASE)
                if m:
                    raw_deadline = m.group(0).strip()
                    field_evidence["application_deadline"] = p[:300]
                    if p not in supporting_quotations and len(supporting_quotations) < 4:
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

        return [
            {
                "scheme_name": scheme_title,
                "government_authority": default_authority,
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
            }
        ]
