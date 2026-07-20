import asyncio
import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints import presentation as presentation_endpoint
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationAndPath
from models.presentation_outline_model import SlideOutlineModel
from models.presentation_structure_model import PresentationStructureModel
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.user import UserModel
from templates.presentation_layout import PresentationLayoutModel, SlideLayoutModel
from tests.conftest import FakeAsyncSession
from utils.access_control import AuthActor


def _run(coro):
    return asyncio.run(coro)


def _mock_layout() -> PresentationLayoutModel:
    return PresentationLayoutModel(
        name="general",
        ordered=False,
        slides=[
            SlideLayoutModel(id="layout-1", name="Title", json_schema={"title": "title"}),
            SlideLayoutModel(id="layout-2", name="Body", json_schema={"title": "body"}),
        ],
    )


def test_generate_presentation_handler_full_flow_uses_mocked_dependencies(fake_async_session):
    request = GeneratePresentationRequest(
        content="Create a two-slide deck about renewable energy.",
        n_slides=2,
        language="English",
        export_as="pptx",
        template="general",
    )
    presentation_id = uuid.uuid4()

    async def fake_outline_stream(*_args, **_kwargs):
        yield '{"slides":[{"content":"## Intro"},{"content":"## Action Plan"}]}'

    get_slide_content = AsyncMock(
        side_effect=[
            {"title": "Intro", "points": ["A"]},
            {"title": "Action Plan", "points": ["B"]},
        ]
    )

    with patch.object(
        presentation_endpoint.MEM0_PRESENTATION_MEMORY_SERVICE,
        "store_generation_context",
        new=AsyncMock(),
    ), patch.object(
        presentation_endpoint.MEM0_PRESENTATION_MEMORY_SERVICE,
        "store_generated_outlines",
        new=AsyncMock(),
    ), patch.object(
        presentation_endpoint,
        "generate_ppt_outline",
        side_effect=fake_outline_stream,
    ), patch.object(
        presentation_endpoint,
        "get_layout_by_name",
        new=AsyncMock(return_value=_mock_layout()),
    ), patch.object(
        presentation_endpoint,
        "generate_presentation_structure",
        new=AsyncMock(return_value=PresentationStructureModel(slides=[0, 1])),
    ), patch.object(
        presentation_endpoint,
        "get_slide_content_from_type_and_outline",
        get_slide_content,
    ), patch.object(
        presentation_endpoint,
        "process_slide_and_fetch_assets",
        new=AsyncMock(return_value=[]),
    ), patch.object(
        presentation_endpoint,
        "get_images_directory",
        return_value="/tmp",
    ), patch.object(
        presentation_endpoint,
        "ImageGenerationService",
        return_value=Mock(),
    ), patch.object(
        presentation_endpoint,
        "export_presentation",
        new=AsyncMock(
            return_value=PresentationAndPath(
                presentation_id=presentation_id,
                path="/tmp/generated/deck.pptx",
            )
        ),
    ), patch.object(
        presentation_endpoint.CONCURRENT_SERVICE,
        "run_task",
        new=Mock(),
    ), patch.object(
        presentation_endpoint,
        "random",
        new=Mock(randint=Mock(return_value=0)),
    ):
        response = _run(
            presentation_endpoint.generate_presentation_handler(
                request=request,
                presentation_id=presentation_id,
                async_status=None,
                sql_session=fake_async_session,
            )
        )

    assert response.path.endswith(".pptx")
    assert response.edit_path == f"/presentation?id={presentation_id}"
    assert len(fake_async_session.added_all) == 2
    assert all(slide.presentation == presentation_id for slide in fake_async_session.added_all)


def test_prepare_presentation_preserves_payload_icon_weight():
    presentation_id = uuid.uuid4()
    user = UserModel(username="user")
    presentation = PresentationModel(
        id=presentation_id,
        content="deck",
        n_slides=1,
        language="English",
        tone="default",
        verbosity="standard",
        instructions=None,
        owner_user_id=user.id,
    )
    request = Mock()
    request.state.auth_actor = AuthActor(user=user, team=None)
    session = FakeAsyncSession(get_results={presentation_id: presentation})
    layout = PresentationLayoutModel(
        name="swift",
        ordered=False,
        icon_weight="thin",
        slides=[
            SlideLayoutModel(
                id="swift:feature",
                name="Feature",
                description="Feature slide",
                json_schema={"title": "Feature"},
            )
        ],
    )

    with patch.object(
        presentation_endpoint,
        "generate_presentation_structure",
        new=AsyncMock(return_value=PresentationStructureModel(slides=[0])),
    ), patch.object(
        presentation_endpoint.MEM0_PRESENTATION_MEMORY_SERVICE,
        "store_generated_outlines",
        new=AsyncMock(),
    ):
            response = _run(
                presentation_endpoint.prepare_presentation(
                    request_http=request,
                    presentation_id=presentation_id,
                    outlines=[SlideOutlineModel(content="## Causes")],
                    layout=layout,
                sql_session=session,
            )
        )

    assert response.layout["icon_weight"] == "thin"
    assert response.get_layout().icon_weight == "thin"
    assert response.language == ""


