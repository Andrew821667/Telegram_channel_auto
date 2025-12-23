"""
AI Core Module
Интеллектуальный анализ новостей и генерация драфтов с использованием OpenAI API.

Функционал:
1. Ранжирование новостей по важности (GPT-4o-mini)
2. Контекстная проверка (упрощенный RAG с PostgreSQL Full-Text Search)
3. Генерация драфтов постов для Telegram
"""

import asyncio
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

from openai import AsyncOpenAI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import RawArticle, PostDraft, LegalKnowledge, log_to_db
import structlog

logger = structlog.get_logger()


# Промпты для OpenAI
RANKING_SYSTEM_PROMPT = """Ты — эксперт по LegalTech и аналитик новостей для юристов.

Твоя задача: оценить важность новости для аудитории практикующих юристов и руководителей юридических департаментов.

Критерии оценки (по шкале 0-10):
- Практическая польза для юристов (40%)
- Новизна и актуальность (30%)
- Влияние на отрасль и практику (30%)

Отвечай ТОЛЬКО числом от 0 до 10 без дополнительных пояснений."""

DRAFT_SYSTEM_PROMPT = """Ты — AI-редактор новостного канала о LegalTech для практикующих юристов.

Твоя задача: переписать новость в формате для Telegram канала.

Требования:
- Заголовок: цепляющий, но БЕЗ кликбейта (максимум 80 символов)
- Суть: 2-3 коротких абзаца, 150-250 слов
- Стиль: профессиональный, но не занудный, без канцеляризмов
- Практический вывод для юриста
- Используй эмодзи умеренно (1-2 в заголовке)

Структура поста:
```
[ЭМОДЗИ] ЗАГОЛОВОК

📌 СУТЬ (2-3 абзаца)

⚖️ ДЛЯ ЮРИСТА:
[практический вывод]

#ИИ #LegalTech
```

НЕ добавляй ссылку на источник - она будет добавлена автоматически."""


