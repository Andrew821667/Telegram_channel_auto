"""
Celery Tasks
Асинхронные задачи для автоматизации workflow.

Расписание:
- 09:00 MSK - ежедневный сбор и обработка новостей
- 17:00 MSK (пятница) - еженедельный подкаст (Phase 2+)
"""

import asyncio
from datetime import datetime
from typing import Dict, Any

from celery import Celery
from celery.schedules import crontab
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import AsyncSessionLocal, log_to_db
from app.modules.fetcher import fetch_news
from app.modules.cleaner import clean_news
from app.modules.ai_core import process_articles_with_ai
from app.modules.media_factory import create_media_for_drafts
from app.bot.handlers import bot, send_draft_for_review
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
)


# ====================
# Утилитарные функции
# ====================

def run_async(coro):
    """
    Запустить асинхронную корутину в синхронном контексте.
    Использует существующий event loop или создаёт новый.

    Args:
        coro: Корутина для выполнения

    Returns:
        Результат выполнения
    """
    try:
        # Пытаемся получить текущий event loop
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            # Если закрыт - создаём новый
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        # Если нет event loop - создаём новый
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        return loop.run_until_complete(coro)
    finally:
        # НЕ закрываем loop - переиспользуем его
        pass


async def notify_admin(message: str):
    """
    Отправить уведомление администратору.

    Args:
        message: Текст уведомления
    """
    try:
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

@app.task(bind=True, max_retries=3)
def fetch_news_task(self):
    """
    Задача сбора новостей из всех источников.

    Запуск: ежедневно в 09:00 MSK
    """
    try:
        logger.info("fetch_news_task_started")

        async def fetch():
            async with AsyncSessionLocal() as session:
                stats = await fetch_news(session)
                return stats

        stats = run_async(fetch())

        logger.info("fetch_news_task_completed", stats=stats)

        # Логируем в БД
        run_async(log_to_db(
            "INFO",
            f"Fetch task completed: {sum(stats.values())} articles",
            {"stats": stats}
        ))

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
            async with AsyncSessionLocal() as session:
                stats = await clean_news(session)
                return stats

        stats = run_async(clean())

        logger.info("clean_news_task_completed", stats=stats)

        run_async(log_to_db(
            "INFO",
            f"Cleaning completed: {stats['filtered']} filtered, {stats['rejected']} rejected",
            stats
        ))

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
            async with AsyncSessionLocal() as session:
                stats = await process_articles_with_ai(session)
                return stats

        stats = run_async(analyze())

        logger.info("analyze_articles_task_completed", stats=stats)

        run_async(log_to_db(
            "INFO",
            f"AI analysis completed: {stats['drafts_created']} drafts created",
            stats
        ))

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
            async with AsyncSessionLocal() as session:
                count = await create_media_for_drafts(session)
                return count

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
            async with AsyncSessionLocal() as session:
                # Получаем драфты в статусе pending_review
                from sqlalchemy import select
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