def test_prepare_presentation_clears_stale_language_for_reviewed_outlines():
    presentation_id = uuid.uuid4()
    user = UserModel(username="user")
    presentation = PresentationModel(
        id=presentation_id,
        content="global warming deck",
        n_slides=1,
        language="Spanish (Español)",
        tone="default",
        verbosity="standard",
        instructions=None,
        owner_user_id=user.id,
    )
    request = Mock()
    request.state.auth_actor = AuthActor(user=user, team=None)
    session = FakeAsyncSession(get_results={presentation_id: presentation})
    layout = PresentationLayoutModel(
        name="swift",
        ordered=False,
        slides=[
            SlideLayoutModel(
                id="swift:feature",
                name="Feature",
                description="Feature slide",
                json_schema={"title": "Feature"},
            )
        ],
    )

    with patch.object(
        presentation_endpoint,
        "generate_presentation_structure",
        new=AsyncMock(return_value=PresentationStructureModel(slides=[0])),
    ), patch.object(
        presentation_endpoint.MEM0_PRESENTATION_MEMORY_SERVICE,
        "store_generated_outlines",
        new=AsyncMock(),
    ):
            response = _run(
                presentation_endpoint.prepare_presentation(
                    request_http=request,
                    presentation_id=presentation_id,
                    outlines=[SlideOutlineModel(content="## 全球变暖的原因")],
                    layout=layout,
                sql_session=session,
            )
        )

    assert response.language == ""


def test_generate_presentation_sync_rejects_invalid_slide_count(fake_async_session):
    request = GeneratePresentationRequest(
        content="deck",
        n_slides=0,
        language="English",
        export_as="pdf",
        template="general",
    )

    with pytest.raises(HTTPException) as exc:
        _run(
            presentation_endpoint.generate_presentation_sync(
                request_http=Mock(cookies={}),
                request=request,
                sql_session=fake_async_session,
            )
        )

    assert exc.value.status_code == 400
    assert "Number of slides must be greater than 0" in exc.value.detail


def test_generate_presentation_handler_rejects_invalid_llm_json(fake_async_session):
    request = GeneratePresentationRequest(
        content="Generate a small deck",
        n_slides=2,
        language="English",
        export_as="pdf",
        template="general",
    )

    async def fake_outline_stream(*_args, **_kwargs):
        yield "{invalid-json"

    with patch.object(
        presentation_endpoint.MEM0_PRESENTATION_MEMORY_SERVICE,
        "store_generation_context",
        new=AsyncMock(),
    ), patch.object(
        presentation_endpoint,
        "generate_ppt_outline",
        side_effect=fake_outline_stream,
    ), patch.object(
        presentation_endpoint.CONCURRENT_SERVICE,
        "run_task",
        new=Mock(),
    ):
        with pytest.raises(HTTPException) as exc:
            _run(
                presentation_endpoint.generate_presentation_handler(
                    request=request,
                    presentation_id=uuid.uuid4(),
                    async_status=None,
                    sql_session=fake_async_session,
                )
            )

    assert exc.value.status_code == 400
    assert "Failed to generate presentation outlines" in exc.value.detail


def test_async_background_uses_fresh_database_session(monkeypatch):
    request = GeneratePresentationRequest(
        content="Generate a small deck",
        n_slides=2,
        language="English",
        export_as="pdf",
        template="general",
    )
    presentation_id = uuid.uuid4()
    async_status = AsyncPresentationGenerationTaskModel(
        id="task-test",
        status="pending",
        message="Queued",
    )
    captured = {}

    class BackgroundSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            captured["get"] = (model, key)
            return async_status

    async def fake_handler(
        request,
        presentation_id,
        actor,
        async_status=None,
        export_cookie_header=None,
        sql_session=None,
    ):
        captured["handler"] = {
            "request": request,
            "presentation_id": presentation_id,
            "actor": actor,
            "async_status": async_status,
            "export_cookie_header": export_cookie_header,
            "sql_session": sql_session,
        }

    monkeypatch.setattr(
        presentation_endpoint,
        "async_session_maker",
        lambda: BackgroundSession(),
    )
    monkeypatch.setattr(
        presentation_endpoint,
        "generate_presentation_handler",
        fake_handler,
    )

    _run(
        presentation_endpoint._generate_presentation_async_background(
            request,
            presentation_id,
            None,
            async_status.id,
            "presenton_session=token",
        )
    )

    assert captured["get"] == (
        AsyncPresentationGenerationTaskModel,
        async_status.id,
    )
    assert captured["handler"]["sql_session"].__class__ is BackgroundSession
    assert captured["handler"]["async_status"] is async_status
    assert captured["handler"]["export_cookie_header"] == "presenton_session=token"
