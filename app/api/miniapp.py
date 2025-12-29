"""
Mini App API Router
Endpoints for Telegram Mini App interface.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
import structlog
import json

from app.models.database import (
    get_db,
    PostDraft,
    Publication,
    RawArticle,
    SystemSettings,
)
from app.modules.settings_manager import SettingsManager
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

        # Average quality score of published articles
        avg_quality = await db.scalar(
            select(func.avg(Publication.quality_score)).where(
                Publication.quality_score.isnot(None)
            )
        ) or 0.0

        # Total views and reactions
        total_views = await db.scalar(
            select(func.sum(Publication.views)).where(
                Publication.views.isnot(None)
            )
        ) or 0

        total_reactions = await db.scalar(
            select(func.sum(Publication.reactions)).where(
                Publication.reactions.isnot(None)
            )
        ) or 0

        # Engagement rate
        engagement_rate = (total_reactions / total_views * 100) if total_views > 0 else 0.0

        # Articles published today
        today = datetime.utcnow().date()
        articles_today = await db.scalar(
            select(func.count(Publication.id)).where(
                func.date(Publication.published_at) == today
            )
        )

        # Top sources
        top_sources_query = select(
            Publication.source,
            func.count(Publication.id).label('count')
        ).group_by(Publication.source).order_by(desc('count')).limit(5)

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
                "source": draft.source,
                "ai_summary": draft.ai_summary,
                "quality_score": draft.quality_score,
                "created_at": draft.created_at.isoformat(),
                "status": draft.status,
                "tags": draft.tags,
                "sentiment": draft.sentiment,
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
        draft = await db.get(PostDraft, draft_id)

        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        return {
            "id": draft.id,
            "title": draft.title,
            "content": draft.content,
            "source": draft.source,
            "original_url": draft.original_url,
            "ai_summary": draft.ai_summary,
            "quality_score": draft.quality_score,
            "created_at": draft.created_at.isoformat(),
            "status": draft.status,
            "tags": draft.tags,
            "sentiment": draft.sentiment,
            "categories": draft.categories,
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
            .order_by(desc(Publication.published_at))
            .limit(limit)
            .offset(offset)
        )

        result = await db.execute(query)
        publications = result.scalars().all()

        return [
            {
                "id": pub.id,
                "title": pub.title,
                "content": pub.content,
                "published_at": pub.published_at.isoformat(),
                "views": pub.views,
                "reactions": pub.reactions,
                "engagement_rate": (pub.reactions / pub.views * 100) if pub.views else 0,
                "source": pub.source,
                "quality_score": pub.quality_score,
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
        settings_mgr = SettingsManager(db)

        # Get all settings grouped by category
        sources = {
            "google_news": await settings_mgr.get("source_google_news_enabled", True),
            "habr": await settings_mgr.get("source_habr_enabled", True),
            "perplexity": await settings_mgr.get("source_perplexity_enabled", True),
            "telegram": await settings_mgr.get("source_telegram_enabled", True),
        }

        llm_models = {
            "analysis_model": await settings_mgr.get("llm_analysis_model", "gpt-4o-mini"),
            "generation_model": await settings_mgr.get("llm_generation_model", "gpt-4o"),
            "ranking_model": await settings_mgr.get("llm_ranking_model", "gpt-4o-mini"),
        }

        dalle = {
            "enabled": await settings_mgr.get("dalle_enabled", True),
            "model": await settings_mgr.get("dalle_model", "dall-e-3"),
            "quality": await settings_mgr.get("dalle_quality", "standard"),
            "size": await settings_mgr.get("dalle_size", "1024x1024"),
        }

        auto_publish = {
            "enabled": await settings_mgr.get("auto_publish_enabled", True),
            "max_per_day": await settings_mgr.get("auto_publish_max_per_day", 5),
            "schedule": await settings_mgr.get("auto_publish_schedule", ["09:00", "14:00", "18:00"]),
        }

        filtering = {
            "min_quality_score": await settings_mgr.get("min_quality_score", 7.0),
            "min_content_length": await settings_mgr.get("min_content_length", 300),
            "similarity_threshold": await settings_mgr.get("similarity_threshold", 0.85),
        }

        budget = {
            "daily_limit": await settings_mgr.get("budget_daily_limit", 50),
            "warning_threshold": await settings_mgr.get("budget_warning_threshold", 80),
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
        settings_mgr = SettingsManager(db)

        # Update sources
        if "sources" in settings_data:
            for source, enabled in settings_data["sources"].items():
                await settings_mgr.set(f"source_{source}_enabled", enabled)

        # Update LLM models
        if "llm_models" in settings_data:
            for key, value in settings_data["llm_models"].items():
                await settings_mgr.set(f"llm_{key}", value)

        # Update DALL-E
        if "dalle" in settings_data:
            for key, value in settings_data["dalle"].items():
                await settings_mgr.set(f"dalle_{key}", value)

        # Update auto-publish
        if "auto_publish" in settings_data:
            for key, value in settings_data["auto_publish"].items():
                await settings_mgr.set(f"auto_publish_{key}", value)

        # Update filtering
        if "filtering" in settings_data:
            for key, value in settings_data["filtering"].items():
                await settings_mgr.set(key, value)

        # Update budget
        if "budget" in settings_data:
            for key, value in settings_data["budget"].items():
                await settings_mgr.set(f"budget_{key}", value)

        await db.commit()

        logger.info("settings_updated", user_id=user.get('id'))

        return {"success": True, "message": "Settings updated"}

    except Exception as e:
        logger.error("update_settings_error", error=str(e))
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update settings")
