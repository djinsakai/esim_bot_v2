from aiogram import Bot
from sqlalchemy import select

from app.db.models import ProblematicSip, DashboardState
from app.db.database import async_session_maker
from app.utils.config import config


async def update_pinned_dashboard(bot: Bot):
    chat_id_str = config.test_slot_notify_chat_id
    if not chat_id_str:
        return

    chat_id = int(chat_id_str)

    async with async_session_maker() as session:
        result = await session.execute(
            select(ProblematicSip).order_by(ProblematicSip.created_at)
        )
        sips = result.scalars().all()

    if not sips:
        text = "🟢 Проблемных сипов нет."
    else:
        instant_sips = [s for s in sips if s.status == "instant"]
        block_sips = [s for s in sips if s.status != "instant"]
        lines = ["🚨 Проблемные сипы (Live):"]
        for s in instant_sips:
            lines.append(f"- {s.sip_number} (Instant)")
        for s in block_sips:
            lines.append(f"- {s.sip_number}")
        text = "\n".join(lines)

    async with async_session_maker() as session:
        result = await session.execute(
            select(DashboardState).where(DashboardState.id == 1)
        )
        state = result.scalar_one_or_none()

        if state and state.pinned_message_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state.pinned_message_id,
                    text=text
                )
                return
            except Exception:
                pass

        msg = await bot.send_message(chat_id=chat_id, text=text)
        try:
            await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
        except Exception:
            pass

        if state:
            state.pinned_message_id = msg.message_id
            state.chat_id = chat_id
        else:
            session.add(DashboardState(
                id=1,
                chat_id=chat_id,
                pinned_message_id=msg.message_id
            ))
        await session.commit()
