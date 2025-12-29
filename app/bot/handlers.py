"""
Telegram Bot Handlers
Обработчики команд и модерация драфтов.
"""

import asyncio
import html
from datetime import datetime
from typing import Optional, Dict, List

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
    get_opinion_keyboard,
    get_edit_mode_keyboard,
    get_llm_selection_keyboard
)
from app.bot.middleware import DbSessionMiddleware
from app.modules.llm_provider import get_llm_provider
from app.modules.vector_search import get_vector_search
from app.modules.analytics import AnalyticsService
import structlog

logger = structlog.get_logger()

# Глобальные переменные (Bot создается лениво чтобы избежать создания aiohttp клиента при импорте)
_bot: Optional[Bot] = None
_selected_llm_provider: str = settings.default_llm_provider  # Хранение выбранного LLM провайдера
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
    waiting_for_manual_edit = State()
    waiting_for_llm_edit = State()


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

    # Получаем ВСЕ драфты в статусе pending_review (без фильтра по дате)
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
    # ВАЖНО: отвечаем сразу, чтобы кнопка не зависала
    await callback.answer("Публикую...")

    if not await check_admin(callback.from_user.id):
        logger.warning("confirm_publish_no_access", user_id=callback.from_user.id)
        return

    draft_id = int(callback.data.split(":")[1])
    logger.info("confirm_publish_start", draft_id=draft_id, user_id=callback.from_user.id)

    # Публикуем пост
    success = await publish_draft(draft_id, db, callback.from_user.id)
    logger.info("confirm_publish_result", draft_id=draft_id, success=success)

    try:
        logger.info("confirm_publish_updating_message", draft_id=draft_id, has_photo=bool(callback.message.photo))
        if success:
            # Проверяем тип сообщения (photo или text)
            if callback.message.photo:
                logger.info("confirm_publish_edit_caption", draft_id=draft_id)
                await callback.message.edit_caption(
                    caption=f"✅ Драфт #{draft_id} успешно опубликован!",
                    reply_markup=None  # Убираем кнопки
                )
            else:
                logger.info("confirm_publish_edit_text", draft_id=draft_id)
                await callback.message.edit_text(
                    text=f"✅ Драфт #{draft_id} успешно опубликован!",
                    reply_markup=None  # Убираем кнопки
                )
            logger.info("confirm_publish_message_updated", draft_id=draft_id)
        else:
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=f"❌ Ошибка при публикации драфта #{draft_id}",
                    reply_markup=None
                )
            else:
                await callback.message.edit_text(
                    text=f"❌ Ошибка при публикации драфта #{draft_id}",
                    reply_markup=None
                )
    except Exception as e:
        logger.error("callback_message_edit_error", error=str(e), draft_id=draft_id, error_type=type(e).__name__)
        # Если не получилось отредактировать, отправим новое сообщение
        status_msg = f"✅ Драфт #{draft_id} успешно опубликован!" if success else f"❌ Ошибка при публикации драфта #{draft_id}"
        await callback.message.answer(status_msg)


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
    # ВАЖНО: отвечаем сразу, чтобы кнопка не зависала
    await callback.answer("Отклоняю...")

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
    else:
        await callback.message.answer("❌ Ошибка при отклонении драфта", show_alert=True)


@router.callback_query(F.data.startswith("edit:"))
async def callback_edit(callback: CallbackQuery):
    """Обработчик кнопки редактирования - показывает выбор способа."""
    await callback.answer()

    if not await check_admin(callback.from_user.id):
        await callback.message.answer("⛔️ Нет прав доступа")
        return

    draft_id = int(callback.data.split(":")[1])

    await callback.message.answer(
        "✏️ Выберите способ редактирования драфта:",
        reply_markup=get_edit_mode_keyboard(draft_id)
    )


