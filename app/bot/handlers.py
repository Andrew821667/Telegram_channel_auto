"""
Telegram Bot Handlers
Обработчики команд и модерация драфтов.
"""

import asyncio
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import (
    PostDraft, Publication, RawArticle,
    FeedbackLabel, get_db
)
from app.bot.keyboards import (
    get_draft_review_keyboard,
    get_confirm_keyboard,
    get_reader_keyboard,
    get_main_menu_keyboard,
    get_rejection_reasons_keyboard
)
import structlog

logger = structlog.get_logger()

# Инициализация бота
bot = Bot(token=settings.telegram_bot_token)
dp = Dispatcher()
router = Router()


# FSM States для редактирования
class EditDraft(StatesGroup):
    waiting_for_edit = State()


# ====================
# Middleware для проверки прав
# ====================

async def check_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь администратором."""
    return user_id == settings.telegram_admin_id


# ====================
# Команды
# ====================

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start."""
    if not await check_admin(message.from_user.id):
        await message.answer("⛔️ У вас нет прав доступа к этому боту.")
        return

    await message.answer(
        "👋 Добро пожаловать в AI-News Aggregator!\n\n"
        "Этот бот помогает модерировать новости о внедрении ИИ в юриспруденцию.\n\n"
        "Доступные команды:\n"
        "/drafts - показать новые драфты\n"
        "/stats - показать статистику\n"
        "/help - помощь",
        reply_markup=get_main_menu_keyboard()
    )


