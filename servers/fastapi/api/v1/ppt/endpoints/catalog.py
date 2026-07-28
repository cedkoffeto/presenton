from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from constants.presentation import DEFAULT_TEMPLATES
from models.sql.template import TemplateModel
from models.sql.theme import ThemeModel
from services.database import get_async_session
from services.resource_catalog_service import RESOURCE_CATALOG_SERVICE, CatalogHit
from utils.request_auth import get_request_actor

CATALOG_ROUTER = APIRouter(prefix="/catalog", tags=["Catalog"])

CatalogKind = Literal["all", "theme", "template"]


async def _load_theme_payloads(
    sql_session: AsyncSession,
    actor,
    *,
    query: Optional[str] = None,
    ids: Optional[list] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    conditions = [RESOURCE_CATALOG_SERVICE.accessible_filter(ThemeModel, actor)]
    if query:
        conditions.append(RESOURCE_CATALOG_SERVICE.text_filter(ThemeModel, query))
    if ids:
        conditions.append(ThemeModel.id.in_(ids))

    result = await sql_session.scalars(
        select(ThemeModel)
        .where(*conditions)
        .order_by(ThemeModel.created_at.desc())
        .limit(limit)
    )
    return [RESOURCE_CATALOG_SERVICE.theme_payload(theme) for theme in result]


async def _load_template_payloads(
    sql_session: AsyncSession,
    actor,
    *,
    query: Optional[str] = None,
    ids: Optional[list] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    conditions = [RESOURCE_CATALOG_SERVICE.accessible_filter(TemplateModel, actor)]
    if query:
        conditions.append(RESOURCE_CATALOG_SERVICE.text_filter(TemplateModel, query))
    if ids:
        conditions.append(TemplateModel.id.in_(ids))

    result = await sql_session.scalars(
        select(TemplateModel)
        .where(*conditions)
        .order_by(TemplateModel.created_at.desc())
        .limit(limit)
    )
    return [
        await RESOURCE_CATALOG_SERVICE.template_payload(template, sql_session)
        for template in result
    ]


def _hit_ids(hits: list[CatalogHit], kind: Literal["theme", "template"]):
    return [hit.id for hit in hits if hit.kind == kind]


@CATALOG_ROUTER.get("/themes")
async def list_catalog_themes(
    request: Request,
    q: Optional[str] = Query(default=None, description="Optional text search query"),
    limit: int = Query(default=20, ge=1, le=100),
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request)
    return await _load_theme_payloads(
        sql_session,
        actor,
        query=q,
        limit=limit,
    )


@CATALOG_ROUTER.get("/templates")
async def list_catalog_templates(
    request: Request,
    q: Optional[str] = Query(default=None, description="Optional text search query"),
    include_defaults: bool = Query(default=True),
    limit: int = Query(default=20, ge=1, le=100),
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request)
    templates = await _load_template_payloads(
        sql_session,
        actor,
        query=q,
        limit=limit,
    )
    if include_defaults and not q:
        defaults = [
            {
                "kind": "template",
                "id": template,
                "name": template,
                "description": None,
                "visibility": "public",
                "team_id": None,
                "total_layouts": None,
                "layout_names": [],
                "selector": {"template": template},
            }
            for template in DEFAULT_TEMPLATES
        ]
        return defaults + templates
    return templates


@CATALOG_ROUTER.get("/search")
async def search_catalog(
    request: Request,
    q: str = Query(..., min_length=1, description="Semantic search query"),
    kind: CatalogKind = Query(default="all"),
    limit: int = Query(default=10, ge=1, le=50),
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request)
    hits = await RESOURCE_CATALOG_SERVICE.search_index(q, actor, kind, limit)

    results: list[dict[str, Any]] = []
    theme_ids = _hit_ids(hits, "theme")
    template_ids = _hit_ids(hits, "template")

    if kind in ("all", "theme") and theme_ids:
        results.extend(
            await _load_theme_payloads(
                sql_session,
                actor,
                ids=theme_ids,
                limit=limit,
            )
        )
    if kind in ("all", "template") and template_ids:
        results.extend(
            await _load_template_payloads(
                sql_session,
                actor,
                ids=template_ids,
                limit=limit,
            )
        )

    if results:
        return results[:limit]

    # Fallback keeps this endpoint useful when Mem0 is disabled or not yet indexed.
    if kind in ("all", "theme"):
        results.extend(
            await _load_theme_payloads(sql_session, actor, query=q, limit=limit)
        )
    if kind in ("all", "template") and len(results) < limit:
        results.extend(
            await _load_template_payloads(
                sql_session,
                actor,
                query=q,
                limit=limit - len(results),
            )
        )
    return results[:limit]


@CATALOG_ROUTER.post("/reindex")
async def reindex_catalog(
    request: Request,
    kind: CatalogKind = Query(default="all"),
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request)
    indexed = {"themes": 0, "templates": 0}

    if kind in ("all", "theme"):
        themes = await sql_session.scalars(
            select(ThemeModel).where(
                RESOURCE_CATALOG_SERVICE.accessible_filter(ThemeModel, actor)
            )
        )
        for theme in themes:
            await RESOURCE_CATALOG_SERVICE.index_theme(theme)
            indexed["themes"] += 1

    if kind in ("all", "template"):
        templates = await sql_session.scalars(
            select(TemplateModel).where(
                RESOURCE_CATALOG_SERVICE.accessible_filter(TemplateModel, actor)
            )
        )
        for template in templates:
            await RESOURCE_CATALOG_SERVICE.index_template(template, sql_session)
            indexed["templates"] += 1

    return indexed
