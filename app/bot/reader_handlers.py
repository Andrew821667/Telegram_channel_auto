"""
Reader Bot Handlers.

Handles user interactions:
- Onboarding flow (/start)
- Commands (/today, /search, /saved, /settings)
- Feedback buttons (like/dislike)
- Save/unsave articles
"""

from typing import Optional
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.services.reader_service import (
    get_user_profile,
    create_user_profile,
    update_user_profile,
    get_personalized_feed,
    search_publications,
    save_user_feedback,
    save_article,
    unsave_article,
    get_saved_articles,
    get_user_stats,
    update_last_active,
    log_interaction
)
from app.models.database import Publication


router = Router()


# ==================== FSM States ====================

class OnboardingStates(StatesGroup):
    topics = State()
    expertise = State()
    digest = State()


# ==================== Helper Functions ====================

def format_article_message(article: Publication, index: Optional[int] = None) -> str:
    """Format article for display."""
    if not article.draft:
        return "Статья не найдена"

    # Calculate engagement
    reactions_count = sum(article.reactions.values()) if article.reactions else 0
    engagement_rate = (reactions_count / article.views * 100) if article.views > 0 else 0

    # Format date
    published_date = article.published_at.strftime('%d.%m.%Y')

    prefix = f"{'📰 ' + str(index) + '. ' if index else '📰 '}"

    return (
        f"{prefix}<b>{article.draft.title}</b>\n\n"
        f"👁 {article.views:,} просмотров • "
        f"💬 {reactions_count} реакций • "
        f"📈 {engagement_rate:.1f}%\n"
        f"📅 {published_date}"
    )


