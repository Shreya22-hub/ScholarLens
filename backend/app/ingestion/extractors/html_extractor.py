"""HTML text and scholarship data extractor for ScholarLens.

Extracts structured text from HTML without external dependencies using standard library html.parser.
Enforces grounded extraction where all claims are backed by verbatim quotations.
Rejects generic scheme titles, normalizes dates, and validates financial amounts.
"""

from html import unescape
from html.parser import HTMLParser
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.app.ingestion.quality import (
    clean_and_validate_amount,
    clean_eligibility_criteria,
    is_generic_scheme_name,
    normalize_date_iso,
)


class CleanHTMLParser(HTMLParser):
    """HTML Parser that strips boilerplate tags and preserves structural blocks."""

    IGNORED_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header"}
    BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "div", "section", "article"}

    def __init__(self):
        super().__init__()
        self.ignore_depth = 0
        self.text_blocks: List[str] = []
        self.current_block: List[str] = []
        self.headings: List[Tuple[str, str]] = []  # (tag, text)
        self.current_tag: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        tag_lower = tag.lower()
        if tag_lower in self.IGNORED_TAGS:
            self.ignore_depth += 1
            return
        if self.ignore_depth > 0:
            return

        self.current_tag = tag_lower
        if tag_lower in self.BLOCK_TAGS and self.current_block:
            text = " ".join(self.current_block).strip()
            if text:
                self.text_blocks.append(text)
            self.current_block = []

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower in self.IGNORED_TAGS:
            if self.ignore_depth > 0:
                self.ignore_depth -= 1
            return
        if self.ignore_depth > 0:
            return

        if tag_lower in self.BLOCK_TAGS and self.current_block:
            text = " ".join(self.current_block).strip()
            if text:
                self.text_blocks.append(text)
                if tag_lower.startswith("h") and len(tag_lower) == 2:
                    self.headings.append((tag_lower, text))
            self.current_block = []

    def handle_data(self, data: str):
        if self.ignore_depth > 0:
            return
        cleaned = re.sub(r"\s+", " ", data).strip()
        if cleaned:
            self.current_block.append(cleaned)

    def close(self):
        super().close()
        if self.current_block:
            text = " ".join(self.current_block).strip()
            if text:
                self.text_blocks.append(text)
            self.current_block = []


