"""
title: Presenton Presentation Generator
author: OpenWebUI
description: Generate and edit Presenton presentations from Open WebUI using the Presenton async API with live status updates.
required_open_webui_version: 0.4.0
requirements:
version: 0.3.0
licence: MIT
"""

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from pydantic import BaseModel, Field

logger = logging.getLogger("openwebui.presenton_tool")

EventEmitter = Callable[[Dict[str, Any]], Awaitable[None]]


class Tools:
    class Valves(BaseModel):
        api_key: str = Field(
            default="",
            description="Presenton API key",
            json_schema_extra={"input": {"type": "password"}},
        )
        base_url: str = Field(
            default="http://presenton:80",
            description="Presenton base URL",
        )
        default_team_slug: str = Field(
            default="",
            description="Optional default Presenton team slug",
        )
        default_team_name: str = Field(
            default="",
            description="Optional default Presenton team name",
        )
        poll_interval_seconds: float = Field(
            default=2.0,
            description="Seconds between async task status checks",
        )

    def __init__(self):
        self.citation = False
        self.valves = self.Valves()
        self._last_base_url: Optional[str] = None

    def _get_api_key(self, api_key: Optional[str] = None) -> str:
        key = (
            api_key or self.valves.api_key or os.getenv("PRESENTON_API_KEY") or ""
        ).strip()
        if not key:
            raise ValueError(
                "Missing Presenton API key. Configure the tool valve or set PRESENTON_API_KEY."
            )
        return key

    def _get_base_url(self) -> str:
        return (
            self._last_base_url
            or self.valves.base_url
            or os.getenv("PRESENTON_API_BASE_URL")
            or "http://presenton:80"
        ).rstrip("/")

    def _candidate_base_urls(self) -> List[str]:
        configured = (
            self.valves.base_url
            or os.getenv("PRESENTON_API_BASE_URL")
            or "http://presenton:80"
        ).strip()
        candidates = [
            configured,
            "http://presenton:80",
            "http://host.docker.internal:5001",
            "http://localhost:5001",
            "http://127.0.0.1:5001",
        ]
        seen = set()
        ordered = []
        for candidate in candidates:
            if not candidate:
                continue
            normalized = candidate.rstrip("/")
            if normalized not in seen:
                seen.add(normalized)
                ordered.append(normalized)
        return ordered

    def _mask_secret(self, value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}...{value[-2:]}"

    def _extract_user_context(self, __user__: Optional[dict]) -> Dict[str, str]:
        user = __user__ or {}
        user_id = (
            user.get("id")
            or user.get("_id")
            or user.get("user_id")
            or user.get("sub")
            or ""
        )
        email = user.get("email") or user.get("mail") or user.get("user_email") or ""
        username = (
            user.get("username") or user.get("user_name") or user.get("login") or ""
        )
        display_name = (
            user.get("name")
            or user.get("full_name")
            or username
            or email
            or user_id
            or ""
        )

        if not username:
            if email:
                username = email
            elif user_id:
                username = str(user_id)

        return {
            "id": str(user_id or ""),
            "email": str(email or ""),
            "username": str(username or ""),
            "name": str(display_name or ""),
        }

    def _build_headers(
        self,
        api_key: Optional[str] = None,
        __user__: Optional[dict] = None,
    ) -> Dict[str, str]:
        api_key_value = self._get_api_key(api_key)
        user_ctx = self._extract_user_context(__user__)

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key_value}",
            "X-Presenton-Api-Key": api_key_value,
        }

        if user_ctx["id"]:
            headers["X-Presenton-User-Id"] = user_ctx["id"]
        if user_ctx["username"]:
            headers["X-Presenton-Username"] = user_ctx["username"]
        if user_ctx["email"]:
            headers["X-Presenton-User-Email"] = user_ctx["email"]
        if user_ctx["name"]:
            headers["X-Presenton-User-Name"] = user_ctx["name"]

        if self.valves.default_team_slug:
            headers["X-Presenton-Team-Slug"] = self.valves.default_team_slug
        if self.valves.default_team_name:
            headers["X-Presenton-Team-Name"] = self.valves.default_team_name

        return headers

    def _request_sync(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        timeout: int = 300,
        __user__: Optional[dict] = None,
    ) -> Dict[str, Any]:
        headers = self._build_headers(api_key=api_key, __user__=__user__)

        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        logger.info(
            "Presenton request start | method=%s path=%s timeout=%s payload=%s auth=%s",
            method,
            path,
            timeout,
            json.dumps(payload or {}, default=str),
            self._mask_secret(headers.get("X-Presenton-Api-Key", "")),
        )

        last_error = None
        for base_url in self._candidate_base_urls():
            url = f"{base_url}{path}"
            request = urllib.request.Request(
                url, data=data, headers=headers, method=method
            )

            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    self._last_base_url = base_url
                    response_text = response.read().decode("utf-8")
                    if not response_text:
                        return {"status": response.status}
                    try:
                        return json.loads(response_text)
                    except json.JSONDecodeError:
                        return {"status": response.status, "raw": response_text}
            except urllib.error.HTTPError as exc:
                error_text = exc.read().decode("utf-8", errors="replace")
                try:
                    details = json.loads(error_text)
                except json.JSONDecodeError:
                    details = {"raw": error_text}
                raise RuntimeError(
                    f"Presenton API request failed with status {exc.code}: {details}"
                ) from exc
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", None)
                last_error = str(reason or exc)
                continue

        raise RuntimeError(
            f"Presenton API request failed: {last_error or 'unknown error'}"
        )

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        timeout: int = 300,
        __user__: Optional[dict] = None,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._request_sync,
            method,
            path,
            payload,
            api_key,
            timeout,
            __user__,
        )

    async def _emit_status(
        self,
        __event_emitter__: Optional[EventEmitter],
        description: str,
        done: bool = False,
        hidden: bool = False,
    ) -> None:
        if not __event_emitter__:
            return
        await __event_emitter__(
            {
                "type": "status",
                "data": {
                    "description": description,
                    "done": done,
                    "hidden": hidden,
                },
            }
        )

    def _absolute_url(self, path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        parsed = urllib.parse.urlparse(path)
        if parsed.scheme and parsed.netloc:
            return path
        return f"{self._get_base_url()}/{path.lstrip('/')}"

    def _format_result(self, response: Dict[str, Any]) -> str:
        download_url = self._absolute_url(
            response.get("download_url") or response.get("path")
        )
        edit_url = self._absolute_url(
            response.get("edit_url") or response.get("edit_path")
        )
        public_share_url = self._absolute_url(response.get("public_share_url"))

        lines = []
        if download_url:
            lines.append(f"[Download presentation]({download_url})")
        if edit_url:
            lines.append(f"[Open in Presenton editor]({edit_url})")
        if public_share_url:
            lines.append(f"[Public share link]({public_share_url})")

        summary = {
            "presentation_id": response.get("presentation_id"),
            "download_url": download_url,
            "edit_url": edit_url,
            "public_share_url": public_share_url,
            "credits_consumed": response.get("credits_consumed"),
        }

        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(summary, indent=2))
        lines.append("```")
        return "\n".join(lines)

    def _format_catalog_results(self, results: Any) -> str:
        if not isinstance(results, list):
            return json.dumps(results, indent=2)

        lines = []
        if not results:
            return "No accessible themes or templates matched the query."

        for index, item in enumerate(results, start=1):
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") or "resource"
            name = item.get("name") or item.get("id") or "Untitled"
            description = item.get("description")
            selector = item.get("selector") or {}
            visibility = item.get("visibility")

            lines.append(f"{index}. **{kind}: {name}**")
            if description:
                lines.append(f"   {description}")
            if visibility:
                lines.append(f"   visibility: `{visibility}`")
            if selector:
                lines.append("")
                lines.append("   ```json")
                lines.append("   " + json.dumps(selector, indent=2).replace("\n", "\n   "))
                lines.append("   ```")

        lines.append("")
        lines.append("Raw results:")
        lines.append("```json")
        lines.append(json.dumps(results, indent=2))
        lines.append("```")
        return "\n".join(lines)

    async def _wait_for_async_task(
        self,
        task_id: str,
        api_key: Optional[str],
        timeout: int,
        __user__: Optional[dict],
        __event_emitter__: Optional[EventEmitter],
    ) -> Dict[str, Any]:
        started_at = time.monotonic()
        last_message = ""
        poll_interval = max(float(self.valves.poll_interval_seconds or 2.0), 0.5)

        while True:
            if time.monotonic() - started_at > timeout:
                await self._emit_status(
                    __event_emitter__,
                    f"Presenton task timed out: {task_id}",
                    done=True,
                )
                raise TimeoutError(
                    f"Timed out waiting for Presenton async task {task_id}"
                )

            status_response = await self._request(
                "GET",
                f"/api/v1/ppt/presentation/status/{urllib.parse.quote(task_id)}",
                api_key=api_key,
                timeout=60,
                __user__=__user__,
            )

            status = str(status_response.get("status") or "").lower()
            message = (
                status_response.get("message")
                or f"Presenton task status: {status or 'unknown'}"
            )

            if message != last_message:
                await self._emit_status(__event_emitter__, message, done=False)
                last_message = message

            if status == "completed":
                data = status_response.get("data")
                if not isinstance(data, dict):
                    raise RuntimeError(
                        f"Presenton async task {task_id} completed without result data"
                    )
                await self._emit_status(
                    __event_emitter__,
                    "Presentation generation completed",
                    done=True,
                )
                return data

            if status in {"error", "failed", "cancelled", "canceled"}:
                error = status_response.get("error") or {}
                await self._emit_status(
                    __event_emitter__,
                    f"Presentation generation failed: {error or message}",
                    done=True,
                )
                raise RuntimeError(
                    f"Presenton async task {task_id} failed: {error or message}"
                )

            await asyncio.sleep(poll_interval)

    async def generate_presentation(
        self,
        content: str,
        n_slides: int = 5,
        language: str = "English",
        template: Optional[str] = None,
        standard_template: Optional[str] = None,
        theme: Optional[str] = None,
        theme_name: Optional[str] = None,
        theme_id: Optional[str] = None,
        instructions: Optional[str] = None,
        export_as: str = "pptx",
        web_search: bool = False,
        api_key: Optional[str] = None,
        timeout: int = 600,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[EventEmitter] = None,
    ) -> str:
        """
        Generate a new presentation through Presenton's async API.
        Open WebUI receives live status updates while Presenton works.

        :param template: Built-in template name or custom template id, e.g. general or custom-...
        :param theme: Backward-compatible saved theme name. Prefer theme_name.
        :param theme_name: Saved UI theme name to apply.
        :param theme_id: Saved theme UUID to apply.
        """
        if not content or not content.strip():
            raise ValueError("content must be a non-empty string")
        if sum(1 for value in (theme, theme_name, theme_id) if value) > 1:
            raise ValueError("Use only one of theme, theme_name, or theme_id")

        payload: Dict[str, Any] = {
            "content": content,
            "n_slides": n_slides,
            "language": language,
            "export_as": export_as,
            "web_search": web_search,
        }

        if instructions:
            payload["instructions"] = instructions
        if theme_name:
            payload["theme_name"] = theme_name
        elif theme_id:
            payload["theme_id"] = theme_id
        elif theme:
            payload["theme_name"] = theme
        if template:
            payload["template"] = template
        elif standard_template:
            payload["template"] = standard_template

        await self._emit_status(
            __event_emitter__,
            "Submitting presentation generation task",
            done=False,
        )

        task = await self._request(
            "POST",
            "/api/v1/ppt/presentation/generate/async",
            payload=payload,
            api_key=api_key,
            timeout=120,
            __user__=__user__,
        )

        task_id = task.get("id")
        if not task_id:
            raise RuntimeError(f"Presenton async API did not return a task id: {task}")

        await self._emit_status(
            __event_emitter__,
            f"Presenton task queued: {task_id}",
            done=False,
        )

        response = await self._wait_for_async_task(
            str(task_id),
            api_key,
            timeout,
            __user__,
            __event_emitter__,
        )
        return self._format_result(response)

    async def search_catalog(
        self,
        query: str,
        kind: str = "all",
        limit: int = 10,
        api_key: Optional[str] = None,
        timeout: int = 120,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[EventEmitter] = None,
    ) -> str:
        """
        Semantically search accessible Presenton themes and templates for the connected Open WebUI user.

        Use this before generate_presentation when you want Presenton to pick an appropriate
        theme/template. The returned selector can be passed directly to generate_presentation.

        :param query: Natural-language search query, e.g. "minimal investor pitch with dark brand".
        :param kind: all, theme, or template.
        :param limit: Maximum number of results.
        """
        if not query or not query.strip():
            raise ValueError("query must be a non-empty string")
        normalized_kind = (kind or "all").strip().lower()
        if normalized_kind not in {"all", "theme", "template"}:
            raise ValueError("kind must be one of: all, theme, template")

        await self._emit_status(
            __event_emitter__,
            f"Searching Presenton catalog for {normalized_kind} matches",
            done=False,
        )
        params = urllib.parse.urlencode(
            {
                "q": query,
                "kind": normalized_kind,
                "limit": max(1, min(int(limit or 10), 50)),
            }
        )
        response = await self._request(
            "GET",
            f"/api/v1/ppt/catalog/search?{params}",
            api_key=api_key,
            timeout=timeout,
            __user__=__user__,
        )
        await self._emit_status(
            __event_emitter__,
            "Presenton catalog search completed",
            done=True,
        )
        return self._format_catalog_results(response)

    async def list_themes(
        self,
        query: Optional[str] = None,
        limit: int = 20,
        api_key: Optional[str] = None,
        timeout: int = 120,
        __user__: Optional[dict] = None,
    ) -> str:
        """
        List accessible saved Presenton themes, optionally filtered by text query.
        """
        params = {"limit": max(1, min(int(limit or 20), 100))}
        if query:
            params["q"] = query
        response = await self._request(
            "GET",
            f"/api/v1/ppt/catalog/themes?{urllib.parse.urlencode(params)}",
            api_key=api_key,
            timeout=timeout,
            __user__=__user__,
        )
        return self._format_catalog_results(response)

    async def list_templates(
        self,
        query: Optional[str] = None,
        include_defaults: bool = True,
        limit: int = 20,
        api_key: Optional[str] = None,
        timeout: int = 120,
        __user__: Optional[dict] = None,
    ) -> str:
        """
        List accessible Presenton templates, optionally filtered by text query.
        """
        params = {
            "include_defaults": str(bool(include_defaults)).lower(),
            "limit": max(1, min(int(limit or 20), 100)),
        }
        if query:
            params["q"] = query
        response = await self._request(
            "GET",
            f"/api/v1/ppt/catalog/templates?{urllib.parse.urlencode(params)}",
            api_key=api_key,
            timeout=timeout,
            __user__=__user__,
        )
        return self._format_catalog_results(response)

    async def reindex_catalog(
        self,
        kind: str = "all",
        api_key: Optional[str] = None,
        timeout: int = 300,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[EventEmitter] = None,
    ) -> str:
        """
        Sync existing accessible themes/templates into Presenton's semantic catalog index.

        New and updated custom themes/templates are indexed automatically, but this is useful
        after enabling the feature for existing data.
        """
        normalized_kind = (kind or "all").strip().lower()
        if normalized_kind not in {"all", "theme", "template"}:
            raise ValueError("kind must be one of: all, theme, template")

        await self._emit_status(
            __event_emitter__,
            f"Reindexing Presenton catalog: {normalized_kind}",
            done=False,
        )
        response = await self._request(
            "POST",
            f"/api/v1/ppt/catalog/reindex?{urllib.parse.urlencode({'kind': normalized_kind})}",
            api_key=api_key,
            timeout=timeout,
            __user__=__user__,
        )
        await self._emit_status(
            __event_emitter__,
            "Presenton catalog reindex completed",
            done=True,
        )
        return json.dumps(response, indent=2)

    async def edit_presentation(
        self,
        presentation_id: str,
        slides: Union[List[Dict[str, Any]], str],
        export_as: str = "pptx",
        api_key: Optional[str] = None,
        timeout: int = 300,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[EventEmitter] = None,
    ) -> str:
        """
        Edit an existing Presenton presentation owned by or shared with the connected Open WebUI user.
        Open WebUI receives live status updates while Presenton applies slide edits and exports.
        """
        if not presentation_id:
            raise ValueError("presentation_id must be provided")

        if isinstance(slides, str):
            try:
                slides_payload = json.loads(slides)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "slides must be a valid JSON array when provided as a string"
                ) from exc
        else:
            slides_payload = slides

        await self._emit_status(
            __event_emitter__,
            "Submitting presentation edit task",
            done=False,
        )
        task = await self._request(
            "POST",
            "/api/v1/ppt/presentation/edit/async",
            payload={
                "presentation_id": presentation_id,
                "slides": slides_payload,
                "export_as": export_as,
            },
            api_key=api_key,
            timeout=timeout,
            __user__=__user__,
        )
        task_id = task.get("id")
        if not task_id:
            raise RuntimeError(f"Presenton async edit API did not return a task id: {task}")

        await self._emit_status(
            __event_emitter__,
            f"Presenton edit task queued: {task_id}",
            done=False,
        )
        response = await self._wait_for_async_task(
            str(task_id),
            api_key,
            timeout,
            __user__,
            __event_emitter__,
        )
        return self._format_result(response)

    async def get_presentation(
        self,
        presentation_id: str,
        api_key: Optional[str] = None,
        timeout: int = 120,
        __user__: Optional[dict] = None,
    ) -> str:
        """
        Fetch an existing presentation for the connected Open WebUI user.
        """
        if not presentation_id:
            raise ValueError("presentation_id must be provided")

        response = await self._request(
            "GET",
            f"/api/v1/ppt/presentation/{presentation_id}",
            api_key=api_key,
            timeout=timeout,
            __user__=__user__,
        )
        return json.dumps(response, indent=2)