def get_article_keyboard(publication_id: int, user_saved: bool = False, show_read_button: bool = True) -> InlineKeyboardMarkup:
    """Get keyboard for article with like/dislike/save buttons."""
    save_text = "❌ Удалить из сохранённых" if user_saved else "🔖 Сохранить"
    save_action = f"unsave:{publication_id}" if user_saved else f"save:{publication_id}"

    keyboard = []

    # Add "Read more" button if needed
    if show_read_button:
        keyboard.append([
            InlineKeyboardButton(text="📖 Читать полностью", callback_data=f"view:{publication_id}")
        ])

    # Feedback buttons
    keyboard.append([
        InlineKeyboardButton(text="👍 Полезно", callback_data=f"feedback:like:{publication_id}"),
        InlineKeyboardButton(text="👎 Не интересно", callback_data=f"feedback:dislike:{publication_id}"),
    ])

    # Save button
    keyboard.append([
        InlineKeyboardButton(text=save_text, callback_data=save_action),
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ==================== /start - Onboarding ====================

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, db: AsyncSession):
    """Handle /start command - onboarding for new users."""
    user_id = message.from_user.id
    profile = await get_user_profile(user_id, db)

    # Check for deep linking parameter
    command_args = message.text.split(maxsplit=1)
    deep_link_param = command_args[1] if len(command_args) > 1 else None

    # Handle deep linking for articles from channel
    if deep_link_param and deep_link_param.startswith("article_"):
        article_id = int(deep_link_param.replace("article_", ""))
        await show_article_from_channel(message, article_id, db, profile)
        return

    # Handle deep linking from channel (general)
    if deep_link_param == "channel":
        # Log channel visit
        await log_interaction(
            user_id=user_id,
            action='channel_visit',
            db=db,
            source='channel'
        )

        if profile:
            await message.answer(
                f"👋 Добро пожаловать из канала Legal AI News!\n\n"
                f"Используйте бот для:\n"
                f"📰 /today - Персональные новости за сегодня\n"
                f"🔍 /search - Поиск по архиву\n"
                f"🔖 /saved - Сохранённые статьи ({len(await get_saved_articles(user_id, db=db))})\n"
                f"⚙️ /settings - Настройки профиля"
            )
        else:
            await message.answer(
                "👋 <b>Добро пожаловать из канала Legal AI News!</b>\n\n"
                "Давайте настроим вашу персональную ленту новостей.",
                parse_mode="HTML"
            )
            await start_onboarding(message, state, db)
        return

    # Normal /start flow
    if profile:
        # Existing user - show main menu
        await message.answer(
            f"С возвращением, {message.from_user.first_name}! 👋\n\n"
            f"Что хотите сделать?\n\n"
            f"/today - Персональные новости за сегодня\n"
            f"/search - Поиск по архиву\n"
            f"/saved - Сохранённые статьи ({len(await get_saved_articles(user_id, db=db))})\n"
            f"/settings - Настройки профиля"
        )
    else:
        # New user - start onboarding
        await start_onboarding(message, state, db)


async def show_article_from_channel(message: Message, article_id: int, db: AsyncSession, profile):
    """Show article directly when user comes from channel link."""
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    from app.models.database import Publication

    # Get publication with draft
    result = await db.execute(
        select(Publication)
        .options(joinedload(Publication.draft))
        .where(Publication.id == article_id)
    )
    article = result.scalar_one_or_none()

    if not article or not article.draft:
        await message.answer(
            "❌ Статья не найдена\n\n"
            "Используйте /search для поиска других статей"
        )
        return

    # If no profile, suggest onboarding
    if not profile:
        await message.answer(
            "👋 <b>Добро пожаловать в Legal AI News Reader Bot!</b>\n\n"
            "Вот статья, которую вы выбрали:",
            parse_mode="HTML"
        )

    # Check if saved
    user_id = message.from_user.id
    from app.services.reader_service import get_saved_articles
    saved_articles = await get_saved_articles(user_id, db=db)
    user_saved = any(s.id == article_id for s in saved_articles)

    # Format full article
    published_date = article.published_at.strftime("%d.%m.%Y")
    full_text = (
        f"📰 <b>{article.draft.title}</b>\n\n"
        f"{article.draft.content}\n\n"
        f"👁 {article.views or 0} | 📅 {published_date}"
    )

    # Show full text with keyboard (without "Read more" button since it's already full)
    keyboard = get_article_keyboard(article_id, user_saved=user_saved, show_read_button=False)

    await message.answer(
        full_text,
        parse_mode="HTML",
        reply_markup=keyboard
    )

    # Log interaction - track that user viewed article from channel
    await log_interaction(
        user_id=user_id,
        action='view',
        db=db,
        publication_id=article_id,
        source='channel_article'
    )

    # Suggest onboarding if new user
    if not profile:
        await message.answer(
            "💡 <b>Хотите получать персонализированные новости?</b>\n\n"
            "Пройдите быструю настройку - выберите интересующие темы и частоту дайджестов.\n\n"
            "Нажмите /start чтобы начать!",
            parse_mode="HTML"
        )



async def start_onboarding(message: Message, state: FSMContext, db: AsyncSession):
    """Start onboarding flow - ask about topics."""
    # Create empty profile
    await create_user_profile(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        db=db
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☐ Персональные данные (GDPR)", callback_data="topic:gdpr")],
        [InlineKeyboardButton(text="☐ ИИ в праве", callback_data="topic:ai_law")],
        [InlineKeyboardButton(text="☐ Криптовалюты и блокчейн", callback_data="topic:crypto")],
        [InlineKeyboardButton(text="☐ Корпоративное право", callback_data="topic:corporate")],
        [InlineKeyboardButton(text="☐ Налоги и финансы", callback_data="topic:tax")],
        [InlineKeyboardButton(text="☐ Интеллектуальная собственность", callback_data="topic:ip")],
        [InlineKeyboardButton(text="Далее →", callback_data="onboarding:expertise")],
    ])

    await message.answer(
        "👋 <b>Добро пожаловать в Legal AI News!</b>\n\n"
        "Давайте настроим вашу персональную ленту новостей.\n\n"
        "<b>1️⃣ Какие темы вас интересуют?</b> (выберите несколько)",
        parse_mode="HTML",
        reply_markup=keyboard
    )

    # Save selected topics in FSM
    await state.update_data(topics=[])
    await state.set_state(OnboardingStates.topics)


