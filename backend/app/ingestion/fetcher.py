"""Polite HTTP fetcher for ScholarLens.

Respects robots.txt, applies timeout/retry policies, identifies user-agent,
avoids authentication/CAPTCHA bypassing, and records HTTP telemetry.
"""

from datetime import datetime, timezone
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse
import urllib.robotparser

import httpx
from pydantic import BaseModel, ConfigDict, Field

from backend.app.config import settings


class FetchResult(BaseModel):
    """Encapsulates the result and telemetry of an HTTP fetch operation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_id: str
    url: str
    status_code: Optional[int] = None
    success: bool = False
    content_type: str = "text/html"
    content_bytes: bytes = Field(default=b"", exclude=True)
    text_content: Optional[str] = None
    fetched_at: datetime
    error_message: Optional[str] = None
    robots_allowed: bool = True


class PoliteFetcher:
    """HTTP fetcher implementing polite access, robots.txt compliance, and retries."""

    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        respect_robots: Optional[bool] = None,
    ):
        self.user_agent = user_agent or settings.user_agent
        self.timeout = timeout or settings.fetch_timeout_seconds
        self.max_retries = max_retries or settings.max_fetch_retries
        self.respect_robots = (
            respect_robots if respect_robots is not None else settings.respect_robots_txt
        )
        self._robots_cache: Dict[str, urllib.robotparser.RobotFileParser] = {}

    def _get_robots_parser(
        self, url: str, client: Optional[httpx.Client] = None
    ) -> Optional[urllib.robotparser.RobotFileParser]:
        """Fetch and cache robots.txt for the given target domain."""
        parsed = urlparse(url)
        base_domain = f"{parsed.scheme}://{parsed.netloc}"

        if base_domain in self._robots_cache:
            return self._robots_cache[base_domain]

        robots_url = f"{base_domain}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)

        try:
            headers = {"User-Agent": self.user_agent}
            if client:
                res = client.get(robots_url, headers=headers, timeout=5.0)
            else:
                with httpx.Client(timeout=5.0, follow_redirects=True) as local_client:
                    res = local_client.get(robots_url, headers=headers)

            if res.status_code == 200:
                lines = res.text.splitlines()
                rp.parse(lines)
            elif res.status_code in (401, 403):
                # Forbidden robots.txt implies restrictions per RFC 9309
                rp.disallow_all = True
            else:
                # 404 or other implies no robots restrictions
                rp.allow_all = True
        except Exception:
            # If robots.txt cannot be fetched, default to allow per standard crawl conventions
            rp.allow_all = True

        self._robots_cache[base_domain] = rp
        return rp

    def can_fetch(self, url: str, client: Optional[httpx.Client] = None) -> bool:
        """Verify if robots.txt permits crawling this URL with our user agent."""
        if not self.respect_robots:
            return True

        rp = self._get_robots_parser(url, client=client)
        if rp is None or getattr(rp, "allow_all", False):
            return True
        if getattr(rp, "disallow_all", False):
            return False

        try:
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def fetch(
        self,
        url: str,
        source_id: str,
        client: Optional[httpx.Client] = None,
    ) -> FetchResult:
        """Fetch content from official government URL with polite rules and retries."""
        now = datetime.now(timezone.utc)

        # 1. Check robots.txt compliance
        if not self.can_fetch(url, client=client):
            return FetchResult(
                source_id=source_id,
                url=url,
                status_code=None,
                success=False,
                content_type="text/plain",
                content_bytes=b"",
                text_content=None,
                fetched_at=now,
                error_message="Access denied: URL disallowed by official portal robots.txt rules.",
                robots_allowed=False,
            )

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        }

        # 2. HTTP Request with limited retries and exponential backoff
        last_error = None
        status_code = None

        close_client = False
        if client is None:
            client = httpx.Client(timeout=self.timeout, follow_redirects=True)
            close_client = True

        try:
            for attempt in range(self.max_retries + 1):
                try:
                    response = client.get(url, headers=headers)
                    status_code = response.status_code
                    content_type = response.headers.get("content-type", "").lower()

                    # Check for access restrictions or CAPTCHA indications
                    # Do not attempt to bypass CAPTCHA, authentication, or blocking
                    if status_code in (401, 403):
                        return FetchResult(
                            source_id=source_id,
                            url=url,
                            status_code=status_code,
                            success=False,
                            content_type=content_type,
                            content_bytes=response.content,
                            text_content=None,
                            fetched_at=now,
                            error_message=f"Access restricted by portal (HTTP {status_code}). Authentication/authorization required.",
                            robots_allowed=True,
                        )

                    if status_code == 429:
                        return FetchResult(
                            source_id=source_id,
                            url=url,
                            status_code=status_code,
                            success=False,
                            content_type=content_type,
                            content_bytes=response.content,
                            text_content=None,
                            fetched_at=now,
                            error_message="Rate limit encountered (HTTP 429). Fetch halted to respect server policies.",
                            robots_allowed=True,
                        )

                    # Successful response
                    if response.is_success:
                        # Detect challenge pages (Cloudflare, security verification)
                        preview_text = response.text[:2000].lower() if "text" in content_type or "html" in content_type else ""
                        if "captcha" in preview_text and "challenge" in preview_text:
                            return FetchResult(
                                source_id=source_id,
                                url=url,
                                status_code=status_code,
                                success=False,
                                content_type=content_type,
                                content_bytes=response.content,
                                text_content=None,
                                fetched_at=now,
                                error_message="Access blocked by anti-bot verification / CAPTCHA challenge. Automated bypass is strictly prevented.",
                                robots_allowed=True,
                            )

                        return FetchResult(
                            source_id=source_id,
                            url=url,
                            status_code=status_code,
                            success=True,
                            content_type=content_type,
                            content_bytes=response.content,
                            text_content=response.text if "text" in content_type or "html" in content_type else None,
                            fetched_at=now,
                            error_message=None,
                            robots_allowed=True,
                        )
                    else:
                        last_error = f"HTTP {status_code}: {response.reason_phrase}"

                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    last_error = f"Network or timeout error: {type(e).__name__} - {str(e)}"
                    if attempt < self.max_retries:
                        time.sleep(0.5 * (2**attempt))
                        continue

            return FetchResult(
                source_id=source_id,
                url=url,
                status_code=status_code,
                success=False,
                content_type="text/plain",
                content_bytes=b"",
                text_content=None,
                fetched_at=now,
                error_message=last_error or "Fetch failed after retries.",
                robots_allowed=True,
            )
        finally:
            if close_client:
                client.close()