@router.callback_query(F.data.startswith("edit_manual:"))
async def callback_edit_manual(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Обработчик ручного редактирования."""
    await callback.answer()

    if not await check_admin(callback.from_user.id):
        await callback.message.answer("⛔️ Нет прав доступа")
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

    await state.update_data(draft_id=draft_id)
    await state.set_state(EditDraft.waiting_for_manual_edit)

    # Отправляем текущий текст отдельным сообщением для удобного копирования
    await callback.message.answer(
        "✍️ <b>ТЕКУЩИЙ ТЕКСТ ПОСТА</b>\n"
        "Скопируйте сообщение ниже ⬇️, отредактируйте и отправьте обратно:",
        parse_mode="HTML"
    )

    # Текст поста отдельным сообщением (легко копировать долгим нажатием)
    await callback.message.answer(draft.content)

    await callback.message.answer(
        "📌 Используйте HTML разметку:\n"
        "<b>жирный</b>, <i>курсив</i>, <code>код</code>\n\n"
        "Отправьте /cancel для отмены.",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("edit_llm:"))
async def callback_edit_llm(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Обработчик AI-редактирования."""
    await callback.answer()

    if not await check_admin(callback.from_user.id):
        await callback.message.answer("⛔️ Нет прав доступа")
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
    await state.set_state(EditDraft.waiting_for_llm_edit)

    await callback.message.answer(
        f"<b>📝 Текущий драфт:</b>\n\n{draft.content}\n\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"🤖 <b>Опишите, что нужно изменить:</b>\n"
        f"Например:\n"
        f"• Сделай тон более деловым\n"
        f"• Убери упоминание о конкретной компании\n"
        f"• Добавь больше юридического контекста\n"
        f"• Сделай короче, без потери смысла\n\n"
        f"Отправьте /cancel для отмены.",
        parse_mode="HTML"
    )


@router.message(EditDraft.waiting_for_manual_edit, Command("cancel"))
@router.message(EditDraft.waiting_for_llm_edit, Command("cancel"))
async def cancel_edit(message: Message, state: FSMContext):
    """Отмена редактирования."""
    await state.clear()
    await message.answer("❌ Редактирование отменено.")


@router.message(EditDraft.waiting_for_manual_edit)
async def process_manual_edit(message: Message, state: FSMContext, db: AsyncSession):
    """Обработка вручную отредактированного текста."""
    data = await state.get_data()
    draft_id = data.get("draft_id")

    # Получаем драфт
    result = await db.execute(
        select(PostDraft).where(PostDraft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if not draft:
        await message.answer(f"❌ Драфт #{draft_id} не найден")
        await state.clear()
        return

    # Обновляем драфт новым текстом
    draft.content = message.text
    draft.status = 'edited'
    await db.commit()

    await message.answer(f"✅ Драфт #{draft_id} обновлен!")

    # Отправляем обновленный драфт на проверку
    await send_draft_for_review(message.chat.id, draft, db)

    await state.clear()


@router.message(EditDraft.waiting_for_llm_edit, F.voice)
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

        # Используем выбранный LLM провайдер для редактирования
        llm = get_llm_provider(_selected_llm_provider)

        prompt = f"""Ты профессиональный редактор Telegram-постов о юридических новостях в сфере AI.

📌 ИСХОДНЫЙ ПОСТ (который нужно отредактировать):
{original_content}

📰 ОРИГИНАЛЬНАЯ СТАТЬЯ (для справки):
{article.content[:1000] if article else 'Не доступна'}

✏️ ИНСТРУКЦИИ ПОЛЬЗОВАТЕЛЯ:
{edit_instructions}

🎯 ТВОЯ ЗАДАЧА:
Внимательно прочитай инструкции пользователя и ТОЧНО выполни их. Не добавляй ничего от себя, только то что просит пользователь.

ВАЖНО:
1. Выполни ТОЛЬКО то, что просит пользователь в инструкциях
2. Сохрани общую структуру поста (заголовок, текст, хештеги)
3. Используй HTML разметку (<b>, <i>, <code>)
4. Если пользователь просит сделать короче - убери лишние детали
5. Если просит добавить - добавь релевантную информацию
6. Если просит изменить тон - измени стиль написания
7. Не выдумывай факты, используй информацию из оригинальной статьи

ВЕРНИ ТОЛЬКО отредактированный текст поста, без комментариев и пояснений."""

        new_content = await llm.generate_completion(
            messages=[
                {"role": "system", "content": "Ты опытный редактор. Строго следуй инструкциям пользователя. Возвращай только финальный текст, без объяснений."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=3500
        )

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
        logger.error("voice_edit_generation_error", error=str(e), provider=_selected_llm_provider)
        await message.answer(
            f"❌ Ошибка при генерации: {str(e)}\n\nПопробуйте еще раз или отправьте /cancel"
        )


@router.message(EditDraft.waiting_for_llm_edit)
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

        # Используем выбранный LLM провайдер
        llm = get_llm_provider(_selected_llm_provider)

        prompt = f"""Ты профессиональный редактор Telegram-постов о юридических новостях в сфере AI.

📌 ИСХОДНЫЙ ПОСТ (который нужно отредактировать):
{original_content}

📰 ОРИГИНАЛЬНАЯ СТАТЬЯ (для справки):
{article.content[:1000] if article else 'Не доступна'}

✏️ ИНСТРУКЦИИ ПОЛЬЗОВАТЕЛЯ:
{edit_instructions}

🎯 ТВОЯ ЗАДАЧА:
Внимательно прочитай инструкции пользователя и ТОЧНО выполни их. Не добавляй ничего от себя, только то что просит пользователь.

ВАЖНО:
1. Выполни ТОЛЬКО то, что просит пользователь в инструкциях
2. Сохрани общую структуру поста (заголовок, текст, хештеги)
3. Используй HTML разметку (<b>, <i>, <code>)
4. Если пользователь просит сделать короче - убери лишние детали
5. Если просит добавить - добавь релевантную информацию
6. Если просит изменить тон - измени стиль написания
7. Не выдумывай факты, используй информацию из оригинальной статьи

ВЕРНИ ТОЛЬКО отредактированный текст поста, без комментариев и пояснений."""

        new_content = await llm.generate_completion(
            messages=[
                {"role": "system", "content": "Ты опытный редактор. Строго следуй инструкциям пользователя. Возвращай только финальный текст, без объяснений."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=3500
        )

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
        logger.error("edit_generation_error", error=str(e), provider=_selected_llm_provider)
        await message.answer(
            f"❌ Ошибка при генерации: {str(e)}\n\n"
            f"Попробуйте еще раз или отправьте /cancel"
        )


@router.callback_query(F.data.startswith("publish_edited:"))
async def callback_publish_edited(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Опубликовать отредактированную версию."""
    # ВАЖНО: отвечаем сразу, чтобы кнопка не зависала
    await callback.answer("Публикую...")

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

        try:
            if success:
                # Проверяем тип сообщения (photo или text)
                if callback.message.photo:
                    await callback.message.edit_caption(
                        caption=f"✅ Отредактированный драфт #{draft_id} успешно опубликован!",
                        reply_markup=None
                    )
                else:
                    await callback.message.edit_text(
                        text=f"✅ Отредактированный драфт #{draft_id} успешно опубликован!",
                        reply_markup=None
                    )
            else:
                if callback.message.photo:
                    await callback.message.edit_caption(
                        caption=f"❌ Ошибка при публикации драфта #{draft_id}",
                        reply_markup=None
                    )
                else:
                    await callback.message.edit_text(
                        text=f"❌ Ошибка при публикации драфта #{draft_id}",
                        reply_markup=None
                    )
        except Exception as e:
            logger.error("callback_publish_edited_error", error=str(e), draft_id=draft_id)
            # Fallback - отправляем новое сообщение если редактирование не удалось
            status_msg = f"✅ Отредактированный драфт #{draft_id} успешно опубликован!" if success else f"❌ Ошибка при публикации драфта #{draft_id}"
            await callback.message.answer(status_msg)
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


@router.callback_query(F.data.startswith("cancel:"))
async def callback_cancel_action(callback: CallbackQuery, db: AsyncSession):
    """Обработчик кнопки 'Отмена' в диалогах подтверждения (publish/reject)."""
    await callback.answer("Отменено")

    if not await check_admin(callback.from_user.id):
        return

    draft_id = int(callback.data.split(":")[1])

    # Возвращаем исходную клавиатуру драфта (отменяем действие)
    await callback.message.edit_reply_markup(
        reply_markup=get_draft_review_keyboard(draft_id)
    )


@router.callback_query(F.data.startswith("back_to_draft:"))
async def callback_back_to_draft(callback: CallbackQuery, db: AsyncSession):
    """Обработчик кнопки 'Назад' - возвращает исходную клавиатуру драфта."""
    await callback.answer("Отменено")

    if not await check_admin(callback.from_user.id):
        return

    draft_id = int(callback.data.split(":")[1])

    # Возвращаем исходную клавиатуру драфта (не отправляем новое сообщение!)
    await callback.message.edit_reply_markup(
        reply_markup=get_draft_review_keyboard(draft_id)
    )


# ====================
# Обработчики кнопок главного меню
# ====================

@router.callback_query(F.data == "show_drafts")
async def callback_show_drafts(callback: CallbackQuery, db: AsyncSession):
    """Показать драфты через кнопку."""
    if not await check_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет прав доступа", show_alert=True)
        return

    # Получаем ВСЕ драфты в статусе pending_review (без фильтра по дате)
    result = await db.execute(
        select(PostDraft)
        .where(PostDraft.status == 'pending_review')
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

    # Определяем название текущего провайдера
    provider_name = "OpenAI (GPT-4o-mini)" if _selected_llm_provider == "openai" else "Perplexity (Llama 3.1)"

    settings_text = f"""
⚙️ <b>Настройки системы</b>

📊 Сбор новостей: автоматически в 09:00 MSK
🤖 AI модель: {provider_name}
📝 Макс. драфтов/день: 3
✅ Требуется модерация: Да

Для изменения настроек используйте переменные окружения в .env файле.
"""

    # Добавляем кнопку выбора LLM
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🤖 Выбрать LLM провайдера",
            callback_data="show_llm_selection"
        )
    )

    await callback.message.answer(settings_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "show_llm_selection")
async def callback_show_llm_selection(callback: CallbackQuery):
    """Показать выбор LLM провайдера."""
    await callback.answer()

    if not await check_admin(callback.from_user.id):
        return

    await callback.message.answer(
        "🤖 <b>Выберите LLM провайдера:</b>\n\n"
        "OpenAI использует модель GPT-4o-mini для быстрой генерации текста.\n"
        "Perplexity использует Llama 3.1 с доступом к актуальной информации.",
        parse_mode="HTML",
        reply_markup=get_llm_selection_keyboard(_selected_llm_provider)
    )


@router.callback_query(F.data.startswith("llm_select:"))
async def callback_llm_select(callback: CallbackQuery):
    """Обработчик выбора LLM провайдера."""
    await callback.answer()

    if not await check_admin(callback.from_user.id):
        return

    global _selected_llm_provider
    provider = callback.data.split(":")[1]
    _selected_llm_provider = provider

    provider_name = "OpenAI (GPT-4o-mini)" if provider == "openai" else "Perplexity (Llama 3.1)"

    await callback.message.edit_text(
        f"✅ <b>Выбран провайдер: {provider_name}</b>\n\n"
        f"Теперь все AI-генерации будут использовать {provider_name}.",
        parse_mode="HTML"
    )

    logger.info("llm_provider_changed", provider=provider, admin_id=callback.from_user.id)


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

        # Формируем preview текст
        preview_header = f"🆕 <b>Новый драфт #{display_number}</b>"

        preview_footer = f"""
━━━━━━━━━━━━━━━━
📊 Confidence: {draft.confidence_score:.2f}
🔗 Источник: {article.source_name if article else 'Unknown'}
⏰ Создан: {draft.created_at.strftime('%d.%m.%Y %H:%M')}
"""

        full_preview_text = f"{preview_header}\n\n{draft.content}\n{preview_footer}"

        # Отправляем с изображением если есть
        if draft.image_path:
            # Отправляем двумя сообщениями для обхода лимита caption (1024 символа)
            photo = FSInputFile(draft.image_path)
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=preview_header
            )

            # Отправляем полный текст preview с кнопками
            await bot.send_message(
                chat_id=chat_id,
                text=f"{draft.content}\n{preview_footer}",
                reply_markup=get_draft_review_keyboard(draft.id),
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=full_preview_text,
                reply_markup=get_draft_review_keyboard(draft.id),
                parse_mode="HTML"
            )

        logger.info("draft_sent_for_review", draft_id=draft.id)

    except Exception as e:
        logger.error("draft_send_error", draft_id=draft.id, error=str(e))


async def _vectorize_publication_background(pub_id: int, content: str, draft_id: int):
    """Фоновая векторизация публикации в Qdrant (не блокирует UI)."""
    try:
        vector_search = get_vector_search()
        await vector_search.add_publication(
            pub_id=pub_id,
            content=content,
            published_at=datetime.utcnow(),
            reactions={}
        )
        logger.info("publication_vectorized", pub_id=pub_id, draft_id=draft_id)
    except Exception as vec_error:
        logger.warning(
            "vectorization_failed",
            draft_id=draft_id,
            error=str(vec_error)
        )


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
        logger.info("publish_draft_before_title_removal", draft_id=draft_id, has_image=bool(draft.image_path), title=draft.title[:50] if draft.title else None, content_start=final_text[:100])

        # Если есть изображение - убираем заголовок из текста (он уже на картинке)
        if draft.image_path and draft.title:
            # Сначала ищем маркеры международных новостей
            intl_markers = ["🌍 Международные новости:\n\n", "🌎 За рубежом:\n\n", "🌏 В мире:\n\n",
                           "🌐 Новости из-за рубежа:\n\n", "🗺️ Зарубежный опыт:\n\n"]

            intl_prefix = ""
            for marker in intl_markers:
                if final_text.startswith(marker):
                    intl_prefix = marker
                    final_text = final_text[len(marker):]  # Временно убираем маркер
                    break

            # Убираем заголовок (обычно в начале в тегах <b>...</b>)
            title_patterns = [
                f"<b>{draft.title}</b>\n\n",
                f"<b>{draft.title}</b>\n",
                f"{draft.title}\n\n",
                f"{draft.title}\n"
            ]
            for pattern in title_patterns:
                if final_text.startswith(pattern):
                    logger.info("publish_draft_title_pattern_matched", draft_id=draft_id, pattern=pattern[:50])
                    final_text = final_text[len(pattern):]
                    break

            # Возвращаем маркер международных новостей если был
            final_text = intl_prefix + final_text

            logger.info("publish_draft_after_title_removal", draft_id=draft_id, content_start=final_text[:100])

        # Добавляем разделитель и источник
        if article:
            final_text += f"\n\n━━━━━━━━━━━━━━━━"

            # Источник с attribution
            source_name = article.source_name if article.source_name else "Источник"
            final_text += f"\n📰 {source_name}"

        # Публикуем в канал
        if draft.image_path:
            # Публикуем двумя последовательными сообщениями для обхода лимита caption (1024 символа)
            # 1. Фото БЕЗ подписи (заголовок уже на изображении)
            photo = FSInputFile(draft.image_path)
            photo_message = await get_bot().send_photo(
                chat_id=settings.telegram_channel_id,
                photo=photo
            )

            # 2. Полный текст с интерактивными кнопками (до 4096 символов)
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
        await db.refresh(publication)

        # Векторизация через Celery (не блокирует UI)
        if settings.qdrant_enabled:
            try:
                from app.tasks.celery_tasks import vectorize_publication_task
                vectorize_publication_task.delay(
                    pub_id=publication.id,
                    content=draft.content,
                    draft_id=draft.id
                )
                logger.info("vectorization_task_queued", pub_id=publication.id, draft_id=draft.id)
            except Exception as e:
                logger.warning("vectorization_task_queue_error", error=str(e))

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
    Показать клавиатуру для выбора мнения о посте (редактирует клавиатуру под постом).
    """
    try:
        # Извлекаем post_id из callback_data
        post_id = int(callback.data.split(":")[1])

        # Редактируем клавиатуру под постом (не создаем новое сообщение!)
        await callback.message.edit_reply_markup(
            reply_markup=get_opinion_keyboard(post_id)
        )

        # Показываем уведомление (не alert, просто тост)
        await callback.answer("📊 Выберите вашу реакцию ⬇️")

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
        reaction_type = parts[2]

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

        # Обновляем quality_score в Qdrant (асинхронно, не блокирует)
        try:
            from app.modules.vector_search import get_vector_search
            vector_search = get_vector_search()
            vector_search.update_quality_score(publication.id, reactions)
        except Exception as e:
            logger.error("qdrant_update_error", error=str(e), pub_id=publication.id)
            # Продолжаем работу даже если Qdrant недоступен

        # Полный словарь всех реакций
        reaction_emoji = {
            "useful": "👍",
            "important": "🔥",
            "controversial": "🤔",
            "banal": "💤",
            "obvious": "🤷",
            "poor_quality": "👎",
            "low_content_quality": "📉",
            "bad_source": "📰"
        }
        reaction_text = {
            "useful": "Полезно",
            "important": "Важно",
            "controversial": "Спорно",
            "banal": "Банальщина",
            "obvious": "Очевидный вывод",
            "poor_quality": "Плохое качество",
            "low_content_quality": "Низкое качество контента",
            "bad_source": "Плохой источник"
        }

        emoji = reaction_emoji.get(reaction_type, "👍")
        text = reaction_text.get(reaction_type, "")

        # Возвращаем исходную клавиатуру "Ваше мнение"
        try:
            # Получаем article URL для клавиатуры
            draft_result = await db.execute(
                select(PostDraft).where(PostDraft.id == post_id)
            )
            draft = draft_result.scalar_one_or_none()

            if draft and draft.article_id:
                article_result = await db.execute(
                    select(RawArticle).where(RawArticle.id == draft.article_id)
                )
                article = article_result.scalar_one_or_none()

                # Возвращаем клавиатуру к исходному виду
                await callback.message.edit_reply_markup(
                    reply_markup=get_reader_keyboard(
                        article.url if article else "",
                        post_id=post_id
                    )
                )
        except Exception as edit_error:
            logger.warning("keyboard_restore_error", error=str(edit_error))
            # Не критично, продолжаем

        # Показываем благодарность
        await callback.answer(f"{emoji} Спасибо за ваше мнение: {text}!", show_alert=True)

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
    from app.modules.api_usage_tracker import get_current_month_cost

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

    # Получаем стоимость API за текущий месяц
    api_cost_data = await get_current_month_cost(db)

    # Получаем статистику AI анализа
    analytics = AnalyticsService(db)
    ai_stats = await analytics.get_ai_analysis_stats()

    stats_text = f"""
📊 <b>Статистика системы</b>

📰 Всего статей: {articles_count}
📝 Всего драфтов: {drafts_count}
✅ Опубликовано: {publications_count}
⏳ Ожидают модерации: {pending_count}

━━━━━━━━━━━━━━━━

💰 <b>Стоимость API за {api_cost_data['month_name']}</b>

💵 Общая стоимость: ${api_cost_data['total_cost_usd']:.4f}
📊 Всего токенов: {api_cost_data['total_tokens']:,}
🔢 Всего запросов: {api_cost_data['total_requests']}
"""

    # Добавляем статистику по провайдерам
    if api_cost_data['by_provider']:
        stats_text += "\n<b>По провайдерам:</b>\n"
        for provider, data in api_cost_data['by_provider'].items():
            provider_name = "OpenAI" if provider == "openai" else "Perplexity"
            stats_text += f"├─ {provider_name}:\n"
            stats_text += f"│  ├─ Стоимость: ${data['cost_usd']:.4f}\n"
            stats_text += f"│  ├─ Токенов: {data['tokens']:,}\n"
            stats_text += f"│  └─ Запросов: {data['requests']}\n"

    # Ссылка на проверку баланса Perplexity
    stats_text += "\n🔗 <a href='https://www.perplexity.ai/account/api/billing'>Проверить баланс Perplexity API</a>\n"

    # Добавляем статистику AI анализа
    stats_text += "\n━━━━━━━━━━━━━━━━\n\n"
    stats_text += "🤖 <b>AI Анализ аналитики</b>\n\n"

    if ai_stats['month']['count'] > 0 or ai_stats['year']['count'] > 0:
        stats_text += f"<b>За текущий месяц:</b>\n"
        stats_text += f"├─ Запросов: {ai_stats['month']['count']}\n"
        stats_text += f"├─ Токенов: {ai_stats['month']['total_tokens']:,}\n"
        stats_text += f"└─ Стоимость: ${ai_stats['month']['total_cost_usd']:.4f}\n"

        # Разбивка по моделям за месяц
        if ai_stats['month']['by_model']:
            for model, data in ai_stats['month']['by_model'].items():
                model_name = model.replace('gpt-', 'GPT-').upper()
                stats_text += f"   └─ {model_name}: {data['count']} запросов, ${data['cost_usd']:.4f}\n"

        stats_text += f"\n<b>За текущий год:</b>\n"
        stats_text += f"├─ Запросов: {ai_stats['year']['count']}\n"
        stats_text += f"├─ Токенов: {ai_stats['year']['total_tokens']:,}\n"
        stats_text += f"└─ Стоимость: ${ai_stats['year']['total_cost_usd']:.2f}\n"

        # Разбивка по моделям за год
        if ai_stats['year']['by_model']:
            for model, data in ai_stats['year']['by_model'].items():
                model_name = model.replace('gpt-', 'GPT-').upper()
                stats_text += f"   └─ {model_name}: {data['count']} запросов, ${data['cost_usd']:.2f}\n"
    else:
        stats_text += "Анализы ещё не запускались\n"

    stats_text += f"\n📅 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"

    return stats_text


# ====================
# Analytics Dashboard
# ====================

def format_analytics_report(
    stats: Dict,
    top_posts: List[Dict],
    worst_posts: List[Dict],
    sources: List[Dict],
    weekday_stats: Dict,
    vector_stats: Optional[Dict],
    source_recommendations: Optional[List[Dict]] = None,
    views_stats: Optional[Dict] = None,
    best_time: Optional[Dict] = None,
    trending_topics: Optional[List[Dict]] = None,
    alerts: Optional[List[Dict]] = None
) -> str:
    """
    Форматировать красивый отчёт аналитики.

    Args:
        stats: Общая статистика
        top_posts: Топ постов
        worst_posts: Худшие посты
        sources: Статистика источников
        weekday_stats: Статистика по дням недели
        vector_stats: Статистика векторной базы

    Returns:
        Отформатированный текст отчёта
    """
    period_days = stats.get("period_days", 7)

    report = f"""📊 <b>Аналитика канала @legal_ai_pro</b>

━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 <b>За последние {period_days} дней:</b>

<b>Публикации:</b>
├─ 📝 Опубликовано: {stats['total_publications']} постов
├─ ✅ Одобрено: {stats['approved_drafts']} из {stats['total_drafts']} драфтов ({stats['approval_rate']:.0f}%)
├─ ❌ Отклонено: {stats['rejected_drafts']} драфтов
└─ 📊 Avg quality score: {stats['avg_quality_score']}

<b>Реакции:</b>
├─ 👍 Полезно: {stats['reactions']['useful']} ({stats['reactions']['useful']/max(stats['total_reactions'],1)*100:.0f}%)
├─ 🔥 Важно: {stats['reactions']['important']} ({stats['reactions']['important']/max(stats['total_reactions'],1)*100:.0f}%)
├─ 🤔 Спорно: {stats['reactions']['controversial']} ({stats['reactions']['controversial']/max(stats['total_reactions'],1)*100:.0f}%)
├─ 💤 Банальщина: {stats['reactions']['banal']} ({stats['reactions']['banal']/max(stats['total_reactions'],1)*100:.0f}%)
├─ 🤷 Очевидно: {stats['reactions']['obvious']} ({stats['reactions']['obvious']/max(stats['total_reactions'],1)*100:.0f}%)
├─ 👎 Плохое: {stats['reactions']['poor_quality']} ({stats['reactions']['poor_quality']/max(stats['total_reactions'],1)*100:.0f}%)
├─ 📉 Низкое качество: {stats['reactions']['low_content_quality']} ({stats['reactions']['low_content_quality']/max(stats['total_reactions'],1)*100:.0f}%)
└─ 📰 Плохой источник: {stats['reactions']['bad_source']} ({stats['reactions']['bad_source']/max(stats['total_reactions'],1)*100:.0f}%)

<b>Engagement:</b>
├─ 📊 Всего реакций: {stats['total_reactions']}
├─ 💬 Постов с реакциями: {stats['engaged_publications']} из {stats['total_publications']}
└─ 🎯 Engagement rate: {stats['engagement_rate']}%
"""

    # Топ посты
    if top_posts:
        report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        report += "🔥 <b>Топ-3 поста:</b>\n\n"

        for i, post in enumerate(top_posts[:3], 1):
            title_raw = post['title'][:80] + "..." if len(post['title']) > 80 else post['title']
            title = html.escape(title_raw)
            date = post['published_at'].strftime('%d.%m.%Y %H:%M')
            reactions = post['reactions']

            report += f"{i}️⃣ <b>{title}</b>\n"
            report += f"   📅 {date}\n"
            report += f"   👍 {reactions.get('useful', 0)} | 🔥 {reactions.get('important', 0)} | 🤔 {reactions.get('controversial', 0)}\n"
            report += f"   📊 Quality: {post['quality_score']}\n"
            if post['telegram_message_id']:
                msg_id = post['telegram_message_id']
                report += f'   🔗 <a href="https://t.me/legal_ai_pro/{msg_id}">Перейти к посту</a>\n'
            report += "\n"

    # Худшие посты
    if worst_posts:
        report += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        report += "💤 <b>Худшие посты (учимся на ошибках):</b>\n\n"

        for i, post in enumerate(worst_posts[:3], 1):
            title_raw = post['title'][:80] + "..." if len(post['title']) > 80 else post['title']
            title = html.escape(title_raw)
            date = post['published_at'].strftime('%d.%m.%Y %H:%M')
            reactions = post['reactions']

            report += f"{i}️⃣ <b>{title}</b>\n"
            report += f"   📅 {date}\n"
            report += f"   💤 {reactions.get('banal', 0)} | 👎 {reactions.get('poor_quality', 0)} | 🤷 {reactions.get('obvious', 0)}\n"
            report += f"   📊 Quality: {post['quality_score']}\n"

            # Определить основную проблему
            if reactions.get('banal', 0) > 0:
                report += "   ⚠️ Проблема: Слишком общо, нет конкретики\n"
            elif reactions.get('obvious', 0) > 0:
                report += "   ⚠️ Проблема: Очевидные выводы\n"
            elif reactions.get('poor_quality', 0) > 0:
                report += "   ⚠️ Проблема: Низкое качество контента\n"
            elif reactions.get('low_content_quality', 0) > 0:
                report += "   ⚠️ Проблема: Плохая подача материала\n"
            elif reactions.get('bad_source', 0) > 0:
                report += "   ⚠️ Проблема: Ненадежный или некачественный источник\n"

            report += "\n"

    # Статистика по дням недели (если есть данные)
    if weekday_stats:
        report += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        report += "📅 <b>Статистика по дням недели:</b>\n\n"

        best_day = None
        best_score = -999.0

        for day in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]:
            if day in weekday_stats:
                day_data = weekday_stats[day]
                total = day_data['total_posts']
                avg_score = day_data['avg_quality_score']

                if avg_score > best_score:
                    best_score = avg_score
                    best_day = day

                marker = "⭐" if day == best_day and total > 0 else ""
                report += f"{day}: {total} постов | Avg quality: {avg_score} {marker}\n"

        if best_day:
            report += f"\n🏆 Лучший день: <b>{best_day}</b> (avg quality: {best_score})\n"

    # Эффективность источников
    if sources:
        report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        report += "📰 <b>Топ источников:</b>\n\n"

        for i, source in enumerate(sources[:5], 1):
            name_raw = source['source_name'][:40] + "..." if len(source['source_name']) > 40 else source['source_name']
            name = html.escape(name_raw)
            collected = source['total_collected']
            published = source['total_published']
            pub_rate = source['publication_rate']
            quality = source['avg_quality_score']

            status = ""
            if quality >= 0.6:
                status = "✅"
            elif quality >= 0.3:
                status = "⚠️"
            else:
                status = "❌"

            report += f"{i}. <b>{name}</b> {status}\n"
            report += f"   ├─ Отобрано: {collected} новостей\n"
            report += f"   ├─ Опубликовано: {published} ({pub_rate:.0f}%)\n"
            report += f"   └─ Avg quality: {quality}\n"
            report += "\n"

    # Статистика векторной базы
    if vector_stats:
        report += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        report += "🗄️ <b>Векторная база Qdrant:</b>\n\n"
        report += f"├─ 📦 Всего векторов: {vector_stats['total_vectors']}\n"
        report += f"├─ ✅ Позитивных примеров: {vector_stats['positive_examples']} (score &gt; 0.5)\n"
        report += f"├─ ❌ Негативных примеров: {vector_stats['negative_examples']} (score &lt; -0.3)\n"
        report += f"├─ ⚖️ Нейтральных: {vector_stats['neutral_examples']}\n"
        report += f"└─ 📊 Avg score всей базы: {vector_stats['avg_quality_score']}\n"

    # Рекомендации по источникам
    if source_recommendations:
        report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        report += "⚡ <b>Рекомендации по источникам:</b>\n\n"

        for rec in source_recommendations[:5]:  # Показываем топ-5
            source_name_escaped = html.escape(rec["source_name"])
            report += f"<b>{source_name_escaped}</b>\n"
            report += f"   {rec['recommendation']}\n"
            report += f"   ├─ Публикаций: {rec['total_publications']}\n"
            report += f"   ├─ Avg quality: {rec['avg_quality_score']}\n"
            report += f"   ├─ Реакций 'Плохой источник': {rec['bad_source_reactions']}\n"
            report += f"   └─ Реакций 'Низкое качество': {rec['low_quality_reactions']}\n"
            report += "\n"

        if not source_recommendations:
            report += "✅ Все источники работают хорошо!\n"

    # Views и Forwards статистика
    # Просмотры и форварды (Telegram metrics)
    report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += "📈 <b>Просмотры и Форварды:</b>\n\n"

    if views_stats and views_stats.get('total_views', 0) > 0:
        report += f"├─ 👁️ Всего просмотров: {views_stats['total_views']:,}\n"
        report += f"├─ 📤 Всего форвардов: {views_stats['total_forwards']:,}\n"
        report += f"├─ 📊 Avg просмотров/пост: {views_stats['avg_views']}\n"
        report += f"├─ 📊 Avg форвардов/пост: {views_stats['avg_forwards']}\n"
        report += f"├─ 🔥 Макс просмотров: {views_stats['max_views']:,}\n"
        report += f"├─ 🔥 Макс форвардов: {views_stats['max_forwards']:,}\n"
        report += f"└─ 🌊 Viral coefficient: {views_stats['viral_coefficient']}%\n"
    else:
        report += "⚠️ <b>Данные недоступны</b>\n"
        report += "├─ Метрики из Telegram еще не собраны\n"
        report += "├─ Celery задача запускается каждые 6 часов\n"
        report += "├─ Следующий запуск: 00:00 / 06:00 / 12:00 / 18:00 MSK\n"
        report += "└─ Или проверьте логи: docker compose logs celery_worker | grep collect_telegram_metrics\n"

    # A/B тестирование времени публикации
    report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += "⏰ <b>Лучшее время для публикации:</b>\n\n"

    if best_time and best_time.get('best_hour') is not None:
        report += f"🎯 {best_time['recommendation']}\n"
        report += f"├─ Engagement rate: {best_time['best_engagement_rate']}%\n"
        report += f"└─ На основе анализа за 30 дней\n"
    else:
        report += "⚠️ <b>Недостаточно данных</b>\n"
        report += "└─ Требуется хотя бы 1 публикация с views для анализа\n"

    # Трендовые темы
    report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += "🔥 <b>Трендовые темы:</b>\n\n"

    if trending_topics:
        for i, topic in enumerate(trending_topics[:5], 1):
            report += f"{i}. <b>{topic['topic']}</b>\n"
            report += f"   ├─ Упоминаний: {topic['mentions']}\n"
            report += f"   └─ Relevance: {topic['relevance_score']}%\n"
    else:
        report += "⚠️ <b>Не найдено</b>\n"
        report += "└─ Требуется больше публикаций с детальным контентом\n"

    # Алерты и предупреждения
    if alerts:
        report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        report += "🚨 <b>Алерты и предупреждения:</b>\n\n"
        for alert in alerts:
            report += f"{alert['message']}\n"
            report += f"   └─ {alert['details']}\n\n"

    report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"📅 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"

    return report


@router.message(Command("analytics"))
async def cmd_analytics(message: Message, db: AsyncSession):
    """Показать аналитику канала."""

    if not await check_admin(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде")
        return

    # Клавиатура выбора периода
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 7 дней", callback_data="analytics:7"),
            InlineKeyboardButton(text="📅 30 дней", callback_data="analytics:30"),
        ],
        [
            InlineKeyboardButton(text="📅 Всё время", callback_data="analytics:all"),
        ],
        [
            InlineKeyboardButton(text="🤖 AI Анализ", callback_data="show_ai_analysis_menu"),
        ]
    ])

    await message.answer(
        "📊 <b>Выберите период для аналитики:</b>",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "show_ai_analysis_menu")
async def callback_show_ai_analysis_menu(callback: CallbackQuery):
    """Показать меню выбора периода для AI анализа."""
    await callback.answer()

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🤖 7 дней", callback_data="ai_analysis:7"),
            InlineKeyboardButton(text="🤖 30 дней", callback_data="ai_analysis:30"),
        ],
        [
            InlineKeyboardButton(text="« Назад", callback_data="back_to_analytics_menu"),
        ]
    ])

    await callback.message.edit_text(
        "🤖 <b>AI Анализ и Рекомендации</b>\n\n"
        "Выберите период для анализа:\n\n"
        "GPT-4 проанализирует все метрики и даст конкретные рекомендации "
        "по улучшению engagement, контент-стратегии и оптимизации источников.",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "back_to_analytics_menu")
async def callback_back_to_analytics_menu(callback: CallbackQuery):
    """Вернуться к меню аналитики."""
    await callback.answer()

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 7 дней", callback_data="analytics:7"),
            InlineKeyboardButton(text="📅 30 дней", callback_data="analytics:30"),
        ],
        [
            InlineKeyboardButton(text="📅 Всё время", callback_data="analytics:all"),
        ],
        [
            InlineKeyboardButton(text="🤖 AI Анализ", callback_data="show_ai_analysis_menu"),
        ]
    ])

    await callback.message.edit_text(
        "📊 <b>Выберите период для аналитики:</b>",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("analytics:"))
