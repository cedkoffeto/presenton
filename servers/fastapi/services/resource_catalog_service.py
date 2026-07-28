import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional

from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.presentation_layout_code import PresentationLayoutCodeModel
from models.sql.template import TemplateModel
from models.sql.theme import ThemeModel
from services.mem0_oss_memory import get_shared_mem0_client
from utils.access_control import VISIBILITY_PUBLIC, VISIBILITY_TEAM, AuthActor
from utils.asset_directory_utils import normalize_slide_asset_url

LOGGER = logging.getLogger(__name__)

CatalogKind = Literal["theme", "template"]

_RESOURCE_RE = re.compile(
    r"kind=(?P<kind>theme|template)\s+id=(?P<id>[0-9a-fA-F-]{36})"
)


@dataclass(frozen=True)
class CatalogHit:
    kind: CatalogKind
    id: uuid.UUID


class ResourceCatalogService:
    """Semantic index for user-visible themes/templates.

    SQL is the source of truth. Mem0/Qdrant is only a best-effort search index:
    stale vector hits are reloaded from SQL and access-checked before returning.
    """

    def __init__(self) -> None:
        self._namespace_prefix = "catalog"
        self._top_k = 20

    def _scope_ids(self, owner_user_id, team_id, visibility: str) -> list[str]:
        scopes: list[str] = []
        if owner_user_id:
            scopes.append(f"{self._namespace_prefix}:user:{owner_user_id}")
        if visibility == VISIBILITY_TEAM and team_id:
            scopes.append(f"{self._namespace_prefix}:team:{team_id}")
        if visibility == VISIBILITY_PUBLIC:
            scopes.append(f"{self._namespace_prefix}:public")
        return scopes

    def _accessible_scope_ids(self, actor: AuthActor) -> list[str]:
        scopes = [
            f"{self._namespace_prefix}:user:{actor.owner_user_id}",
            f"{self._namespace_prefix}:public",
        ]
        if actor.team_id:
            scopes.append(f"{self._namespace_prefix}:team:{actor.team_id}")
        return scopes

    async def _get_client(self):
        try:
            return get_shared_mem0_client()
        except Exception:
            LOGGER.exception("Resource catalog memory client unavailable")
            return None

    @staticmethod
    def _theme_document(theme: ThemeModel) -> str:
        data = theme.data or {}
        fonts = data.get("fonts") if isinstance(data, dict) else {}
        colors = data.get("colors") if isinstance(data, dict) else {}
        return "\n".join(
            [
                "[presenton_catalog_resource]",
                f"kind=theme id={theme.id}",
                f"name={theme.name}",
                f"description={theme.description}",
                f"company_name={theme.company_name or ''}",
                f"visibility={theme.visibility}",
                f"fonts={json.dumps(fonts, ensure_ascii=False)}",
                f"colors={json.dumps(colors, ensure_ascii=False)}",
            ]
        )

    @staticmethod
    async def _template_document(
        template: TemplateModel,
        sql_session: AsyncSession,
    ) -> str:
        result = await sql_session.execute(
            select(
                PresentationLayoutCodeModel.layout_id,
                PresentationLayoutCodeModel.layout_name,
            ).where(PresentationLayoutCodeModel.presentation == template.id)
        )
        layouts = [
            {"id": layout_id, "name": layout_name}
            for layout_id, layout_name in result.all()
        ]
        return "\n".join(
            [
                "[presenton_catalog_resource]",
                f"kind=template id={template.id}",
                f"name={template.name}",
                f"description={template.description or ''}",
                f"visibility={template.visibility}",
                f"layouts={json.dumps(layouts, ensure_ascii=False)}",
            ]
        )

    async def _add_document(self, document: str, scope_ids: list[str]) -> None:
        client = await self._get_client()
        if client is None:
            return

        def _add(scope_id: str):
            payload = [{"role": "user", "content": document}]
            try:
                return client.add(payload, user_id=scope_id, infer=False)
            except TypeError:
                return client.add(messages=payload, user_id=scope_id, infer=False)

        for scope_id in scope_ids:
            try:
                await asyncio.to_thread(_add, scope_id)
            except Exception:
                LOGGER.exception("Failed to index catalog resource in scope=%s", scope_id)

    async def index_theme(self, theme: ThemeModel) -> None:
        await self._add_document(
            self._theme_document(theme),
            self._scope_ids(theme.owner_user_id, theme.team_id, theme.visibility),
        )

    async def index_template(
        self,
        template: TemplateModel,
        sql_session: AsyncSession,
    ) -> None:
        await self._add_document(
            await self._template_document(template, sql_session),
            self._scope_ids(template.owner_user_id, template.team_id, template.visibility),
        )

    @staticmethod
    def _parse_hits(response: Any, kind: Optional[str]) -> list[CatalogHit]:
        if isinstance(response, dict):
            results = response.get("results") or []
        elif isinstance(response, list):
            results = response
        else:
            results = []

        hits: list[CatalogHit] = []
        seen: set[tuple[str, uuid.UUID]] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            text = str(item.get("memory") or item.get("text") or item.get("data") or "")
            match = _RESOURCE_RE.search(text)
            if not match:
                continue
            hit_kind = match.group("kind")
            if kind not in (None, "all") and hit_kind != kind:
                continue
            try:
                hit_id = uuid.UUID(match.group("id"))
            except ValueError:
                continue
            key = (hit_kind, hit_id)
            if key in seen:
                continue
            seen.add(key)
            hits.append(CatalogHit(kind=hit_kind, id=hit_id))  # type: ignore[arg-type]
        return hits

    async def search_index(
        self,
        query: str,
        actor: AuthActor,
        kind: Optional[str] = None,
        limit: int = 10,
    ) -> list[CatalogHit]:
        client = await self._get_client()
        if client is None or not query.strip():
            return []

        hits: list[CatalogHit] = []
        seen: set[tuple[str, uuid.UUID]] = set()

        def _search(scope_id: str):
            try:
                return client.search(
                    query,
                    filters={"user_id": scope_id},
                    top_k=self._top_k,
                )
            except TypeError:
                return client.search(query, user_id=scope_id, top_k=self._top_k)

        for scope_id in self._accessible_scope_ids(actor):
            try:
                response = await asyncio.to_thread(_search, scope_id)
            except Exception:
                LOGGER.exception("Failed to search catalog scope=%s", scope_id)
                continue
            for hit in self._parse_hits(response, kind):
                key = (hit.kind, hit.id)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(hit)
                if len(hits) >= limit:
                    return hits
        return hits

    @staticmethod
    def theme_payload(theme: ThemeModel) -> dict[str, Any]:
        return {
            "kind": "theme",
            "id": str(theme.id),
            "name": theme.name,
            "description": theme.description,
            "company_name": theme.company_name,
            "logo": theme.logo,
            "logo_url": normalize_slide_asset_url(theme.logo_url) if theme.logo_url else None,
            "data": theme.data or {},
            "visibility": theme.visibility,
            "team_id": str(theme.team_id) if theme.team_id else None,
            "selector": {"theme_name": theme.name, "theme_id": str(theme.id)},
        }

    @staticmethod
    async def template_payload(
        template: TemplateModel,
        sql_session: AsyncSession,
    ) -> dict[str, Any]:
        result = await sql_session.execute(
            select(PresentationLayoutCodeModel.layout_name).where(
                PresentationLayoutCodeModel.presentation == template.id
            )
        )
        layout_names = list(result.scalars().all())
        return {
            "kind": "template",
            "id": f"custom-{template.id}",
            "name": template.name,
            "description": template.description,
            "visibility": template.visibility,
            "team_id": str(template.team_id) if template.team_id else None,
            "total_layouts": len(layout_names),
            "layout_names": layout_names,
            "selector": {"template": f"custom-{template.id}"},
        }

    @staticmethod
    def accessible_filter(model, actor: AuthActor):
        return or_(
            model.owner_user_id == actor.owner_user_id,
            model.visibility == VISIBILITY_PUBLIC,
            (model.visibility == VISIBILITY_TEAM) & (model.team_id == actor.team_id),
        )

    @staticmethod
    def text_filter(model, query: str):
        normalized = f"%{query.strip().lower()}%"
        return or_(
            func.lower(model.name).like(normalized),
            func.lower(model.description).like(normalized),
        )


RESOURCE_CATALOG_SERVICE = ResourceCatalogService()
