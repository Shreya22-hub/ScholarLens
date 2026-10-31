"""HTML text and scholarship data extractor for ScholarLens.

Extracts structured text from HTML without external dependencies using standard library html.parser.
Enforces grounded extraction where all claims are backed by verbatim quotations.
Rejects generic scheme titles, normalizes dates, and validates financial amounts.
"""

from html import unescape
from html.parser import HTMLParser
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from backend.app.ingestion.quality import (
    clean_and_validate_amount,
    clean_eligibility_criteria,
    determine_application_status,
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
        self.non_table_blocks: List[str] = []
        self.current_block: List[str] = []
        self.headings: List[Tuple[str, str]] = []  # (tag, text)
        self.current_tag: Optional[str] = None

        self.tables: List[List[List[str]]] = []
        self._table_depth = 0
        self._current_table: List[List[str]] = []
        self._current_row: List[str] = []
        self._current_cell: List[str] = []

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

        if tag_lower == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._current_table = []
                self._current_row = []
                self._current_cell = []
        elif self._table_depth > 0:
            if tag_lower == "tr":
                self._current_row = []
            elif tag_lower in ("td", "th"):
                self._current_cell = []

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
                if self._table_depth == 0:
                    self.non_table_blocks.append(text)
                if tag_lower.startswith("h") and len(tag_lower) == 2:
                    self.headings.append((tag_lower, text))
            self.current_block = []

        if self._table_depth > 0:
            if tag_lower in ("td", "th"):
                cell_text = " ".join(self._current_cell).strip()
                self._current_row.append(cell_text)
                self._current_cell = []
            elif tag_lower == "tr":
                if any(c for c in self._current_row):
                    self._current_table.append(self._current_row)
                self._current_row = []
            elif tag_lower == "table":
                self._table_depth -= 1
                if self._table_depth == 0:
                    if self._current_table:
                        self.tables.append(self._current_table)
                    self._current_table = []

    def handle_data(self, data: str):
        if self.ignore_depth > 0:
            return
        cleaned = re.sub(r"\s+", " ", data).strip()
        if cleaned:
            self.current_block.append(cleaned)
            if self._table_depth > 0:
                self._current_cell.append(cleaned)

    def close(self):
        super().close()
        if self.current_block:
            text = " ".join(self.current_block).strip()
            if text:
                self.text_blocks.append(text)
            self.current_block = []
        if self._table_depth > 0 and self._current_table:
            self.tables.append(self._current_table)
            self._current_table = []
            self._table_depth = 0


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

        seen_names = set()
        candidates: List[Dict[str, Any]] = []

        # 1. Extract candidates from semantic scheme containers (e.g. schemeListItem)
        container_candidates = cls._extract_from_scheme_containers(
            html_content=html_content,
            source_url=source_url,
            default_authority=default_authority,
        )
        for cand in container_candidates:
            norm = cand.get("scheme_name", "").strip().lower()
            if norm and norm not in seen_names:
                seen_names.add(norm)
                candidates.append(cand)

        # 2. Extract candidates from structured HTML tables
        table_candidates = cls._extract_scholarships_from_tables(
            tables=parser.tables,
            source_url=source_url,
            default_authority=default_authority,
        )

        for cand in table_candidates:
            norm = cand.get("scheme_name", "").strip().lower()
            if norm and norm not in seen_names:
                seen_names.add(norm)
                candidates.append(cand)

        # 3. Extract candidates from headings and structural text blocks
        blocks = parser.non_table_blocks if parser.tables else parser.text_blocks

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
            if (
                t_clean not in unique_titles
                and not is_generic_scheme_name(t_clean)
                and len(t_clean) >= 8
                and t_clean.lower() not in seen_names
            ):
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
                norm = extracted.get("scheme_name", "").strip().lower()
                if norm not in seen_names:
                    seen_names.add(norm)
                    candidates.append(extracted)

        return candidates

    @classmethod
    def _extract_from_scheme_containers(
        cls,
        html_content: str,
        source_url: str,
        default_authority: str,
    ) -> List[Dict[str, Any]]:
        """Extract candidate scholarships from semantic scheme container blocks (e.g. schemeListItem)."""
        candidates: List[Dict[str, Any]] = []
        pat = re.compile(
            r"<div\b[^>]*\bclass=[\"'][^\"']*(?:schemeListItem|scheme-item|scholarship-item)[^\"']*[\"'][^>]*>",
            re.IGNORECASE,
        )
        parts = pat.split(html_content)
        container_blocks = parts[1:]

        for block in container_blocks:
            h_match = re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", block, re.DOTALL | re.IGNORECASE)
            if not h_match:
                continue
            title = re.sub(r"<[^>]+>", " ", h_match.group(1)).strip()
            title = unescape(title)
            title = re.sub(r"\s+", " ", title).strip()
            if is_generic_scheme_name(title) or len(title) < 5:
                continue

            p_match = re.search(
                r"<p[^>]*class=[\"'][^\"']*schemeDetailsText[^\"']*[\"'][^>]*>(.*?)</p>",
                block,
                re.DOTALL | re.IGNORECASE,
            )
            if not p_match:
                p_match = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL | re.IGNORECASE)

            desc = ""
            if p_match:
                desc = re.sub(r"<[^>]+>", " ", p_match.group(1)).strip()
                desc = unescape(desc)
                desc = re.sub(r"\s+", " ", desc).strip()
            if not desc:
                desc = f"Official scheme details for {title}."

            field_evidence: Dict[str, str] = {
                "scheme_name": title,
                "description": desc[:300],
                "government_authority": default_authority,
            }
            supporting_quotations: List[str] = [title, default_authority]
            if desc and desc != title:
                supporting_quotations.append(desc[:300])

            clean_block_text = re.sub(r"<[^>]+>", " ", block)
            clean_block_text = unescape(clean_block_text)
            clean_block_text = re.sub(r"\s+", " ", clean_block_text).strip()

            # Scoped financial amount from within this container ONLY
            m_amt = re.search(
                r"(?:rs\.?|₹)\s*[\d,]+(?:\s*(?:per|p\.?a\.?|per annum|per month|yearly|monthly|lakh|crore)[^\n,.;]*)?",
                clean_block_text,
                re.IGNORECASE,
            )
            raw_amt = m_amt.group(0).strip() if m_amt else None
            validated_amt, amt_valid, _ = clean_and_validate_amount(raw_amt)
            amount_final = validated_amt if amt_valid else None
            if amount_final and raw_amt:
                for sent in re.split(r"[.\n]", clean_block_text):
                    if raw_amt in sent:
                        amount_quote = sent.strip()[:300]
                        field_evidence["scholarship_amount"] = amount_quote
                        if amount_quote not in supporting_quotations:
                            supporting_quotations.append(amount_quote)
                        break

            # Scoped eligibility criteria
            eligibility_candidates = []
            for sent in re.split(r"[.\n]", desc):
                sent_clean = sent.strip()
                if any(
                    k in sent_clean.lower()
                    for k in [
                        "meritorious",
                        "differently abled",
                        "girl",
                        "admission",
                        "degree",
                        "diploma",
                        "category",
                        "orphan",
                        "student",
                        "gate",
                        "gpat",
                    ]
                ):
                    if len(sent_clean) > 20:
                        eligibility_candidates.append(sent_clean)
            cleaned_elig = clean_eligibility_criteria(eligibility_candidates)
            if cleaned_elig:
                field_evidence["eligibility_criteria"] = cleaned_elig[0][:300]
                if cleaned_elig[0] not in supporting_quotations:
                    supporting_quotations.append(cleaned_elig[0][:300])

            # Scoped student submission deadline (rejecting verification milestones)
            m_dl = re.search(r"(?:last date for (?:submission|applying)|deadline|closing date)\s*[:\-]?\s*([^\n.;]{4,40})", clean_block_text, re.IGNORECASE)
            raw_dl = None
            if m_dl:
                cand = m_dl.group(0).strip()
                if not re.search(r"\b(?:verification|scrutiny|l1 level|l2 level|sno)\b", cand, re.IGNORECASE):
                    raw_dl = cand
            iso_dl, raw_dl_ev = normalize_date_iso(raw_dl)
            if iso_dl:
                field_evidence["application_deadline"] = raw_dl_ev

            app_status, detected_year = determine_application_status(
                deadline_iso=iso_dl,
                raw_text=clean_block_text[:2000],
            )

            # Check for linked guideline PDF within this container
            container_links = re.findall(r"<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", block, re.DOTALL | re.IGNORECASE)
            guideline_url = None
            for href_str, link_txt in container_links:
                h_clean = href_str.strip()
                t_clean = re.sub(r"<[^>]+>", " ", link_txt).strip().lower()
                if h_clean.lower().endswith(".pdf") or "guideline" in t_clean or "notification" in t_clean:
                    guideline_url = urljoin(source_url, h_clean)
                    break

            candidates.append({
                "scheme_name": title,
                "government_authority": default_authority,
                "description": desc,
                "eligibility_criteria": cleaned_elig,
                "scholarship_amount": amount_final,
                "application_deadline": iso_dl,
                "application_deadline_raw": raw_dl_ev,
                "required_documents": [],
                "official_source_url": source_url,
                "supporting_quotations": supporting_quotations,
                "field_evidence": field_evidence,
                "verification_status": "unverified",
                "scheme_year": detected_year,
                "application_status": app_status,
                "guideline_url": guideline_url,
            })

        return candidates

    @classmethod
    def extract_guideline_links(cls, html_content: str, base_url: str) -> List[Dict[str, Any]]:
        """Extract links to official scheme guideline PDFs and notification documents."""
        results: List[Dict[str, Any]] = []
        if not html_content:
            return results

        seen_urls = set()

        # 1. First look inside scheme containers to associate links with scheme names
        pat = re.compile(
            r"<div\b[^>]*\bclass=[\"'][^\"']*(?:schemeListItem|scheme-item|scholarship-item)[^\"']*[\"'][^>]*>",
            re.IGNORECASE,
        )
        parts = pat.split(html_content)
        container_blocks = parts[1:]

        for block in container_blocks:
            h_match = re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", block, re.DOTALL | re.IGNORECASE)
            scheme_name = None
            if h_match:
                scheme_name = re.sub(r"<[^>]+>", " ", h_match.group(1)).strip()

            a_matches = re.finditer(
                r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                block,
                re.IGNORECASE | re.DOTALL,
            )
            for m in a_matches:
                raw_href = m.group(1).strip()
                raw_text = re.sub(r"<[^>]+>", " ", m.group(2)).strip()
                raw_text = unescape(raw_text)
                raw_text = re.sub(r"\s+", " ", raw_text).strip()
                if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                full_url = urljoin(base_url, raw_href)
                norm_url = full_url.lower()

                is_pdf = norm_url.endswith(".pdf") or ".pdf?" in norm_url or "/pdf/" in norm_url
                is_guideline = any(
                    k in raw_text.lower() or k in norm_url
                    for k in ["guideline", "notification", "scheme document", "manual", "features", "scheme"]
                )
                if (is_pdf or is_guideline) and full_url not in seen_urls:
                    seen_urls.add(full_url)
                    results.append({
                        "url": full_url,
                        "title": raw_text or "Scheme Guidelines",
                        "scheme_name": scheme_name,
                        "is_pdf": is_pdf,
                    })

        # 2. General page links for pages without containers
        a_matches = re.finditer(
            r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
            html_content,
            re.IGNORECASE | re.DOTALL,
        )
        for m in a_matches:
            raw_href = m.group(1).strip()
            raw_text = re.sub(r"<[^>]+>", " ", m.group(2)).strip()
            raw_text = unescape(raw_text)
            raw_text = re.sub(r"\s+", " ", raw_text).strip()
            if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            full_url = urljoin(base_url, raw_href)
            norm_url = full_url.lower()

            is_pdf = norm_url.endswith(".pdf") or ".pdf?" in norm_url
            is_guideline = any(
                k in raw_text.lower()
                for k in ["scheme guideline", "scholarship guideline", "scheme notification", "scheme circular"]
            )
            if (is_pdf or is_guideline) and full_url not in seen_urls:
                seen_urls.add(full_url)
                results.append({
                    "url": full_url,
                    "title": raw_text or "Scheme Document",
                    "scheme_name": None,
                    "is_pdf": is_pdf,
                })

        return results

    @classmethod
    def _extract_scholarships_from_tables(
        cls,
        tables: List[List[List[str]]],
        source_url: str,
        default_authority: str,
    ) -> List[Dict[str, Any]]:
        """Extract candidate scholarships from structured HTML tables.

        Preserves verbatim source text, context quotations, and field-level evidence.
        Rejects navigation, generic headers, and non-scholarship tables.
        """
        candidates: List[Dict[str, Any]] = []
        scholarship_kw = [
            "scholarship",
            "fellowship",
            "scheme",
            "stipend",
            "financial assistance",
            "yojana",
            "pragati",
            "saksham",
            "swanath",
        ]

        for table in tables:
            # Need at least a header row and a data row
            if len(table) < 2:
                continue

            # Quick check: does this table contain any scholarship keywords?
            table_flat_text = " ".join(" ".join(cell for cell in row) for row in table).lower()
            if not any(kw in table_flat_text for kw in scholarship_kw):
                continue

            # Identify headers and map column roles
            header_row = table[0]
            col_name: Optional[int] = None
            col_eligibility: Optional[int] = None
            col_amount: Optional[int] = None
            col_deadline: Optional[int] = None
            col_authority: Optional[int] = None

            for idx, h_raw in enumerate(header_row):
                h = h_raw.strip().lower()
                # Skip serial number / counter columns
                if any(sn in h for sn in ["s.no", "sl.no", "sl no", "sr.no", "s no", "serial"]):
                    continue

                if col_name is None and any(k in h for k in ["scheme", "scholarship", "fellowship", "name", "title", "yojana"]):
                    col_name = idx
                elif col_eligibility is None and any(k in h for k in ["eligib", "criteria", "who can apply", "condition", "target"]):
                    col_eligibility = idx
                elif col_amount is None and any(k in h for k in ["amount", "rate", "stipend", "assistance", "allowance", "value"]):
                    col_amount = idx
                elif col_deadline is None and any(k in h for k in ["deadline", "last date", "closing", "date"]):
                    col_deadline = idx
                elif col_authority is None and any(k in h for k in ["ministry", "department", "authority", "agency"]):
                    col_authority = idx

            # Process data rows
            for row in table[1:]:
                # Ignore empty or near-empty rows
                non_empty_cells = [c for c in row if c and c.strip()]
                if len(non_empty_cells) < 2:
                    continue

                # Ignore repeated header rows (e.g. within multiple table header sections)
                row_flat = " ".join(non_empty_cells).lower()
                if any(hdr in row_flat for hdr in ["s.no", "sl. no", "name of scheme", "eligibility criteria"]):
                    continue

                # 1. Scheme Title
                scheme_title = None
                if col_name is not None and col_name < len(row):
                    candidate_name = row[col_name].strip()
                    if not is_generic_scheme_name(candidate_name) and len(candidate_name) >= 8:
                        scheme_title = candidate_name

                # Fallback: inspect each cell if col_name was not identified or cell was generic
                if not scheme_title:
                    for cell in row:
                        cell_clean = cell.strip()
                        if (
                            not is_generic_scheme_name(cell_clean)
                            and any(k in cell_clean.lower() for k in ["scheme", "scholarship", "fellowship", "yojana"])
                            and len(cell_clean) >= 8
                            and len(cell_clean) <= 150
                        ):
                            scheme_title = cell_clean
                            break

                if not scheme_title or is_generic_scheme_name(scheme_title):
                    continue

                # 2. Authority
                authority = default_authority
                authority_cell_text = None
                if col_authority is not None and col_authority < len(row):
                    auth_cand = row[col_authority].strip()
                    if len(auth_cand) >= 4:
                        authority = auth_cand
                        authority_cell_text = auth_cand
                else:
                    for cell in row:
                        if any(term in cell.lower() for term in ["ministry of", "department of", "commission", "council"]):
                            m = re.search(r"((?:Ministry|Department|Commission|Council|Board) of [A-Za-z\s,]+)", cell)
                            if m:
                                authority = m.group(1).strip()
                                authority_cell_text = cell.strip()
                                break

                # 3. Eligibility Criteria
                raw_eligibility: List[str] = []
                eligibility_cell_text = None
                if col_eligibility is not None and col_eligibility < len(row):
                    elig_text = row[col_eligibility].strip()
                    if elig_text:
                        raw_eligibility.append(elig_text)
                        eligibility_cell_text = elig_text
                else:
                    for cell in row:
                        if any(term in cell.lower() for term in ["income", "class", "degree", "marks", "percent", "%", "resident", "student"]):
                            raw_eligibility.append(cell.strip())
                            if not eligibility_cell_text:
                                eligibility_cell_text = cell.strip()

                cleaned_eligibility = clean_eligibility_criteria(raw_eligibility)

                # 4. Scholarship Amount
                raw_amount = None
                amount_cell_text = None
                if col_amount is not None and col_amount < len(row):
                    raw_amount = row[col_amount].strip()
                    amount_cell_text = raw_amount
                else:
                    pat_currency = r"(?i)(?:rs\.?|₹)\s*[\d,]+(?:\s*(?:per|p\.?a\.?|per annum|per month|yearly|monthly|lakh|crore)[^\n,.;]*)?"
                    for cell in row:
                        if any(inc in cell.lower() for inc in ["family income", "parental income", "gross income"]):
                            continue
                        m = re.search(pat_currency, cell)
                        if m:
                            raw_amount = m.group(0).strip()
                            amount_cell_text = cell.strip()
                            break

                validated_amount, amount_is_valid, _ = clean_and_validate_amount(raw_amount)
                amount_final = validated_amount if amount_is_valid else None

                # 5. Application Deadline
                raw_deadline = None
                deadline_cell_text = None
                if col_deadline is not None and col_deadline < len(row):
                    raw_deadline = row[col_deadline].strip()
                    deadline_cell_text = raw_deadline
                else:
                    deadline_patterns = [
                        r"(?:last date|deadline|closing date|closing dates)(?:\s*is|\s*for submission)?\s*[:\-]?\s*([^\n.;]{4,60})",
                        r"(?:up to|till)\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
                        r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})",
                    ]
                    for cell in row:
                        for pat in deadline_patterns:
                            m = re.search(pat, cell, flags=re.IGNORECASE)
                            if m:
                                raw_deadline = m.group(0).strip()
                                deadline_cell_text = cell.strip()
                                break
                        if raw_deadline:
                            break

                iso_deadline, raw_deadline_evidence = normalize_date_iso(raw_deadline)

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
                for cell in row:
                    for kw in doc_keywords:
                        if kw in cell.lower() and kw.title() not in required_docs:
                            required_docs.append(kw.title())

                # 7. Evidence & Quotations
                row_quotation = " | ".join(c for c in non_empty_cells)[:300]
                supporting_quotations = [row_quotation]
                field_evidence: Dict[str, str] = {
                    "scheme_name": scheme_title[:300],
                }
                if authority_cell_text:
                    field_evidence["government_authority"] = authority_cell_text[:300]
                    if authority_cell_text[:300] not in supporting_quotations and len(supporting_quotations) < 4:
                        supporting_quotations.append(authority_cell_text[:300])
                else:
                    field_evidence["government_authority"] = row_quotation

                if amount_final and amount_cell_text:
                    field_evidence["scholarship_amount"] = amount_cell_text[:300]
                    if amount_cell_text[:300] not in supporting_quotations and len(supporting_quotations) < 4:
                        supporting_quotations.append(amount_cell_text[:300])

                if cleaned_eligibility and eligibility_cell_text:
                    field_evidence["eligibility_criteria"] = eligibility_cell_text[:300]
                    if eligibility_cell_text[:300] not in supporting_quotations and len(supporting_quotations) < 4:
                        supporting_quotations.append(eligibility_cell_text[:300])

                if iso_deadline or raw_deadline_evidence:
                    field_evidence["application_deadline"] = (deadline_cell_text or raw_deadline_evidence or "")[:300]

                if required_docs:
                    field_evidence["required_documents"] = row_quotation

                # Description
                description = eligibility_cell_text or row_quotation

                candidates.append({
                    "scheme_name": scheme_title,
                    "government_authority": authority,
                    "description": description[:500],
                    "eligibility_criteria": cleaned_eligibility,
                    "scholarship_amount": amount_final,
                    "application_deadline": iso_deadline,
                    "application_deadline_raw": raw_deadline_evidence,
                    "required_documents": required_docs,
                    "official_source_url": source_url,
                    "supporting_quotations": supporting_quotations,
                    "field_evidence": field_evidence,
                    "verification_status": "unverified",
                })

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
