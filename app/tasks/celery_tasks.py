"""
Celery Tasks
Асинхронные задачи для автоматизации workflow.

Расписание:
- 09:00 MSK - ежедневный сбор и обработка новостей
- 17:00 MSK (пятница) - еженедельный подкаст (Phase 2+)
"""

import asyncio
import sys

# КРИТИЧНО: Отключаем uvloop для Celery worker
# uvloop привязывается к event loop и вызывает "Event loop is closed" при asyncio.run()
# Устанавливаем стандартную asyncio policy до любых импортов asyncpg
if 'celery' in sys.argv[0] or 'celery' in ' '.join(sys.argv):
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

from datetime import datetime, timedelta
from typing import Dict, Any

from celery import Celery
from celery.schedules import crontab
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.fetcher import fetch_news
from app.modules.cleaner import clean_news
from app.modules.ai_core import process_articles_with_ai
from app.modules.media_factory import create_media_for_drafts
# НЕ импортируем bot и send_draft_for_review здесь!
# Bot() создаёт aiohttp клиент который привязывается к event loop
# Импортируем их внутри async функций где они нужны
from app.models.database import PostDraft

import structlog

logger = structlog.get_logger()


# Инициализация Celery
app = Celery('legal_ai_news')

# Конфигурация Celery
app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer=settings.celery_task_serializer,
    result_serializer=settings.celery_result_serializer,
    accept_content=settings.celery_accept_content,
    timezone=settings.celery_timezone,
    enable_utc=settings.celery_enable_utc,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    # КРИТИЧНО: Принудительная установка threads pool
    worker_pool='threads',
    worker_concurrency=1,
)


# ====================
# Утилитарные функции
# ====================

def run_async(coro):
    """
    Запустить асинхронную корутину в синхронном контексте.
    Использует asyncio.run() для чистого выполнения (Python 3.11+).

    Args:
        coro: Корутина для выполнения

    Returns:
        Результат выполнения
    """
    # asyncio.run() автоматически создаёт новый event loop,
    # выполняет корутину и ПРАВИЛЬНО закрывает все ресурсы
    return asyncio.run(coro)


async def notify_admin(message: str, bot=None):
    """
    Отправить уведомление администратору.

    Args:
        message: Текст уведомления
        bot: Опциональный экземпляр Bot (для использования в Celery tasks)
    """
    try:
        if bot is None:
            # Импортируем get_bot ЗДЕСЬ чтобы избежать создания aiohttp клиента при импорте модуля
            from app.bot.handlers import get_bot
            bot = get_bot()

        await bot.send_message(
            chat_id=settings.telegram_admin_id,
            text=message,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error("admin_notification_error", error=str(e))


# ====================
# Задачи
# ====================

async def send_fetch_statistics(stats: dict):
    """
    Отправить детальную статистику сбора новостей администратору.

    Args:
        stats: Словарь с количеством новостей по источникам
    """
    try:
        from app.bot.handlers import get_bot
        from app.config import settings

        total_articles = sum(stats.values())
        source_count = len(stats)

        # Формируем детальное сообщение
        message = "📊 <b>Статистика сбора новостей</b>\n\n"

        message += f"📰 <b>Всего собрано:</b> {total_articles} статей\n"
        message += f"📡 <b>Источников обработано:</b> {source_count}\n\n"

        if stats:
            message += "📋 <b>По источникам:</b>\n"
            # Сортируем по количеству (от большего к меньшему)
            sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)

            for source_name, count in sorted_stats:
                if count > 0:
                    message += f"  ✅ <b>{source_name}:</b> {count} шт.\n"
                else:
                    message += f"  ⚠️ <b>{source_name}:</b> нет новых\n"

            # Топ-3 источника
            top_sources = sorted_stats[:3]
            if top_sources and top_sources[0][1] > 0:
                message += f"\n🏆 <b>Топ-3 источника:</b>\n"
                for i, (source_name, count) in enumerate(top_sources, 1):
                    if count > 0:
                        message += f"  {i}. {source_name} ({count})\n"
        else:
            message += "⚠️ <i>Новости не найдены</i>\n"

        message += f"\n⏱️ <i>Время сбора: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"

        bot = get_bot()
        await bot.send_message(
            chat_id=settings.telegram_admin_id,
            text=message,
            parse_mode="HTML"
        )

        logger.info("fetch_statistics_sent", total=total_articles, sources=source_count)

    except Exception as e:
        logger.error("send_fetch_statistics_error", error=str(e))
        # Не падаем если статистика не отправилась


