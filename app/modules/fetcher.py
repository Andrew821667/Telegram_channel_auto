"""
News Fetcher Module
Легальный сбор контента из проверенных источников.

Источники:
1. Google News RSS (русский и английский)
2. Официальные RSS источники
3. Telegram каналы (только через официальный экспорт)
"""

import asyncio
import json
import random
from datetime import datetime
from typing import List, Optional, Dict, Any
from urllib.parse import urlencode, quote_plus

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import RawArticle, Source, log_to_db
import structlog

logger = structlog.get_logger()


# User-Agent ротация для легального скрапинга
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]


class NewsFetcher:
    """Сборщик новостей из различных источников."""

    def __init__(self, db_session: AsyncSession):
        """
        Инициализация fetcher.

        Args:
            db_session: Асинхронная сессия базы данных
        """
        self.db = db_session
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry."""
        self.client = httpx.AsyncClient(
            timeout=settings.fetcher_request_timeout,
            follow_redirects=True,
            headers={"User-Agent": self._get_random_user_agent()}
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.client:
            await self.client.aclose()

    def _get_random_user_agent(self) -> str:
        """Получить случайный User-Agent для ротации."""
        return random.choice(USER_AGENTS)

    async def _fetch_with_retry(
        self,
        url: str,
        max_retries: Optional[int] = None
    ) -> Optional[str]:
        """
        Получить контент с retry механизмом.

        Args:
            url: URL для запроса
            max_retries: Максимальное количество попыток

        Returns:
            Контент страницы или None при ошибке
        """
        if max_retries is None:
            max_retries = settings.fetcher_max_retries

        for attempt in range(max_retries):
            try:
                # Rate limiting - 1 запрос в секунду
                if attempt > 0:
                    delay = settings.fetcher_retry_delay * (2 ** attempt)  # Exponential backoff
                    await asyncio.sleep(delay)
                else:
                    await asyncio.sleep(1)  # Base rate limit

                # Обновляем User-Agent для каждой попытки
                self.client.headers["User-Agent"] = self._get_random_user_agent()

                response = await self.client.get(url)
                response.raise_for_status()

                logger.info(
                    "fetch_success",
                    url=url,
                    status_code=response.status_code,
                    attempt=attempt + 1
                )

                return response.text

            except httpx.HTTPError as e:
                logger.warning(
                    "fetch_error",
                    url=url,
                    error=str(e),
                    attempt=attempt + 1,
                    max_retries=max_retries
                )

                if attempt == max_retries - 1:
                    logger.error(
                        "fetch_failed",
                        url=url,
                        error=str(e),
                        total_attempts=max_retries
                    )
                    await log_to_db(
                        "ERROR",
                        f"Failed to fetch URL after {max_retries} attempts",
                        {"url": url, "error": str(e)},
                        session=self.db  # Передаём существующую сессию
                    )
                    return None

        return None

    def _build_google_news_rss_url(
        self,
        query: str,
        lang: str = "ru",
        region: str = "RU"
    ) -> str:
        """
        Построить URL для Google News RSS.

        Args:
            query: Поисковый запрос
            lang: Язык (ru, en)
            region: Регион (RU, US)

        Returns:
            URL для RSS feed
        """
        params = {
            "q": query,
            "hl": lang,
            "gl": region,
            "ceid": f"{region}:{lang}"
        }
        return f"{settings.google_news_rss_url}?{urlencode(params, quote_via=quote_plus)}"

    async def fetch_google_news_rss(self, lang: str = "ru") -> List[Dict[str, Any]]:
        """
        Получить новости из Google News RSS.

        Args:
            lang: Язык новостей (ru или en)

        Returns:
            Список словарей с новостями
        """
        articles = []

        # Определяем запрос и регион в зависимости от языка
        if lang == "ru":
            query = settings.google_news_query_ru
            region = settings.google_news_region
        else:
            query = settings.google_news_query_en
            region = "US"

        rss_url = self._build_google_news_rss_url(query, lang, region)

        logger.info("fetching_google_news", lang=lang, url=rss_url)

        # Получаем RSS feed
        content = await self._fetch_with_retry(rss_url)
        if not content:
            return articles

        # Парсим RSS
        feed = feedparser.parse(content)

        for entry in feed.entries[:settings.fetcher_max_articles_per_source]:
            try:
                # Извлекаем данные из RSS entry
                article_data = {
                    "url": entry.link,
                    "title": entry.title,
                    "content": entry.get("summary", ""),
                    "source_name": f"Google News RSS ({lang.upper()})",
                    "published_at": self._parse_date(entry.get("published")),
                }

                # Пытаемся получить полный текст статьи
                full_content = await self._fetch_article_content(entry.link)
                if full_content:
                    article_data["content"] = full_content

                articles.append(article_data)

                logger.info(
                    "article_fetched",
                    source="google_news",
                    lang=lang,
                    title=article_data["title"][:50]
                )

            except Exception as e:
                logger.error(
                    "article_parse_error",
                    error=str(e),
                    entry_title=entry.get("title", "Unknown")
                )
                continue

        logger.info(
            "google_news_fetch_complete",
            lang=lang,
            articles_count=len(articles)
        )

        return articles

    async def _fetch_article_content(self, url: str) -> Optional[str]:
        """
        Получить полный текст статьи со страницы.

        Args:
            url: URL статьи

        Returns:
            Текст статьи или None
        """
        try:
            content = await self._fetch_with_retry(url)
            if not content:
                return None

            # Парсим HTML с помощью BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")

            # Удаляем скрипты и стили
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.decompose()

            # Пытаемся найти основной контент
            # Ищем по распространенным тегам для статей
            article_tags = [
                soup.find("article"),
                soup.find("div", class_=lambda x: x and "content" in x.lower()),
                soup.find("div", class_=lambda x: x and "article" in x.lower()),
                soup.find("main"),
            ]

            for tag in article_tags:
                if tag:
                    # Извлекаем текст
                    text = tag.get_text(separator="\n", strip=True)
                    # Очищаем от лишних пробелов и переносов
                    text = "\n".join(line.strip() for line in text.split("\n") if line.strip())
                    if len(text) > 200:  # Минимальная длина для валидного контента
                        return text[:5000]  # Ограничиваем размер

            # Если не нашли специфичные теги, берем весь body
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n", strip=True)
                text = "\n".join(line.strip() for line in text.split("\n") if line.strip())
                return text[:5000]

        except Exception as e:
            logger.warning(
                "content_fetch_error",
                url=url,
                error=str(e)
            )

        return None

    async def fetch_rss_feed(self, source: Source) -> List[Dict[str, Any]]:
        """
        Получить новости из RSS источника.

        Args:
            source: Объект источника из БД

        Returns:
            Список словарей с новостями
        """
        articles = []

        logger.info("fetching_rss", source_name=source.name, url=source.url)

        content = await self._fetch_with_retry(source.url)
        if not content:
            return articles

        feed = feedparser.parse(content)

        for entry in feed.entries[:settings.fetcher_max_articles_per_source]:
            try:
                article_data = {
                    "url": entry.link,
                    "title": entry.title,
                    "content": entry.get("summary", ""),
                    "source_name": source.name,
                    "published_at": self._parse_date(entry.get("published")),
                }

                # Пытаемся получить полный контент
                full_content = await self._fetch_article_content(entry.link)
                if full_content:
                    article_data["content"] = full_content

                articles.append(article_data)

            except Exception as e:
                logger.error(
                    "rss_parse_error",
                    source=source.name,
                    error=str(e)
                )
                continue

        logger.info(
            "rss_fetch_complete",
            source_name=source.name,
            articles_count=len(articles)
        )

        return articles

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """
        Парсить дату из различных форматов.

        Args:
            date_str: Строка с датой

        Returns:
            datetime объект или None (без timezone)
        """
        if not date_str:
            return None

        try:
            # feedparser обычно предоставляет parsed время
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            # Убираем timezone для совместимости с БД
            return dt.replace(tzinfo=None) if dt else None
        except Exception:
            try:
                # Fallback на ISO формат
                from dateutil import parser
                dt = parser.parse(date_str)
                # Убираем timezone для совместимости с БД
                return dt.replace(tzinfo=None) if dt else None
            except Exception:
                logger.warning("date_parse_error", date_str=date_str)
                return None

    async def fetch_perplexity_news(self, lang: str = "ru") -> List[Dict[str, Any]]:
        """
        Получить новости через Perplexity AI real-time search.

        Args:
            lang: Язык новостей (ru или en)

        Returns:
            Список словарей с новостями
        """
        articles = []

        # Определяем запрос в зависимости от языка
        if lang == "ru":
            query = settings.google_news_query_ru.replace(" AND ", " ")
            search_prompt = f"""Найди последние новости (за последние 24 часа) по запросу: {query}

