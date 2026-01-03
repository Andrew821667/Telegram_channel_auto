export interface User {
  id: number
  first_name: string
  last_name?: string
  username?: string
}

export interface Article {
  id: number
  title: string
  content: string
  source: string
  created_at: string
  published_at?: string
  views?: number
  reactions?: number
  quality_score?: number
  ai_summary?: string
  tags?: string[]
  status: 'draft' | 'approved' | 'published' | 'rejected'
}

export interface Stats {
  total_drafts: number
  total_published: number
  avg_quality_score: number
  total_views: number
  total_reactions: number
  engagement_rate: number
  articles_today: number
}

export interface ChartData {
  date: string
  views: number
  reactions: number
  articles: number
}

export interface SourceStats {
  source: string
  count: number
  avg_quality: number
  total_views: number
}

export interface ChannelAnalytics {
  period_days: number
  total_interactions: number
  unique_users: number
  channel_clicks: number
  article_views_from_channel: number
  total_from_channel: number
  conversion_rate: number
  top_articles: Array<{
    publication_id: number
    title: string
    views_from_channel: number
    unique_users: number
    published_at: string
  }>
  daily_stats: Array<{
    date: string
    channel_clicks: number
    article_views: number
    unique_users: number
  }>
}
