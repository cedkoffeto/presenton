import asyncio
from datetime import datetime
import json
import logging
import os
import random
import traceback
from typing import Annotated, Any, List, Literal, Optional, Tuple
import dirtyjson
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from constants.presentation import DEFAULT_TEMPLATES, MAX_NUMBER_OF_SLIDES
from enums.webhook_event import WebhookEvent
from models.api_error_model import APIErrorModel
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationPathAndEditPath
from models.presentation_from_template import EditPresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from enums.tone import Tone
from enums.verbosity import Verbosity
from models.presentation_structure_model import PresentationStructureModel
from models.presentation_with_slides import (
    PresentationWithSlides,
)
from models.sql.template import TemplateModel
from services.documents_loader import DocumentsLoader
from services.temp_file_service import TEMP_FILE_SERVICE
from services.webhook_service import WebhookService
from services.image_generation_service import ImageGenerationService
from services.mem0_presentation_memory_service import (
    MEM0_PRESENTATION_MEMORY_SERVICE,
)
from utils.dict_utils import deep_update
from utils.export_utils import export_presentation
from utils.access_control import (
    ALLOWED_VISIBILITIES,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    VISIBILITY_TEAM,
    apply_actor_ownership,
    generate_share_token,
    require_resource_access,
)
from utils.llm_calls.generate_presentation_outlines import (
    generate_ppt_outline,
    get_messages as get_outline_messages,
)
from models.sql.slide import SlideModel
from models.sql.presentation_layout_code import PresentationLayoutCodeModel
from models.sse_response import SSECompleteResponse, SSEErrorResponse, SSEResponse

from services.database import async_session_maker, get_async_session
from services.concurrent_service import CONCURRENT_SERVICE
from models.sql.presentation import PresentationModel
from models.sql.theme import ThemeModel
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from utils.asset_directory_utils import get_images_directory, normalize_slide_asset_url
from utils.llm_calls.generate_presentation_structure import (
    generate_presentation_structure,
)
from utils.llm_calls.generate_slide_content import (
    get_slide_content_from_type_and_outline,
)
from utils.ppt_utils import (
    select_toc_or_list_slide_layout_index,
)
from utils.outline_utils import (
    get_images_for_slides_from_outline,
    get_no_of_outlines_to_generate_for_n_slides,
    get_no_of_toc_required_for_n_outlines,
    get_presentation_outline_model_with_toc,
    get_presentation_title_from_presentation_outline,
)
from utils.process_slides import (
    process_slide_add_placeholder_assets,
    process_slide_and_fetch_assets,
)
from utils.get_layout_by_name import get_layout_by_name
from utils.llm_utils import message_content_to_text
from utils.sse import safe_sse_stream
from utils.simple_auth import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_session_token_from_request,
    validate_session_token,
)
from utils.web_search import get_selected_web_search_provider, get_web_search_route
from models.presentation_layout import PresentationLayoutModel
import uuid
from utils.request_auth import get_request_actor

logger = logging.getLogger(__name__)


PRESENTATION_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])


class UpdatePresentationAccessRequest(BaseModel):
    visibility: str