class HTMLExtractor:
    """Extracts text and candidate scholarship records grounded in retrieved HTML."""

    @staticmethod
    def extract_clean_text(html_content: str) -> str:
        """Extract readable plain text from raw HTML."""
        if not html_content or not html_content.strip():
            return ""
        parser = CleanHTMLParser()
        try:
            parser.feed(html_content)
            parser.close()
            return "\n\n".join(parser.text_blocks)
        except Exception:
            clean = re.sub(r"<script.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
            clean = re.sub(r"<style.*?</style>", "", clean, flags=re.DOTALL | re.IGNORECASE)
            clean = re.sub(r"<[^>]+>", " ", clean)
            clean = unescape(clean)
            return re.sub(r"\s+", " ", clean).strip()

    @classmethod
    def extract_scholarships_from_html(
        cls,
        html_content: str,
        source_url: str,
        default_authority: str = "Government of India",
    ) -> List[Dict[str, Any]]:
        """Extract candidate scholarship records backed by verbatim content quotations.

        Never guesses missing amounts, deadlines, or eligibility criteria.
        """
        parser = CleanHTMLParser()
        try:
            parser.feed(html_content)
            parser.close()
        except Exception:
            pass

        full_text = "\n\n".join(parser.text_blocks)
        if not full_text:
            full_text = cls.extract_clean_text(html_content)

        if not full_text or len(full_text.strip()) < 50:
            return []

        scholarship_keywords = [
            "scholarship",
            "fellowship",
            "scheme",
            "stipend",
            "financial assistance",
            "merit",
        ]
        if not any(kw in full_text.lower() for kw in scholarship_keywords):
            return []

        candidates: List[Dict[str, Any]] = []
        blocks = parser.text_blocks

        # Find non-generic scheme titles
        scheme_titles = []
        for tag, h_text in parser.headings:
            h_clean = h_text.strip()
            if not is_generic_scheme_name(h_clean) and any(
                kw in h_clean.lower()
                for kw in ["scheme", "scholarship", "fellowship", "pragati", "saksham", "swanath", "yojana"]
            ):
                scheme_titles.append(h_clean)

        # If headings are generic or empty, search body blocks for specific scheme titles
        if not scheme_titles:
            for b in blocks:
                if (
                    len(b) < 150
                    and not is_generic_scheme_name(b)
                    and any(kw in b.lower() for kw in ["scheme", "scholarship", "fellowship", "yojana"])
                    and not any(
                        neg in b.lower()
                        for neg in ["login", "register", "apply online", "copyright", "privacy policy", "disclaimer"]
                    )
                ):
                    scheme_titles.append(b.strip())

        # Also search for specific scheme patterns within blocks if still nothing non-generic
        if not scheme_titles:
            for b in blocks:
                # Look for capitalized multi-word phrases ending in or containing Scheme/Scholarship/Fellowship
                matches = re.finditer(
                    r"([A-Z0-9][A-Za-z0-9\-\(\)]*(?:\s+[A-Z0-9][A-Za-z0-9\-\(\)]*|\s+(?:of|for|in|and|the|to)\s+[A-Za-z0-9\-\(\)]*){2,})",
                    b,
                )
                for m in matches:
                    cand = m.group(1).strip()
                    if (
                        not is_generic_scheme_name(cand)
                        and any(k in cand.lower() for k in ["scheme", "scholarship", "fellowship", "yojana"])
                        and len(cand) >= 12
                    ):
                        scheme_titles.append(cand)
                        break
                if scheme_titles:
                    break

        # Deduplicate while preserving order
        unique_titles = []
        for t in scheme_titles:
            t_clean = t.strip()
            if t_clean not in unique_titles and not is_generic_scheme_name(t_clean) and len(t_clean) >= 8:
                unique_titles.append(t_clean)

        for title in unique_titles[:5]:
            context_blocks = cls._find_surrounding_context(blocks, title)
            if not context_blocks:
                continue

            extracted = cls._parse_scheme_context(
                title=title,
                context_blocks=context_blocks,
                source_url=source_url,
                default_authority=default_authority,
            )
            if extracted:
                candidates.append(extracted)

        return candidates

    @classmethod
    def _find_surrounding_context(cls, blocks: List[str], target_title: str) -> List[str]:
        """Collect text blocks under and around the target title."""
        idx = -1
        for i, b in enumerate(blocks):
            if target_title.lower() in b.lower():
                idx = i
                break
        if idx == -1:
            return []

        gathered = [blocks[idx]]
        for j in range(idx + 1, min(idx + 12, len(blocks))):
            block = blocks[j]
            if len(block) < 60 and any(
                w in block.lower() for w in ["contact us", "quick links", "disclaimer", "faqs", "copyright"]
            ):
                break
            gathered.append(block)
        return gathered

    @classmethod
    def _parse_scheme_context(
        cls,
        title: str,
        context_blocks: List[str],
        source_url: str,
        default_authority: str,
    ) -> Optional[Dict[str, Any]]:
        """Parse structured scheme fields strictly grounded in the context text."""
        supporting_quotations: List[str] = []
        field_evidence: Dict[str, str] = {}

        # 1. Scheme Title Evidence
        for b in context_blocks:
            if title.lower() in b.lower():
                supporting_quotations.append(b[:300])
                field_evidence["scheme_name"] = b[:300]
                break

        # 2. Description
        desc_candidates = [
            b
            for b in context_blocks[1:]
            if len(b) > 40
            and not any(
                kw in b.lower()
                for kw in ["rs.", "₹", "deadline", "last date", "eligibility", "documents required"]
            )
        ]
        description = desc_candidates[0] if desc_candidates else context_blocks[0]
        # Do not manufacture a description from the title or URL. If the source
        # page contains no substantive descriptive text, keep only source text;
        # downstream quality checks will leave the record unverified.
        description = description.strip()
        field_evidence["description"] = description[:300] if len(description) >= 15 else ""
        if description[:300] not in supporting_quotations and len(supporting_quotations) < 4:
            supporting_quotations.append(description[:300])

        # 3. Eligibility Criteria (cleaned & deduplicated)
        raw_eligibility: List[str] = []
        eligibility_patterns = [
            r"eligib\w+.*?(?=[.;\n]|$)",
            r"students (?:who|having|pursuing|admitted).*?(?=[.;\n]|$)",
            r"family income.*?(?=[.;\n]|$)",
            r"parental income.*?(?=[.;\n]|$)",
            r"class (?:10|12|x|xii).*?(?=[.;\n]|$)",
            r"percentage.*?(?=[.;\n]|$)",
            r"marks.*?(?=[.;\n]|$)",
        ]
        for b in context_blocks:
            for pat in eligibility_patterns:
                matches = re.findall(pat, b, flags=re.IGNORECASE)
                for m in matches:
                    m_clean = m.strip()
                    if m_clean not in raw_eligibility:
                        raw_eligibility.append(m_clean)
                        if "eligibility_criteria" not in field_evidence:
                            field_evidence["eligibility_criteria"] = b[:300]
                        if b[:300] not in supporting_quotations and len(supporting_quotations) < 4:
                            supporting_quotations.append(b[:300])

        cleaned_eligibility = clean_eligibility_criteria(raw_eligibility)

        # 4. Scholarship Amount (with validation against malformed tokens like 'Rs 30')
        raw_amount_text = None
        # Pass 1: Explicit label with colon/dash (e.g., Scholarship Amount: Rs. 50,000 ...)
        pat_explicit = r"(?i)(?:scholarship amount|rate of scholarship|stipend|allowance)\s*[:\-]\s*([^\n;]+)"
        for b in context_blocks:
            m = re.search(pat_explicit, b)
            if m:
                raw_amount_text = m.group(1).strip()
                field_evidence["scholarship_amount"] = b[:300]
                if b[:300] not in supporting_quotations and len(supporting_quotations) < 4:
                    supporting_quotations.append(b[:300])
                break

        # Pass 2: Currency amount in blocks that are not parental/family income limits
        if not raw_amount_text:
            pat_currency = r"(?i)(?:rs\.?|₹)\s*[\d,]+(?:\s*(?:per|p\.?a\.?|per annum|per month|yearly|monthly|lakh|crore)[^\n,.;]*)?"
            for b in context_blocks:
                if any(inc in b.lower() for inc in ["family income", "parental income", "gross income"]):
                    continue
                m = re.search(pat_currency, b)
                if m:
                    raw_amount_text = m.group(0).strip()
                    field_evidence["scholarship_amount"] = b[:300]
                    if b[:300] not in supporting_quotations and len(supporting_quotations) < 4:
                        supporting_quotations.append(b[:300])
                    break

        # Validate amount sanity (rejects < 500 or malformed tokens)
        validated_amount, amount_is_valid, _ = clean_and_validate_amount(raw_amount_text)
        amount_final = validated_amount if amount_is_valid else None

        # 5. Application Deadline & ISO Date Normalization
        raw_deadline_text = None
        deadline_patterns = [
            r"(?:last date|deadline|closing date|closing dates)(?:\s*is|\s*for student application is|\s*for submission)?\s*[:\-]?\s*([^\n.;]{4,60})",
            r"(?:up to|till)\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
            r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})",
        ]
        for b in context_blocks:
            for pat in deadline_patterns:
                match = re.search(pat, b, flags=re.IGNORECASE)
                if match:
                    raw_deadline_text = match.group(0).strip()
                    field_evidence["application_deadline"] = b[:300]
                    if b[:300] not in supporting_quotations and len(supporting_quotations) < 4:
                        supporting_quotations.append(b[:300])
                    break
            if raw_deadline_text:
                break

        # Normalize date to ISO YYYY-MM-DD while keeping raw evidence
        iso_deadline, raw_deadline_evidence = normalize_date_iso(raw_deadline_text)

        # 6. Required Documents
        required_docs: List[str] = []
        doc_keywords = [
            "aadhaar",
            "marksheet",
            "income certificate",
            "caste certificate",
            "bank account",
            "bonafide",
            "domicile",
            "disability certificate",
        ]
        for b in context_blocks:
            for kw in doc_keywords:
                if kw in b.lower() and kw.title() not in required_docs:
                    required_docs.append(kw.title())
                    if "required_documents" not in field_evidence:
                        field_evidence["required_documents"] = b[:300]

        # 7. Authority
        authority = default_authority
        for b in context_blocks:
            if any(term in b.lower() for term in ["ministry of", "department of", "commission", "council"]):
                auth_match = re.search(
                    r"((?:Ministry|Department|Commission|Council|Board) of [A-Za-z\s,]+)",
                    b,
                )
                if auth_match:
                    authority = auth_match.group(1).strip()
                    field_evidence["government_authority"] = b[:300]
                    break

        return {
            "scheme_name": title,
            "government_authority": authority,
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
