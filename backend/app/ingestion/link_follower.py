"""Safe guideline document retrieval and domain policy enforcement for ScholarLens.

Enforces:
- Restricted link following only to registered source domains or official Indian government (.gov.in / .nic.in) domains.
- Strict rejection of third-party, commercial, or unverified external links.
- Response size limits (maximum 15MB for guideline PDFs).
- Robots.txt compliance and polite headers.
- Safe redirect validation ensuring redirect targets do not jump to third-party domains.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx

from backend.app.config import settings
from backend.app.ingestion.fetcher import FetchResult, PoliteFetcher


# Explicitly permitted official government domain suffixes
OFFICIAL_GOV_SUFFIXES = (
    ".gov.in",
    ".nic.in",
    ".ac.in",
    ".edu.in",
)

# Known official government portals permitted for guideline hosting
ALLOWED_OFFICIAL_DOMAINS = {
    "aicte.gov.in",
    "www.aicte.gov.in",
    "scholarships.gov.in",
    "tribal.nic.in",
    "ugc.ac.in",
    "education.gov.in",
    "www.education.gov.in",
}

# Maximum permitted download size for official guideline documents (15 MB)
MAX_GUIDELINE_BYTES = 15 * 1024 * 1024


def is_allowed_guideline_url(url: str, source_url: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Verify whether a linked guideline URL belongs to an allowed official government domain.

    Returns:
        (is_allowed: bool, reason: Optional[str])
    """
    if not url or not isinstance(url, str):
        return False, "Empty or invalid URL provided."

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported URL scheme '{parsed.scheme}'. Only HTTP and HTTPS are permitted."

    host = (parsed.hostname or "").lower()
    if not host:
        return False, "URL does not contain a valid hostname."

    # Reject localhost, IP addresses, internal hostnames
    if host in ("localhost", "127.0.0.1", "0.0.0.0") or re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host):
        return False, "Access to IP addresses and localhost is forbidden."

    # Check 1: Exact match with known official domains
    if host in ALLOWED_OFFICIAL_DOMAINS:
        return True, None

    # Check 2: Match with registered source domain or its subdomains
    if source_url:
        source_parsed = urlparse(source_url)
        source_host = (source_parsed.hostname or "").lower()
        if host == source_host or host.endswith("." + source_host):
            return True, None
        # Handle cases like aicte.gov.in when source was www.aicte.gov.in
        clean_source_host = re.sub(r"^www\.", "", source_host)
        clean_host = re.sub(r"^www\.", "", host)
        if clean_host == clean_source_host or clean_host.endswith("." + clean_source_host):
            return True, None

    # Check 3: Domain ends with official Indian government suffixes
    if any(host.endswith(suffix) for suffix in OFFICIAL_GOV_SUFFIXES):
        return True, None

    return False, f"Domain '{host}' is not a registered source domain or official .gov.in/.nic.in government domain."


