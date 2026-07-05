from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.image_asset import ImageAsset
from models.sql.integration_client import IntegrationClientModel
from models.sql.presentation import PresentationModel
from models.sql.presentation_layout_code import PresentationLayoutCodeModel
from models.sql.template import TemplateModel
from models.sql.template_create_info import TemplateCreateInfoModel
from utils.access_control import (
    bootstrap_default_identity,
)
from utils.simple_auth import get_configured_auth_username


async def bootstrap_identity_data(sql_session: AsyncSession) -> None:
    username = get_configured_auth_username() or "admin"
    actor = await bootstrap_default_identity(sql_session, username)

    await _backfill_null_ownership(sql_session, actor)


async def _backfill_null_ownership(sql_session: AsyncSession, actor) -> None:
    updates = (
        (PresentationModel, PresentationModel.owner_user_id),
        (TemplateModel, TemplateModel.owner_user_id),
        (ImageAsset, ImageAsset.owner_user_id),
        (TemplateCreateInfoModel, TemplateCreateInfoModel.owner_user_id),
        (AsyncPresentationGenerationTaskModel, AsyncPresentationGenerationTaskModel.owner_user_id),
        (PresentationLayoutCodeModel, PresentationLayoutCodeModel.owner_user_id),
    )
    for model, owner_column in updates:
        existing = await sql_session.scalar(select(model).where(owner_column.is_(None)))
        if existing is None:
            continue
        await sql_session.execute(
            update(model)
            .where(owner_column.is_(None))
            .values(
                owner_user_id=actor.owner_user_id,
                team_id=actor.team_id,
            )
        )
    await sql_session.commit()