Верни результаты в формате JSON массива, где каждый элемент содержит:
- title: заголовок новости
- content: краткое содержание (2-3 предложения)
- url: ссылка на источник
- source_name: название источника
- published_at: дата публикации в формате ISO 8601

Ищи только актуальные новости. Верни максимум 10 новостей."""
        else:
            query = settings.google_news_query_en.replace(" AND ", " ")
            search_prompt = f"""Find latest news (from last 24 hours) for query: {query}

Return results as JSON array where each element contains:
- title: news headline
- content: brief summary (2-3 sentences)
- url: source link
- source_name: source name
- published_at: publication date in ISO 8601 format

Search only for recent news. Return maximum 10 articles."""

        logger.info("fetching_perplexity_news", lang=lang)

        try:
            # Используем LLM provider для Perplexity
            from app.modules.llm_provider import get_llm_provider

            llm = get_llm_provider("perplexity")

            # Делаем запрос к Perplexity с real-time search
            response = await llm.generate_completion(
                messages=[
                    {"role": "system", "content": "You are a news aggregator assistant. Always return valid JSON."},
                    {"role": "user", "content": search_prompt}
                ],
                max_tokens=3000,
                temperature=0.3
            )

            # Парсим JSON ответ
            import json
            import re

            # Извлекаем JSON из ответа (может быть обернут в markdown)
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Пытаемся парсить весь ответ как JSON
                json_str = response.strip()

            try:
                news_data = json.loads(json_str)

                # Проверяем что это список
                if not isinstance(news_data, list):
                    logger.warning("perplexity_response_not_list", response=response[:200])
                    return articles

                for item in news_data:
                    try:
                        # Парсим дату если есть
                        published_at = None
                        if "published_at" in item and item["published_at"]:
                            published_at = self._parse_date(item["published_at"])

                        # Создаем статью
                        article_data = {
                            "url": item.get("url", ""),
                            "title": item.get("title", ""),
                            "content": item.get("content", ""),
                            "source_name": f"Perplexity Search ({lang.upper()})",
                            "published_at": published_at or datetime.utcnow(),
                        }

                        # Проверяем обязательные поля
                        if article_data["url"] and article_data["title"]:
                            articles.append(article_data)

                            logger.info(
                                "perplexity_article_fetched",
                                lang=lang,
                                title=article_data["title"][:50]
                            )

                    except Exception as e:
                        logger.error(
                            "perplexity_article_parse_error",
                            error=str(e),
                            item=str(item)[:200]
                        )
                        continue

            except json.JSONDecodeError as e:
                logger.error(
                    "perplexity_json_parse_error",
                    error=str(e),
                    response=response[:500]
                )

        except Exception as e:
            logger.error(
                "perplexity_fetch_error",
                lang=lang,
                error=str(e)
            )

        logger.info(
            "perplexity_fetch_complete",
            lang=lang,
            articles_count=len(articles)
        )

        return articles

    async def fetch_hackernews(self) -> List[Dict[str, Any]]:
        """
        Получить новости из Hacker News API.

        Returns:
            Список словарей с новостями
        """
        articles = []

        logger.info("fetching_hackernews")

        try:
            # Получаем топ-500 историй
            top_stories_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
            response = await self._fetch_with_retry(top_stories_url)

            if not response:
                return articles

            story_ids = json.loads(response)

            # Ключевые слова для фильтрации
            keywords = [
                'ai', 'artificial intelligence', 'machine learning', 'ml',
                'legal tech', 'legaltech', 'law', 'lawyer', 'court',
                'automation', 'neural', 'llm', 'gpt', 'openai',
                'compliance', 'contract', 'regulation'
            ]

            # Берем первые 100 историй (топ самые релевантные)
            checked_count = 0
            for story_id in story_ids[:100]:
                if len(articles) >= 10:  # Лимит на количество
                    break

                try:
                    # Получаем детали истории
                    story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                    story_response = await self._fetch_with_retry(story_url)

                    if not story_response:
                        continue

                    story = json.loads(story_response)

                    # Фильтруем только stories (не jobs, polls)
                    if story.get('type') != 'story':
                        continue

                    # Проверяем наличие URL
                    if not story.get('url'):
                        continue

                    title = story.get('title', '')
                    text = story.get('text', '')

                    # Проверяем релевантность по ключевым словам
                    combined_text = f"{title} {text}".lower()
                    is_relevant = any(keyword in combined_text for keyword in keywords)

                    if not is_relevant:
                        checked_count += 1
                        continue

                    # Формируем дату
                    published_at = None
                    if 'time' in story:
                        from datetime import datetime
                        published_at = datetime.utcfromtimestamp(story['time'])

                    # Создаем статью
                    article_data = {
                        "url": story['url'],
                        "title": title,
                        "content": text or f"{title}\n\nDiscussion: https://news.ycombinator.com/item?id={story_id}",
                        "source_name": "Hacker News",
                        "published_at": published_at,
                    }

                    articles.append(article_data)

                    logger.info(
                        "hackernews_article_fetched",
                        title=title[:50],
                        score=story.get('score', 0)
                    )

                    checked_count += 1

                except Exception as e:
                    logger.error(
                        "hackernews_story_parse_error",
                        story_id=story_id,
                        error=str(e)
                    )
                    continue

            logger.info(
                "hackernews_fetch_complete",
                articles_count=len(articles),
                checked_count=checked_count
            )

        except Exception as e:
            logger.error(
                "hackernews_fetch_error",
                error=str(e)
            )

        return articles

    async def fetch_reddit(self, subreddit: str = "MachineLearning") -> List[Dict[str, Any]]:
        """
        Получить новости из Reddit (без OAuth, через JSON API).

        Args:
            subreddit: Название subreddit

        Returns:
            Список словарей с новостями
        """
        articles = []

        logger.info("fetching_reddit", subreddit=subreddit)

        try:
            # Reddit JSON API (не требует OAuth для публичных постов)
            reddit_url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=25"

            # Специальный User-Agent для Reddit API
            old_user_agent = self.client.headers.get("User-Agent")
            self.client.headers["User-Agent"] = "LegalTechNewsBot/1.0 (AI News Aggregator)"

            response = await self._fetch_with_retry(reddit_url)

            # Возвращаем старый User-Agent
            if old_user_agent:
                self.client.headers["User-Agent"] = old_user_agent

            if not response:
                return articles

            data = json.loads(response)

            # Парсим посты
            for post in data.get('data', {}).get('children', []):
                try:
                    post_data = post.get('data', {})

                    # Пропускаем stickied посты
                    if post_data.get('stickied'):
                        continue

                    # Пропускаем удаленные
                    if post_data.get('removed_by_category'):
                        continue

                    title = post_data.get('title', '')
                    selftext = post_data.get('selftext', '')
                    url = post_data.get('url', '')
                    permalink = f"https://www.reddit.com{post_data.get('permalink', '')}"

                    # Если это self post (текстовый), используем permalink
                    if post_data.get('is_self'):
                        url = permalink

                    # Формируем контент
                    content = selftext[:1000] if selftext else title

                    # Добавляем метаданные
                    score = post_data.get('score', 0)
                    num_comments = post_data.get('num_comments', 0)
                    content += f"\n\n👍 {score} upvotes | 💬 {num_comments} comments"

                    # Дата
                    published_at = None
                    if 'created_utc' in post_data:
                        published_at = datetime.utcfromtimestamp(post_data['created_utc'])

                    article_data = {
                        "url": url,
                        "title": title,
                        "content": content,
                        "source_name": f"Reddit r/{subreddit}",
                        "published_at": published_at,
                    }

                    articles.append(article_data)

                    logger.info(
                        "reddit_post_fetched",
                        subreddit=subreddit,
                        title=title[:50],
                        score=score
                    )

                    # Лимит
                    if len(articles) >= 10:
                        break

                except Exception as e:
                    logger.error(
                        "reddit_post_parse_error",
                        subreddit=subreddit,
                        error=str(e)
                    )
                    continue

            logger.info(
                "reddit_fetch_complete",
                subreddit=subreddit,
                articles_count=len(articles)
            )

        except Exception as e:
            logger.error(
                "reddit_fetch_error",
                subreddit=subreddit,
                error=str(e)
            )

        return articles

    async def fetch_arxiv(self, category: str = "cs.AI") -> List[Dict[str, Any]]:
        """
        Получить научные статьи из ArXiv API.

        Args:
            category: Категория (cs.AI, cs.LG, cs.CL)

        Returns:
            Список словарей с новостями
        """
        articles = []

        logger.info("fetching_arxiv", category=category)

        try:
            # ArXiv API query
            # Ищем статьи за последние 7 дней, отсортированные по дате
            arxiv_url = (
                f"http://export.arxiv.org/api/query?"
                f"search_query=cat:{category}&"
                f"sortBy=submittedDate&"
                f"sortOrder=descending&"
                f"max_results=20"
            )

            response = await self._fetch_with_retry(arxiv_url)

            if not response:
                return articles

            # Парсим XML ответ
            from xml.etree import ElementTree as ET

            root = ET.fromstring(response)

            # Namespace для ArXiv
            ns = {
                'atom': 'http://www.w3.org/2005/Atom',
                'arxiv': 'http://arxiv.org/schemas/atom'
            }

            # Парсим entries
            for entry in root.findall('atom:entry', ns):
                try:
                    title_elem = entry.find('atom:title', ns)
                    summary_elem = entry.find('atom:summary', ns)
                    link_elem = entry.find('atom:id', ns)
                    published_elem = entry.find('atom:published', ns)

                    if not all([title_elem, summary_elem, link_elem]):
                        continue

                    title = title_elem.text.strip().replace('\n', ' ')
                    summary = summary_elem.text.strip().replace('\n', ' ')[:500]
                    url = link_elem.text.strip()

                    # Дата публикации
                    published_at = None
                    if published_elem is not None:
                        published_at = self._parse_date(published_elem.text)

                    # Авторы
                    authors = []
                    for author in entry.findall('atom:author', ns):
                        name_elem = author.find('atom:name', ns)
                        if name_elem is not None:
                            authors.append(name_elem.text)

                    authors_str = ', '.join(authors[:3])  # Первые 3 автора
                    if len(authors) > 3:
                        authors_str += ' et al.'

                    # Формируем контент
                    content = f"{summary}\n\nAuthors: {authors_str}"

                    article_data = {
                        "url": url,
                        "title": title,
                        "content": content,
                        "source_name": f"ArXiv {category}",
                        "published_at": published_at,
                    }

                    articles.append(article_data)

                    logger.info(
                        "arxiv_article_fetched",
                        category=category,
                        title=title[:50]
                    )

                    # Лимит
                    if len(articles) >= 5:  # Меньше научных статей, они длиннее
                        break

                except Exception as e:
                    logger.error(
                        "arxiv_entry_parse_error",
                        category=category,
                        error=str(e)
                    )
                    continue

            logger.info(
                "arxiv_fetch_complete",
                category=category,
                articles_count=len(articles)
            )

        except Exception as e:
            logger.error(
                "arxiv_fetch_error",
                category=category,
                error=str(e)
            )

        return articles

    async def fetch_medium_rss(self, tag: str = "artificial-intelligence") -> List[Dict[str, Any]]:
        """
        Получить статьи из Medium по тегу через RSS.

        Args:
            tag: Тег на Medium (artificial-intelligence, machine-learning, legaltech)

        Returns:
            Список словарей с новостями
        """
        articles = []

        logger.info("fetching_medium", tag=tag)

        try:
            # Medium RSS feed для тега
            medium_url = f"https://medium.com/feed/tag/{tag}"

            response = await self._fetch_with_retry(medium_url)

            if not response:
                return articles

            # Парсим RSS
            feed = feedparser.parse(response)

            for entry in feed.entries[:10]:  # Лимит 10 статей
                try:
                    title = entry.title
                    summary = entry.get('summary', '')

                    # Убираем HTML теги из summary
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(summary, 'html.parser')
                    clean_summary = soup.get_text()[:500]

                    url = entry.link

                    # Дата
                    published_at = self._parse_date(entry.get('published'))

                    # Автор
                    author = entry.get('author', 'Unknown')

                    # Формируем контент
                    content = f"{clean_summary}\n\nAuthor: {author}"

                    article_data = {
                        "url": url,
                        "title": title,
                        "content": content,
                        "source_name": f"Medium ({tag})",
                        "published_at": published_at,
                    }

                    articles.append(article_data)

                    logger.info(
                        "medium_article_fetched",
                        tag=tag,
                        title=title[:50]
                    )

                except Exception as e:
                    logger.error(
                        "medium_entry_parse_error",
                        tag=tag,
                        error=str(e)
                    )
                    continue

            logger.info(
                "medium_fetch_complete",
                tag=tag,
                articles_count=len(articles)
            )

        except Exception as e:
            logger.error(
                "medium_fetch_error",
                tag=tag,
                error=str(e)
            )

        return articles

    async def save_articles(self, articles: List[Dict[str, Any]]) -> int:
        """
        Сохранить статьи в базу данных.

        Args:
            articles: Список статей для сохранения

        Returns:
            Количество сохраненных статей
        """
        saved_count = 0

        for article_data in articles:
            try:
                # Проверяем, существует ли статья с таким URL
                result = await self.db.execute(
                    select(RawArticle).where(RawArticle.url == article_data["url"])
                )
                existing = result.scalar_one_or_none()

                if existing:
                    logger.debug(
                        "article_exists",
                        url=article_data["url"]
                    )
                    continue

                # Создаем новую статью
                article = RawArticle(**article_data)
                self.db.add(article)
                saved_count += 1

                logger.info(
                    "article_saved",
                    url=article_data["url"],
                    title=article_data["title"][:50]
                )

            except Exception as e:
                logger.error(
                    "article_save_error",
                    error=str(e),
                    url=article_data.get("url", "Unknown")
                )
                continue

        await self.db.commit()

        logger.info("articles_save_complete", saved_count=saved_count)

        return saved_count

    async def fetch_all_sources(self) -> Dict[str, int]:
        """
        Получить новости из всех активных источников.

        Returns:
            Словарь с количеством статей по источникам
        """
        stats = {}

        # Google News RSS (русский)
        if settings.fetcher_enabled:
            articles_ru = await self.fetch_google_news_rss("ru")
            saved_ru = await self.save_articles(articles_ru)
            stats["Google News RU"] = saved_ru

            # Google News RSS (английский)
            articles_en = await self.fetch_google_news_rss("en")
            saved_en = await self.save_articles(articles_en)
            stats["Google News EN"] = saved_en

            # Perplexity Real-Time Search (если включен)
            if settings.perplexity_search_enabled:
                # Русские новости через Perplexity
                perplexity_articles_ru = await self.fetch_perplexity_news("ru")
                saved_perplexity_ru = await self.save_articles(perplexity_articles_ru)
                stats["Perplexity Search RU"] = saved_perplexity_ru

                # Английские новости через Perplexity
                perplexity_articles_en = await self.fetch_perplexity_news("en")
                saved_perplexity_en = await self.save_articles(perplexity_articles_en)
                stats["Perplexity Search EN"] = saved_perplexity_en

            # Hacker News
            if settings.hackernews_enabled:
                hn_articles = await self.fetch_hackernews()
                saved_hn = await self.save_articles(hn_articles)
                stats["Hacker News"] = saved_hn

            # Reddit - несколько subreddits
            if settings.reddit_enabled:
                for subreddit in settings.reddit_subreddits_list:
                    reddit_articles = await self.fetch_reddit(subreddit)
                    saved_reddit = await self.save_articles(reddit_articles)
                    stats[f"Reddit r/{subreddit}"] = saved_reddit

            # ArXiv - научные публикации
            if settings.arxiv_enabled:
                for category in settings.arxiv_categories_list:
                    arxiv_articles = await self.fetch_arxiv(category)
                    saved_arxiv = await self.save_articles(arxiv_articles)
                    stats[f"ArXiv {category}"] = saved_arxiv

            # Medium
            if settings.medium_enabled:
                for tag in settings.medium_tags_list:
                    medium_articles = await self.fetch_medium_rss(tag)
                    saved_medium = await self.save_articles(medium_articles)
                    stats[f"Medium {tag}"] = saved_medium

        # Дополнительные RSS источники из БД
        result = await self.db.execute(
            select(Source).where(Source.enabled == True, Source.type == "rss")
        )
        sources = result.scalars().all()

        for source in sources:
            try:
                articles = await self.fetch_rss_feed(source)
                saved = await self.save_articles(articles)
                stats[source.name] = saved

                # Обновляем статистику источника
                source.last_fetch = datetime.utcnow()
                source.fetch_errors = 0

            except Exception as e:
                logger.error(
                    "source_fetch_failed",
                    source_name=source.name,
                    error=str(e)
                )
                source.fetch_errors += 1

        await self.db.commit()

        # Логируем общую статистику
        total_articles = sum(stats.values())
        await log_to_db(
            "INFO",
            f"Fetch completed: {total_articles} articles from {len(stats)} sources",
            {"stats": stats},
            session=self.db  # Передаём существующую сессию
        )

        logger.info(
            "fetch_all_complete",
            total_articles=total_articles,
            sources_count=len(stats),
            stats=stats
        )

        return stats


async def fetch_news(db_session: AsyncSession) -> Dict[str, int]:
    """
    Удобная функция для запуска сбора новостей.

    Args:
        db_session: Асинхронная сессия БД

    Returns:
        Статистика по собранным новостям
    """
    async with NewsFetcher(db_session) as fetcher:
        return await fetcher.fetch_all_sources()
