"""
Mini App API Router
Endpoints for Telegram Mini App interface.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
import structlog
import json

from app.models.database import (
    get_db,
    PostDraft,
    Publication,
    RawArticle,
    SystemSettings,
)
from app.modules.settings_manager import get_setting, set_setting
from app.config import settings as app_settings

logger = structlog.get_logger()

router = APIRouter(prefix="/api/miniapp", tags=["miniapp"])


# ====================
# Auth Middleware
# ====================

async def verify_telegram_user(
    x_telegram_init_data: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """Verify Telegram WebApp init data."""
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram auth data")

    try:
        init_data = json.loads(x_telegram_init_data)
        user = init_data.get("user")

        if not user:
            raise HTTPException(status_code=401, detail="Invalid auth data")

        # In production, verify hash with bot token
        # For now, just return user data
        return user
    except json.JSONDecodeError:
        raise HTTPException(status_code=401, detail="Invalid auth data format")


# ====================
# Dashboard
# ====================

@router.get("/dashboard/stats")
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    user: Dict = Depends(verify_telegram_user)
):
    """Get dashboard statistics."""
    try:
        # Total drafts pending review
        total_drafts = await db.scalar(
            select(func.count(PostDraft.id)).where(
                PostDraft.status == 'pending_review'
            )
        )

        # Total published
        total_published = await db.scalar(
            select(func.count(Publication.id))
        )

        # Average quality score of published articles (from drafts)
        avg_quality_result = await db.execute(
            select(func.avg(PostDraft.confidence_score))
            .join(Publication, Publication.draft_id == PostDraft.id)
            .where(PostDraft.confidence_score.isnot(None))
        )
        avg_quality = avg_quality_result.scalar() or 0.0

        # Total views
        total_views = await db.scalar(
            select(func.sum(Publication.views)).where(
                Publication.views.isnot(None)
            )
        ) or 0

        # Total reactions - need to count from all publications manually since reactions is JSONB
        pubs_result = await db.execute(select(Publication))
        all_pubs = pubs_result.scalars().all()
        total_reactions = 0
        for pub in all_pubs:
            if pub.reactions and isinstance(pub.reactions, dict):
                total_reactions += sum(pub.reactions.values())

        # Engagement rate
        engagement_rate = (total_reactions / total_views * 100) if total_views > 0 else 0.0

        # Articles published today
        today = datetime.utcnow().date()
        articles_today = await db.scalar(
            select(func.count(Publication.id)).where(
                func.date(Publication.published_at) == today
            )
        )

        # Top sources - join with draft and article
        top_sources_query = (
            select(
                RawArticle.source_name,
                func.count(Publication.id).label('count')
            )
            .join(PostDraft, Publication.draft_id == PostDraft.id)
            .join(RawArticle, PostDraft.article_id == RawArticle.id)
            .group_by(RawArticle.source_name)
            .order_by(desc('count'))
            .limit(5)
        )

        result = await db.execute(top_sources_query)
        top_sources = [
            {"source": row[0], "count": row[1]}
            for row in result.all()
        ]

        return {
            "total_drafts": total_drafts or 0,
            "total_published": total_published or 0,
            "avg_quality_score": round(float(avg_quality), 2),
            "total_views": total_views,
            "total_reactions": total_reactions,
            "engagement_rate": round(engagement_rate, 2),
            "articles_today": articles_today or 0,
            "top_sources": top_sources,
        }

    except Exception as e:
        logger.error("dashboard_stats_error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to load dashboard stats")


# ====================
# Drafts Management
# ====================

@router.get("/drafts")
async def get_drafts(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: Dict = Depends(verify_telegram_user)
):
    """Get list of draft articles for moderation."""
    try:
        query = (
            select(PostDraft)
            .options(joinedload(PostDraft.article))
            .where(PostDraft.status == 'pending_review')
            .order_by(desc(PostDraft.created_at))
            .limit(limit)
            .offset(offset)
        )

        result = await db.execute(query)
        drafts = result.scalars().all()

        return [
            {
                "id": draft.id,
                "title": draft.title,
                "content": draft.content,
                "source": draft.article.source_name if draft.article else "unknown",
                "ai_summary": draft.content[:200] + "..." if len(draft.content) > 200 else draft.content,  # First 200 chars as summary
                "quality_score": draft.confidence_score or 0.0,
                "created_at": draft.created_at.isoformat(),
                "status": draft.status,
                "tags": [],  # Not implemented yet
                "sentiment": "neutral",  # Not implemented yet
            }
            for draft in drafts
        ]

    except Exception as e:
        logger.error("get_drafts_error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to load drafts")


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
    user: Dict = Depends(verify_telegram_user)
):
    """Get single draft by ID."""
    try:
        result = await db.execute(
            select(PostDraft).options(joinedload(PostDraft.article)).where(PostDraft.id == draft_id)
        )
        draft = result.scalar_one_or_none()

        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        return {
            "id": draft.id,
            "title": draft.title,
            "content": draft.content,
            "source": draft.article.source_name if draft.article else "unknown",
            "original_url": draft.article.url if draft.article else None,
            "ai_summary": draft.content[:200] + "..." if len(draft.content) > 200 else draft.content,
            "quality_score": draft.confidence_score or 0.0,
            "created_at": draft.created_at.isoformat(),
            "status": draft.status,
            "tags": [],  # Not implemented yet
            "sentiment": "neutral",  # Not implemented yet
            "categories": [],  # Not implemented yet
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_draft_error", draft_id=draft_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to load draft")


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
    user: Dict = Depends(verify_telegram_user)
):
    """Approve draft for publication."""
    try:
        draft = await db.get(PostDraft, draft_id)

        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        draft.status = 'approved'
        await db.commit()

        logger.info("draft_approved", draft_id=draft_id, user_id=user.get('id'))

        return {"success": True, "message": "Draft approved"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("approve_draft_error", draft_id=draft_id, error=str(e))
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to approve draft")


@router.post("/drafts/{draft_id}/reject")
async def reject_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
    user: Dict = Depends(verify_telegram_user)
):
    """Reject draft."""
    try:
        draft = await db.get(PostDraft, draft_id)

        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        draft.status = 'rejected'
        await db.commit()

        logger.info("draft_rejected", draft_id=draft_id, user_id=user.get('id'))

        return {"success": True, "message": "Draft rejected"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("reject_draft_error", draft_id=draft_id, error=str(e))
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to reject draft")


# ====================
# Published Articles
# ====================

@router.get("/published")
async def get_published(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: Dict = Depends(verify_telegram_user)
):
    """Get list of published articles."""
    try:
        query = (
            select(Publication)
            .options(
                joinedload(Publication.draft).joinedload(PostDraft.article)
            )
            .order_by(desc(Publication.published_at))
            .limit(limit)
            .offset(offset)
        )

        result = await db.execute(query)
        publications = result.scalars().all()

        # Calculate total reactions from JSONB
        def get_total_reactions(reactions_dict):
            if not reactions_dict:
                return 0
            return sum(reactions_dict.values()) if isinstance(reactions_dict, dict) else 0

        return [
            {
                "id": pub.id,
                "title": pub.draft.title if pub.draft else "No title",
                "content": pub.draft.content if pub.draft else "",
                "published_at": pub.published_at.isoformat(),
                "views": pub.views or 0,
                "reactions": get_total_reactions(pub.reactions),
                "engagement_rate": (get_total_reactions(pub.reactions) / pub.views * 100) if pub.views and pub.views > 0 else 0,
                "source": pub.draft.article.source_name if pub.draft and pub.draft.article else "unknown",
                "quality_score": pub.draft.confidence_score if pub.draft else 0.0,
            }
            for pub in publications
        ]

    except Exception as e:
        logger.error("get_published_error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to load published articles")


@router.get("/published/stats")
async def get_published_stats(
    period: str = "7d",
    db: AsyncSession = Depends(get_db),
    user: Dict = Depends(verify_telegram_user)
):
    """Get published articles statistics for a period."""
    try:
        # Parse period
        days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 7)
        since = datetime.utcnow() - timedelta(days=days)

        # Total articles in period
        total = await db.scalar(
            select(func.count(Publication.id)).where(
                Publication.published_at >= since
            )
        )

        # Total views and reactions
        views = await db.scalar(
            select(func.sum(Publication.views)).where(
                and_(
                    Publication.published_at >= since,
                    Publication.views.isnot(None)
                )
            )
        ) or 0

        reactions = await db.scalar(
            select(func.sum(Publication.reactions)).where(
                and_(
                    Publication.published_at >= since,
                    Publication.reactions.isnot(None)
                )
            )
        ) or 0

        # Average quality score
        avg_quality = await db.scalar(
            select(func.avg(Publication.quality_score)).where(
                and_(
                    Publication.published_at >= since,
                    Publication.quality_score.isnot(None)
                )
            )
        ) or 0.0

        # Top performing articles
        top_articles_query = (
            select(Publication)
            .where(Publication.published_at >= since)
            .order_by(desc(Publication.views))
            .limit(10)
        )

        result = await db.execute(top_articles_query)
        top_articles = [
            {
                "id": pub.id,
                "title": pub.title,
                "views": pub.views,
                "reactions": pub.reactions,
                "published_at": pub.published_at.isoformat(),
            }
            for pub in result.scalars().all()
        ]

        return {
            "period": period,
            "total_articles": total or 0,
            "total_views": views,
            "total_reactions": reactions,
            "avg_quality_score": round(float(avg_quality), 2),
            "engagement_rate": round((reactions / views * 100) if views > 0 else 0, 2),
            "top_articles": top_articles,
        }

    except Exception as e:
        logger.error("published_stats_error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to load published stats")


# ====================
# System Settings
# ====================

@router.get("/settings")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    user: Dict = Depends(verify_telegram_user)
):
    """Get all system settings."""
    try:


        # Get all settings grouped by category
        sources = {
            "google_news_ru": await get_setting("sources.google_news_ru.enabled", db, True),
            "google_news_en": await get_setting("sources.google_news_en.enabled", db, True),
            "habr": await get_setting("sources.habr.enabled", db, True),
            "perplexity_ru": await get_setting("sources.perplexity_ru.enabled", db, True),
            "perplexity_en": await get_setting("sources.perplexity_en.enabled", db, True),
            "telegram_channels": await get_setting("sources.telegram_channels.enabled", db, False),
        }

        llm_models = {
            "analysis": await get_setting("llm.analysis.model", db, "gpt-4o"),
            "draft_generation": await get_setting("llm.draft_generation.model", db, "gpt-4o-mini"),
            "ranking": await get_setting("llm.ranking.model", db, "gpt-4o-mini"),
        }

        dalle = {
            "enabled": await get_setting("dalle.enabled", db, False),
            "model": await get_setting("dalle.model", db, "dall-e-3"),
            "quality": await get_setting("dalle.quality", db, "standard"),
            "size": await get_setting("dalle.size", db, "1024x1024"),
            "auto_generate": await get_setting("dalle.auto_generate", db, False),
            "ask_on_review": await get_setting("dalle.ask_on_review", db, True),
        }

        auto_publish = {
            "enabled": await get_setting("auto_publish.enabled", db, False),
            "mode": await get_setting("auto_publish.mode", db, "best_time"),
            "max_per_day": await get_setting("auto_publish.max_per_day", db, 3),
            "weekdays_only": await get_setting("auto_publish.weekdays_only", db, False),
            "skip_holidays": await get_setting("auto_publish.skip_holidays", db, False),
        }

        filtering = {
            "min_score": await get_setting("quality.min_score", db, 0.6),
            "min_content_length": await get_setting("quality.min_content_length", db, 300),
            "similarity_threshold": await get_setting("quality.similarity_threshold", db, 0.85),
        }

        budget = {
            "max_per_month": await get_setting("budget.max_per_month", db, 10.0),
            "warning_threshold": await get_setting("budget.warning_threshold", db, 8.0),
            "stop_on_exceed": await get_setting("budget.stop_on_exceed", db, False),
            "switch_to_cheap": await get_setting("budget.switch_to_cheap", db, True),
        }

        return {
            "sources": sources,
            "llm_models": llm_models,
            "dalle": dalle,
            "auto_publish": auto_publish,
            "filtering": filtering,
            "budget": budget,
        }

    except Exception as e:
        logger.error("get_settings_error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to load settings")


@router.put("/settings")
async def update_settings(
    settings_data: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user: Dict = Depends(verify_telegram_user)
):
    """Update system settings."""
    try:


        # Update sources
        if "sources" in settings_data:
            for source, enabled in settings_data["sources"].items():
                await set_setting(f"sources.{source}.enabled", enabled, db)

        # Update LLM models
        if "llm_models" in settings_data:
            for key, value in settings_data["llm_models"].items():
                await set_setting(f"llm.{key}.model", value, db)

        # Update DALL-E
        if "dalle" in settings_data:
            for key, value in settings_data["dalle"].items():
                await set_setting(f"dalle.{key}", value, db)

        # Update auto-publish
        if "auto_publish" in settings_data:
            for key, value in settings_data["auto_publish"].items():
                await set_setting(f"auto_publish.{key}", value, db)

        # Update filtering
        if "filtering" in settings_data:
            for key, value in settings_data["filtering"].items():
                await set_setting(f"quality.{key}", value, db)

        # Update budget
        if "budget" in settings_data:
            for key, value in settings_data["budget"].items():
                await set_setting(f"budget.{key}", value, db)

        await db.commit()

        logger.info("settings_updated", user_id=user.get('id'))

        return {"success": True, "message": "Settings updated"}

    except Exception as e:
        logger.error("update_settings_error", error=str(e))
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update settings")