@router.callback_query(F.data.startswith("topic:"), StateFilter(OnboardingStates.topics))
async def toggle_topic(callback: CallbackQuery, state: FSMContext):
    """Toggle topic selection during onboarding."""
    topic = callback.data.split(":")[1]

    # Get current topics
    data = await state.get_data()
    topics = data.get('topics', [])

    # Toggle
    if topic in topics:
        topics.remove(topic)
    else:
        topics.append(topic)

    await state.update_data(topics=topics)

    # Update keyboard
    topic_labels = {
        'gdpr': 'Персональные данные (GDPR)',
        'ai_law': 'ИИ в праве',
        'crypto': 'Криптовалюты и блокчейн',
        'corporate': 'Корпоративное право',
        'tax': 'Налоги и финансы',
        'ip': 'Интеллектуальная собственность'
    }

    buttons = []
    for topic_key, label in topic_labels.items():
        icon = "✅" if topic_key in topics else "☐"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {label}",
            callback_data=f"topic:{topic_key}"
        )])

    buttons.append([InlineKeyboardButton(text="Далее →", callback_data="onboarding:expertise")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "onboarding:expertise", StateFilter(OnboardingStates.topics))
async def ask_expertise(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Ask about expertise level."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎓 Студент юрфака", callback_data="expertise:student")],
        [InlineKeyboardButton(text="⚖️ Практикующий юрист", callback_data="expertise:lawyer")],
        [InlineKeyboardButton(text="🏢 In-house юрист", callback_data="expertise:in_house")],
        [InlineKeyboardButton(text="💼 Бизнес/предприниматель", callback_data="expertise:business")],
    ])

    await callback.message.edit_text(
        "👋 <b>Добро пожаловать в Legal AI News!</b>\n\n"
        "Давайте настроим вашу персональную ленту новостей.\n\n"
        "<b>2️⃣ Ваш уровень экспертизы?</b>",
        parse_mode="HTML",
        reply_markup=keyboard
    )

    await state.set_state(OnboardingStates.expertise)
    await callback.answer()


@router.callback_query(F.data.startswith("expertise:"), StateFilter(OnboardingStates.expertise))
async def save_expertise(callback: CallbackQuery, state: FSMContext):
    """Save expertise and ask about digest frequency."""
    expertise = callback.data.split(":")[1]
    await state.update_data(expertise=expertise)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☀️ Ежедневно утром", callback_data="digest:daily")],
        [InlineKeyboardButton(text="📅 2 раза в неделю", callback_data="digest:twice_week")],
        [InlineKeyboardButton(text="📆 Еженедельно в пятницу", callback_data="digest:weekly")],
        [InlineKeyboardButton(text="🚫 Не нужно", callback_data="digest:never")],
    ])

    await callback.message.edit_text(
        "👋 <b>Добро пожаловать в Legal AI News!</b>\n\n"
        "Давайте настроим вашу персональную ленту новостей.\n\n"
        "<b>3️⃣ Как часто получать дайджесты?</b>",
        parse_mode="HTML",
        reply_markup=keyboard
    )

    await state.set_state(OnboardingStates.digest)
    await callback.answer()