@app.task(max_retries=3, autoretry_for=(Exception,), retry_backoff=60, retry_backoff_max=600)
def fetch_news_task():
    """
    Задача сбора новостей из всех источников.

    Запуск: ежедневно в 09:00 MSK
    """
    logger.info("fetch_news_task_started")

    async def fetch():
        # Создаём новый engine внутри asyncio.run() контекста
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy.pool import NullPool
        from app.config import settings

        # КРИТИЧНО: Используем NullPool вместо обычного пула
        # NullPool НЕ кэширует соединения и закрывает их сразу
        # Это предотвращает RuntimeError: Event loop is closed при garbage collection
        engine = create_async_engine(
            settings.database_url,
            echo=settings.debug,
            poolclass=NullPool,  # Отключаем пул соединений
        )

        SessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        try:
            async with SessionLocal() as session:
                stats = await fetch_news(session)

            # Отправляем статистику админу
            await send_fetch_statistics(stats)

            return stats
        finally:
            # Закрываем engine ДО выхода из asyncio.run()
            await engine.dispose()

    stats = run_async(fetch())

    logger.info("fetch_news_task_completed", stats=stats)

    # НЕ используем log_to_db в Celery - она использует глобальный AsyncSessionLocal
    # который привязан к старому event loop
    # Вместо этого логируем только в structlog

    return f"Fetched {sum(stats.values())} articles from {len(stats)} sources"


@app.task(max_retries=3, autoretry_for=(Exception,), retry_backoff=60, retry_backoff_max=600)
def clean_news_task():
    """
    Задача фильтрации и дедупликации новостей.

    Запуск: ежедневно в 09:10 MSK (через 10 минут после fetch)
    """
    logger.info("clean_news_task_started")

    async def clean():
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy.pool import NullPool
        from app.config import settings

        engine = create_async_engine(
            settings.database_url,
            poolclass=NullPool,
        )

        SessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        try:
            async with SessionLocal() as session:
                stats = await clean_news(session)
            return stats
        finally:
            await engine.dispose()

    stats = run_async(clean())

    logger.info("clean_news_task_completed", stats=stats)

    # НЕ используем log_to_db - она использует глобальный AsyncSessionLocal

    return f"Filtered: {stats['filtered']}, Rejected: {stats['rejected']}"


@app.task(max_retries=3, autoretry_for=(Exception,), retry_backoff=60, retry_backoff_max=600)
def analyze_articles_task():
    """
    Задача AI анализа и генерации драфтов.

    Запуск: ежедневно в 09:15 MSK (через 15 минут после fetch)
    """
    logger.info("analyze_articles_task_started")

    async def analyze():
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy.pool import NullPool
        from app.config import settings

        engine = create_async_engine(
            settings.database_url,
            poolclass=NullPool,
        )

        SessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        try:
            async with SessionLocal() as session:
                stats = await process_articles_with_ai(session)
            return stats
        finally:
            await engine.dispose()

    stats = run_async(analyze())

    logger.info("analyze_articles_task_completed", stats=stats)

    # НЕ используем log_to_db - она использует глобальный AsyncSessionLocal

    return f"Created {stats['drafts_created']} drafts"