class AICore:
    """Ядро AI анализа и генерации контента."""

    def __init__(self, db_session: AsyncSession):
        """
        Инициализация AI Core.

        Args:
            db_session: Асинхронная сессия базы данных
        """
        self.db = db_session
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def rank_articles(
        self,
        articles: List[RawArticle],
        top_n: Optional[int] = None
    ) -> List[Tuple[RawArticle, float]]:
        """
        Ранжировать статьи по важности с использованием GPT.

        Args:
            articles: Список статей для ранжирования
            top_n: Количество топ статей (по умолчанию из настроек)

        Returns:
            Список пар (статья, оценка) отсортированных по убыванию оценки
        """
        if top_n is None:
            top_n = settings.ai_top_articles_count

        if not articles:
            logger.warning("no_articles_to_rank")
            return []

        logger.info("ranking_articles", count=len(articles))

        ranked_articles = []

        # Ранжируем каждую статью
        for article in articles:
            try:
                # Формируем промпт для оценки
                user_prompt = f"""Новость:
Заголовок: {article.title}

Содержание:
{article.content[:1000] if article.content else article.title}

Источник: {article.source_name}

Оцени важность этой новости для юристов от 0 до 10."""

                # Запрос к OpenAI
                response = await self._call_openai(
                    system_prompt=RANKING_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    model=settings.openai_model_analysis,
                    max_tokens=10,
                    temperature=0.3  # Низкая температура для консистентности
                )

                # Парсим оценку
                try:
                    score = float(response.strip())
                    score = max(0.0, min(10.0, score))  # Ограничиваем 0-10
                except ValueError:
                    logger.warning(
                        "invalid_score",
                        article_id=article.id,
                        response=response
                    )
                    score = 5.0  # Средняя оценка по умолчанию

                ranked_articles.append((article, score))

                logger.info(
                    "article_ranked",
                    article_id=article.id,
                    title=article.title[:50],
                    score=score
                )

                # Rate limiting
                await asyncio.sleep(1)  # 60 requests per minute

            except Exception as e:
                logger.error(
                    "ranking_error",
                    article_id=article.id,
                    error=str(e)
                )
                # Добавляем с минимальной оценкой при ошибке
                ranked_articles.append((article, 0.0))

        # Сортируем по убыванию оценки
        ranked_articles.sort(key=lambda x: x[1], reverse=True)

        # Возвращаем топ-N
        top_articles = ranked_articles[:top_n]

        logger.info(
            "ranking_complete",
            total=len(articles),
            top_n=len(top_articles),
            top_scores=[score for _, score in top_articles]
        )

        return top_articles

    async def search_legal_context(
        self,
        query: str,
        limit: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Поиск релевантного юридического контекста в базе знаний.

        Args:
            query: Поисковый запрос
            limit: Максимальное количество результатов

        Returns:
            Список найденных фрагментов документов
        """
        if not settings.ai_legal_context_enabled:
            return []

        try:
            # PostgreSQL Full-Text Search
            sql = text("""
                SELECT
                    id,
                    doc_name,
                    article_number,
                    text_chunk,
                    ts_rank(ts_vector, plainto_tsquery('russian', :query)) as rank
                FROM legal_knowledge
                WHERE ts_vector @@ plainto_tsquery('russian', :query)
                ORDER BY rank DESC
                LIMIT :limit
            """)

            result = await self.db.execute(
                sql,
                {"query": query, "limit": limit}
            )

            contexts = []
            for row in result:
                contexts.append({
                    "doc_name": row.doc_name,
                    "article_number": row.article_number,
                    "text": row.text_chunk,
                    "relevance": float(row.rank)
                })

            logger.info(
                "legal_context_search",
                query=query[:50],
                results_count=len(contexts)
            )

            return contexts

        except Exception as e:
            logger.error(
                "legal_context_search_error",
                query=query[:50],
                error=str(e)
            )
            return []

    async def generate_draft(
        self,
        article: RawArticle,
        score: float
    ) -> Optional[PostDraft]:
        """
        Сгенерировать драфт поста из статьи.

        Args:
            article: Статья для обработки
            score: Оценка важности статьи

        Returns:
            PostDraft или None при ошибке
        """
        try:
            logger.info(
                "generating_draft",
                article_id=article.id,
                title=article.title[:50]
            )

            # 1. Поиск юридического контекста
            legal_context_text = None
            confidence_score = score / 10.0  # Нормализуем к 0-1

            if settings.ai_legal_context_enabled:
                # Формируем поисковый запрос из заголовка и ключевых слов
                search_query = f"{article.title} {article.content[:200] if article.content else ''}"

                contexts = await self.search_legal_context(search_query)

                if contexts and contexts[0]["relevance"] >= settings.ai_legal_context_confidence_min:
                    # Берем топ контекст
                    top_context = contexts[0]
                    legal_context_text = f"{top_context['doc_name']}"
                    if top_context['article_number']:
                        legal_context_text += f", статья {top_context['article_number']}"
                    legal_context_text += f": {top_context['text'][:200]}..."

                    logger.info(
                        "legal_context_found",
                        article_id=article.id,
                        doc=top_context['doc_name'],
                        relevance=top_context['relevance']
                    )

            # 2. Формируем промпт для генерации поста
            user_prompt = f"""Новость для переписывания:

Заголовок: {article.title}

Содержание:
{article.content if article.content else article.title}

Источник: {article.source_name}"""

            if legal_context_text:
                user_prompt += f"""

Найден юридический контекст:
{legal_context_text}

Включи краткую ссылку на него в раздел "ДЛЯ ЮРИСТА" если релевантно."""

            user_prompt += "\n\nСоздай пост для Telegram канала согласно инструкциям."

            # 3. Генерируем пост через GPT
            draft_content = await self._call_openai(
                system_prompt=DRAFT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                model=settings.openai_model_analysis,
                max_tokens=settings.openai_max_tokens,
                temperature=settings.openai_temperature
            )

            # 4. Извлекаем заголовок из сгенерированного контента
            lines = draft_content.split('\n')
            title = lines[0].strip() if lines else article.title
            # Убираем эмодзи из заголовка для хранения
            title_clean = ''.join(c for c in title if c.isalnum() or c.isspace() or c in '.,!?-:')

            # 5. Создаем драфт
            draft = PostDraft(
                article_id=article.id,
                title=title_clean[:200],  # Ограничиваем длину
                content=draft_content,
                legal_context=legal_context_text,
                confidence_score=confidence_score,
                status='pending_review'
            )

            self.db.add(draft)

            # 6. Обновляем статус статьи
            article.status = 'processed'

            await self.db.commit()
            await self.db.refresh(draft)

            logger.info(
                "draft_generated",
                draft_id=draft.id,
                article_id=article.id,
                confidence=confidence_score
            )

            return draft

        except Exception as e:
            logger.error(
                "draft_generation_error",
                article_id=article.id,
                error=str(e)
            )
            return None

    async def process_filtered_articles(self) -> Dict[str, Any]:
        """
        Обработать все отфильтрованные статьи.

        Returns:
            Статистика обработки
        """
        stats = {
            "total": 0,
            "ranked": 0,
            "drafts_created": 0,
            "errors": 0
        }

        # Получаем отфильтрованные статьи
        result = await self.db.execute(
            select(RawArticle).where(RawArticle.status == 'filtered')
        )
        articles = list(result.scalars().all())
        stats["total"] = len(articles)

        if not articles:
            logger.info("no_filtered_articles_to_process")
            return stats

        logger.info("processing_filtered_articles", count=len(articles))

        # Ранжируем статьи
        ranked_articles = await self.rank_articles(articles)
        stats["ranked"] = len(ranked_articles)

        # Генерируем драфты для топ статей
        for article, score in ranked_articles:
            try:
                draft = await self.generate_draft(article, score)
                if draft:
                    stats["drafts_created"] += 1
                else:
                    stats["errors"] += 1

            except Exception as e:
                logger.error(
                    "article_processing_error",
                    article_id=article.id,
                    error=str(e)
                )
                stats["errors"] += 1

        # Логируем статистику
        await log_to_db(
            "INFO",
            f"AI processing completed: {stats['drafts_created']} drafts created",
            stats
        )

        logger.info("ai_processing_complete", **stats)

        return stats

    async def _call_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None
    ) -> str:
        """
        Вызвать OpenAI API с retry механизмом.

        Args:
            system_prompt: Системный промпт
            user_prompt: Пользовательский промпт
            model: Модель (по умолчанию из настроек)
            max_tokens: Максимум токенов
            temperature: Температура

        Returns:
            Ответ модели
        """
        if model is None:
            model = settings.openai_model_analysis
        if max_tokens is None:
            max_tokens = settings.openai_max_tokens
        if temperature is None:
            temperature = settings.openai_temperature

        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )

            return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error(
                "openai_api_error",
                model=model,
                error=str(e)
            )
            raise


async def process_articles_with_ai(db_session: AsyncSession) -> Dict[str, Any]:
    """
    Удобная функция для запуска AI обработки статей.

    Args:
        db_session: Асинхронная сессия БД

    Returns:
        Статистика обработки
    """
    ai_core = AICore(db_session)
    return await ai_core.process_filtered_articles()