@router.message(Command("drafts"))
async def cmd_drafts(message: Message, db: AsyncSession = None):
    """Показать новые драфты для модерации."""
    if not await check_admin(message.from_user.id):
        return

    if db is None:
        async for session in get_db():
            db = session
            break

    # Получаем драфты в статусе pending_review
    result = await db.execute(
        select(PostDraft)
        .where(PostDraft.status == 'pending_review')
        .order_by(PostDraft.created_at.desc())
    )
    drafts = list(result.scalars().all())

    if not drafts:
        await message.answer("📭 Нет новых драфтов для модерации.")
        return

    await message.answer(f"📝 Найдено {len(drafts)} драфтов. Отправляю...")

    # Отправляем каждый драфт
    for draft in drafts[:5]:  # Ограничиваем 5 драфтами за раз
        await send_draft_for_review(message.chat.id, draft, db)


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: AsyncSession = None):
    """Показать статистику."""
    if not await check_admin(message.from_user.id):
        return

    if db is None:
        async for session in get_db():
            db = session
            break

    # Собираем статистику
    stats_text = await get_statistics(db)
    await message.answer(stats_text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Показать помощь."""
    if not await check_admin(message.from_user.id):
        return

    help_text = """
📚 <b>Помощь по боту</b>

<b>Команды:</b>
/start - Главное меню
/drafts - Показать новые драфты
/stats - Статистика системы
/help - Эта справка

<b>Модерация драфтов:</b>
✅ Опубликовать - опубликовать пост в канал
✏️ Редактировать - редактировать текст поста
❌ Отклонить - отклонить драфт

<b>Workflow:</b>
1. Система автоматически собирает новости (09:00 MSK)
2. AI анализирует и генерирует драфты
3. Вы получаете уведомление о новых драфтах
4. Вы модерируете каждый драфт
5. Одобренные посты публикуются в канал

⚠️ <b>Важно:</b> Все драфты требуют модерации перед публикацией!
"""
    await message.answer(help_text, parse_mode="HTML")


# ====================
# Callback обработчики
# ====================

@router.callback_query(F.data.startswith("publish:"))
async def callback_publish(callback: CallbackQuery, db: AsyncSession = None):
    """Обработчик кнопки публикации."""
    if not await check_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав доступа", show_alert=True)
        return

    draft_id = int(callback.data.split(":")[1])

    if db is None:
        async for session in get_db():
            db = session
            break

    # Запрашиваем подтверждение
    await callback.message.edit_reply_markup(
        reply_markup=get_confirm_keyboard("publish", draft_id)
    )
    await callback.answer("Подтвердите публикацию")


@router.callback_query(F.data.startswith("confirm_publish:"))
async def callback_confirm_publish(callback: CallbackQuery, db: AsyncSession = None):
    """Подтверждение публикации."""
    if not await check_admin(callback.from_user.id):
        return

    draft_id = int(callback.data.split(":")[1])

    if db is None:
        async for session in get_db():
            db = session
            break

    # Публикуем пост
    success = await publish_draft(draft_id, db, callback.from_user.id)

    if success:
        await callback.message.edit_text(
            f"✅ Драфт #{draft_id} успешно опубликован!"
        )
        await callback.answer("Опубликовано!")
    else:
        await callback.message.edit_text(
            f"❌ Ошибка при публикации драфта #{draft_id}"
        )
        await callback.answer("Ошибка!", show_alert=True)


@router.callback_query(F.data.startswith("reject:"))
async def callback_reject(callback: CallbackQuery):
    """Обработчик кнопки отклонения."""
    if not await check_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав доступа", show_alert=True)
        return

    draft_id = int(callback.data.split(":")[1])

    # Показываем причины отклонения
    await callback.message.edit_reply_markup(
        reply_markup=get_rejection_reasons_keyboard(draft_id)
    )
    await callback.answer("Выберите причину отклонения")


@router.callback_query(F.data.startswith("reject_reason:"))
async def callback_reject_reason(callback: CallbackQuery, db: AsyncSession = None):
    """Обработка выбора причины отклонения."""
    if not await check_admin(callback.from_user.id):
        return

    parts = callback.data.split(":")
    draft_id = int(parts[1])
    reason = parts[2]

    if db is None:
        async for session in get_db():
            db = session
            break

    # Отклоняем драфт
    success = await reject_draft(draft_id, reason, db, callback.from_user.id)

    if success:
        await callback.message.edit_text(
            f"❌ Драфт #{draft_id} отклонен\nПричина: {reason}"
        )
        await callback.answer("Отклонено")
    else:
        await callback.answer("Ошибка!", show_alert=True)


@router.callback_query(F.data.startswith("edit:"))
async def callback_edit(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки редактирования."""
    if not await check_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав доступа", show_alert=True)
        return

    draft_id = int(callback.data.split(":")[1])

    await state.update_data(draft_id=draft_id)
    await state.set_state(EditDraft.waiting_for_edit)

    await callback.message.answer(
        "✏️ Отправьте новый текст для поста.\n"
        "Используйте Markdown разметку.\n\n"
        "Отправьте /cancel для отмены."
    )
    await callback.answer()


@router.message(EditDraft.waiting_for_edit, Command("cancel"))
async def cancel_edit(message: Message, state: FSMContext):
    """Отмена редактирования."""
    await state.clear()
    await message.answer("❌ Редактирование отменено.")


@router.message(EditDraft.waiting_for_edit)
async def process_edit(message: Message, state: FSMContext, db: AsyncSession = None):
    """Обработка отредактированного текста."""
    data = await state.get_data()
    draft_id = data.get("draft_id")

    if db is None:
        async for session in get_db():
            db = session
            break

    # Обновляем драфт
    result = await db.execute(
        select(PostDraft).where(PostDraft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if draft:
        draft.content = message.text
        draft.status = 'edited'
        await db.commit()

        await message.answer(f"✅ Драфт #{draft_id} обновлен!")
        # Отправляем обновленный драфт на проверку
        await send_draft_for_review(message.chat.id, draft, db)
    else:
        await message.answer(f"❌ Драфт #{draft_id} не найден")

    await state.clear()


# ====================
# Утилитарные функции
# ====================

async def send_draft_for_review(chat_id: int, draft: PostDraft, db: AsyncSession):
    """
    Отправить драфт администратору на модерацию.

    Args:
        chat_id: ID чата для отправки
        draft: Драфт поста
        db: Сессия БД
    """
    try:
        # Получаем информацию об оригинальной статье
        result = await db.execute(
            select(RawArticle).where(RawArticle.id == draft.article_id)
        )
        article = result.scalar_one_or_none()

        # Формируем сообщение
        preview_text = f"""
🆕 <b>Новый драфт #{draft.id}</b>

{draft.content}

━━━━━━━━━━━━━━━━
📊 Confidence: {draft.confidence_score:.2f}
🔗 Источник: {article.source_name if article else 'Unknown'}
⏰ Создан: {draft.created_at.strftime('%d.%m.%Y %H:%M')}
"""

        # Отправляем с изображением если есть
        if draft.image_path:
            photo = FSInputFile(draft.image_path)
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=preview_text[:1024],  # Telegram limit
                reply_markup=get_draft_review_keyboard(draft.id),
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=preview_text,
                reply_markup=get_draft_review_keyboard(draft.id),
                parse_mode="HTML"
            )

        logger.info("draft_sent_for_review", draft_id=draft.id)

    except Exception as e:
        logger.error("draft_send_error", draft_id=draft.id, error=str(e))


async def publish_draft(draft_id: int, db: AsyncSession, admin_id: int) -> bool:
    """
    Опубликовать драфт в канал.

    Args:
        draft_id: ID драфта
        db: Сессия БД
        admin_id: ID администратора

    Returns:
        True если успешно, False иначе
    """
    try:
        # Получаем драфт
        result = await db.execute(
            select(PostDraft).where(PostDraft.id == draft_id)
        )
        draft = result.scalar_one_or_none()

        if not draft:
            return False

        # Получаем оригинальную статью для ссылки
        result = await db.execute(
            select(RawArticle).where(RawArticle.id == draft.article_id)
        )
        article = result.scalar_one_or_none()

        # Формируем финальный текст с ссылкой
        final_text = draft.content

        if article:
            final_text += f"\n\n🔗 <a href='{article.url}'>Источник</a>"

        # Публикуем в канал
        if draft.image_path:
            photo = FSInputFile(draft.image_path)
            message = await bot.send_photo(
                chat_id=settings.telegram_channel_id,
                photo=photo,
                caption=final_text,
                parse_mode="HTML",
                reply_markup=get_reader_keyboard(article.url) if article else None
            )
        else:
            message = await bot.send_message(
                chat_id=settings.telegram_channel_id,
                text=final_text,
                parse_mode="HTML",
                reply_markup=get_reader_keyboard(article.url) if article else None
            )

        # Сохраняем публикацию в БД
        publication = Publication(
            draft_id=draft.id,
            message_id=message.message_id,
            channel_id=settings.telegram_channel_id_numeric,
        )
        db.add(publication)

        # Обновляем статус драфта
        draft.status = 'approved'
        draft.reviewed_at = datetime.utcnow()
        draft.reviewed_by = admin_id

        # Сохраняем feedback
        feedback = FeedbackLabel(
            draft_id=draft.id,
            admin_action='published'
        )
        db.add(feedback)

        await db.commit()

        logger.info(
            "draft_published",
            draft_id=draft.id,
            message_id=message.message_id
        )

        return True

    except Exception as e:
        logger.error("publish_error", draft_id=draft_id, error=str(e))
        return False


async def reject_draft(
    draft_id: int,
    reason: str,
    db: AsyncSession,
    admin_id: int
) -> bool:
    """
    Отклонить драфт.

    Args:
        draft_id: ID драфта
        reason: Причина отклонения
        db: Сессия БД
        admin_id: ID администратора

    Returns:
        True если успешно, False иначе
    """
    try:
        result = await db.execute(
            select(PostDraft).where(PostDraft.id == draft_id)
        )
        draft = result.scalar_one_or_none()

        if not draft:
            return False

        draft.status = 'rejected'
        draft.rejection_reason = reason
        draft.reviewed_at = datetime.utcnow()
        draft.reviewed_by = admin_id

        # Сохраняем feedback
        feedback = FeedbackLabel(
            draft_id=draft.id,
            admin_action='rejected',
            rejection_reason=reason
        )
        db.add(feedback)

        await db.commit()

        logger.info("draft_rejected", draft_id=draft.id, reason=reason)

        return True

    except Exception as e:
        logger.error("reject_error", draft_id=draft_id, error=str(e))
        return False


async def get_statistics(db: AsyncSession) -> str:
    """Получить статистику системы."""
    from sqlalchemy import func

    # Количество статей
    articles_count = await db.scalar(select(func.count(RawArticle.id)))

    # Количество драфтов
    drafts_count = await db.scalar(select(func.count(PostDraft.id)))

    # Количество публикаций
    publications_count = await db.scalar(select(func.count(Publication.id)))

    # Драфты в ожидании
    pending_count = await db.scalar(
        select(func.count(PostDraft.id)).where(PostDraft.status == 'pending_review')
    )

    stats_text = f"""
📊 <b>Статистика системы</b>

📰 Всего статей: {articles_count}
📝 Всего драфтов: {drafts_count}
✅ Опубликовано: {publications_count}
⏳ Ожидают модерации: {pending_count}

📅 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}
"""

    return stats_text


# ====================
# Запуск бота
# ====================

async def start_bot():
    """Запустить бота."""
    dp.include_router(router)

    logger.info("bot_starting")

    # Удаляем вебхуки если есть
    await bot.delete_webhook(drop_pending_updates=True)

    # Запускаем polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(start_bot())
