"""
Settings Manager - управление системными настройками через БД.
Все настройки хранятся в таблице system_settings и могут быть изменены через UI.
"""

import json
from typing import Any, Optional, Dict, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import SystemSettings
import structlog

logger = structlog.get_logger()


# ====================
# Default Settings
# ====================

DEFAULT_SETTINGS = {
    # Источники новостей
    "sources.google_news_ru.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Google News RSS (RU)"},
    "sources.google_news_en.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Google News RSS (EN)"},
    "sources.habr.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Habr - Новости"},
    "sources.perplexity_ru.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Perplexity Search (RU)"},
    "sources.perplexity_en.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Perplexity Search (EN)"},
    "sources.telegram_channels.enabled": {"value": False, "type": "bool", "category": "sources", "description": "Telegram каналы"},

    # Модели LLM
    "llm.analysis.model": {"value": "gpt-4o", "type": "string", "category": "llm", "description": "Модель для AI анализа"},
    "llm.draft_generation.model": {"value": "sonar", "type": "string", "category": "llm", "description": "Модель для генерации драфтов"},
    "llm.ranking.model": {"value": "gpt-4o-mini", "type": "string", "category": "llm", "description": "Модель для ranking статей"},

    # DALL-E генерация
    "dalle.enabled": {"value": False, "type": "bool", "category": "media", "description": "Включить DALL-E генерацию"},
    "dalle.model": {"value": "dall-e-3", "type": "string", "category": "media", "description": "Модель DALL-E"},
    "dalle.quality": {"value": "standard", "type": "string", "category": "media", "description": "Качество (standard/hd)"},
    "dalle.size": {"value": "1024x1024", "type": "string", "category": "media", "description": "Размер изображения"},
    "dalle.auto_generate": {"value": False, "type": "bool", "category": "media", "description": "Автоматически для всех постов"},
    "dalle.ask_on_review": {"value": True, "type": "bool", "category": "media", "description": "Спрашивать при модерации"},

    # Автопубликация
    "auto_publish.enabled": {"value": False, "type": "bool", "category": "publishing", "description": "Включить автопубликацию"},
    "auto_publish.mode": {"value": "best_time", "type": "string", "category": "publishing", "description": "Режим (best_time/schedule/even)"},
    "auto_publish.max_per_day": {"value": 3, "type": "int", "category": "publishing", "description": "Макс постов/день"},
    "auto_publish.weekdays_only": {"value": False, "type": "bool", "category": "publishing", "description": "Только в будни"},
    "auto_publish.skip_holidays": {"value": False, "type": "bool", "category": "publishing", "description": "Пропускать праздники"},

    # Уведомления
    "alerts.low_engagement.enabled": {"value": True, "type": "bool", "category": "alerts", "description": "Падение engagement"},
    "alerts.low_engagement.threshold": {"value": 20.0, "type": "float", "category": "alerts", "description": "Порог engagement %"},
    "alerts.viral_post.enabled": {"value": True, "type": "bool", "category": "alerts", "description": "Viral пост"},
    "alerts.viral_post.threshold": {"value": 100, "type": "int", "category": "alerts", "description": "Порог просмотров"},
    "alerts.low_approval.enabled": {"value": True, "type": "bool", "category": "alerts", "description": "Низкий approval rate"},
    "alerts.low_approval.threshold": {"value": 30.0, "type": "float", "category": "alerts", "description": "Порог approval %"},
    "alerts.fetch_errors.enabled": {"value": True, "type": "bool", "category": "alerts", "description": "Ошибки сбора"},
    "alerts.api_limits.enabled": {"value": True, "type": "bool", "category": "alerts", "description": "Лимиты API"},

    # Фильтрация и качество
    "quality.min_score": {"value": 0.6, "type": "float", "category": "quality", "description": "Мин. quality score"},
    "quality.min_content_length": {"value": 300, "type": "int", "category": "quality", "description": "Мин. длина контента"},
    "quality.similarity_threshold": {"value": 0.85, "type": "float", "category": "quality", "description": "Порог схожести"},
    "quality.languages": {"value": ["ru", "en"], "type": "json", "category": "quality", "description": "Разрешённые языки"},

    # Бюджет
    "budget.max_per_month": {"value": 10.0, "type": "float", "category": "budget", "description": "Макс $/месяц"},
    "budget.warning_threshold": {"value": 8.0, "type": "float", "category": "budget", "description": "Предупреждение при $ (80%)"},
    "budget.stop_on_exceed": {"value": False, "type": "bool", "category": "budget", "description": "Остановить при превышении"},
    "budget.switch_to_cheap": {"value": True, "type": "bool", "category": "budget", "description": "Переключиться на дешевые модели"},
}


# ====================
# Core Functions
# ====================

async def init_default_settings(db: AsyncSession) -> None:
    """
    Инициализация дефолтных настроек в БД (если их еще нет).
    Вызывается при старте приложения.
    """
    for key, config in DEFAULT_SETTINGS.items():
        # Проверяем существует ли настройка
        result = await db.execute(
            select(SystemSettings).where(SystemSettings.key == key)
        )
        existing = result.scalar_one_or_none()

        if not existing:
            # Создаём новую настройку
            value_str = _serialize_value(config["value"], config["type"])

            setting = SystemSettings(
                key=key,
                value=value_str,
                type=config["type"],
                category=config["category"],
                description=config["description"]
            )
            db.add(setting)

    await db.commit()
    logger.info("default_settings_initialized", count=len(DEFAULT_SETTINGS))