def fetch_guideline_document(
    url: str,
    source_id: str,
    fetcher: PoliteFetcher,
    source_url: Optional[str] = None,
    client: Optional[httpx.Client] = None,
    max_size_bytes: int = MAX_GUIDELINE_BYTES,
) -> FetchResult:
    """Safely fetch an official scheme guideline or notification document.

    Enforces domain safety, robots.txt, content length checks, and redirect policies.
    """
    now = datetime.now(timezone.utc)
    clean_url = url.strip()

    # 1. Domain safety verification
    is_allowed, denial_reason = is_allowed_guideline_url(clean_url, source_url=source_url)
    if not is_allowed:
        return FetchResult(
            source_id=source_id,
            url=clean_url,
            status_code=None,
            success=False,
            content_type="text/plain",
            content_bytes=b"",
            text_content=None,
            fetched_at=now,
            error_message=f"Access denied: {denial_reason}",
            robots_allowed=True,
        )

    # 2. Robots.txt verification
    if not fetcher.can_fetch(clean_url, client=client):
        return FetchResult(
            source_id=source_id,
            url=clean_url,
            status_code=None,
            success=False,
            content_type="text/plain",
            content_bytes=b"",
            text_content=None,
            fetched_at=now,
            error_message="Access denied: URL disallowed by portal robots.txt rules.",
            robots_allowed=False,
        )

    headers = {
        "User-Agent": fetcher.user_agent,
        "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
    }

    close_client = False
    if client is None:
        client = httpx.Client(timeout=fetcher.timeout, follow_redirects=False, verify=True)
        close_client = True

    try:
        current_url = clean_url
        redirect_count = 0
        max_redirects = 3

        while redirect_count <= max_redirects:
            try:
                # Perform streaming GET to enforce max_size_bytes
                with client.stream("GET", current_url, headers=headers) as resp:
                    status_code = resp.status_code

                    # Handle Redirects safely
                    if status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            return FetchResult(
                                source_id=source_id,
                                url=current_url,
                                status_code=status_code,
                                success=False,
                                fetched_at=now,
                                error_message="Redirect header missing.",
                            )
                        redirect_url = urljoin(current_url, location)
                        # Verify redirect target adheres to government domain policy
                        allowed_redir, redir_reason = is_allowed_guideline_url(redirect_url, source_url=source_url)
                        if not allowed_redir:
                            return FetchResult(
                                source_id=source_id,
                                url=redirect_url,
                                status_code=status_code,
                                success=False,
                                fetched_at=now,
                                error_message=f"Redirect blocked: Destination '{redirect_url}' is not an allowed official government domain. ({redir_reason})",
                            )
                        current_url = redirect_url
                        redirect_count += 1
                        continue

                    # Check Content-Length header if provided
                    content_len_hdr = resp.headers.get("content-length")
                    if content_len_hdr:
                        try:
                            if int(content_len_hdr) > max_size_bytes:
                                return FetchResult(
                                    source_id=source_id,
                                    url=current_url,
                                    status_code=status_code,
                                    success=False,
                                    fetched_at=now,
                                    error_message=f"Document exceeds maximum permitted size ({content_len_hdr} > {max_size_bytes} bytes).",
                                )
                        except ValueError:
                            pass

                    # Read bytes under size limit
                    chunks: List[bytes] = []
                    total_bytes = 0
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        total_bytes += len(chunk)
                        if total_bytes > max_size_bytes:
                            return FetchResult(
                                source_id=source_id,
                                url=current_url,
                                status_code=status_code,
                                success=False,
                                fetched_at=now,
                                error_message=f"Document download exceeded maximum permitted size ({max_size_bytes} bytes).",
                            )
                        chunks.append(chunk)

                    body = b"".join(chunks)
                    content_type = resp.headers.get("content-type", "").lower()

                    if resp.is_success:
                        return FetchResult(
                            source_id=source_id,
                            url=current_url,
                            status_code=status_code,
                            success=True,
                            content_type=content_type,
                            content_bytes=body,
                            text_content=body.decode("utf-8", errors="replace") if "text" in content_type else None,
                            fetched_at=now,
                            error_message=None,
                            robots_allowed=True,
                        )
                    else:
                        return FetchResult(
                            source_id=source_id,
                            url=current_url,
                            status_code=status_code,
                            success=False,
                            content_type=content_type,
                            content_bytes=body,
                            fetched_at=now,
                            error_message=f"HTTP {status_code}: {resp.reason_phrase}",
                            robots_allowed=True,
                        )

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                return FetchResult(
                    source_id=source_id,
                    url=current_url,
                    status_code=None,
                    success=False,
                    fetched_at=now,
                    error_message=f"Network error while fetching guideline document: {type(e).__name__} - {str(e)}",
                    robots_allowed=True,
                )

        return FetchResult(
            source_id=source_id,
            url=clean_url,
            status_code=None,
            success=False,
            fetched_at=now,
            error_message="Too many redirects encountered while following guideline link.",
            robots_allowed=True,
        )

    finally:
        if close_client:
            client.close()
