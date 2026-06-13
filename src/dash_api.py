"""Typed client for Dropbox Dash HTTP APIs.

Provides Pydantic models and an async client with sane defaults, including
timeouts and simple retry/backoff for transient 429/5xx responses. Request
and response metadata is logged at DEBUG level without including bodies.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ---------------------
# Pydantic data models
# ---------------------


class DashSearchRequest(BaseModel):
    query_text: str
    file_type: str | None = None
    connector_id: str | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None
    max_results: int = 20
    disable_spell_correction: bool = False


class QueryResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    uuid: str | None = None
    # Some responses return a union-like dict: {".tag": "file"}
    # Accept either a string or a dict to avoid validation errors.
    record_type: RecordTypeValue | None = None
    title: str | None = None
    url: str | None = None
    preview: str | None = None
    updated_at_ms: int | None = None
    provider_updated_at_ms: int | None = None
    display_name: str | None = None
    email: str | None = None
    relevance_score: float | None = None
    file_type_info: dict | None = None
    connector_info: dict | None = None
    creator: dict | None = None
    last_modifier: dict | None = None
    description: str | None = None
    upstream_id: str | None = None
    mime_type: str | None = None


class DashSearchResultItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    query_result: QueryResult | None = None


class DashSearchResponse(BaseModel):
    results: list[DashSearchResultItem] = Field(default_factory=list)


# Pydantic-friendly alias for heterogeneous record_type field
RecordTypeValue = str | dict


class GetLinkMetadataRequest(BaseModel):
    uuid: str
    include_body: bool = True
    include_media_metadata: bool = True
    include_preview_url: bool = True


class FileMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str | None = None
    link: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    provider_last_updated_at_ms: int | None = None
    updated_at_ms: int | None = None
    mime_type: str | None = None
    connector_info: dict | None = None
    creator: dict | None = None
    last_modifier: dict | None = None
    media_metadata: dict | None = None
    thumbnail: dict | None = None
    body: dict | None = None


class GetLinkMetadataResponse(BaseModel):
    results: list[FileMetadata] = Field(default_factory=list)


# ---------------------
# API client
# ---------------------


class DashAPI:
    """Thin client for Dropbox Dash endpoints using typed Pydantic models."""

    def __init__(self, access_token: str) -> None:
        self._access_token = access_token
        self._headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        # Reasonable timeouts
        self._timeout = httpx.Timeout(10.0, connect=10.0)
        # Retry configuration
        self._max_retries = 3
        self._backoff_base = 0.5  # seconds

    async def _post(self, url: str, json: dict) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    logger.debug("POST %s attempt=%d", url, attempt)
                    resp = await client.post(url, json=json, headers=self._headers)
                logger.debug("Response status %s for %s", resp.status_code, url)
                if resp.status_code == 401:
                    # Invalid/expired token
                    raise PermissionError("Unauthorized (401)")
                if resp.status_code in (429, 500, 502, 503, 504):
                    # Retryable statuses
                    retry_after = resp.headers.get("retry-after")
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            delay = self._backoff_base * (2 ** (attempt - 1))
                    else:
                        delay = self._backoff_base * (2 ** (attempt - 1))
                    if attempt < self._max_retries:
                        logger.debug(
                            "Retrying %s after %ss (status=%s)", url, delay, resp.status_code
                        )
                        await asyncio.sleep(delay)
                        continue
                # Non-retryable HTTP error (4xx except 401/429) — raise immediately
                resp.raise_for_status()
                return resp
            except PermissionError:
                raise
            except httpx.HTTPStatusError:
                # Non-retryable HTTP error — raise immediately, no retry
                raise
            except Exception as e:  # network or other transient errors
                last_exc = e
                if attempt < self._max_retries:
                    delay = self._backoff_base * (2 ** (attempt - 1))
                    logger.debug("Transient error for %s: %s; retrying in %ss", url, e, delay)
                    await asyncio.sleep(delay)
                    continue
                break
        # After retries exhausted
        if last_exc:
            raise last_exc

    async def search(self, req: DashSearchRequest) -> DashSearchResponse:
        body: dict[str, Any] = {
            "query_text": req.query_text,
            "filters": [],
            "query_options": {"disable_spell_correction": req.disable_spell_correction},
            "max_results": req.max_results,
        }

        # Build filters list
        filters = []

        # Add file type filter if specified
        if req.file_type and req.file_type != "document":
            filters.append(
                {
                    "filter": {
                        ".tag": "file_type_filter",
                        "file_type": req.file_type,
                    }
                }
            )
            body["search_vertical"] = {".tag": "multimedia"}

        # Add connector filter if specified
        if req.connector_id:
            filters.append(
                {
                    "filter": {
                        ".tag": "connector_filter",
                        "connector_id": req.connector_id,
                    }
                }
            )

        # Add time range filter if specified
        if req.start_datetime or req.end_datetime:
            time_filter: dict[str, Any] = {".tag": "time_range_filter"}
            if req.start_datetime:
                time_filter["start_datetime"] = req.start_datetime
            if req.end_datetime:
                time_filter["end_datetime"] = req.end_datetime
            filters.append({"filter": time_filter})

        if filters:
            body["filters"] = filters

        resp = await self._post("https://api.dropboxapi.com/2/dcs/search_mcp", json=body)
        data = resp.json()

        results = []
        for item in data.get("results", []):
            q = item.get("query_result", {}) or {}
            results.append(DashSearchResultItem(query_result=QueryResult(**q)))
        return DashSearchResponse(results=results)

    async def get_link_metadata(self, req: GetLinkMetadataRequest) -> GetLinkMetadataResponse:
        body = {
            "include_body": req.include_body,
            "url_or_uuids": [{"link_type": {".tag": "uuid", "uuid": req.uuid}}],
            "include_media_metadata": req.include_media_metadata,
            "include_preview_url": req.include_preview_url,
        }

        resp = await self._post("https://api.dropboxapi.com/2/dcs/get_link_metadata_mcp", json=body)
        data = resp.json()

        results = [FileMetadata(**item) for item in data.get("results", [])]
        return GetLinkMetadataResponse(results=results)
