"""
Analytics Module
Модуль для сбора и анализа метрик канала.

Функционал:
1. Статистика за период (публикации, реакции, engagement)
2. Топ лучших и худших постов
3. Анализ эффективности источников
4. Статистика по дням недели
5. Статистика векторной базы Qdrant
"""

from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy import text, func
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

logger = structlog.get_logger()


class AnalyticsService:
    """Сервис для работы с аналитикой канала."""

    def __init__(self, db: AsyncSession):
        """
        Инициализация сервиса аналитики.

        Args:
            db: Асинхронная сессия базы данных
        """
        self.db = db

    async def get_period_stats(self, days: int = 7) -> Dict:
        """
        Получить общую статистику за период.

        Args:
            days: Количество дней для анализа

        Returns:
            Словарь с метриками
        """
        try:
            date_from = datetime.utcnow() - timedelta(days=days)

            # Количество публикаций
            query_pubs = text("""
                SELECT COUNT(*) as total_publications
                FROM publications
                WHERE published_at >= :date_from
            """)
            result_pubs = await self.db.execute(query_pubs, {"date_from": date_from})
            total_pubs = result_pubs.scalar() or 0

            # Количество драфтов (одобренных и отклоненных)
            query_drafts = text("""
                SELECT
                    COUNT(*) as total_drafts,
                    COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved_drafts,
                    COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected_drafts
                FROM post_drafts
                WHERE created_at >= :date_from
            """)
            result_drafts = await self.db.execute(query_drafts, {"date_from": date_from})
            drafts_row = result_drafts.fetchone()

            total_drafts = drafts_row.total_drafts or 0
            approved_drafts = drafts_row.approved_drafts or 0
            rejected_drafts = drafts_row.rejected_drafts or 0

            # Агрегация реакций
            query_reactions = text("""
                SELECT
                    SUM(COALESCE((reactions->>'useful')::int, 0)) as useful,
                    SUM(COALESCE((reactions->>'important')::int, 0)) as important,
                    SUM(COALESCE((reactions->>'controversial')::int, 0)) as controversial,
                    SUM(COALESCE((reactions->>'banal')::int, 0)) as banal,
                    SUM(COALESCE((reactions->>'obvious')::int, 0)) as obvious,
                    SUM(COALESCE((reactions->>'poor_quality')::int, 0)) as poor_quality
                FROM publications
                WHERE published_at >= :date_from
            """)
            result_reactions = await self.db.execute(query_reactions, {"date_from": date_from})
            reactions_row = result_reactions.fetchone()

            reactions = {
                "useful": reactions_row.useful or 0,
                "important": reactions_row.important or 0,
                "controversial": reactions_row.controversial or 0,
                "banal": reactions_row.banal or 0,
                "obvious": reactions_row.obvious or 0,
                "poor_quality": reactions_row.poor_quality or 0
            }

            total_reactions = sum(reactions.values())

            # Средний quality score
            if total_pubs > 0:
                query_avg_score = text("""
                    SELECT AVG(
                        (
                            COALESCE((reactions->>'useful')::int, 0) +
                            COALESCE((reactions->>'important')::int, 0) -
                            COALESCE((reactions->>'banal')::int, 0) -
                            COALESCE((reactions->>'obvious')::int, 0) -
                            COALESCE((reactions->>'poor_quality')::int, 0)
                        )::float / NULLIF(
                            COALESCE((reactions->>'useful')::int, 0) +
                            COALESCE((reactions->>'important')::int, 0) +
                            COALESCE((reactions->>'banal')::int, 0) +
                            COALESCE((reactions->>'obvious')::int, 0) +
                            COALESCE((reactions->>'poor_quality')::int, 0) +
                            COALESCE((reactions->>'controversial')::int, 0),
                            0
                        )
                    ) as avg_quality_score
                    FROM publications
                    WHERE published_at >= :date_from
                    AND (
                        COALESCE((reactions->>'useful')::int, 0) +
                        COALESCE((reactions->>'important')::int, 0) +
                        COALESCE((reactions->>'banal')::int, 0) +
                        COALESCE((reactions->>'obvious')::int, 0) +
                        COALESCE((reactions->>'poor_quality')::int, 0) +
                        COALESCE((reactions->>'controversial')::int, 0)
                    ) > 0
                """)
                result_avg = await self.db.execute(query_avg_score, {"date_from": date_from})
                avg_quality_score = result_avg.scalar() or 0.0
            else:
                avg_quality_score = 0.0

            return {
                "period_days": days,
                "total_publications": total_pubs,
                "total_drafts": total_drafts,
                "approved_drafts": approved_drafts,
                "rejected_drafts": rejected_drafts,
                "approval_rate": (approved_drafts / total_drafts * 100) if total_drafts > 0 else 0,
                "reactions": reactions,
                "total_reactions": total_reactions,
                "avg_quality_score": round(avg_quality_score, 2)
            }

        except Exception as e:
            logger.error("get_period_stats_error", error=str(e), days=days)
            raise

    async def get_top_posts(self, limit: int = 3, days: int = 7) -> List[Dict]:
        """
        Получить топ постов по quality score.

        Args:
            limit: Максимум результатов
            days: Количество дней для анализа

        Returns:
            Список постов с метриками
        """
        try:
            date_from = datetime.utcnow() - timedelta(days=days)

            query = text("""
                SELECT
                    p.id,
                    d.title,
                    d.content,
                    p.published_at,
                    p.message_id as telegram_message_id,
                    p.reactions,
                    (
                        COALESCE((p.reactions->>'useful')::int, 0) +
                        COALESCE((p.reactions->>'important')::int, 0) -
                        COALESCE((p.reactions->>'banal')::int, 0) -
                        COALESCE((p.reactions->>'obvious')::int, 0) -
                        COALESCE((p.reactions->>'poor_quality')::int, 0)
                    )::float / NULLIF(
                        COALESCE((p.reactions->>'useful')::int, 0) +
                        COALESCE((p.reactions->>'important')::int, 0) +
                        COALESCE((p.reactions->>'banal')::int, 0) +
                        COALESCE((p.reactions->>'obvious')::int, 0) +
                        COALESCE((p.reactions->>'poor_quality')::int, 0) +
                        COALESCE((p.reactions->>'controversial')::int, 0),
                        0
                    ) as quality_score,
                    (
                        COALESCE((p.reactions->>'useful')::int, 0) +
                        COALESCE((p.reactions->>'important')::int, 0) +
                        COALESCE((p.reactions->>'banal')::int, 0) +
                        COALESCE((p.reactions->>'obvious')::int, 0) +
                        COALESCE((p.reactions->>'poor_quality')::int, 0) +
                        COALESCE((p.reactions->>'controversial')::int, 0)
                    ) as total_reactions
                FROM publications p
                JOIN post_drafts d ON p.draft_id = d.id
                WHERE p.published_at >= :date_from
                AND (
                    COALESCE((p.reactions->>'useful')::int, 0) +
                    COALESCE((p.reactions->>'important')::int, 0) +
                    COALESCE((p.reactions->>'banal')::int, 0) +
                    COALESCE((p.reactions->>'obvious')::int, 0) +
                    COALESCE((p.reactions->>'poor_quality')::int, 0) +
                    COALESCE((p.reactions->>'controversial')::int, 0)
                ) > 0
                ORDER BY quality_score DESC, total_reactions DESC
                LIMIT :limit
            """)

            result = await self.db.execute(query, {
                "date_from": date_from,
                "limit": limit
            })

            posts = []
            for row in result.fetchall():
                posts.append({
                    "id": row.id,
                    "title": row.title,
                    "content": row.content,
                    "published_at": row.published_at,
                    "telegram_message_id": row.telegram_message_id,
                    "reactions": row.reactions or {},
                    "quality_score": round(row.quality_score or 0, 2),
                    "total_reactions": row.total_reactions or 0
                })

            return posts

        except Exception as e:
            logger.error("get_top_posts_error", error=str(e), limit=limit, days=days)
            return []

    async def get_worst_posts(self, limit: int = 3, days: int = 7) -> List[Dict]:
        """
        Получить худшие посты по quality score.

        Args:
            limit: Максимум результатов
            days: Количество дней для анализа

        Returns:
            Список постов с метриками
        """
        try:
            date_from = datetime.utcnow() - timedelta(days=days)

            query = text("""
                SELECT
                    p.id,
                    d.title,
                    d.content,
                    p.published_at,
                    p.message_id as telegram_message_id,
                    p.reactions,
                    (
                        COALESCE((p.reactions->>'useful')::int, 0) +
                        COALESCE((p.reactions->>'important')::int, 0) -
                        COALESCE((p.reactions->>'banal')::int, 0) -
                        COALESCE((p.reactions->>'obvious')::int, 0) -
                        COALESCE((p.reactions->>'poor_quality')::int, 0)
                    )::float / NULLIF(
                        COALESCE((p.reactions->>'useful')::int, 0) +
                        COALESCE((p.reactions->>'important')::int, 0) +
                        COALESCE((p.reactions->>'banal')::int, 0) +
                        COALESCE((p.reactions->>'obvious')::int, 0) +
                        COALESCE((p.reactions->>'poor_quality')::int, 0) +
                        COALESCE((p.reactions->>'controversial')::int, 0),
                        0
                    ) as quality_score,
                    (
                        COALESCE((p.reactions->>'useful')::int, 0) +
                        COALESCE((p.reactions->>'important')::int, 0) +
                        COALESCE((p.reactions->>'banal')::int, 0) +
                        COALESCE((p.reactions->>'obvious')::int, 0) +
                        COALESCE((p.reactions->>'poor_quality')::int, 0) +
                        COALESCE((p.reactions->>'controversial')::int, 0)
                    ) as total_reactions
                FROM publications p
                JOIN post_drafts d ON p.draft_id = d.id
                WHERE p.published_at >= :date_from
                AND (
                    COALESCE((p.reactions->>'banal')::int, 0) +
                    COALESCE((p.reactions->>'obvious')::int, 0) +
                    COALESCE((p.reactions->>'poor_quality')::int, 0)
                ) > 0
                ORDER BY quality_score ASC, total_reactions DESC
                LIMIT :limit
            """)

            result = await self.db.execute(query, {
                "date_from": date_from,
                "limit": limit
            })

            posts = []
            for row in result.fetchall():
                posts.append({
                    "id": row.id,
                    "title": row.title,
                    "content": row.content,
                    "published_at": row.published_at,
                    "telegram_message_id": row.telegram_message_id,
                    "reactions": row.reactions or {},
                    "quality_score": round(row.quality_score or 0, 2),
                    "total_reactions": row.total_reactions or 0
                })

            return posts

        except Exception as e:
            logger.error("get_worst_posts_error", error=str(e), limit=limit, days=days)
            return []

    async def get_source_stats(self, days: int = 7) -> List[Dict]:
        """
        Получить статистику по источникам.

        Args:
            days: Количество дней для анализа

        Returns:
            Список источников с метриками
        """
        try:
            date_from = datetime.utcnow() - timedelta(days=days)

            # Статистика по сырым статьям (собрано новостей)
            query_raw = text("""
                SELECT
                    source_name,
                    COUNT(*) as total_collected
                FROM raw_articles
                WHERE fetched_at >= :date_from
                GROUP BY source_name
                ORDER BY total_collected DESC
            """)
            result_raw = await self.db.execute(query_raw, {"date_from": date_from})

            sources_collected = {}
            for row in result_raw.fetchall():
                sources_collected[row.source_name] = row.total_collected

            # Статистика по опубликованным постам
            query_pubs = text("""
                SELECT
                    a.source_name,
                    COUNT(*) as total_published,
                    AVG(
                        (
                            COALESCE((p.reactions->>'useful')::int, 0) +
                            COALESCE((p.reactions->>'important')::int, 0) -
                            COALESCE((p.reactions->>'banal')::int, 0) -
                            COALESCE((p.reactions->>'obvious')::int, 0) -
                            COALESCE((p.reactions->>'poor_quality')::int, 0)
                        )::float / NULLIF(
                            COALESCE((p.reactions->>'useful')::int, 0) +
                            COALESCE((p.reactions->>'important')::int, 0) +
                            COALESCE((p.reactions->>'banal')::int, 0) +
                            COALESCE((p.reactions->>'obvious')::int, 0) +
                            COALESCE((p.reactions->>'poor_quality')::int, 0) +
                            COALESCE((p.reactions->>'controversial')::int, 0),
                            0
                        )
                    ) as avg_quality_score
                FROM publications p
                JOIN post_drafts d ON p.draft_id = d.id
                JOIN raw_articles a ON d.article_id = a.id
                WHERE p.published_at >= :date_from
                GROUP BY a.source_name
            """)
            result_pubs = await self.db.execute(query_pubs, {"date_from": date_from})

            sources = []
            for row in result_pubs.fetchall():
                source_name = row.source_name
                total_collected = sources_collected.get(source_name, 0)
                total_published = row.total_published or 0

                sources.append({
                    "source_name": source_name,
                    "total_collected": total_collected,
                    "total_published": total_published,
                    "publication_rate": (total_published / total_collected * 100) if total_collected > 0 else 0,
                    "avg_quality_score": round(row.avg_quality_score or 0, 2)
                })

            # Сортировка по качеству
            sources.sort(key=lambda x: x["avg_quality_score"], reverse=True)

            return sources

        except Exception as e:
            logger.error("get_source_stats_error", error=str(e), days=days)
            return []

    async def get_weekday_stats(self, days: int = 30) -> Dict:
        """
        Получить статистику по дням недели.

        Args:
            days: Количество дней для анализа (минимум 7)

        Returns:
            Словарь с метриками по дням недели
        """
        try:
            date_from = datetime.utcnow() - timedelta(days=days)

            query = text("""
                SELECT
                    EXTRACT(DOW FROM published_at) as day_of_week,
                    COUNT(*) as total_posts,
                    AVG(
                        (
                            COALESCE((reactions->>'useful')::int, 0) +
                            COALESCE((reactions->>'important')::int, 0) -
                            COALESCE((reactions->>'banal')::int, 0) -
                            COALESCE((reactions->>'obvious')::int, 0) -
                            COALESCE((reactions->>'poor_quality')::int, 0)
                        )::float / NULLIF(
                            COALESCE((reactions->>'useful')::int, 0) +
                            COALESCE((reactions->>'important')::int, 0) +
                            COALESCE((reactions->>'banal')::int, 0) +
                            COALESCE((reactions->>'obvious')::int, 0) +
                            COALESCE((reactions->>'poor_quality')::int, 0) +
                            COALESCE((reactions->>'controversial')::int, 0),
                            0
                        )
                    ) as avg_quality_score
                FROM publications
                WHERE published_at >= :date_from
                GROUP BY day_of_week
                ORDER BY day_of_week
            """)

            result = await self.db.execute(query, {"date_from": date_from})

            # Маппинг дней недели (0=Воскресенье, 1=Понедельник, ...)
            day_names = {
                0: "Вс",
                1: "Пн",
                2: "Вт",
                3: "Ср",
                4: "Чт",
                5: "Пт",
                6: "Сб"
            }

            weekday_stats = {}
            for row in result.fetchall():
                day_num = int(row.day_of_week)
                day_name = day_names.get(day_num, "??")

                weekday_stats[day_name] = {
                    "total_posts": row.total_posts or 0,
                    "avg_quality_score": round(row.avg_quality_score or 0, 2)
                }

            return weekday_stats

        except Exception as e:
            logger.error("get_weekday_stats_error", error=str(e), days=days)
            return {}

    async def get_vector_db_stats(self) -> Optional[Dict]:
        """
        Получить статистику векторной базы Qdrant.

        Returns:
            Словарь с метриками Qdrant или None если недоступен
        """
        try:
            from app.modules.vector_search import get_vector_search
            from app.config import settings

            if not getattr(settings, "qdrant_enabled", False):
                return None

            vector_search = get_vector_search()

            # Получить информацию о коллекции
            collection_info = vector_search.client.get_collection(
                collection_name=vector_search.COLLECTION_NAME
            )

            total_vectors = collection_info.points_count

            # Подсчитать векторы с разными quality_score
            # Scroll через все точки и агрегировать
            positive_count = 0
            negative_count = 0
            neutral_count = 0
            total_score = 0.0

            offset = None
            while True:
                results, next_offset = vector_search.client.scroll(
                    collection_name=vector_search.COLLECTION_NAME,
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False
                )

                if not results:
                    break

                for point in results:
                    quality_score = point.payload.get("quality_score", 0.0)
                    total_score += quality_score

                    if quality_score > 0.5:
                        positive_count += 1
                    elif quality_score < -0.3:
                        negative_count += 1
                    else:
                        neutral_count += 1

                offset = next_offset
                if offset is None:
                    break

            avg_score = (total_score / total_vectors) if total_vectors > 0 else 0.0

            return {
                "total_vectors": total_vectors,
                "positive_examples": positive_count,
                "negative_examples": negative_count,
                "neutral_examples": neutral_count,
                "avg_quality_score": round(avg_score, 2)
            }

        except Exception as e:
            logger.error("get_vector_db_stats_error", error=str(e))
            return None