@router.callback_query(F.data.startswith("digest:"), StateFilter(OnboardingStates.digest))
async def complete_onboarding(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Complete onboarding and save profile."""
    digest = callback.data.split(":")[1]

    # Get all data
    data = await state.get_data()
    topics = data.get('topics', [])
    expertise = data.get('expertise')

    # Update profile
    await update_user_profile(
        user_id=callback.from_user.id,
        topics=topics,
        expertise_level=expertise,
        digest_frequency=digest,
        db=db
    )

    # Clear FSM
    await state.clear()

    # Success message
    topic_labels = {
        'gdpr': 'Персональные данные',
        'ai_law': 'ИИ в праве',
        'crypto': 'Криптовалюты',
        'corporate': 'Корпоративное право',
        'tax': 'Налоги',
        'ip': 'Интеллектуальная собственность'
    }
    topics_text = ', '.join([topic_labels.get(t, t) for t in topics]) if topics else 'все темы'

    digest_text = {
        'daily': 'ежедневно',
        'twice_week': '2 раза в неделю',
        'weekly': 'еженедельно',
        'never': 'не будете получать'
    }

    await callback.message.edit_text(
        f"✅ <b>Готово! Профиль настроен.</b>\n\n"
        f"📋 Ваши интересы: {topics_text}\n"
        f"📬 Дайджесты: {digest_text[digest]}\n\n"
        f"Теперь вы будете получать:\n"
        f"• Персональные рекомендации статей\n"
        f"• Дайджесты по вашим темам\n"
        f"• Доступ к поиску по архиву\n\n"
        f"<b>Попробуйте:</b>\n"
        f"/today - Что интересного сегодня\n"
        f"/search - Поиск статей\n"
        f"/saved - Сохранённые статьи",
        parse_mode="HTML"
    )

    await callback.answer("✅ Профиль настроен!")


# ==================== /today - Personalized Feed ====================

@router.message(Command("today"))
async def cmd_today(message: Message, db: AsyncSession):
    """Show personalized feed for today."""
    user_id = message.from_user.id
    profile = await get_user_profile(user_id, db)

    if not profile:
        await message.answer(
            "Сначала завершите настройку профиля: /start"
        )
        return

    # Update last active
    await update_last_active(user_id, db)

    # Get personalized feed
    articles = await get_personalized_feed(user_id, limit=5, db=db)

    if not articles:
        await message.answer(
            "📭 Сегодня пока нет новых статей по вашим темам.\n\n"
            "Попробуйте:\n"
            "/search - Поиск по архиву\n"
            "/saved - Ваши сохранённые статьи"
        )
        return

    await message.answer(
        f"📬 <b>Ваши персональные новости за сегодня:</b>\n\n"
        f"Найдено {len(articles)} статей по вашим темам.",
        parse_mode="HTML"
    )

    # Send each article with keyboard
    for i, article in enumerate(articles, 1):
        text = format_article_message(article, index=i)
        keyboard = get_article_keyboard(article.id)

        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )


# ==================== /search - Search ====================

@router.message(Command("search"))
async def cmd_search(message: Message, db: AsyncSession):
    """Search articles."""
    query = message.text.replace("/search", "").strip()

    if not query:
        await message.answer(
            "🔍 <b>Поиск по архиву</b>\n\n"
            "Введите поисковый запрос:\n"
            "Например: <i>GDPR</i>, <i>искусственный интеллект</i>, <i>налоги</i>",
            parse_mode="HTML",
            reply_markup=ForceReply(input_field_placeholder="Введите тему для поиска...")
        )
        return

    # Search
    user_id = message.from_user.id
    results = await search_publications(query, user_id=user_id, limit=10, db=db)

    if not results:
        await message.answer(
            f"По запросу '<b>{query}</b>' ничего не найдено 😔\n\n"
            "Попробуйте другой запрос или используйте /today",
            parse_mode="HTML"
        )
        return

    # Show results
    await message.answer(
        f"🔍 Найдено <b>{len(results)}</b> статей по запросу '<b>{query}</b>':",
        parse_mode="HTML"
    )

    for i, article in enumerate(results, 1):
        text = format_article_message(article, index=i)
        keyboard = get_article_keyboard(article.id)

        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )


@router.message(F.reply_to_message, F.text)
async def handle_search_reply(message: Message, db: AsyncSession):
    """Handle reply to search prompt (ForceReply)."""
    # Check if replying to bot's search message
    if (message.reply_to_message and
        message.reply_to_message.from_user.is_bot and
        "Поиск по архиву" in message.reply_to_message.text):

        query = message.text.strip()
        user_id = message.from_user.id

        # Perform search
        results = await search_publications(query, user_id=user_id, limit=10, db=db)

        if not results:
            await message.answer(
                f"По запросу '<b>{query}</b>' ничего не найдено 😔\n\n"
                "Попробуйте другой запрос или используйте /today",
                parse_mode="HTML"
            )
            return

        # Show results
        await message.answer(
            f"🔍 Найдено <b>{len(results)}</b> статей по запросу '<b>{query}</b>':",
            parse_mode="HTML"
        )

        for i, article in enumerate(results, 1):
            text = format_article_message(article, index=i)
            keyboard = get_article_keyboard(article.id)

            await message.answer(
                text,
                parse_mode="HTML",
                reply_markup=keyboard
            )


# ==================== /saved - Saved Articles ====================

@router.message(Command("saved"))
async def cmd_saved(message: Message, db: AsyncSession):
    """Show saved articles."""
    user_id = message.from_user.id
    saved = await get_saved_articles(user_id, limit=20, db=db)

    if not saved:
        await message.answer(
            "🔖 У вас пока нет сохранённых статей.\n\n"
            "Используйте кнопку '🔖 Сохранить' под статьёй чтобы добавить в избранное."
        )
        return

    await message.answer(
        f"🔖 <b>Ваши сохранённые статьи</b> ({len(saved)}):",
        parse_mode="HTML"
    )

    for i, article in enumerate(saved, 1):
        text = format_article_message(article, index=i)
        keyboard = get_article_keyboard(article.id, user_saved=True)

        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )


# ==================== Feedback Callbacks ====================

@router.callback_query(F.data.startswith("feedback:"))
async def process_feedback(callback: CallbackQuery, db: AsyncSession):
    """Handle like/dislike feedback."""
    _, action, article_id = callback.data.split(":")
    user_id = callback.from_user.id

    is_useful = (action == "like")

    # Save feedback
    await save_user_feedback(
        user_id=user_id,
        publication_id=int(article_id),
        is_useful=is_useful,
        db=db
    )

    if is_useful:
        await callback.answer("✅ Спасибо за отзыв!")
    else:
        # Ask for reason
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Слишком сложно", callback_data=f"feedback_type:too_complex:{article_id}")],
            [InlineKeyboardButton(text="Не по моей теме", callback_data=f"feedback_type:not_relevant:{article_id}")],
            [InlineKeyboardButton(text="Устаревшая информация", callback_data=f"feedback_type:outdated:{article_id}")],
            [InlineKeyboardButton(text="Слишком поверхностно", callback_data=f"feedback_type:shallow:{article_id}")],
        ])

        await callback.message.answer(
            "Что не понравилось?",
            reply_markup=keyboard
        )
        await callback.answer()


@router.callback_query(F.data.startswith("feedback_type:"))
async def save_feedback_type(callback: CallbackQuery, db: AsyncSession):
    """Save detailed feedback type."""
    _, feedback_type, article_id = callback.data.split(":")
    user_id = callback.from_user.id

    # Update feedback with type
    await save_user_feedback(
        user_id=user_id,
        publication_id=int(article_id),
        is_useful=False,
        feedback_type=feedback_type,
        db=db
    )

    await callback.message.delete()
    await callback.answer("✅ Спасибо! Учтем в рекомендациях")


# ==================== Save/Unsave Callbacks ====================

@router.callback_query(F.data.startswith("save:"))
async def save_article_callback(callback: CallbackQuery, db: AsyncSession):
    """Save article to bookmarks."""
    article_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    await save_article(user_id, article_id, db)

    # Update keyboard
    keyboard = get_article_keyboard(article_id, user_saved=True)
    await callback.message.edit_reply_markup(reply_markup=keyboard)

    await callback.answer("✅ Сохранено!")


@router.callback_query(F.data.startswith("unsave:"))
async def unsave_article_callback(callback: CallbackQuery, db: AsyncSession):
    """Remove article from bookmarks."""
    article_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    await unsave_article(user_id, article_id, db)

    # Update keyboard
    keyboard = get_article_keyboard(article_id, user_saved=False)
    await callback.message.edit_reply_markup(reply_markup=keyboard)

    await callback.answer("❌ Удалено из сохранённых")


@router.callback_query(F.data.startswith("view:"))
async def view_article_callback(callback: CallbackQuery, db: AsyncSession):
    """Show full article text."""
    article_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    # Get publication with draft
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    from app.models.database import Publication

    result = await db.execute(
        select(Publication)
        .options(joinedload(Publication.draft))
        .where(Publication.id == article_id)
    )
    article = result.scalar_one_or_none()

    if not article or not article.draft:
        await callback.answer("❌ Статья не найдена", show_alert=True)
        return

    # Check if saved
    from app.services.reader_service import get_saved_articles
    saved_articles = await get_saved_articles(user_id, db=db)
    user_saved = any(s.id == article_id for s in saved_articles)

    # Format full article
    published_date = article.published_at.strftime("%d.%m.%Y")

    full_text = (
        f"📰 <b>{article.draft.title}</b>\n\n"
        f"{article.draft.content}\n\n"
        f"👁 {article.views or 0} | 📅 {published_date}"
    )

    # Show full text with keyboard (without "Read more" button)
    keyboard = get_article_keyboard(article_id, user_saved=user_saved, show_read_button=False)

    await callback.message.edit_text(
        full_text,
        parse_mode="HTML",
        reply_markup=keyboard
    )

    await callback.answer()


# ==================== /settings ====================

@router.message(Command("settings"))
async def cmd_settings(message: Message, db: AsyncSession):
    """Show user settings and stats."""
    user_id = message.from_user.id
    profile = await get_user_profile(user_id, db)

    if not profile:
        await message.answer("Сначала завершите настройку: /start")
        return

    # Get stats
    stats = await get_user_stats(user_id, db)

    # Format topics
    topic_labels = {
        'gdpr': 'GDPR',
        'ai_law': 'ИИ в праве',
        'crypto': 'Криптовалюты',
        'corporate': 'Корпоративное право',
        'tax': 'Налоги',
        'ip': 'Интеллектуальная собственность'
    }
    topics_text = ', '.join([topic_labels.get(t, t) for t in profile.topics]) if profile.topics else 'не выбраны'

    expertise_labels = {
        'student': 'Студент',
        'lawyer': 'Практикующий юрист',
        'in_house': 'In-house юрист',
        'business': 'Бизнес'
    }

    digest_labels = {
        'daily': 'Ежедневно',
        'twice_week': '2 раза в неделю',
        'weekly': 'Еженедельно',
        'never': 'Не получать'
    }

    await message.answer(
        f"⚙️ <b>Ваши настройки</b>\n\n"
        f"<b>Профиль:</b>\n"
        f"📋 Темы: {topics_text}\n"
        f"🎓 Уровень: {expertise_labels.get(profile.expertise_level, 'не указан')}\n"
        f"📬 Дайджесты: {digest_labels[profile.digest_frequency]}\n\n"
        f"<b>Статистика:</b>\n"
        f"👁 Просмотрено статей: {stats.get('articles_viewed', 0)}\n"
        f"💬 Дано отзывов: {stats.get('feedback_given', 0)}\n"
        f"🔖 Сохранено: {stats.get('articles_saved', 0)}\n"
        f"👍 Понравилось: {stats.get('positive_feedback', 0)}\n\n"
        f"<i>Для изменения настроек используйте /start</i>",
        parse_mode="HTML"
    )