async def get_setting(key: str, db: AsyncSession, default: Any = None) -> Any:
    """
    Получить значение настройки.

    Args:
        key: Ключ настройки (например "sources.google_news_ru.enabled")
        db: Database session
        default: Значение по умолчанию если настройка не найдена

    Returns:
        Значение настройки (типизированное)
    """
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == key)
    )
    setting = result.scalar_one_or_none()

    if not setting:
        return default

    return _deserialize_value(setting.value, setting.type)


async def set_setting(key: str, value: Any, db: AsyncSession) -> None:
    """
    Установить значение настройки.

    Args:
        key: Ключ настройки
        value: Новое значение
        db: Database session
    """
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key == key)
    )
    setting = result.scalar_one_or_none()

    if not setting:
        raise ValueError(f"Setting '{key}' not found. Initialize defaults first.")

    # Сериализуем значение
    setting.value = _serialize_value(value, setting.type)

    await db.commit()
    logger.info("setting_updated", key=key, value=value)


async def get_category_settings(category: str, db: AsyncSession) -> Dict[str, Any]:
    """
    Получить все настройки категории.

    Args:
        category: Категория (sources, llm, media, publishing, alerts, quality, budget)
        db: Database session

    Returns:
        Dict с настройками категории
    """
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.category == category)
    )
    settings = result.scalars().all()

    return {
        s.key: _deserialize_value(s.value, s.type)
        for s in settings
    }


async def get_all_settings(db: AsyncSession) -> Dict[str, Any]:
    """Получить все настройки."""
    result = await db.execute(select(SystemSettings))
    settings = result.scalars().all()

    return {
        s.key: {
            "value": _deserialize_value(s.value, s.type),
            "type": s.type,
            "category": s.category,
            "description": s.description
        }
        for s in settings
    }


# ====================
# Typed Getters (для удобства)
# ====================

async def is_source_enabled(source_key: str, db: AsyncSession) -> bool:
    """Проверить включен ли источник новостей."""
    key = f"sources.{source_key}.enabled"
    return await get_setting(key, db, default=True)


async def get_llm_model(operation: str, db: AsyncSession) -> str:
    """
    Получить модель LLM для операции.

    Args:
        operation: analysis, draft_generation, ranking
    """
    key = f"llm.{operation}.model"
    defaults = {
        "analysis": "gpt-4o",
        "draft_generation": "sonar",
        "ranking": "gpt-4o-mini"
    }
    return await get_setting(key, db, default=defaults.get(operation, "gpt-4o-mini"))


async def is_dalle_enabled(db: AsyncSession) -> bool:
    """Проверить включена ли генерация DALL-E."""
    return await get_setting("dalle.enabled", db, default=False)


async def get_dalle_config(db: AsyncSession) -> Dict[str, Any]:
    """Получить конфигурацию DALL-E."""
    return {
        "enabled": await get_setting("dalle.enabled", db, default=False),
        "model": await get_setting("dalle.model", db, default="dall-e-3"),
        "quality": await get_setting("dalle.quality", db, default="standard"),
        "size": await get_setting("dalle.size", db, default="1024x1024"),
        "auto_generate": await get_setting("dalle.auto_generate", db, default=False),
        "ask_on_review": await get_setting("dalle.ask_on_review", db, default=True),
    }


async def is_auto_publish_enabled(db: AsyncSession) -> bool:
    """Проверить включена ли автопубликация."""
    return await get_setting("auto_publish.enabled", db, default=False)


async def get_auto_publish_config(db: AsyncSession) -> Dict[str, Any]:
    """Получить конфигурацию автопубликации."""
    return {
        "enabled": await get_setting("auto_publish.enabled", db, default=False),
        "mode": await get_setting("auto_publish.mode", db, default="best_time"),
        "max_per_day": await get_setting("auto_publish.max_per_day", db, default=3),
        "weekdays_only": await get_setting("auto_publish.weekdays_only", db, default=False),
        "skip_holidays": await get_setting("auto_publish.skip_holidays", db, default=False),
    }


async def get_enabled_sources(db: AsyncSession) -> List[str]:
    """Получить список включенных источников."""
    sources = await get_category_settings("sources", db)

    enabled = []
    for key, value in sources.items():
        if value and key.endswith(".enabled"):
            # Извлекаем название источника из ключа
            # sources.google_news_ru.enabled -> google_news_ru
            source_name = key.replace("sources.", "").replace(".enabled", "")
            enabled.append(source_name)

    return enabled


# ====================
# Helper Functions
# ====================

def _serialize_value(value: Any, value_type: str) -> str:
    """Сериализация значения в строку для хранения в БД."""
    if value_type == "bool":
        return "true" if value else "false"
    elif value_type == "json":
        return json.dumps(value, ensure_ascii=False)
    else:
        return str(value)


def _deserialize_value(value_str: str, value_type: str) -> Any:
    """Десериализация значения из строки."""
    if value_type == "bool":
        return value_str.lower() == "true"
    elif value_type == "int":
        return int(value_str)
    elif value_type == "float":
        return float(value_str)
    elif value_type == "json":
        return json.loads(value_str)
    else:  # string
        return value_str
