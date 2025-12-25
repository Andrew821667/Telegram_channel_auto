"""
Telegram Bot Handlers
Обработчики команд и модерация драфтов.
"""

import asyncio
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile, BotCommand
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
    get_rejection_reasons_keyboard,
    get_opinion_keyboard
)
from app.bot.middleware import DbSessionMiddleware
import structlog

logger = structlog.get_logger()

# Глобальные переменные (Bot создается лениво чтобы избежать создания aiohttp клиента при импорте)
_bot: Optional[Bot] = None
dp = Dispatcher()
router = Router()


def get_bot() -> Bot:
    """
    Получить экземпляр бота (ленивая инициализация).

    Bot создается только при первом вызове, чтобы избежать создания aiohttp клиента
    при импорте модуля (важно для Celery worker).
    """
    global _bot
    if _bot is None:
        _bot = Bot(token=settings.telegram_bot_token)
    return _bot


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
async def cmd_drafts(message: Message, db: AsyncSession):
    """Показать новые драфты для модерации."""
    if not await check_admin(message.from_user.id):
        return

    # Получаем драфты в статусе pending_review, созданные СЕГОДНЯ
    from datetime import date
    today_start = datetime.combine(date.today(), datetime.min.time())

    result = await db.execute(
        select(PostDraft)
        .where(
            PostDraft.status == 'pending_review',
            PostDraft.created_at >= today_start
        )
        .order_by(PostDraft.created_at.desc())
    )
    drafts = list(result.scalars().all())

    if not drafts:
        await message.answer("📭 Нет новых драфтов для модерации.")
        return

    await message.answer(f"📝 Найдено {len(drafts)} драфтов. Отправляю...")

    # Отправляем каждый драфт (ограничиваем настройкой publisher_max_posts_per_day)
    max_drafts = min(len(drafts), settings.publisher_max_posts_per_day)
    for index, draft in enumerate(drafts[:max_drafts], start=1):
        await send_draft_for_review(message.chat.id, draft, db, draft_number=index)


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: AsyncSession):
    """Показать статистику."""
    if not await check_admin(message.from_user.id):
        return

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
/fetch - Запустить сбор новостей вручную
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


@router.message(Command("fetch"))
async def cmd_fetch(message: Message):
    """Запустить сбор новостей вручную."""
    if not await check_admin(message.from_user.id):
        return

    await message.answer("🔄 Запускаю сбор новостей...")

    try:
        # Импортируем и запускаем задачу Celery
        from app.tasks.celery_tasks import manual_workflow
        task = manual_workflow.delay()

        await message.answer(
            f"✅ Задача запущена!\n"
            f"ID задачи: <code>{task.id}</code>\n\n"
            f"Процесс займет 5-10 минут.\n"
            f"Используйте /drafts чтобы проверить новые драфты.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error("fetch_error", error=str(e))
        await message.answer(f"❌ Ошибка запуска: {str(e)}")


# ====================
# Callback обработчики
# ====================

@router.callback_query(F.data.startswith("publish:"))
async def callback_publish(callback: CallbackQuery, db: AsyncSession):
    """Обработчик кнопки публикации."""
    if not await check_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав доступа", show_alert=True)
        return

    draft_id = int(callback.data.split(":")[1])

    # Запрашиваем подтверждение
    await callback.message.edit_reply_markup(
        reply_markup=get_confirm_keyboard("publish", draft_id)
    )
    await callback.answer("Подтвердите публикацию")


@router.callback_query(F.data.startswith("confirm_publish:"))
async def callback_confirm_publish(callback: CallbackQuery, db: AsyncSession):
    """Подтверждение публикации."""
    if not await check_admin(callback.from_user.id):
        return

    draft_id = int(callback.data.split(":")[1])

    # Публикуем пост
    success = await publish_draft(draft_id, db, callback.from_user.id)

    if success:
        # Проверяем тип сообщения (photo или text)
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=f"✅ Драфт #{draft_id} успешно опубликован!"
            )
        else:
            await callback.message.edit_text(
                f"✅ Драфт #{draft_id} успешно опубликован!"
            )
        await callback.answer("Опубликовано!")
    else:
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=f"❌ Ошибка при публикации драфта #{draft_id}"
            )
        else:
            await callback.message.edit_text(
                f"❌ Ошибка при публикации драфта #{draft_id}"
            )
        await callback.answer("Ошибка!", show_alert=True)


