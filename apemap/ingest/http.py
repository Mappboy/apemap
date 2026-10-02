"""HTTP session creation and retry helpers for APEMAP data collection."""

from __future__ import annotations

from typing import Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from apemap.constants import DEFAULT_USER_AGENT


def create_retry_session(
    user_agent: str = DEFAULT_USER_AGENT,
    total_retries: int = 3,
    backoff_factor: float = 1.5,
    status_forcelist: Sequence[int] = (429, 500, 502, 503, 504),
    accept: str | None = None,
) -> requests.Session:
    """Create a configured requests.Session with bounded exponential backoff and polite headers.

    Args:
        user_agent: Custom User-Agent header string identifying the research client.
        total_retries: Maximum number of retry attempts for transient server errors.
        backoff_factor: Backoff multiplier applied between retry attempts.
        status_forcelist: HTTP status codes that trigger a retry.
        accept: Optional Accept header string (e.g. 'application/json').

    Returns:
        Configured requests.Session instance.
    """
    session = requests.Session()
    headers = {"User-Agent": user_agent}
    if accept:
        headers["Accept"] = accept
    session.headers.update(headers)

    retries = Retry(
        total=total_retries,
        read=total_retries,
        connect=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=list(status_forcelist),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
