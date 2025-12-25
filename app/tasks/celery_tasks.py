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

from datetime import datetime
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


async def notify_admin(message: str):
    """
    Отправить уведомление администратору.

    Args:
        message: Текст уведомления
    """
    try:
        # Импортируем get_bot ЗДЕСЬ чтобы избежать создания aiohttp клиента при импорте модуля
        from app.bot.handlers import get_bot

        await get_bot().send_message(
            chat_id=settings.telegram_admin_id,
            text=message,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error("admin_notification_error", error=str(e))


# ====================
# Задачи
# ====================

@app.task(bind=True, max_retries=3)
def fetch_news_task(self):
    """
    Задача сбора новостей из всех источников.

    Запуск: ежедневно в 09:00 MSK
    """
    try:
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

    except Exception as exc:
        logger.error("fetch_news_task_error", error=str(exc))

        # Retry с экспоненциальной задержкой
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


@app.task(bind=True, max_retries=3)
def clean_news_task(self):
    """
    Задача фильтрации и дедупликации новостей.

    Запуск: ежедневно в 09:10 MSK (через 10 минут после fetch)
    """
    try:
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

    except Exception as exc:
        logger.error("clean_news_task_error", error=str(exc))
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


@app.task(bind=True, max_retries=3)
def analyze_articles_task(self):
    """
    Задача AI анализа и генерации драфтов.

    Запуск: ежедневно в 09:15 MSK (через 15 минут после fetch)
    """
    try:
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

    except Exception as exc:
        logger.error("analyze_articles_task_error", error=str(exc))
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


@app.task(bind=True, max_retries=3)
def generate_media_task(self):
    """
    Задача генерации медиа (обложек) для драфтов.

    Запуск: ежедневно в 09:20 MSK (через 20 минут после fetch)
    """
    try:
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

    except Exception as exc:
        logger.error("generate_media_task_error", error=str(exc))
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


@app.task(bind=True)
def send_drafts_to_admin_task(self):
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
            # Импортируем send_draft_for_review ЗДЕСЬ чтобы избежать создания Bot() при импорте модуля
            from app.bot.handlers import send_draft_for_review

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
                    # Получаем драфты в статусе pending_review
                    result = await session.execute(
                        select(PostDraft)
                        .where(PostDraft.status == 'pending_review')
                        .order_by(PostDraft.created_at.desc())
                    )
                    drafts = list(result.scalars().all())

                    if not drafts:
                        await notify_admin("📭 Нет новых драфтов сегодня.")
                        return 0

                    # Отправляем уведомление
                    await notify_admin(
                        f"📝 <b>Новые драфты готовы к модерации!</b>\n\n"
                        f"Количество: {len(drafts)}\n"
                        f"Используйте /drafts для просмотра."
                    )

                    # Отправляем каждый драфт
                    for draft in drafts[:5]:  # Ограничиваем 5 за раз
                        await send_draft_for_review(
                            settings.telegram_admin_id,
                            draft,
                            session
                        )
                        await asyncio.sleep(1)  # Rate limiting

                    return len(drafts)
            finally:
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
        workflow = chain(
            fetch_news_task.s(),
            clean_news_task.s(),
            analyze_articles_task.s(),
            generate_media_task.s(),
            send_drafts_to_admin_task.s()
        )

        # Запускаем цепочку
        result = workflow.apply_async()

        logger.info("daily_workflow_task_chain_started", task_id=result.id)

        # Отправляем уведомление о запуске
        run_async(notify_admin(
            "🔄 <b>Ежедневный workflow запущен!</b>\n\n"
            "Ожидайте завершения через 10-15 минут.\n"
            "Проверьте новые драфты с помощью /drafts"
        ))

        return f"Daily workflow chain started: {result.id}"

    except Exception as e:
        logger.error("daily_workflow_task_error", error=str(e))

        run_async(notify_admin(
            f"❌ <b>Ошибка в ежедневном workflow!</b>\n\n"
            f"Ошибка: {str(e)}"
        ))

        raise


# ====================
# Расписание задач
# ====================

app.conf.beat_schedule = {
    # Ежедневный workflow в 09:00 MSK
    'daily-workflow': {
        'task': 'daily_workflow_task',
        'schedule': crontab(hour=9, minute=0),  # 09:00 MSK
    },

    # Альтернативно: запуск отдельных задач по расписанию
    # 'fetch-news-daily': {
    #     'task': 'app.tasks.celery_tasks.fetch_news_task',
    #     'schedule': crontab(hour=9, minute=0),
    # },
    # 'clean-news-daily': {
    #     'task': 'app.tasks.celery_tasks.clean_news_task',
    #     'schedule': crontab(hour=9, minute=10),
    # },
    # 'analyze-articles-daily': {
    #     'task': 'app.tasks.celery_tasks.analyze_articles_task',
    #     'schedule': crontab(hour=9, minute=15),
    # },
    # 'generate-media-daily': {
    #     'task': 'app.tasks.celery_tasks.generate_media_task',
    #     'schedule': crontab(hour=9, minute=20),
    # },
    # 'send-drafts-daily': {
    #     'task': 'app.tasks.celery_tasks.send_drafts_to_admin_task',
    #     'schedule': crontab(hour=9, minute=25),
    # },
}


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