@app.task(max_retries=3, autoretry_for=(Exception,), retry_backoff=60, retry_backoff_max=600)
def generate_media_task():
    """
    Задача генерации медиа (обложек) для драфтов.

    Запуск: ежедневно в 09:20 MSK (через 20 минут после fetch)
    """
    logger.info("generate_media_task_started")

    async def generate():
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        from sqlalchemy.pool import NullPool
        from app.config import settings

        engine = create_async_engine(
            settings.database_url,
            poolclass=NullPool,
        )

        SessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        try:
            async with SessionLocal() as session:
                count = await create_media_for_drafts(session)
            return count
        finally:
            await engine.dispose()

    count = run_async(generate())

    logger.info("generate_media_task_completed", count=count)

    return f"Generated {count} covers"


@app.task()
def send_drafts_to_admin_task():
    """
    Задача отправки драфтов администратору на модерацию.

    Запуск: ежедневно в 09:25 MSK (через 25 минут после fetch)
    """
    try:
        logger.info("send_drafts_to_admin_task_started")

        async def send_drafts():
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
            from sqlalchemy.pool import NullPool
            from sqlalchemy import select
            from app.config import settings
            from aiogram import Bot
            # Импортируем send_draft_for_review ЗДЕСЬ чтобы избежать создания Bot() при импорте модуля
            from app.bot.handlers import send_draft_for_review

            # Создаём Bot ВНУТРИ asyncio.run() контекста
            # чтобы aiohttp клиент привязался к правильному event loop
            bot = Bot(token=settings.telegram_bot_token)

            engine = create_async_engine(
                settings.database_url,
                poolclass=NullPool,
            )

            SessionLocal = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            try:
                async with SessionLocal() as session:
                    # Получаем драфты в статусе pending_review, созданные СЕГОДНЯ
                    # Фильтруем по началу текущего дня (00:00 UTC), чтобы отправлялись только свежие драфты
                    from datetime import date
                    today_start = datetime.combine(date.today(), datetime.min.time())  # 00:00 UTC сегодня

                    result = await session.execute(
                        select(PostDraft)
                        .where(
                            PostDraft.status == 'pending_review',
                            PostDraft.created_at >= today_start
                        )
                        .order_by(PostDraft.created_at.desc())
                    )
                    drafts = list(result.scalars().all())

                    if not drafts:
                        await notify_admin("📭 Нет новых драфтов сегодня.", bot=bot)
                        return 0

                    # Отправляем уведомление
                    await notify_admin(
                        f"📝 <b>Новые драфты готовы к модерации!</b>\n\n"
                        f"Количество: {len(drafts)}\n"
                        f"Используйте /drafts для просмотра.",
                        bot=bot
                    )

                    # Отправляем каждый драфт (ограничиваем настройкой publisher_max_posts_per_day)
                    max_drafts = min(len(drafts), settings.publisher_max_posts_per_day)
                    for index, draft in enumerate(drafts[:max_drafts], start=1):
                        await send_draft_for_review(
                            settings.telegram_admin_id,
                            draft,
                            session,
                            bot=bot,
                            draft_number=index  # Порядковый номер за день
                        )
                        await asyncio.sleep(1)  # Rate limiting

                    return max_drafts
            finally:
                # Закрываем Bot сессию перед закрытием engine
                await bot.session.close()
                await engine.dispose()

        count = run_async(send_drafts())

        logger.info("send_drafts_to_admin_task_completed", count=count)

        return f"Sent {count} drafts to admin"

    except Exception as exc:
        logger.error("send_drafts_to_admin_task_error", error=str(exc))
        # Не делаем retry для этой задачи
        return "Error sending drafts"