@router.callback_query(F.data.startswith("reject:"))
async def callback_reject(callback: CallbackQuery, db: AsyncSession):
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
async def callback_reject_reason(callback: CallbackQuery, db: AsyncSession):
    """Обработка выбора причины отклонения."""
    if not await check_admin(callback.from_user.id):
        return

    parts = callback.data.split(":")
    draft_id = int(parts[1])
    reason = parts[2]

    # Отклоняем драфт
    success = await reject_draft(draft_id, reason, db, callback.from_user.id)

    if success:
        # Проверяем тип сообщения (photo или text)
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=f"❌ Драфт #{draft_id} отклонен\nПричина: {reason}"
            )
        else:
            await callback.message.edit_text(
                f"❌ Драфт #{draft_id} отклонен\nПричина: {reason}"
            )
        await callback.answer("Отклонено")
    else:
        await callback.answer("Ошибка!", show_alert=True)


@router.callback_query(F.data.startswith("edit:"))
async def callback_edit(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Обработчик кнопки редактирования."""
    if not await check_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав доступа", show_alert=True)
        return

    draft_id = int(callback.data.split(":")[1])

    # Получаем текущий драфт
    result = await db.execute(
        select(PostDraft).where(PostDraft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if not draft:
        await callback.answer("❌ Драфт не найден", show_alert=True)
        return

    # Сохраняем в state
    await state.update_data(
        draft_id=draft_id,
        original_content=draft.content,
        article_id=draft.article_id
    )
    await state.set_state(EditDraft.waiting_for_edit)

    await callback.message.answer(
        f"<b>📝 Текущий драфт:</b>\n\n{draft.content}\n\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"✏️ <b>Опишите, что нужно изменить:</b>\n"
        f"Например:\n"
        f"• Сделай тон более деловым\n"
        f"• Убери упоминание о конкретной компании\n"
        f"• Добавь больше юридического контекста\n"
        f"• Сделай короче, без потери смысла\n\n"
        f"Отправьте /cancel для отмены.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(EditDraft.waiting_for_edit, Command("cancel"))
async def cancel_edit(message: Message, state: FSMContext):
    """Отмена редактирования."""
    await state.clear()
    await message.answer("❌ Редактирование отменено.")


@router.message(EditDraft.waiting_for_edit, F.voice)
async def process_voice_edit(message: Message, state: FSMContext, db: AsyncSession):
    """Обработка голосовых инструкций по редактированию."""
    await message.answer("🎤 Обрабатываю голосовое сообщение...")

    try:
        # Скачиваем голосовое сообщение
        voice_file = await get_bot().get_file(message.voice.file_id)
        voice_path = f"/tmp/voice_{message.voice.file_id}.ogg"
        await get_bot().download_file(voice_file.file_path, voice_path)

        # Транскрибируем через Whisper API
        from openai import AsyncOpenAI
        from app.config import settings

        client = AsyncOpenAI(api_key=settings.openai_api_key)

        with open(voice_path, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="ru"
            )

        edit_instructions = transcript.text

        await message.answer(
            f"✅ <b>Распознал:</b>\n<i>{edit_instructions}</i>\n\n⏳ Генерирую новый вариант...",
            parse_mode="HTML"
        )

        # Удаляем временный файл
        import os
        if os.path.exists(voice_path):
            os.remove(voice_path)

    except Exception as e:
        logger.error("voice_transcription_error", error=str(e))
        await message.answer(
            f"❌ Ошибка при распознавании голоса: {str(e)}\n\nПопробуйте отправить текстом"
        )
        return

    # Далее та же логика редактирования
    data = await state.get_data()
    draft_id = data.get("draft_id")
    original_content = data.get("original_content")
    article_id = data.get("article_id")

    try:
        # Получаем оригинальную статью
        result = await db.execute(
            select(RawArticle).where(RawArticle.id == article_id)
        )
        article = result.scalar_one_or_none()

        # Вызываем LLM для редактирования
        prompt = f"""Ты редактор контента для Telegram канала о AI в юриспруденции.

ИСХОДНЫЙ ПОСТ:
{original_content}

ОРИГИНАЛЬНАЯ СТАТЬЯ:
{article.content if article else 'Не доступна'}

ИНСТРУКЦИИ ПО РЕДАКТИРОВАНИЮ:
{edit_instructions}

Создай новую версию поста с учётом инструкций. Сохрани структуру с заголовком, основным текстом и хештегами. Формат тот же что в исходном посте."""

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты профессиональный редактор контента для Telegram канала."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=800
        )

        new_content = response.choices[0].message.content.strip()

        # Сохраняем новую версию в state
        await state.update_data(new_content=new_content)

        # Показываем новый вариант с кнопками
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Опубликовать",
                    callback_data=f"publish_edited:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="✏️ Редактировать дальше",
                    callback_data=f"continue_edit:{draft_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"cancel_edit:{draft_id}"
                )
            ]
        ])

        await message.answer(
            f"<b>📝 Новый вариант:</b>\n\n{new_content}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error("voice_edit_generation_error", error=str(e))
        await message.answer(
            f"❌ Ошибка при генерации: {str(e)}\n\nПопробуйте еще раз или отправьте /cancel"
        )


@router.message(EditDraft.waiting_for_edit)
async def process_edit(message: Message, state: FSMContext, db: AsyncSession):
    """Обработка текстовых инструкций по редактированию через LLM."""
    data = await state.get_data()
    draft_id = data.get("draft_id")
    original_content = data.get("original_content")
    article_id = data.get("article_id")
    edit_instructions = message.text

    await message.answer("⏳ Генерирую новый вариант...")

    try:
        # Получаем оригинальную статью
        result = await db.execute(
            select(RawArticle).where(RawArticle.id == article_id)
        )
        article = result.scalar_one_or_none()

        # Вызываем LLM для редактирования
        from openai import AsyncOpenAI
        from app.config import settings

        client = AsyncOpenAI(api_key=settings.openai_api_key)

        prompt = f"""Ты редактор контента для Telegram канала о AI в юриспруденции.

ИСХОДНЫЙ ПОСТ:
{original_content}

ОРИГИНАЛЬНАЯ СТАТЬЯ:
{article.content if article else 'Не доступна'}

ИНСТРУКЦИИ ПО РЕДАКТИРОВАНИЮ:
{edit_instructions}

Создай новую версию поста с учётом инструкций. Сохрани структуру с заголовком, основным текстом и хештегами. Формат тот же что в исходном посте."""

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты профессиональный редактор контента для Telegram канала."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=800
        )

        new_content = response.choices[0].message.content.strip()

        # Сохраняем новую версию в state
        await state.update_data(new_content=new_content)

        # Показываем новый вариант с кнопками
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Опубликовать",
                    callback_data=f"publish_edited:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="✏️ Редактировать дальше",
                    callback_data=f"continue_edit:{draft_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"cancel_edit:{draft_id}"
                )
            ]
        ])

        await message.answer(
            f"<b>📝 Новый вариант:</b>\n\n{new_content}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error("edit_generation_error", error=str(e))
        await message.answer(
            f"❌ Ошибка при генерации: {str(e)}\n\n"
            f"Попробуйте еще раз или отправьте /cancel"
        )


@router.callback_query(F.data.startswith("publish_edited:"))
async def callback_publish_edited(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Опубликовать отредактированную версию."""
    if not await check_admin(callback.from_user.id):
        return

    draft_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    new_content = data.get("new_content")

    # Обновляем драфт
    result = await db.execute(
        select(PostDraft).where(PostDraft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if draft and new_content:
        draft.content = new_content
        draft.status = 'edited'
        await db.commit()

        # Публикуем
        success = await publish_draft(draft_id, db, callback.from_user.id)

        if success:
            # Проверяем тип сообщения (photo или text)
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=f"✅ Отредактированный драфт #{draft_id} успешно опубликован!"
                )
            else:
                await callback.message.edit_text(
                    f"✅ Отредактированный драфт #{draft_id} успешно опубликован!"
                )
            await callback.answer("Опубликовано!")
        else:
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=f"❌ Ошибка при публикации драфта #{draft_id}"
                )
            else:
                await callback.message.edit_text(
                    f"❌ Ошибка при публикации драфта #{draft_id}"
                )
            await callback.answer("Ошибка!", show_alert=True)
    else:
        await callback.answer("❌ Ошибка: драфт не найден", show_alert=True)

    await state.clear()


@router.callback_query(F.data.startswith("continue_edit:"))
async def callback_continue_edit(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Продолжить редактирование."""
    if not await check_admin(callback.from_user.id):
        return

    draft_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    new_content = data.get("new_content")

    # Обновляем original_content на новую версию для следующей итерации
    await state.update_data(original_content=new_content)

    text = (f"<b>📝 Текущая версия:</b>\n\n{new_content}\n\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"✏️ <b>Опишите дополнительные изменения:</b>")

    # Проверяем тип сообщения (photo или text)
    if callback.message.photo:
        await callback.message.edit_caption(caption=text, parse_mode="HTML")
    else:
        await callback.message.edit_text(text, parse_mode="HTML")

    await callback.answer("Опишите дополнительные изменения")


@router.callback_query(F.data.startswith("cancel_edit:"))
async def callback_cancel_edit(callback: CallbackQuery, state: FSMContext):
    """Отменить редактирование."""
    if not await check_admin(callback.from_user.id):
        return

    await state.clear()

    # Проверяем тип сообщения (photo или text)
    if callback.message.photo:
        await callback.message.edit_caption(caption="❌ Редактирование отменено.")
    else:
        await callback.message.edit_text("❌ Редактирование отменено.")

    await callback.answer("Отменено")


# ====================
# Обработчики кнопок главного меню
# ====================

@router.callback_query(F.data == "show_drafts")
async def callback_show_drafts(callback: CallbackQuery, db: AsyncSession):
    """Показать драфты через кнопку."""
    if not await check_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав доступа", show_alert=True)
        return

    # Получаем драфты в статусе pending_review, созданные СЕГОДНЯ
    from datetime import date
    today_start = datetime.combine(date.today(), datetime.min.time())

    result = await db.execute(
        select(PostDraft)
        .where(
            PostDraft.status == 'pending_review',
            PostDraft.created_at >= today_start
        )
        .order_by(PostDraft.created_at.desc())
    )
    drafts = list(result.scalars().all())

    if not drafts:
        await callback.message.answer("📭 Нет новых драфтов для модерации.")
        await callback.answer()
        return

    await callback.message.answer(f"📝 Найдено {len(drafts)} драфтов. Отправляю...")

    # Отправляем каждый драфт (ограничиваем настройкой publisher_max_posts_per_day)
    max_drafts = min(len(drafts), settings.publisher_max_posts_per_day)
    for index, draft in enumerate(drafts[:max_drafts], start=1):
        await send_draft_for_review(callback.message.chat.id, draft, db, draft_number=index)

    await callback.answer("Драфты отправлены")


@router.callback_query(F.data == "run_fetch")
async def callback_run_fetch(callback: CallbackQuery):
    """Запустить сбор новостей через кнопку."""
    if not await check_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав доступа", show_alert=True)
        return

    await callback.message.answer("🔄 Запускаю сбор новостей...")

    try:
        from app.tasks.celery_tasks import manual_workflow
        task = manual_workflow.delay()

        await callback.message.answer(
            f"✅ Задача запущена!\n"
            f"ID задачи: <code>{task.id}</code>\n\n"
            f"Процесс займет 5-10 минут.\n"
            f"Используйте /drafts чтобы проверить новые драфты.",
            parse_mode="HTML"
        )
        await callback.answer("Сбор запущен")
    except Exception as e:
        logger.error("fetch_error", error=str(e))
        await callback.message.answer(f"❌ Ошибка запуска: {str(e)}")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data == "show_stats")
async def callback_show_stats(callback: CallbackQuery, db: AsyncSession):
    """Показать статистику через кнопку."""
    if not await check_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав доступа", show_alert=True)
        return

    stats_text = await get_statistics(db)
    await callback.message.answer(stats_text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "show_settings")
async def callback_show_settings(callback: CallbackQuery):
    """Показать настройки через кнопку."""
    if not await check_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав доступа", show_alert=True)
        return

    settings_text = """
⚙️ <b>Настройки системы</b>

📊 Сбор новостей: автоматически в 09:00 MSK
🤖 AI модель: GPT-4o-mini
📝 Макс. драфтов/день: 3
✅ Требуется модерация: Да

Для изменения настроек используйте переменные окружения в .env файле.
"""
    await callback.message.answer(settings_text, parse_mode="HTML")
    await callback.answer()


# ====================
# Утилитарные функции
# ====================

async def send_draft_for_review(chat_id: int, draft: PostDraft, db: AsyncSession, bot=None, draft_number: int = None):
    """
    Отправить драфт администратору на модерацию.

    Args:
        chat_id: ID чата для отправки
        draft: Драфт поста
        db: Сессия БД
        bot: Опциональный экземпляр Bot (для использования в Celery tasks)
        draft_number: Порядковый номер драфта за день (если None, используется draft.id)
    """
    try:
        if bot is None:
            bot = get_bot()

        # Получаем информацию об оригинальной статье
        result = await db.execute(
            select(RawArticle).where(RawArticle.id == draft.article_id)
        )
        article = result.scalar_one_or_none()

        # Используем порядковый номер или ID
        display_number = draft_number if draft_number is not None else draft.id

        # Формируем сообщение
        preview_text = f"""
🆕 <b>Новый драфт #{display_number}</b>

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

        # Формируем финальный текст с интерактивными элементами
        final_text = draft.content

        # Добавляем разделитель и источник
        if article:
            final_text += f"\n\n━━━━━━━━━━━━━━━━"

            # Источник с attribution
            source_name = article.source_name if article.source_name else "Источник"
            final_text += f"\n📰 {source_name}"

        # Публикуем в канал
        if draft.image_path:
            # Telegram ограничивает caption до 1024 символов
            caption = final_text[:1024] if len(final_text) > 1024 else final_text
            photo = FSInputFile(draft.image_path)
            message = await get_bot().send_photo(
                chat_id=settings.telegram_channel_id,
                photo=photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=get_reader_keyboard(
                    article.url,
                    post_id=draft.id
                ) if article else None
            )
        else:
            # Telegram ограничивает text до 4096 символов
            text = final_text[:4096] if len(final_text) > 4096 else final_text
            message = await get_bot().send_message(
                chat_id=settings.telegram_channel_id,
                text=text,
                parse_mode="HTML",
                reply_markup=get_reader_keyboard(
                    article.url,
                    post_id=draft.id
                ) if article else None
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


@router.callback_query(F.data.startswith("opinion:"))
async def callback_opinion(callback: CallbackQuery, db: AsyncSession):
    """
    Показать клавиатуру для выбора мнения о посте.
    """
    try:
        # Извлекаем post_id из callback_data
        post_id = int(callback.data.split(":")[1])

        # Показываем опции для выражения мнения
        await callback.answer()
        await callback.message.answer(
            "📊 Выберите ваше мнение о посте:",
            reply_markup=get_opinion_keyboard(post_id)
        )

    except Exception as e:
        logger.error("opinion_callback_error", error=str(e))
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("react:"))
async def callback_react(callback: CallbackQuery, db: AsyncSession):
    """
    Обработать реакцию пользователя на пост.
    """
    try:
        # Извлекаем данные из callback_data: react:post_id:reaction_type
        parts = callback.data.split(":")
        post_id = int(parts[1])
        reaction_type = parts[2]  # useful, important, controversial

        # Получаем публикацию
        result = await db.execute(
            select(Publication)
            .join(PostDraft)
            .where(PostDraft.id == post_id)
        )
        publication = result.scalar_one_or_none()

        if not publication:
            await callback.answer("❌ Публикация не найдена", show_alert=True)
            return

        # Получаем текущие реакции
        reactions = publication.reactions or {}

        # Увеличиваем счетчик для выбранной реакции
        reactions[reaction_type] = reactions.get(reaction_type, 0) + 1

        # Сохраняем обновленные реакции
        publication.reactions = reactions
        await db.commit()

        # Формируем ответное сообщение
        reaction_emoji = {
            "useful": "👍",
            "important": "🔥",
            "controversial": "🤔"
        }
        reaction_text = {
            "useful": "Полезно",
            "important": "Важно",
            "controversial": "Спорно"
        }

        emoji = reaction_emoji.get(reaction_type, "👍")
        text = reaction_text.get(reaction_type, "")

        await callback.answer(f"{emoji} Спасибо за ваше мнение: {text}!", show_alert=True)

        # Удаляем сообщение с кнопками
        try:
            await callback.message.delete()
        except Exception:
            pass  # Игнорируем ошибки удаления

        logger.info(
            "user_reaction_recorded",
            post_id=post_id,
            reaction_type=reaction_type,
            user_id=callback.from_user.id
        )

    except Exception as e:
        logger.error("react_callback_error", error=str(e))
        await callback.answer("❌ Произошла ошибка", show_alert=True)


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
# Настройка команд бота
# ====================

async def setup_bot_commands():
    """Установить меню команд бота (кнопка меню слева внизу)."""
    commands = [
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="drafts", description="📝 Новые драфты"),
        BotCommand(command="fetch", description="🔄 Запустить сбор новостей"),
        BotCommand(command="stats", description="📊 Статистика системы"),
        BotCommand(command="help", description="❓ Помощь"),
    ]
    await get_bot().set_my_commands(commands)
    logger.info("bot_commands_set", count=len(commands))


# ====================
# Запуск бота
# ====================

async def start_bot():
    """Запустить бота."""
    # Регистрируем middleware для БД сессий
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())

    # Регистрируем роутер
    dp.include_router(router)

    logger.info("bot_starting")

    # Удаляем вебхуки если есть
    await get_bot().delete_webhook(drop_pending_updates=True)

    # Устанавливаем меню команд
    await setup_bot_commands()

    # Запускаем polling
    await dp.start_polling(get_bot())


if __name__ == "__main__":
    asyncio.run(start_bot())
