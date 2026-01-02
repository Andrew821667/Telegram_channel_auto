import axios from 'axios'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

// Log API configuration on module load
console.log('[API Config] Initializing API client')
console.log('[API Config] NEXT_PUBLIC_API_URL:', process.env.NEXT_PUBLIC_API_URL)
console.log('[API Config] Using baseURL:', API_URL)
console.log('[API Config] NODE_ENV:', process.env.NODE_ENV)

export const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
    'ngrok-skip-browser-warning': 'true',
  },
})

// Add Telegram auth to requests
api.interceptors.request.use((config) => {
  console.log('[API Request]', config.method?.toUpperCase(), config.url)

  if (typeof window !== 'undefined' && window.Telegram?.WebApp) {
    const initDataUnsafe = window.Telegram.WebApp.initDataUnsafe
    // Send user data as JSON
    if (initDataUnsafe?.user?.id) {
      config.headers['X-Telegram-Init-Data'] = JSON.stringify(initDataUnsafe)
      console.log('[API Request] Using Telegram user:', initDataUnsafe.user.id)
    } else {
      // Fallback: send minimal data for development
      console.warn('[Mini App] No Telegram user data, using fallback')
      config.headers['X-Telegram-Init-Data'] = JSON.stringify({
        user: {
          id: 0,
          first_name: 'Dev',
          username: 'dev_user'
        }
      })
    }
  } else {
    console.warn('[API Request] No Telegram WebApp available, using fallback auth')
    config.headers['X-Telegram-Init-Data'] = JSON.stringify({
      user: {
        id: 0,
        first_name: 'Dev',
        username: 'dev_user'
      }
    })
  }

  console.log('[API Request] Full URL:', (config.baseURL || '') + (config.url || ''))
  console.log('[API Request] Headers:', config.headers)

  return config
})

// Add response/error logging
api.interceptors.response.use(
  (response) => {
    console.log('[API Response] Success:', response.config.url, response.status)
    return response
  },
  (error) => {
    console.error('[API Response] Error:', error.config?.url)
    console.error('[API Response] Status:', error.response?.status)
    console.error('[API Response] Data:', error.response?.data)
    return Promise.reject(error)
  }
)

// API types
export interface DraftArticle {
  id: number
  title: string
  content: string
  source: string
  ai_summary?: string
  quality_score?: number
  created_at: string
  status: string
  tags?: string[]
}

export interface PublishedArticle {
  id: number
  title: string
  content: string
  published_at: string
  views?: number
  reactions?: number
  engagement_rate?: number
  source: string
  quality_score?: number
}

export interface DashboardStats {
  total_drafts: number
  total_published: number
  avg_quality_score: number
  total_views: number
  total_reactions: number
  engagement_rate: number
  articles_today: number
  top_sources: Array<{ source: string; count: number }>
}

export interface SystemSettings {
  sources: Record<string, boolean>
  llm_models: {
    analysis: string
    draft_generation: string
    ranking: string
  }
  dalle: {
    enabled: boolean
    model: string
    quality: string
    size: string
    auto_generate: boolean
    ask_on_review: boolean
  }
  auto_publish: {
    enabled: boolean
    mode: string
    max_per_day: number
    weekdays_only: boolean
    skip_holidays: boolean
  }
  filtering: {
    min_score: number
    min_content_length: number
    similarity_threshold: number
  }
  budget: {
    max_per_month: number
    warning_threshold: number
    stop_on_exceed: boolean
    switch_to_cheap: boolean
  }
}

// API methods
export const apiMethods = {
  // Dashboard
  getDashboardStats: () => api.get<DashboardStats>('/api/miniapp/dashboard/stats'),

  // Drafts
  getDrafts: (limit = 50) => api.get<DraftArticle[]>(`/api/miniapp/drafts?limit=${limit}`),
  getDraft: (id: number) => api.get<DraftArticle>(`/api/miniapp/drafts/${id}`),
  approveDraft: (id: number) => api.post(`/api/miniapp/drafts/${id}/approve`),
  rejectDraft: (id: number) => api.post(`/api/miniapp/drafts/${id}/reject`),

  // Published
  getPublished: (limit = 50, offset = 0) =>
    api.get<PublishedArticle[]>(`/api/miniapp/published?limit=${limit}&offset=${offset}`),
  getPublishedStats: (period: '7d' | '30d' | '90d') =>
    api.get(`/api/miniapp/published/stats?period=${period}`),

  // Settings
  getSettings: () => api.get<SystemSettings>('/api/miniapp/settings'),
  updateSettings: (settings: Partial<SystemSettings>) =>
    api.put('/api/miniapp/settings', settings),
}