@app.task(name="daily_workflow_task")
def daily_workflow_task():
    """
    Полный ежедневный workflow.

    Последовательно запускает:
    1. fetch_news_task
    2. clean_news_task
    3. analyze_articles_task
    4. generate_media_task
    5. send_drafts_to_admin_task

    Запуск: ежедневно в 09:00 MSK
    """
    from celery import chain

    logger.info("daily_workflow_task_started")

    try:
        # Создаем цепочку задач для последовательного выполнения
        # Используем .si() (immutable signature) вместо .s() потому что
        # задачи не принимают результат предыдущей задачи как аргумент
        workflow = chain(
            fetch_news_task.si(),
            clean_news_task.si(),
            analyze_articles_task.si(),
            generate_media_task.si(),
            send_drafts_to_admin_task.si()
        )

        # Запускаем цепочку
        result = workflow.apply_async()

        logger.info("daily_workflow_task_chain_started", task_id=result.id)

        # Отправляем уведомление о запуске
        async def send_notification():
            from aiogram import Bot
            bot = Bot(token=settings.telegram_bot_token)
            try:
                await notify_admin(
                    "🔄 <b>Ежедневный workflow запущен!</b>\n\n"
                    "Ожидайте завершения через 10-15 минут.\n"
                    "Проверьте новые драфты с помощью /drafts",
                    bot=bot
                )
            finally:
                await bot.session.close()

        run_async(send_notification())

        return f"Daily workflow chain started: {result.id}"

    except Exception as e:
        logger.error("daily_workflow_task_error", error=str(e))

        async def send_error_notification():
            from aiogram import Bot
            bot = Bot(token=settings.telegram_bot_token)
            try:
                await notify_admin(
                    f"❌ <b>Ошибка в ежедневном workflow!</b>\n\n"
                    f"Ошибка: {str(e)}",
                    bot=bot
                )
            finally:
                await bot.session.close()

        run_async(send_error_notification())

        raise


# ====================
# Расписание задач
# ====================

app.conf.beat_schedule = {
    # БУДНИЕ ДНИ (Понедельник-Пятница): 3 генерации в день
    # Утренняя генерация: 09:00 MSK
    'weekday-morning-workflow': {
        'task': 'daily_workflow_task',
        'schedule': crontab(hour=9, minute=0, day_of_week='1-5'),  # Пн-Пт 09:00
    },
    # Дневная генерация: 13:00 MSK
    'weekday-afternoon-workflow': {
        'task': 'daily_workflow_task',
        'schedule': crontab(hour=13, minute=0, day_of_week='1-5'),  # Пн-Пт 13:00
    },
    # Вечерняя генерация: 17:00 MSK
    'weekday-evening-workflow': {
        'task': 'daily_workflow_task',
        'schedule': crontab(hour=17, minute=0, day_of_week='1-5'),  # Пн-Пт 17:00
    },

    # ВЫХОДНЫЕ (Суббота-Воскресенье): 1 итоговая генерация
    # Утренняя генерация: 10:00 MSK
    'weekend-workflow': {
        'task': 'daily_workflow_task',
        'schedule': crontab(hour=10, minute=0, day_of_week='0,6'),  # Сб-Вс 10:00
    },
}



# ====================
# Векторизация публикаций
# ====================

@app.task(max_retries=3, autoretry_for=(Exception,), retry_backoff=60)
def vectorize_publication_task(pub_id: int, content: str, draft_id: int):
    """
    Векторизация опубликованного поста в Qdrant.

    Args:
        pub_id: ID публикации
        content: Текст поста
        draft_id: ID драфта
    """
    logger.info("vectorize_publication_task_started", pub_id=pub_id, draft_id=draft_id)

    async def vectorize():
        from app.modules.vector_search import get_vector_search
        from datetime import datetime

        try:
            vector_search = get_vector_search()
            await vector_search.add_publication(
                pub_id=pub_id,
                content=content,
                published_at=datetime.utcnow(),
                reactions={}
            )
            logger.info("vectorize_publication_task_success", pub_id=pub_id, draft_id=draft_id)
            return {"status": "success", "pub_id": pub_id}
        except Exception as e:
            logger.error("vectorize_publication_task_error", pub_id=pub_id, error=str(e))
            raise

    try:
        result = asyncio.run(vectorize())
        return result
    except Exception as e:
        logger.error("vectorize_publication_task_failed", pub_id=pub_id, error=str(e))
        raise


# ====================
# Команды для ручного запуска
# ====================

@app.task(name="manual_fetch")
def manual_fetch():
    """Ручной запуск сбора новостей."""
    return fetch_news_task.delay()


@app.task(name="manual_workflow")
def manual_workflow():
    """Ручной запуск полного workflow."""
    return daily_workflow_task.delay()
