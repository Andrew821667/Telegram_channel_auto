DEFAULT_SETTINGS = {
    # Источники новостей
    "sources.google_news_ru.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Google News RSS (RU)"},
    "sources.google_news_en.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Google News RSS (EN)"},
    "sources.google_news_rss_ru.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Google News RU"},
    "sources.google_news_rss_en.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Google News EN"},
    "sources.habr.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Habr - Новости"},
    "sources.perplexity_ru.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Perplexity Search (RU)"},
    "sources.perplexity_en.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Perplexity Search (EN)"},
    "sources.telegram_channels.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Telegram Channels"},
    "sources.interfax.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Interfax - Наука и технологии"},
    "sources.lenta.enabled": {"value": True, "type": "bool", "category": "sources", "description": "Lenta.ru - Технологии"},
    "sources.rbc.enabled": {"value": True, "type": "bool", "category": "sources", "description": "RBC - Технологии"},
    "sources.tass.enabled": {"value": True, "type": "bool", "category": "sources", "description": "TASS - Наука и технологии"},

    # Модели LLM
    "llm.analysis.model": {"value": "gpt-4o", "type": "string", "category": "llm", "description": "Модель для AI анализа"},
    "llm.draft_generation.model": {"value": "gpt-4o-mini", "type": "string", "category": "llm", "description": "Модель для генерации драфтов"},
    "llm.ranking.model": {"value": "gpt-4o-mini", "type": "string", "category": "llm", "description": "Модель для ranking статей"},

    # DALL-E
    "dalle.enabled": {"value": False, "type": "bool", "category": "media", "description": "Включить генерацию изображений"},
    "dalle.model": {"value": "dall-e-3", "type": "string", "category": "media", "description": "Модель DALL-E"},
    "dalle.quality": {"value": "standard", "type": "string", "category": "media", "description": "Качество изображений"},
    "dalle.size": {"value": "1024x1024", "type": "string", "category": "media", "description": "Размер изображений"},
    "dalle.auto_generate": {"value": False, "type": "bool", "category": "media", "description": "Автоматическая генерация"},
    "dalle.ask_on_review": {"value": True, "type": "bool", "category": "media", "description": "Спрашивать при модерации"},

    # Автопубликация
    "auto_publish.enabled": {"value": False, "type": "bool", "category": "publishing", "description": "Включить автопубликацию"},
    "auto_publish.mode": {"value": "best_time", "type": "string", "category": "publishing", "description": "Режим автопубликации"},
    "auto_publish.max_per_day": {"value": 3, "type": "int", "category": "publishing", "description": "Максимум постов в день"},
    "auto_publish.weekdays_only": {"value": False, "type": "bool", "category": "publishing", "description": "Только будни"},
    "auto_publish.skip_holidays": {"value": False, "type": "bool", "category": "publishing", "description": "Пропускать праздники"},

    # Фильтрация
    "filtering.min_score": {"value": 0.6, "type": "float", "category": "quality", "description": "Минимальный скор качества"},
    "filtering.min_content_length": {"value": 300, "type": "int", "category": "quality", "description": "Минимальная длина контента"},
    "filtering.similarity_threshold": {"value": 0.85, "type": "float", "category": "quality", "description": "Порог схожести"},

    # Бюджет API
    "budget.max_per_month": {"value": 50, "type": "float", "category": "budget", "description": "Максимум $ в месяц"},
    "budget.warning_threshold": {"value": 40, "type": "float", "category": "budget", "description": "Порог предупреждения"},
    "budget.stop_on_exceed": {"value": False, "type": "bool", "category": "budget", "description": "Останавливать при превышении"},
    "budget.switch_to_cheap": {"value": True, "type": "bool", "category": "budget", "description": "Переходить на дешевые модели"},
}