async def callback_analytics(callback: CallbackQuery, db: AsyncSession):
    """Отобразить аналитику за период."""

    await callback.answer()

    if not await check_admin(callback.from_user.id):
        await callback.message.answer("⛔ У вас нет доступа")
        return

    try:
        period = callback.data.split(":")[1]
        days = int(period) if period != "all" else 9999

        # Показываем loading сообщение
        loading_msg = await callback.message.answer(
            "⏳ <b>Собираю аналитику...</b>\n\n"
            "Анализирую публикации, метрики и источники...",
            parse_mode="HTML"
        )

        logger.info("analytics_requested", period=period, days=days, user_id=callback.from_user.id)

        # Создаём сервис аналитики
        analytics = AnalyticsService(db)

        # Собираем все данные (базовые + новые)
        stats = await analytics.get_period_stats(days)
        top_posts = await analytics.get_top_posts(3, days)
        worst_posts = await analytics.get_worst_posts(3, days)
        sources = await analytics.get_source_stats(days)
        weekday_stats = await analytics.get_weekday_stats(min(days, 30))  # Максимум 30 дней для статистики по дням
        vector_stats = await analytics.get_vector_db_stats()
        source_recommendations = await analytics.get_source_recommendations(min(days, 30))

        # НОВЫЕ методы аналитики
        views_stats = await analytics.get_views_and_forwards_stats(days)
        best_time = await analytics.get_best_publish_time(min(days, 30))
        trending_topics = await analytics.get_trending_topics(days, top_n=5)
        alerts = await analytics.get_performance_alerts(days)

        # Форматируем отчёт
        report = format_analytics_report(
            stats=stats,
            top_posts=top_posts,
            worst_posts=worst_posts,
            sources=sources,
            weekday_stats=weekday_stats,
            vector_stats=vector_stats,
            source_recommendations=source_recommendations,
            views_stats=views_stats,
            best_time=best_time,
            trending_topics=trending_topics,
            alerts=alerts
        )


        # Удаляем loading сообщение
        await loading_msg.delete()

        # Telegram ограничивает сообщения до 4096 символов
        # Если отчёт длинный - разбиваем на части
        if len(report) > 4096:
            # Разбиваем по разделителям
            parts = report.split("━━━━━━━━━━━━━━━━━━━━━━━━━━")

            current_part = ""
            for part in parts:
                if len(current_part + part) > 4000:
                    # Отправляем текущую часть
                    await callback.message.answer(current_part, parse_mode="HTML", disable_web_page_preview=True)
                    current_part = part
                else:
                    current_part += "━━━━━━━━━━━━━━━━━━━━━━━━━━" + part if current_part else part

            # Отправляем последнюю часть
            if current_part:
                await callback.message.answer(current_part, parse_mode="HTML", disable_web_page_preview=True)
        else:
            # Отправляем целиком
            await callback.message.answer(report, parse_mode="HTML", disable_web_page_preview=True)

        logger.info("analytics_sent", period=period, report_length=len(report))

    except Exception as e:
        logger.error("analytics_error", error=str(e), period=callback.data)
        # Удаляем loading сообщение если оно существует
        try:
            await loading_msg.delete()
        except:
            pass
        await callback.message.answer(
            "❌ Произошла ошибка при сборе аналитики. Попробуйте позже.",
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("ai_analysis:"))
async def callback_ai_analysis(callback: CallbackQuery, db: AsyncSession):
    """AI-анализ аналитики с рекомендациями от GPT-4."""

    await callback.answer()

    if not await check_admin(callback.from_user.id):
        await callback.message.answer("⛔ У вас нет доступа")
        return

    try:
        period = callback.data.split(":")[1]
        days = int(period) if period != "all" else 30  # Ограничиваем для AI анализа

        loading_msg = await callback.message.answer(
            "🤖 <b>AI Анализ запущен...</b>\n\n"
            "⏳ Собираю данные и анализирую метрики...\n"
            "⏳ Отправляю запрос к GPT-4...",
            parse_mode="HTML"
        )

        logger.info("ai_analysis_requested", period=period, days=days, user_id=callback.from_user.id)

        # Собираем данные аналитики
        analytics = AnalyticsService(db)

        stats = await analytics.get_period_stats(days)
        top_posts = await analytics.get_top_posts(3, days)
        worst_posts = await analytics.get_worst_posts(3, days)
        sources = await analytics.get_source_stats(days)
        views_stats = await analytics.get_views_and_forwards_stats(days)
        best_time = await analytics.get_best_publish_time(min(days, 30))
        trending_topics = await analytics.get_trending_topics(days, top_n=5)
        alerts = await analytics.get_performance_alerts(days)
        source_recommendations = await analytics.get_source_recommendations(min(days, 30))

        # Формируем данные для GPT
        analytics_data = f"""
ПЕРИОД АНАЛИЗА: {days} дней

ОСНОВНЫЕ МЕТРИКИ:
- Публикаций: {stats['total_publications']}
- Одобрено драфтов: {stats['approved_drafts']} из {stats['total_drafts']} ({stats['approval_rate']:.1f}%)
- Engagement rate: {stats['engagement_rate']:.1f}%
- Avg quality score: {stats['avg_quality_score']}

РЕАКЦИИ:
- Полезно: {stats['reactions']['useful']}
- Важно: {stats['reactions']['important']}
- Спорно: {stats['reactions']['controversial']}
- Банально: {stats['reactions']['banal']}
- Плохое качество: {stats['reactions']['poor_quality']}

VIEWS И FORWARDS:
- Всего просмотров: {views_stats.get('total_views', 0)}
- Avg просмотров/пост: {views_stats.get('avg_views', 0)}
- Всего форвардов: {views_stats.get('total_forwards', 0)}
- Viral coefficient: {views_stats.get('viral_coefficient', 0)}%

ЛУЧШЕЕ ВРЕМЯ ПУБЛИКАЦИИ:
{best_time.get('recommendation', 'Нет данных')}

ТРЕНДОВЫЕ ТЕМЫ:
{chr(10).join([f"- {t['topic']} ({t['mentions']} упоминаний)" for t in trending_topics[:5]]) if trending_topics else 'Нет данных'}

ТОП-3 ПОСТА:
{chr(10).join([f"- {p['title'][:60]}... (quality: {p['quality_score']})" for p in top_posts[:3]]) if top_posts else 'Нет данных'}

ХУДШИЕ ПОСТЫ:
{chr(10).join([f"- {p['title'][:60]}... (quality: {p['quality_score']})" for p in worst_posts[:3]]) if worst_posts else 'Нет данных'}

ПРОБЛЕМНЫЕ ИСТОЧНИКИ:
{chr(10).join([f"- {s['source_name']}: {s['recommendation']}" for s in source_recommendations[:3]]) if source_recommendations else 'Нет проблем'}

АЛЕРТЫ:
{chr(10).join([f"[{a['severity'].upper()}] {a['message']}" for a in alerts]) if alerts else 'Нет алертов'}
"""

        # Вызываем GPT-4 для анализа
        from app.modules.ai_core import call_openai_chat

        prompt = f"""Ты - эксперт по аналитике Telegram каналов и контент-маркетингу.

Проанализируй следующие данные аналитики канала @legal_ai_pro (новости о внедрении ИИ в юриспруденцию и бизнес):

{analytics_data}

Дай детальный анализ и конкретные рекомендации:

1. **АНАЛИЗ СИТУАЦИИ** (2-3 предложения):
   - Общая оценка производительности канала
   - Ключевые проблемы и возможности

2. **ПРИОРИТЕТНЫЕ РЕКОМЕНДАЦИИ** (топ-3, нумерованный список):
   - Конкретные действия для улучшения метрик
   - Фокус на engagement, quality score, и viral coefficient

3. **КОНТЕНТ-СТРАТЕГИЯ**:
   - Какие темы работают лучше всего (на основе trending topics)
   - Рекомендации по улучшению худших постов
   - Как повысить viral coefficient

4. **ИСТОЧНИКИ КОНТЕНТА**:
   - Какие источники стоит оптимизировать/отключить
   - Рекомендации по поиску новых источников

5. **ТАЙМИНГ ПУБЛИКАЦИЙ**:
   - Оптимальное время на основе данных
   - Рекомендации по частоте публикаций

Формат ответа: структурированный, с эмодзи, конкретными цифрами и actionable советами. Не более 800 слов."""

        ai_response, usage_stats = await call_openai_chat(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-4o",  # Используем GPT-4o для качественного анализа и рекомендаций
            temperature=0.7,
            max_tokens=2000,
            db=db,
            operation="ai_analysis"
        )

        # Получаем общую статистику AI анализов
        ai_stats = await analytics.get_ai_analysis_stats()

        # Форматируем ответ
        report = f"""🤖 <b>AI АНАЛИЗ АНАЛИТИКИ</b>

━━━━━━━━━━━━━━━━━━━━━━━━━━

{ai_response}

━━━━━━━━━━━━━━━━━━━━━━━━━━

<i>Анализ выполнен GPT-4 на основе данных за {days} дней</i>
📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}

💰 <b>Стоимость анализа:</b>
📊 Токенов: {usage_stats['total_tokens']:,} (prompt: {usage_stats['prompt_tokens']:,}, completion: {usage_stats['completion_tokens']:,})
💵 Стоимость: ${usage_stats['cost_usd']:.4f}

📈 <b>Общая статистика AI анализов:</b>
• За месяц: {ai_stats['month']['count']} запросов, {ai_stats['month']['total_tokens']:,} токенов, ${ai_stats['month']['total_cost_usd']:.2f}
• За год: {ai_stats['year']['count']} запросов, {ai_stats['year']['total_tokens']:,} токенов, ${ai_stats['year']['total_cost_usd']:.2f}"""

        # Удаляем loading сообщение
        await loading_msg.delete()

        # Отправляем ответ (может быть длинным, поэтому разбиваем если нужно)
        if len(report) > 4096:
            # Разбиваем на части
            parts = report.split("━━━━━━━━━━━━━━━━━━━━━━━━━━")
            for i, part in enumerate(parts):
                if part.strip():
                    await callback.message.answer(
                        part if i == 0 else "━━━━━━━━━━━━━━━━━━━━━━━━━━" + part,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
        else:
            await callback.message.answer(report, parse_mode="HTML", disable_web_page_preview=True)

        logger.info("ai_analysis_sent", period=period, response_length=len(ai_response))

    except Exception as e:
        logger.error("ai_analysis_error", error=str(e), period=callback.data)
        # Удаляем loading сообщение если оно существует
        try:
            await loading_msg.delete()
        except:
            pass
        await callback.message.answer(
            "❌ Произошла ошибка при AI анализе. Попробуйте позже.\n\n"
            f"Ошибка: {str(e)}",
            parse_mode="HTML"
        )


@router.message(Command("alerts"))
async def cmd_alerts(message: Message, db: AsyncSession):
    """Проверить алерты и предупреждения о проблемах."""

    if not await check_admin(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде")
        return

    await message.answer("🔍 Проверяю метрики...")

    try:
        analytics = AnalyticsService(db)

        # Проверяем за последние 7 дней
        alerts = await analytics.get_performance_alerts(days=7)

        if not alerts:
            await message.answer(
                "✅ <b>Всё в порядке!</b>\n\n"
                "Проблем не обнаружено. Система работает нормально.",
                parse_mode="HTML"
            )
        else:
            # Формируем отчёт с алертами
            report = "🚨 <b>Обнаружены проблемы:</b>\n\n"

            # Группируем по severity
            critical = [a for a in alerts if a.get('severity') == 'critical']
            warnings = [a for a in alerts if a.get('severity') == 'warning']
            info = [a for a in alerts if a.get('severity') == 'info']

            if critical:
                report += "🔴 <b>КРИТИЧЕСКИЕ:</b>\n"
                for alert in critical:
                    report += f"{alert['message']}\n"
                    report += f"   └─ {alert['details']}\n\n"

            if warnings:
                report += "⚠️ <b>ПРЕДУПРЕЖДЕНИЯ:</b>\n"
                for alert in warnings:
                    report += f"{alert['message']}\n"
                    report += f"   └─ {alert['details']}\n\n"

            if info:
                report += "💡 <b>ИНФОРМАЦИЯ:</b>\n"
                for alert in info:
                    report += f"{alert['message']}\n"
                    report += f"   └─ {alert['details']}\n\n"

            report += f"\n📅 Проверено: {datetime.now().strftime('%d.%m.%Y %H:%M')}"

            await message.answer(report, parse_mode="HTML")

        logger.info("alerts_checked", user_id=message.from_user.id, alerts_count=len(alerts))

    except Exception as e:
        logger.error("alerts_error", error=str(e))
        await message.answer(
            "❌ Произошла ошибка при проверке алертов. Попробуйте позже.",
            parse_mode="HTML"
        )


# ====================
# Настройка команд бота
# ====================

async def setup_bot_commands():
    """Установить меню команд бота (кнопка меню слева внизу)."""
    commands = [
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="drafts", description="📝 Новые драфты"),
        BotCommand(command="fetch", description="🔄 Запустить сбор новостей"),
        BotCommand(command="analytics", description="📊 Аналитика канала"),
        BotCommand(command="alerts", description="🚨 Проверить проблемы"),
        BotCommand(command="stats", description="📈 Статистика системы"),
        BotCommand(command="help", description="❓ Помощь"),
    ]
    await get_bot().set_my_commands(commands)
    logger.info("bot_commands_set", count=len(commands))


# ====================
# Запуск бота
# ====================

async def start_bot():
    """Запустить бота."""
    # Инициализация базы данных (создаём таблицы если их нет)
    from app.models.database import init_db, get_db
    from app.modules.settings_manager import init_default_settings
    try:
        await init_db()
        logger.info("database_initialized")

        # Инициализация дефолтных настроек
        async for db in get_db():
            await init_default_settings(db)
            logger.info("default_settings_initialized")
            break

    except Exception as e:
        logger.error("database_init_error", error=str(e))

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