def _log_generation_stage(
    stage: str,
    *,
    presentation_id: uuid.UUID,
    template: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    logger.info(
        "[presentation.generate] stage=%s presentation_id=%s%s%s",
        stage,
        presentation_id,
        f" template={template}" if template else "",
        f" details={details}" if details else "",
    )


def _extract_custom_template_id(layout_name: Optional[str]) -> Optional[uuid.UUID]:
    if not layout_name or not layout_name.startswith("custom-"):
        return None
    try:
        return uuid.UUID(layout_name.replace("custom-", ""))
    except Exception:
        return None


async def _resolve_presentation_fonts(
    presentation: PresentationModel,
    slides: List[SlideModel],
    sql_session: AsyncSession,
):
    candidate_template_ids: List[uuid.UUID] = []
    seen = set()

    layout_name = None
    if isinstance(presentation.layout, dict):
        layout_name = presentation.layout.get("name")
    layout_template_id = _extract_custom_template_id(layout_name)
    if layout_template_id and layout_template_id not in seen:
        candidate_template_ids.append(layout_template_id)
        seen.add(layout_template_id)

    for slide in slides:
        template_id = _extract_custom_template_id(slide.layout_group)
        if template_id and template_id not in seen:
            candidate_template_ids.append(template_id)
            seen.add(template_id)

    for template_id in candidate_template_ids:
        result = await sql_session.execute(
            select(PresentationLayoutCodeModel.fonts).where(
                PresentationLayoutCodeModel.presentation == template_id
            )
        )
        fonts_list = result.scalars().all()
        for fonts in fonts_list:
            if fonts is not None:
                return fonts

    return None


def _insert_toc_layouts(
    structure: PresentationStructureModel,
    n_toc_slides: int,
    include_title_slide: bool,
    toc_slide_layout_index: int,
):
    if n_toc_slides <= 0 or toc_slide_layout_index == -1:
        return

    insertion_index = 1 if include_title_slide else 0
    for i in range(n_toc_slides):
        structure.slides.insert(insertion_index + i, toc_slide_layout_index)


def _build_export_cookie_header(request: Request) -> Optional[str]:
    cookie_header = (request.headers.get("cookie") or "").strip()
    if cookie_header:
        return cookie_header

    session_token = get_session_token_from_request(request)
    if session_token and validate_session_token(session_token):
        return f"{SESSION_COOKIE_NAME}={session_token}"

    username = getattr(request.state, "auth_username", None)
    if isinstance(username, str) and username.strip():
        try:
            session_token = create_session_token(username.strip())
            return f"{SESSION_COOKIE_NAME}={session_token}"
        except Exception:
            logger.exception(
                "[presentation.generate] failed to create export session token"
            )

    return None


def _build_public_share_path(presentation: PresentationModel) -> Optional[str]:
    if presentation.visibility != "public" or not presentation.share_token:
        return None
    return f"/presentation?id={presentation.id}&share_token={presentation.share_token}"


def _presentation_response(
    presentation: PresentationModel,
    slides: List[SlideModel],
    fonts: Optional[Any],
    actor=None,
) -> PresentationWithSlides:
    return PresentationWithSlides(
        **presentation.model_dump(),
        slides=slides,
        fonts=fonts,
        can_edit_access=bool(actor and presentation.owner_user_id == actor.owner_user_id),
        public_share_url=_build_public_share_path(presentation),
    )


def _require_presentation_owner(actor, presentation: PresentationModel) -> None:
    if not presentation.owner_user_id or presentation.owner_user_id != actor.owner_user_id:
        raise HTTPException(status_code=404, detail="Presentation not found")


def _validate_visibility(visibility: str) -> str:
    normalized = (visibility or "").strip().lower()
    if normalized not in ALLOWED_VISIBILITIES:
        raise HTTPException(status_code=400, detail="Invalid visibility")
    return normalized


def _theme_payload_from_model(theme: ThemeModel) -> dict[str, Any]:
    return {
        "id": str(theme.id),
        "name": theme.name,
        "description": theme.description,
        "logo": theme.logo,
        "logo_url": normalize_slide_asset_url(theme.logo_url) if theme.logo_url else None,
        "company_name": theme.company_name,
        "data": theme.data or {},
        "visibility": theme.visibility,
        "team_id": str(theme.team_id) if theme.team_id else None,
    }


async def _resolve_request_theme(
    request: GeneratePresentationRequest,
    actor,
    sql_session: AsyncSession,
) -> Optional[dict[str, Any]]:
    selectors = [
        request.theme is not None,
        bool((request.theme_id or "").strip()),
        bool((request.theme_name or "").strip()),
    ]
    if sum(1 for selected in selectors if selected) > 1:
        raise HTTPException(
            status_code=400,
            detail="Use only one of theme, theme_id, or theme_name",
        )

    if request.theme is not None:
        return request.theme

    theme_id = (request.theme_id or "").strip()
    theme_name = (request.theme_name or "").strip()
    if not theme_id and not theme_name:
        return None
    if actor is None:
        raise HTTPException(status_code=401, detail="Authentication required for saved themes")

    theme = None
    if theme_id:
        try:
            theme_uuid = uuid.UUID(theme_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="theme_id must be a valid UUID")
        theme = await sql_session.get(ThemeModel, theme_uuid)
    else:
        theme = await sql_session.scalar(
            select(ThemeModel)
            .where(
                ThemeModel.name == theme_name,
                or_(
                    ThemeModel.owner_user_id == actor.owner_user_id,
                    ThemeModel.visibility == VISIBILITY_PUBLIC,
                    (
                        (ThemeModel.visibility == VISIBILITY_TEAM)
                        & (ThemeModel.team_id == actor.team_id)
                    ),
                ),
            )
            .order_by(ThemeModel.created_at.desc())
        )

    if theme is not None:
        require_resource_access(
            actor,
            owner_user_id=theme.owner_user_id,
            team_id=theme.team_id,
            visibility=theme.visibility,
        )

    if theme is None:
        raise HTTPException(status_code=404, detail="Theme not found")

    return _theme_payload_from_model(theme)


def _optional_request_actor(request: Optional[Request]):
    if request is None:
        return None
    state = getattr(request, "state", None)
    return getattr(state, "auth_actor", None)


def _extract_slide_asset_urls(value: Any) -> dict[str, list[str]]:
    assets: dict[str, list[str]] = {
        "images": [],
        "icons": [],
        "media": [],
    }

    def add(kind: str, raw_url: Any) -> None:
        if not isinstance(raw_url, str) or not raw_url.strip():
            return
        url = normalize_slide_asset_url(raw_url)
        if url not in assets[kind]:
            assets[kind].append(url)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                normalized_key = str(key).lower()
                if normalized_key == "__image_url__":
                    add("images", child)
                elif normalized_key == "__icon_url__":
                    add("icons", child)
                elif normalized_key.endswith("_url") or normalized_key.endswith("url"):
                    add("media", child)
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return assets


@PRESENTATION_ROUTER.get("/all", response_model=List[PresentationWithSlides])
async def get_all_presentations(
    request: Request, sql_session: AsyncSession = Depends(get_async_session)
):
    actor = get_request_actor(request)
    query = (
        select(PresentationModel, SlideModel)
        .join(
            SlideModel,
            (SlideModel.presentation == PresentationModel.id) & (SlideModel.index == 0),
        )
        .where(
            (PresentationModel.owner_user_id == actor.owner_user_id)
            | (
                (PresentationModel.visibility == "team")
                & (PresentationModel.team_id == actor.team_id)
            )
            | (PresentationModel.visibility == "public")
        )
        .order_by(PresentationModel.created_at.desc())
    )

    results = await sql_session.execute(query)
    rows = results.all()
    presentations_with_slides = []
    for presentation, first_slide in rows:
        slides = [first_slide]
        fonts = await _resolve_presentation_fonts(presentation, slides, sql_session)
        presentations_with_slides.append(
            _presentation_response(
                presentation,
                slides,
                fonts,
                actor,
            )
        )
    return presentations_with_slides


@PRESENTATION_ROUTER.patch("/{id}/access", response_model=PresentationWithSlides)
async def update_presentation_access(
    id: uuid.UUID,
    request_http: Request,
    request: UpdatePresentationAccessRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request_http)
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    _require_presentation_owner(actor, presentation)

    visibility = _validate_visibility(request.visibility)
    presentation.visibility = visibility
    if visibility == VISIBILITY_TEAM:
        presentation.team_id = actor.team_id
    presentation.share_token = (
        generate_share_token() if visibility == VISIBILITY_PUBLIC else None
    )

    await sql_session.commit()
    await sql_session.refresh(presentation)

    slides_result = await sql_session.scalars(
        select(SlideModel)
        .where(SlideModel.presentation == id)
        .order_by(SlideModel.index)
    )
    slides = list(slides_result)
    fonts = await _resolve_presentation_fonts(presentation, slides, sql_session)
    return _presentation_response(presentation, slides, fonts, actor)


@PRESENTATION_ROUTER.get("/{id}", response_model=PresentationWithSlides)
async def get_presentation(
    id: uuid.UUID,
    request: Request,
    share_token: Optional[str] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(404, "Presentation not found")
    if (
        share_token
        and presentation.share_token == share_token
        and presentation.visibility == "public"
    ):
        actor = None
    else:
        actor = get_request_actor(request)
        require_resource_access(
            actor,
            owner_user_id=presentation.owner_user_id,
            team_id=presentation.team_id,
            visibility=presentation.visibility,
        )
    slides_result = await sql_session.scalars(
        select(SlideModel)
        .where(SlideModel.presentation == id)
        .order_by(SlideModel.index)
    )
    slides = list(slides_result)
    fonts = await _resolve_presentation_fonts(presentation, slides, sql_session)
    return _presentation_response(presentation, slides, fonts, actor)


@PRESENTATION_ROUTER.get("/{id}/manifest")
async def get_presentation_manifest(
    id: uuid.UUID,
    request: Request,
    share_token: Optional[str] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(404, "Presentation not found")
    if share_token and presentation.share_token == share_token and presentation.visibility == "public":
        actor = None
    else:
        actor = get_request_actor(request)
        require_resource_access(
            actor,
            owner_user_id=presentation.owner_user_id,
            team_id=presentation.team_id,
            visibility=presentation.visibility,
        )

    slides_result = await sql_session.scalars(
        select(SlideModel)
        .where(SlideModel.presentation == id)
        .order_by(SlideModel.index)
    )
    slides = list(slides_result)
    fonts = await _resolve_presentation_fonts(presentation, slides, sql_session)

    return {
        "id": str(presentation.id),
        "title": presentation.title,
        "content": presentation.content,
        "n_slides": presentation.n_slides,
        "language": presentation.language,
        "created_at": presentation.created_at.isoformat(),
        "updated_at": presentation.updated_at.isoformat(),
        "tone": presentation.tone,
        "verbosity": presentation.verbosity,
        "theme": presentation.theme,
        "fonts": fonts,
        "layout": presentation.layout,
        "structure": presentation.structure,
        "outlines": presentation.outlines,
        "slides": [
            {
                "id": str(slide.id),
                "index": slide.index,
                "layout_group": slide.layout_group,
                "layout": slide.layout,
                "content": slide.content,
                "html_content": slide.html_content,
                "speaker_note": slide.speaker_note,
                "properties": slide.properties,
                "assets": _extract_slide_asset_urls(slide.content),
            }
            for slide in slides
        ],
        "render": {
            "format": "presenton-react",
            "width": 1280,
            "height": 720,
            "preview_path": f"/presentation?id={presentation.id}",
            "export_preview_path": f"/pdf-maker?id={presentation.id}",
        },
    }


@PRESENTATION_ROUTER.delete("/{id}", status_code=204)
async def delete_presentation(
    id: uuid.UUID,
    request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request)
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(404, "Presentation not found")
    require_resource_access(
        actor,
        owner_user_id=presentation.owner_user_id,
        team_id=presentation.team_id,
        visibility=presentation.visibility,
    )

    await sql_session.delete(presentation)
    await sql_session.commit()


@PRESENTATION_ROUTER.post("/create", response_model=PresentationModel)
async def create_presentation(
    request_http: Request,
    content: Annotated[str, Body()],
    n_slides: Annotated[Optional[int], Body()] = None,
    language: Annotated[Optional[str], Body()] = None,
    file_paths: Annotated[Optional[List[str]], Body()] = None,
    tone: Annotated[Tone, Body()] = Tone.DEFAULT,
    verbosity: Annotated[Verbosity, Body()] = Verbosity.STANDARD,
    instructions: Annotated[Optional[str], Body()] = None,
    include_table_of_contents: Annotated[bool, Body()] = False,
    include_title_slide: Annotated[bool, Body()] = True,
    web_search: Annotated[bool, Body()] = False,
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = _optional_request_actor(request_http) or get_request_actor(request_http)

    if n_slides is not None and n_slides < 1:
        raise HTTPException(
            status_code=400,
            detail="Number of slides must be greater than 0",
        )

    if n_slides is not None and n_slides > MAX_NUMBER_OF_SLIDES:
        raise HTTPException(
            status_code=400,
            detail=f"Number of slides cannot be greater than {MAX_NUMBER_OF_SLIDES}",
        )

    if include_table_of_contents and n_slides is not None and n_slides < 3:
        raise HTTPException(
            status_code=400,
            detail="Number of slides cannot be less than 3 if table of contents is included",
    )

    presentation_id = uuid.uuid4()
    language_to_store = (language or "").strip()
    validated_file_paths = (
        TEMP_FILE_SERVICE.resolve_existing_temp_paths(file_paths)
        if file_paths
        else None
    )
    # DB schema stores an int; 0 is used as internal marker for auto slide count.
    n_slides_to_store = n_slides if n_slides is not None else 0

    presentation = PresentationModel(
        id=presentation_id,
        content=content,
        n_slides=n_slides_to_store,
        language=language_to_store,
        file_paths=validated_file_paths,
        tone=tone.value,
        verbosity=verbosity.value,
        instructions=instructions,
        include_table_of_contents=include_table_of_contents,
        include_title_slide=include_title_slide,
        web_search=web_search,
        share_token=generate_share_token(),
    )
    apply_actor_ownership(presentation, actor, VISIBILITY_PRIVATE)

    sql_session.add(presentation)
    await sql_session.commit()

    search_route, actual_search_provider = get_web_search_route()
    logger.info(
        "Created presentation: id=%s web_search_enabled=%s selected_web_search_provider=%s "
        "web_search_route=%s actual_web_search_provider=%s",
        presentation_id,
        web_search,
        get_selected_web_search_provider().value,
        search_route,
        (
            actual_search_provider.value
            if actual_search_provider
            else ("model-native" if search_route == "native" else "none")
        ),
    )

    return presentation


@PRESENTATION_ROUTER.post("/prepare", response_model=PresentationModel)
async def prepare_presentation(
    request_http: Request,
    presentation_id: Annotated[uuid.UUID, Body()],
    outlines: Annotated[List[SlideOutlineModel], Body()],
    layout: Annotated[PresentationLayoutModel, Body()],
    title: Annotated[Optional[str], Body()] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = _optional_request_actor(request_http) or get_request_actor(request_http)
    if not outlines:
        raise HTTPException(status_code=400, detail="Outlines are required")

    presentation = await sql_session.get(PresentationModel, presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    require_resource_access(
        actor,
        owner_user_id=presentation.owner_user_id,
        team_id=presentation.team_id,
        visibility=presentation.visibility,
    )

    presentation_outline_model = PresentationOutlineModel(slides=outlines)

    total_slide_layouts = len(layout.slides)
    total_outlines = len(outlines)

    if layout.ordered:
        presentation_structure = layout.to_presentation_structure()
    else:
        presentation_structure: PresentationStructureModel = (
            await generate_presentation_structure(
                presentation_outline=presentation_outline_model,
                presentation_layout=layout,
                instructions=presentation.instructions,
            )
        )

    presentation_structure.slides = presentation_structure.slides[: len(outlines)]
    for index in range(total_outlines):
        random_slide_index = random.randint(0, total_slide_layouts - 1)
        if index >= total_outlines:
            presentation_structure.slides.append(random_slide_index)
            continue
        if presentation_structure.slides[index] >= total_slide_layouts:
            presentation_structure.slides[index] = random_slide_index

    if presentation.include_table_of_contents:
        n_toc_slides = get_no_of_toc_required_for_n_outlines(
            n_outlines=total_outlines,
            title_slide=presentation.include_title_slide,
            target_total_slides=(presentation.n_slides if presentation.n_slides > 0 else None),
        )
        toc_slide_layout_index = select_toc_or_list_slide_layout_index(layout)
        _insert_toc_layouts(
            presentation_structure,
            n_toc_slides,
            presentation.include_title_slide,
            toc_slide_layout_index,
        )
        if toc_slide_layout_index != -1 and n_toc_slides > 0:
            presentation_outline_model = get_presentation_outline_model_with_toc(
                outline=presentation_outline_model,
                n_toc_slides=n_toc_slides,
                title_slide=presentation.include_title_slide,
            )

    sql_session.add(presentation)
    presentation.outlines = presentation_outline_model.model_dump(mode="json")
    presentation.title = title or presentation.title
    # Final slide generation should follow the reviewed outline text. The
    # original upload language can be stale after outline-page chat edits such
    # as "convert these to Chinese".
    presentation.language = ""
    presentation.set_layout(layout)
    presentation.set_structure(presentation_structure)
    await sql_session.commit()

    await MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines(
        presentation.id,
        presentation.outlines,
    )

    return presentation


@PRESENTATION_ROUTER.get("/stream/{id}", response_model=PresentationWithSlides)
async def stream_presentation(
    id: uuid.UUID,
    request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    actor = get_request_actor(request)
    require_resource_access(
        actor,
        owner_user_id=presentation.owner_user_id,
        team_id=presentation.team_id,
        visibility=presentation.visibility,
    )
    if not presentation.structure:
        raise HTTPException(
            status_code=400,
            detail="Presentation not prepared for stream",
        )
    if not presentation.outlines:
        raise HTTPException(
            status_code=400,
            detail="Outlines can not be empty",
        )

    try:
        structure = presentation.get_structure()
        layout = presentation.get_layout()
        outline = presentation.get_presentation_outline()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Presentation has invalid generated data",
        ) from exc

    if not layout.slides:
        raise HTTPException(status_code=400, detail="Presentation layout has no slides")
    if len(structure.slides) > len(outline.slides):
        raise HTTPException(
            status_code=400,
            detail="Presentation structure has more slides than outlines",
        )
    invalid_layout_index = next(
        (
            slide_layout_index
            for slide_layout_index in structure.slides
            if slide_layout_index < 0 or slide_layout_index >= len(layout.slides)
        ),
        None,
    )
    if invalid_layout_index is not None:
        raise HTTPException(
            status_code=400,
            detail="Presentation structure contains an invalid slide layout",
        )

    image_generation_service = ImageGenerationService(get_images_directory())

    async def inner():
        icon_weight = layout.icon_weight
        image_urls_for_slides = get_images_for_slides_from_outline(outline.slides)

        async_assets_generation_tasks: List[asyncio.Task] = []
        asset_events: asyncio.Queue = asyncio.Queue()
        asset_warnings_by_slide: dict[int, list[dict]] = {}

        async def notify_slide_assets_ready(slide_index: int, asset_task: asyncio.Task):
            try:
                await asset_task
            except Exception:
                logger.exception(
                    "Slide asset generation failed: presentation_id=%s slide_index=%s",
                    id,
                    slide_index,
                )
                asset_warnings_by_slide.setdefault(slide_index, []).append(
                    {
                        "type": "asset_generation_failed",
                        "message": "Some slide assets could not be generated.",
                    }
                )
            finally:
                await asset_events.put(slide_index)

        slides: List[SlideModel] = []
        yield SSEResponse(
            event="response",
            data=json.dumps({"type": "chunk", "chunk": '{ "slides": [ '}),
        ).to_string()
        yielded_slide_asset_sse_count = 0

        for i, slide_layout_index in enumerate(structure.slides):
            slide_layout = layout.slides[slide_layout_index]

            try:
                slide_content = await get_slide_content_from_type_and_outline(
                    slide_layout,
                    outline.slides[i],
                    presentation.language,
                    presentation.tone,
                    presentation.verbosity,
                    presentation.instructions,
                )
            except HTTPException as e:
                yield SSEErrorResponse(detail=e.detail).to_string()
                return

            slide = SlideModel(
                presentation=id,
                layout_group=layout.name,
                layout=slide_layout.id,
                index=i,
                speaker_note=slide_content.get("__speaker_note__", ""),
                content=slide_content,
            )
            slides.append(slide)

            # This will mutate slide and add placeholder assets
            process_slide_add_placeholder_assets(slide)

            # This will mutate slide - start task immediately so it runs in parallel with next slide LLM generation
            asset_warnings_by_slide[i] = []
            asset_task = asyncio.create_task(
                process_slide_and_fetch_assets(
                    image_generation_service,
                    slide,
                    outline_image_urls=(
                        image_urls_for_slides[i]
                        if i < len(image_urls_for_slides)
                        else None
                    ),
                    icon_weight=icon_weight,
                    allow_image_fallback=True,
                    image_warnings=asset_warnings_by_slide[i],
                )
            )
            async_assets_generation_tasks.append(asset_task)
            asyncio.create_task(notify_slide_assets_ready(i, asset_task))

            yield SSEResponse(
                event="response",
                data=json.dumps({"type": "chunk", "chunk": slide.model_dump_json()}),
            ).to_string()

            while True:
                try:
                    done_idx = asset_events.get_nowait()
                except asyncio.QueueEmpty:
                    break
                yielded_slide_asset_sse_count += 1
                yield SSEResponse(
                    event="response",
                    data=json.dumps(
                        {
                            "type": "slide_assets",
                            "slide_index": done_idx,
                            "slide": slides[done_idx].model_dump(mode="json"),
                            "warnings": asset_warnings_by_slide.get(done_idx, []),
                        }
                    ),
                ).to_string()

        yield SSEResponse(
            event="response",
            data=json.dumps({"type": "chunk", "chunk": " ] }"}),
        ).to_string()

        while yielded_slide_asset_sse_count < len(slides):
            done_idx = await asset_events.get()
            yielded_slide_asset_sse_count += 1
            yield SSEResponse(
                event="response",
                data=json.dumps(
                    {
                        "type": "slide_assets",
                        "slide_index": done_idx,
                        "slide": slides[done_idx].model_dump(mode="json"),
                        "warnings": asset_warnings_by_slide.get(done_idx, []),
                    }
                ),
            ).to_string()

        generated_assets_lists = await asyncio.gather(
            *async_assets_generation_tasks,
            return_exceptions=True,
        )
        generated_assets = []
        for assets_list in generated_assets_lists:
            if isinstance(assets_list, Exception):
                logger.error(
                    "Slide asset generation failed during final collection: %s",
                    assets_list,
                )
                continue
            generated_assets.extend(assets_list)
        if actor is not None:
            for asset in generated_assets:
                apply_actor_ownership(asset, actor, presentation.visibility)

        # Moved this here to make sure new slides are generated before deleting the old ones
        await sql_session.execute(
            delete(SlideModel).where(SlideModel.presentation == id)
        )
        await sql_session.commit()

        sql_session.add(presentation)
        sql_session.add_all(slides)
        sql_session.add_all(generated_assets)
        await sql_session.commit()

        response = _presentation_response(
            presentation,
            slides,
            await _resolve_presentation_fonts(presentation, slides, sql_session),
            actor,
        )

        yield SSECompleteResponse(
            key="presentation",
            value=response.model_dump(mode="json"),
        ).to_string()

    async def rollback_stream_session():
        await sql_session.rollback()

    return StreamingResponse(
        safe_sse_stream(
            inner(),
            logger=logger,
            error_detail="Failed to generate presentation slides. Please try again.",
            on_error=rollback_stream_session,
        ),
        media_type="text/event-stream",
    )


@PRESENTATION_ROUTER.patch("/update", response_model=PresentationWithSlides)
async def update_presentation(
    request: Request,
    id: Annotated[uuid.UUID, Body()],
    n_slides: Annotated[Optional[int], Body()] = None,
    title: Annotated[Optional[str], Body()] = None,
    theme: Annotated[Optional[dict], Body()] = None,
    slides: Annotated[Optional[List[SlideModel]], Body()] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    actor = get_request_actor(request)
    require_resource_access(
        actor,
        owner_user_id=presentation.owner_user_id,
        team_id=presentation.team_id,
        visibility=presentation.visibility,
    )

    presentation_update_dict = {}
    if n_slides is not None:
        presentation_update_dict["n_slides"] = n_slides
    if title:
        presentation_update_dict["title"] = title
    if theme or theme is None:
        presentation_update_dict["theme"] = theme

    if presentation_update_dict:
        presentation.sqlmodel_update(presentation_update_dict)
    if slides:
        # Just to make sure id is UUID
        for slide in slides:
            slide.presentation = uuid.UUID(slide.presentation)
            slide.id = uuid.UUID(slide.id)

        await sql_session.execute(
            delete(SlideModel).where(SlideModel.presentation == presentation.id)
        )
        sql_session.add_all(slides)

    await sql_session.commit()

    response_slides = slides or []
    fonts = await _resolve_presentation_fonts(
        presentation,
        response_slides,
        sql_session,
    )

    return _presentation_response(presentation, response_slides, fonts, actor)
async def check_if_api_request_is_valid(
    request: GeneratePresentationRequest,
    actor=None,
    sql_session: AsyncSession = Depends(get_async_session),
) -> Tuple[uuid.UUID,]:
    presentation_id = uuid.uuid4()
    print(f"Presentation ID: {presentation_id}")

    # Making sure either content, slides markdown or files is provided
    if not (request.content or request.slides_markdown or request.files):
        raise HTTPException(
            status_code=400,
            detail="Either content or slides markdown or files is required to generate presentation",
        )

    if request.n_slides is not None and request.n_slides <= 0:
        raise HTTPException(
            status_code=400,
            detail="Number of slides must be greater than 0",
        )

    if request.n_slides is not None and request.n_slides > MAX_NUMBER_OF_SLIDES:
        raise HTTPException(
            status_code=400,
            detail=f"Number of slides cannot be greater than {MAX_NUMBER_OF_SLIDES}",
        )

    if (
        request.include_table_of_contents
        and request.n_slides is not None
        and request.n_slides < 3
    ):
        raise HTTPException(
            status_code=400,
            detail="Number of slides cannot be less than 3 if table of contents is included",
        )

    # Checking if template is valid
    if request.template not in DEFAULT_TEMPLATES:
        request.template = request.template.lower()
        if not request.template.startswith("custom-"):
            raise HTTPException(
                status_code=400,
                detail="Template not found. Please use a valid template.",
            )
        template_id = request.template.replace("custom-", "")
        try:
            template = await sql_session.get(TemplateModel, uuid.UUID(template_id))
            if not template:
                raise Exception()
            if actor is not None:
                require_resource_access(
                    actor,
                    owner_user_id=template.owner_user_id,
                    team_id=template.team_id,
                    visibility=template.visibility,
                )
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Template not found. Please use a valid template.",
            )

    await _resolve_request_theme(request, actor, sql_session)

    return (presentation_id,)


async def generate_presentation_handler(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    actor=None,
    async_status: Optional[AsyncPresentationGenerationTaskModel] = None,
    export_cookie_header: Optional[str] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    try:
        resolved_theme = await _resolve_request_theme(request, actor, sql_session)
        _log_generation_stage(
            "start",
            presentation_id=presentation_id,
            template=request.template,
            details=f"export_as={request.export_as}",
        )
        using_slides_markdown = False
        language_to_use = (request.language or "").strip() or None
        additional_context = ""

        if request.slides_markdown:
            using_slides_markdown = True
            request.n_slides = len(request.slides_markdown)

        if not using_slides_markdown:
            # Updating async status
            if async_status:
                async_status.message = "Generating presentation outlines"
                async_status.updated_at = datetime.now()
                sql_session.add(async_status)
                await sql_session.commit()
            _log_generation_stage(
                "outlines_start",
                presentation_id=presentation_id,
                template=request.template,
            )

            if request.files:
                documents_loader = DocumentsLoader(
                    file_paths=request.files,
                    presentation_language=request.language,
                )
                await documents_loader.load_documents()
                documents = documents_loader.documents
                if documents:
                    additional_context = "\n\n".join(documents)

            # Finding number of slides to generate by considering table of contents
            n_slides_to_generate = request.n_slides
            if request.include_table_of_contents and request.n_slides is not None:
                n_slides_to_generate = (
                    get_no_of_outlines_to_generate_for_n_slides(
                        n_slides=request.n_slides,
                        toc=True,
                        title_slide=request.include_title_slide,
                    )
                )

            outline_messages = get_outline_messages(
                request.content,
                n_slides_to_generate,
                language_to_use,
                additional_context,
                request.tone.value,
                request.verbosity.value,
                request.instructions,
                request.include_title_slide,
                request.include_table_of_contents,
            )
            await MEM0_PRESENTATION_MEMORY_SERVICE.store_generation_context(
                presentation_id=presentation_id,
                system_prompt=(
                    message_content_to_text(outline_messages[0].content)
                    if len(outline_messages) > 0
                    else None
                ),
                user_prompt=(
                    message_content_to_text(outline_messages[1].content)
                    if len(outline_messages) > 1
                    else None
                ),
                extracted_document_text=additional_context,
                source_content=request.content,
                instructions=request.instructions,
            )

            presentation_outlines_text = ""
            async for chunk in generate_ppt_outline(
                request.content,
                n_slides_to_generate,
                language_to_use,
                additional_context,
                request.tone.value,
                request.verbosity.value,
                request.instructions,
                request.include_title_slide,
                request.web_search,
                request.include_table_of_contents,
            ):

                if isinstance(chunk, HTTPException):
                    raise chunk

                presentation_outlines_text += chunk

            try:
                presentation_outlines_json = dict(
                    dirtyjson.loads(presentation_outlines_text)
                )
            except Exception:
                traceback.print_exc()
                raise HTTPException(
                    status_code=400,
                    detail="Failed to generate presentation outlines. Please try again.",
                )
            presentation_outlines = PresentationOutlineModel(
                **presentation_outlines_json
            )

            if (
                n_slides_to_generate is not None
                and len(presentation_outlines.slides) != n_slides_to_generate
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Failed to generate presentation outlines with requested "
                        "number of slides. Please try again."
                    ),
                )

            total_outlines = len(presentation_outlines.slides)
            _log_generation_stage(
                "outlines_ready",
                presentation_id=presentation_id,
                template=request.template,
                details=f"count={total_outlines}",
            )

        else:
            # Setting outlines to slides markdown
            presentation_outlines = PresentationOutlineModel(
                slides=[
                    SlideOutlineModel(content=slide)
                    for slide in request.slides_markdown
                ]
            )
            total_outlines = len(request.slides_markdown)
            _log_generation_stage(
                "slides_markdown_ready",
                presentation_id=presentation_id,
                template=request.template,
                details=f"count={total_outlines}",
            )

            await MEM0_PRESENTATION_MEMORY_SERVICE.store_generation_context(
                presentation_id=presentation_id,
                system_prompt=None,
                user_prompt=None,
                extracted_document_text=None,
                source_content=request.content,
                instructions=request.instructions,
            )

        await MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines(
            presentation_id,
            presentation_outlines.model_dump(mode="json"),
        )

        # Updating async status
        if async_status:
            async_status.message = "Selecting layout for each slide"
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
            await sql_session.commit()
        _log_generation_stage(
            "layout_start",
            presentation_id=presentation_id,
            template=request.template,
            details=f"outlines={total_outlines}",
        )

        print("-" * 40)
        print(f"Generated {total_outlines} outlines for the presentation")

        layout_model = await get_layout_by_name(request.template)
        _log_generation_stage(
            "layout_ready",
            presentation_id=presentation_id,
            template=request.template,
            details=(
                f"slides={len(layout_model.slides)} "
                f"ordered={layout_model.ordered} "
                f"icon_weight={layout_model.icon_weight}"
            ),
        )
        total_slide_layouts = len(layout_model.slides)

        # Generate Structure
        if layout_model.ordered:
            presentation_structure = layout_model.to_presentation_structure()
        else:
            if async_status:
                async_status.message = "Choosing slide layouts"
                async_status.updated_at = datetime.now()
                sql_session.add(async_status)
                await sql_session.commit()
            _log_generation_stage(
                "structure_start",
                presentation_id=presentation_id,
                template=request.template,
            )
            presentation_structure: PresentationStructureModel = (
                await generate_presentation_structure(
                    presentation_outlines,
                    layout_model,
                    request.instructions,
                    using_slides_markdown,
                )
            )
            _log_generation_stage(
                "structure_ready",
                presentation_id=presentation_id,
                template=request.template,
                details=f"slides={len(presentation_structure.slides)}",
            )

        presentation_structure.slides = presentation_structure.slides[:total_outlines]
        for index in range(total_outlines):
            random_slide_index = random.randint(0, total_slide_layouts - 1)
            if index >= total_outlines:
                presentation_structure.slides.append(random_slide_index)
                continue
            if presentation_structure.slides[index] >= total_slide_layouts:
                presentation_structure.slides[index] = random_slide_index

        should_include_toc = (
            request.include_table_of_contents and not using_slides_markdown
        )
        if should_include_toc:
            n_toc_slides = get_no_of_toc_required_for_n_outlines(
                n_outlines=total_outlines,
                title_slide=request.include_title_slide,
                target_total_slides=request.n_slides,
            )
            toc_slide_layout_index = select_toc_or_list_slide_layout_index(layout_model)
            _insert_toc_layouts(
                presentation_structure,
                n_toc_slides,
                request.include_title_slide,
                toc_slide_layout_index,
            )
            if toc_slide_layout_index != -1 and n_toc_slides > 0:
                presentation_outlines = get_presentation_outline_model_with_toc(
                    outline=presentation_outlines,
                    n_toc_slides=n_toc_slides,
                    title_slide=request.include_title_slide,
                )

        final_n_slides = request.n_slides
        if final_n_slides is None:
            final_n_slides = len(presentation_outlines.slides)

        # Create PresentationModel
        presentation = PresentationModel(
            id=presentation_id,
            content=request.content,
            n_slides=final_n_slides,
            language=language_to_use or "",
            title=get_presentation_title_from_presentation_outline(
                presentation_outlines
            ),
            outlines=presentation_outlines.model_dump(),
            layout=layout_model.model_dump(),
            structure=presentation_structure.model_dump(),
            tone=request.tone.value,
            verbosity=request.verbosity.value,
            instructions=request.instructions,
            theme=resolved_theme,
            share_token=generate_share_token(),
        )
        if actor is not None:
            apply_actor_ownership(presentation, actor, VISIBILITY_PRIVATE)

        # Updating async status
        if async_status:
            async_status.message = "Generating slides"
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
            await sql_session.commit()

        image_generation_service = ImageGenerationService(get_images_directory())
        async_assets_generation_tasks = []

        # 7. Generate slide content concurrently (batched), then build slides and fetch assets
        slides: List[SlideModel] = []

        slide_layout_indices = presentation_structure.slides
        slide_layouts = [layout_model.slides[idx] for idx in slide_layout_indices]
        _log_generation_stage(
            "slides_start",
            presentation_id=presentation_id,
            template=request.template,
            details=f"slides={len(slide_layouts)}",
        )

        # Schedule slide content generation and asset fetching in batches of 10
        batch_size = 10
        for start in range(0, len(slide_layouts), batch_size):
            end = min(start + batch_size, len(slide_layouts))

            print(f"Generating slides from {start} to {end}")

            # Generate contents for this batch concurrently
            content_tasks = [
                get_slide_content_from_type_and_outline(
                    slide_layouts[i],
                    presentation_outlines.slides[i],
                    language_to_use,
                    request.tone.value,
                    request.verbosity.value,
                    request.instructions,
                )
                for i in range(start, end)
            ]
            batch_contents: List[dict] = await asyncio.gather(*content_tasks)

            # Build slides for this batch
            batch_slides: List[SlideModel] = []
            for offset, slide_content in enumerate(batch_contents):
                i = start + offset
                slide_layout = slide_layouts[i]
                slide = SlideModel(
                    presentation=presentation_id,
                    layout_group=layout_model.name,
                    layout=slide_layout.id,
                    index=i,
                    speaker_note=slide_content.get("__speaker_note__"),
                    content=slide_content,
                )
                slides.append(slide)
                batch_slides.append(slide)

            if using_slides_markdown:
                image_urls_for_batch = get_images_for_slides_from_outline(
                    presentation_outlines.slides[start:end]
                )
            else:
                image_urls_for_batch = [[] for _ in batch_slides]

            # Start asset fetch tasks immediately so they run in parallel with next batch's LLM calls
            asset_tasks = [
                asyncio.create_task(
                    process_slide_and_fetch_assets(
                        image_generation_service,
                        slide,
                        outline_image_urls=image_urls_for_batch[offset],
                        icon_weight=layout_model.icon_weight,
                    )
                )
                for offset, slide in enumerate(batch_slides)
            ]
            async_assets_generation_tasks.extend(asset_tasks)

        if async_status:
            async_status.message = "Fetching assets for slides"
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
            await sql_session.commit()
        _log_generation_stage(
            "assets_start",
            presentation_id=presentation_id,
            template=request.template,
        )

        # Run all asset tasks concurrently while batches may still be generating content
        generated_assets_list = await asyncio.gather(*async_assets_generation_tasks)
        generated_assets = []
        for assets_list in generated_assets_list:
            generated_assets.extend(assets_list)
        if actor is not None:
            for asset in generated_assets:
                apply_actor_ownership(asset, actor, presentation.visibility)
        _log_generation_stage(
            "assets_ready",
            presentation_id=presentation_id,
            template=request.template,
            details=f"count={len(generated_assets)}",
        )

        # 8. Save PresentationModel and Slides
        sql_session.add(presentation)
        sql_session.add_all(slides)
        sql_session.add_all(generated_assets)
        await sql_session.commit()

        if async_status:
            async_status.message = "Exporting presentation"
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
        _log_generation_stage(
            "export_start",
            presentation_id=presentation_id,
            template=request.template,
            details=f"format={request.export_as}",
        )

        # 9. Export
        presentation_and_path = await export_presentation(
            presentation_id,
            presentation.title or str(uuid.uuid4()),
            request.export_as,
            cookie_header=export_cookie_header,
        )

        response = PresentationPathAndEditPath(
            **presentation_and_path.model_dump(),
            edit_path=f"/presentation?id={presentation_id}",
            download_url=presentation_and_path.path,
            public_share_url=_build_public_share_path(presentation),
        )

        if async_status:
            async_status.message = "Presentation generation completed"
            async_status.status = "completed"
            async_status.data = response.model_dump(mode="json")
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
            await sql_session.commit()
        _log_generation_stage(
            "complete",
            presentation_id=presentation_id,
            template=request.template,
            details=f"path={response.path}",
        )

        # Triggering webhook on success
        CONCURRENT_SERVICE.run_task(
            None,
            WebhookService.send_webhook,
            WebhookEvent.PRESENTATION_GENERATION_COMPLETED,
            response.model_dump(mode="json"),
        )

        return response

    except Exception as e:
        _log_generation_stage(
            "failed",
            presentation_id=presentation_id,
            template=request.template,
            details=repr(e),
        )
        if not isinstance(e, HTTPException):
            traceback.print_exc()
            e = HTTPException(status_code=500, detail="Presentation generation failed")

        api_error_model = APIErrorModel.from_exception(e)

        # Triggering webhook on failure
        CONCURRENT_SERVICE.run_task(
            None,
            WebhookService.send_webhook,
            WebhookEvent.PRESENTATION_GENERATION_FAILED,
            api_error_model.model_dump(mode="json"),
        )

        if async_status:
            async_status.status = "error"
            async_status.message = "Presentation generation failed"
            async_status.updated_at = datetime.now()
            async_status.error = api_error_model.model_dump(mode="json")
            sql_session.add(async_status)
            await sql_session.commit()

        else:
            raise e


async def _generate_presentation_async_background(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    actor,
    async_status_id: str,
    export_cookie_header: Optional[str],
):
    async with async_session_maker() as background_session:
        async_status = await background_session.get(
            AsyncPresentationGenerationTaskModel,
            async_status_id,
        )
        if async_status is None:
            logger.error(
                "[presentation.generate] async status missing task_id=%s presentation_id=%s",
                async_status_id,
                presentation_id,
            )
            return

        await generate_presentation_handler(
            request,
            presentation_id,
            actor,
            async_status=async_status,
            export_cookie_header=export_cookie_header,
            sql_session=background_session,
        )


@PRESENTATION_ROUTER.post("/generate", response_model=PresentationPathAndEditPath)
async def generate_presentation_sync(
    request_http: Request,
    request: GeneratePresentationRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    try:
        actor = _optional_request_actor(request_http)
        (presentation_id,) = await check_if_api_request_is_valid(request, actor, sql_session)
        return await generate_presentation_handler(
            request,
            presentation_id,
            actor,
            None,
            export_cookie_header=_build_export_cookie_header(request_http),
            sql_session=sql_session,
        )
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Presentation generation failed")


@PRESENTATION_ROUTER.post(
    "/generate/async", response_model=AsyncPresentationGenerationTaskModel
)
async def generate_presentation_async(
    request_http: Request,
    request: GeneratePresentationRequest,
    background_tasks: BackgroundTasks,
    sql_session: AsyncSession = Depends(get_async_session),
):
    try:
        actor = _optional_request_actor(request_http)
        (presentation_id,) = await check_if_api_request_is_valid(request, actor, sql_session)

        async_status = AsyncPresentationGenerationTaskModel(
            status="pending",
            message="Queued for generation",
            data=None,
        )
        if actor is not None:
            apply_actor_ownership(async_status, actor, VISIBILITY_PRIVATE)
        sql_session.add(async_status)
        await sql_session.commit()

        background_tasks.add_task(
            _generate_presentation_async_background,
            request,
            presentation_id,
            actor,
            async_status.id,
            _build_export_cookie_header(request_http),
        )
        return async_status

    except Exception as e:
        if not isinstance(e, HTTPException):
            print(e)
            e = HTTPException(status_code=500, detail="Presentation generation failed")

        raise e


@PRESENTATION_ROUTER.get(
    "/status/{id}", response_model=AsyncPresentationGenerationTaskModel
)
async def check_async_presentation_generation_status(
    request: Request,
    id: str = Path(description="ID of the presentation generation task"),
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request)
    status = await sql_session.get(AsyncPresentationGenerationTaskModel, id)
    if not status:
        raise HTTPException(
            status_code=404, detail="No presentation generation task found"
        )
    require_resource_access(
        actor,
        owner_user_id=status.owner_user_id,
        team_id=status.team_id,
        visibility=status.visibility,
    )
    return status


@PRESENTATION_ROUTER.post("/edit", response_model=PresentationPathAndEditPath)
async def edit_presentation_with_new_content(
    request_http: Request,
    data: Annotated[EditPresentationRequest, Body()],
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request_http)
    return await edit_presentation_handler(
        request_http,
        data,
        actor,
        sql_session,
    )


async def edit_presentation_handler(
    request_http: Request,
    data: EditPresentationRequest,
    actor,
    sql_session: AsyncSession,
    async_status: Optional[AsyncPresentationGenerationTaskModel] = None,
) -> PresentationPathAndEditPath:
    presentation = await sql_session.get(PresentationModel, data.presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    require_resource_access(
        actor,
        owner_user_id=presentation.owner_user_id,
        team_id=presentation.team_id,
        visibility=presentation.visibility,
    )

    if async_status:
        async_status.status = "processing"
        async_status.message = "Applying slide edits"
        async_status.updated_at = datetime.now()
        sql_session.add(async_status)
        await sql_session.commit()

    slides = await sql_session.scalars(
        select(SlideModel).where(SlideModel.presentation == data.presentation_id)
    )

    new_slides = []
    slides_to_delete = []
    for each_slide in slides:
        updated_content = None
        new_slide_data = list(
            filter(lambda x: x.index == each_slide.index, data.slides)
        )
        if new_slide_data:
            updated_content = deep_update(each_slide.content, new_slide_data[0].content)
            new_slides.append(
                each_slide.get_new_slide(presentation.id, updated_content)
            )
            slides_to_delete.append(each_slide.id)

    await sql_session.execute(
        delete(SlideModel).where(SlideModel.id.in_(slides_to_delete))
    )

    sql_session.add_all(new_slides)
    await sql_session.commit()

    if async_status:
        async_status.message = "Exporting updated presentation"
        async_status.updated_at = datetime.now()
        sql_session.add(async_status)
        await sql_session.commit()

    presentation_and_path = await export_presentation(
        presentation.id,
        presentation.title or str(uuid.uuid4()),
        data.export_as,
        cookie_header=_build_export_cookie_header(request_http),
    )

    return PresentationPathAndEditPath(
        **presentation_and_path.model_dump(),
        edit_path=f"/presentation?id={presentation.id}",
        download_url=presentation_and_path.path,
        public_share_url=_build_public_share_path(presentation),
    )


async def _edit_presentation_async_background(
    data: EditPresentationRequest,
    actor,
    async_status_id: str,
    export_cookie_header: Optional[str],
):
    async with async_session_maker() as background_session:
        async_status = await background_session.get(
            AsyncPresentationGenerationTaskModel,
            async_status_id,
        )
        if async_status is None:
            logger.error(
                "[presentation.edit] async status missing task_id=%s presentation_id=%s",
                async_status_id,
                data.presentation_id,
            )
            return

        request_http = type(
            "EditAsyncRequest",
            (),
            {"headers": {"cookie": export_cookie_header or ""}, "state": type("State", (), {})()},
        )()
        try:
            response = await edit_presentation_handler(
                request_http,
                data,
                actor,
                background_session,
                async_status=async_status,
            )
            async_status.status = "completed"
            async_status.message = "Presentation edit completed"
            async_status.updated_at = datetime.now()
            async_status.data = response.model_dump(mode="json")
            background_session.add(async_status)
            await background_session.commit()
        except Exception as exc:
            if not isinstance(exc, HTTPException):
                traceback.print_exc()
                exc = HTTPException(status_code=500, detail="Presentation edit failed")
            async_status.status = "error"
            async_status.message = "Presentation edit failed"
            async_status.updated_at = datetime.now()
            async_status.error = APIErrorModel.from_exception(exc).model_dump(mode="json")
            background_session.add(async_status)
            await background_session.commit()


@PRESENTATION_ROUTER.post(
    "/edit/async", response_model=AsyncPresentationGenerationTaskModel
)
async def edit_presentation_with_new_content_async(
    request_http: Request,
    data: Annotated[EditPresentationRequest, Body()],
    background_tasks: BackgroundTasks,
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request_http)
    presentation = await sql_session.get(PresentationModel, data.presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    require_resource_access(
        actor,
        owner_user_id=presentation.owner_user_id,
        team_id=presentation.team_id,
        visibility=presentation.visibility,
    )

    async_status = AsyncPresentationGenerationTaskModel(
        status="pending",
        message="Queued for presentation edit",
        data=None,
    )
    apply_actor_ownership(async_status, actor, VISIBILITY_PRIVATE)
    sql_session.add(async_status)
    await sql_session.commit()

    background_tasks.add_task(
        _edit_presentation_async_background,
        data,
        actor,
        async_status.id,
        _build_export_cookie_header(request_http),
    )
    return async_status


@PRESENTATION_ROUTER.post("/derive", response_model=PresentationPathAndEditPath)
async def derive_presentation_from_existing_one(
    request_http: Request,
    data: Annotated[EditPresentationRequest, Body()],
    sql_session: AsyncSession = Depends(get_async_session),
):
    actor = get_request_actor(request_http)
    presentation = await sql_session.get(PresentationModel, data.presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    require_resource_access(
        actor,
        owner_user_id=presentation.owner_user_id,
        team_id=presentation.team_id,
        visibility=presentation.visibility,
    )

    slides = await sql_session.scalars(
        select(SlideModel).where(SlideModel.presentation == data.presentation_id)
    )

    new_presentation = presentation.get_new_presentation()
    new_presentation.share_token = generate_share_token()
    apply_actor_ownership(new_presentation, actor, VISIBILITY_PRIVATE)
    new_slides = []
    for each_slide in slides:
        updated_content = None
        new_slide_data = list(
            filter(lambda x: x.index == each_slide.index, data.slides)
        )
        if new_slide_data:
            updated_content = deep_update(each_slide.content, new_slide_data[0].content)
        new_slides.append(
            each_slide.get_new_slide(new_presentation.id, updated_content)
        )

    sql_session.add(new_presentation)
    sql_session.add_all(new_slides)
    await sql_session.commit()

    presentation_and_path = await export_presentation(
        new_presentation.id,
        new_presentation.title or str(uuid.uuid4()),
        data.export_as,
        cookie_header=_build_export_cookie_header(request_http),
    )

    return PresentationPathAndEditPath(
        **presentation_and_path.model_dump(),
        edit_path=f"/presentation?id={new_presentation.id}",
        download_url=presentation_and_path.path,
        public_share_url=_build_public_share_path(new_presentation),
    )
