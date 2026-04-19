"""EDINET API v2 client with rate limiting and caching."""

from __future__ import annotations

import asyncio
import logging
import time
import zipfile
from io import BytesIO
from typing import Any

import httpx

from .config import EDINET_API_BASE, EDINET_API_KEY

logger = logging.getLogger(__name__)

_RATE_LIMIT_INTERVAL = 0.8  # seconds between requests (~1.25 req/s, safe for EDINET)
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 5.0  # seconds, exponential backoff


class EdinetAPIError(Exception):
    """Raised on EDINET API errors."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"EDINET API error {status_code}: {message}")


class EdinetClient:
    """Async client for EDINET API v2."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or EDINET_API_KEY
        if not self._api_key:
            raise ValueError(
                "EDINET API key required. Set EDINET_API_KEY env var or pass api_key parameter. "
                "Register free at https://disclosure.edinet-fsa.go.jp/"
            )
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float = 0
        # Metrics counters
        self.metrics_api_calls: int = 0
        self.metrics_retries: int = 0
        self.metrics_429_count: int = 0
        self.metrics_5xx_count: int = 0
        self.metrics_bytes_downloaded: int = 0
        self._response_times: list[float] = []

    def reset_metrics(self) -> None:
        """Reset per-date metrics counters."""
        self.metrics_api_calls = 0
        self.metrics_retries = 0
        self.metrics_429_count = 0
        self.metrics_5xx_count = 0
        self.metrics_bytes_downloaded = 0
        self._response_times = []

    def get_metrics(self) -> dict[str, Any]:
        """Return current metrics snapshot."""
        avg_ms = (
            sum(self._response_times) / len(self._response_times) * 1000
            if self._response_times else None
        )
        max_ms = max(self._response_times) * 1000 if self._response_times else None
        return {
            "api_calls": self.metrics_api_calls,
            "api_retries": self.metrics_retries,
            "api_429_count": self.metrics_429_count,
            "api_5xx_count": self.metrics_5xx_count,
            "total_bytes_downloaded": self.metrics_bytes_downloaded,
            "avg_response_ms": round(avg_ms, 1) if avg_ms else None,
            "max_response_ms": round(max_ms, 1) if max_ms else None,
        }

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers={"Ocp-Apim-Subscription-Key": self._api_key},
            )
        return self._client

    async def _rate_limit(self) -> None:
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < _RATE_LIMIT_INTERVAL:
            await asyncio.sleep(_RATE_LIMIT_INTERVAL - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        client = await self._ensure_client()
        url = f"{EDINET_API_BASE}{path}"

        for attempt in range(_MAX_RETRIES):
            await self._rate_limit()
            self.metrics_api_calls += 1
            t0 = time.monotonic()
            try:
                response = await client.get(url, params=params)
            except httpx.TransportError as exc:
                logger.warning("Network error (attempt %d/%d): %s", attempt + 1, _MAX_RETRIES, exc)
                self.metrics_retries += 1
                if attempt == _MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
                continue

            elapsed = time.monotonic() - t0
            self._response_times.append(elapsed)
            self.metrics_bytes_downloaded += len(response.content)

            if response.status_code == 200:
                return response

            if response.status_code == 429:  # Rate limited
                self.metrics_429_count += 1
                self.metrics_retries += 1
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("Rate limited (429). Waiting %.0fs (attempt %d/%d)", delay, attempt + 1, _MAX_RETRIES)
                await asyncio.sleep(delay)
                continue

            if response.status_code >= 500:  # Server error, retry
                self.metrics_5xx_count += 1
                self.metrics_retries += 1
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("Server error %d. Waiting %.0fs (attempt %d/%d)", response.status_code, delay, attempt + 1, _MAX_RETRIES)
                await asyncio.sleep(delay)
                continue

            # Client error (4xx except 429) — don't retry
            raise EdinetAPIError(response.status_code, response.text[:500])

        raise EdinetAPIError(response.status_code, f"Failed after {_MAX_RETRIES} retries")

    async def get_documents_by_date(
        self,
        date: str,
        doc_type: str = "2",
    ) -> list[dict[str, Any]]:
        """Fetch document list for a specific date.

        Args:
            date: Date in YYYY-MM-DD format.
            doc_type: "1" for metadata, "2" for full data including doc info.
        """
        response = await self._get(
            "/documents.json",
            params={"date": date, "type": doc_type},
        )
        data = response.json()
        results = data.get("results", [])
        if not isinstance(results, list):
            return []
        return results

    async def download_document(self, doc_id: str, doc_type: str = "1") -> bytes:
        """Download a document by ID.

        Args:
            doc_id: Document ID (e.g., "S100VWVY").
            doc_type: "1"=submission doc, "2"=PDF, "3"=attachments,
                     "4"=XBRL, "5"=English disclosure.
        """
        response = await self._get(
            f"/documents/{doc_id}",
            params={"type": doc_type},
        )
        return response.content

    async def download_and_extract_xbrl(self, doc_id: str) -> dict[str, bytes]:
        """Download XBRL zip and extract all files.

        Returns:
            Dict mapping filename to file content.
        """
        zip_bytes = await self.download_document(doc_id, doc_type="1")
        files: dict[str, bytes] = {}
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if not info.is_dir():
                    files[info.filename] = zf.read(info.filename)
        return files

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> EdinetClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
