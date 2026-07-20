import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from services.export_task_service import EXPORT_TASK_SERVICE
from templates import get_layout_by_name as tpl_layout_fetcher
from templates.presentation_layout import PresentationLayoutModel


def _make_aio_layout_session(resp: AsyncMock):
    sess = MagicMock()
    getter = MagicMock()
    getter.__aenter__ = AsyncMock(return_value=resp)
    getter.__aexit__ = AsyncMock(return_value=None)
    sess.get = MagicMock(return_value=getter)
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=None)
    return sess


def test_template_url_candidates_prefer_loopback_8080(monkeypatch):
    monkeypatch.delenv("TEMPLATE_LAYOUT_INTERNAL_BASE_URL", raising=False)
    monkeypatch.delenv("PRESENTON_NEXT_URL", raising=False)
    monkeypatch.delenv("NEXT_PUBLIC_URL", raising=False)

    urls = tpl_layout_fetcher._template_url_candidates("/schema", layout_name="deck")

    assert urls[0] == "http://127.0.0.1:8080/schema?group=deck"
    assert "http://localhost/schema?group=deck" in urls


def test_get_layout_by_name_uses_http_template_payload_first(monkeypatch):
    resp = AsyncMock()
    resp.status = 200
    resp.json = AsyncMock(
        return_value={
            "name": "grp",
            "ordered": False,
            "slides": [{"id": "layout", "json_schema": {"title": "t"}}],
        }
    )

    captured_urls: list[str] = []

    monkeypatch.delenv("TEMPLATE_LAYOUT_INTERNAL_BASE_URL", raising=False)
    monkeypatch.delenv("PRESENTON_NEXT_URL", raising=False)
    monkeypatch.delenv("NEXT_PUBLIC_URL", raising=False)

    def capture_session(*_a, **_k):
        sess = MagicMock()

        def _get(url, *, headers=None, **_kw):
            del headers
            captured_urls.append(url)
            inner = MagicMock()
            inner.__aenter__ = AsyncMock(return_value=resp)
            inner.__aexit__ = AsyncMock(return_value=None)
            return inner

        sess.get = MagicMock(side_effect=_get)
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=None)
        return sess

    extract_schema = AsyncMock(
        side_effect=HTTPException(status_code=500, detail="boom")
    )

    async def runner():
        with patch.object(
            EXPORT_TASK_SERVICE,
            "extract_schema",
            new=extract_schema,
        ), patch(
            "templates.get_layout_by_name.aiohttp.ClientSession",
            side_effect=capture_session,
        ):
            layout = await tpl_layout_fetcher.get_layout_by_name("deck")
            assert isinstance(layout, PresentationLayoutModel)

    asyncio.run(runner())
    assert captured_urls[0] == "http://127.0.0.1:8080/api/template?group=deck"
    extract_schema.assert_not_called()


def test_get_layout_by_name_falls_back_to_extract_schema_when_http_fails(monkeypatch):
    resp = AsyncMock()
    resp.status = 500
    resp.text = AsyncMock(return_value="down")

    monkeypatch.setenv("TEMPLATE_LAYOUT_INTERNAL_BASE_URL", "http://next.local")
    monkeypatch.delenv("PRESENTON_NEXT_URL", raising=False)
    monkeypatch.delenv("NEXT_PUBLIC_URL", raising=False)

    async def runner():
        with patch(
            "templates.get_layout_by_name.aiohttp.ClientSession",
            return_value=_make_aio_layout_session(resp),
        ), patch.object(
            EXPORT_TASK_SERVICE,
            "extract_schema",
            new=AsyncMock(
                return_value=PresentationLayoutModel(
                    name="grp",
                    ordered=False,
                    slides=[{"id": "layout", "json_schema": {"title": "t"}}],
                )
            ),
        ) as extract_schema:
            layout = await tpl_layout_fetcher.get_layout_by_name("deck")
            assert isinstance(layout, PresentationLayoutModel)
            extract_schema.assert_awaited()

    asyncio.run(runner())
