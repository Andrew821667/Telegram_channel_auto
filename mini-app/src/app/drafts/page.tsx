'use client'

import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { apiMethods } from '@/lib/api'
import { ArrowLeft, Check, X, ExternalLink } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'

interface Draft {
  id: number
  title: string
  content: string
  source: string
  ai_summary?: string
  quality_score?: number
  created_at: string
  tags?: string[]
}

export default function DraftsPage() {
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [selectedDraft, setSelectedDraft] = useState<Draft | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState(false)
  const router = useRouter()

  useEffect(() => {
    loadDrafts()
  }, [])

  const loadDrafts = async () => {
    try {
      console.log('[Drafts] Loading from API...')
      const response = await apiMethods.getDrafts(50)
      console.log('[Drafts] Received', response.data.length, 'drafts')
      setDrafts(response.data)
    } catch (error: any) {
      console.error('[Drafts] Failed to load:', error)
      console.error('[Drafts] Error details:', {
        message: error.message,
        response: error.response?.data,
        status: error.response?.status,
      })

      // Show error to user
      if (window.Telegram?.WebApp) {
        window.Telegram.WebApp.showAlert(`Ошибка загрузки черновиков: ${error.message}`)
      }

      // Mock data ONLY in development
      if (process.env.NODE_ENV === 'development') {
        console.warn('[Drafts] Using mock data (development mode)')
        setDrafts([
          {
            id: 1,
            title: 'Новый закон о персональных данных',
            content: 'Минцифры предложило ужесточить требования к обработке персональных данных...',
            source: 'Google News',
            quality_score: 8.5,
            created_at: new Date().toISOString(),
            tags: ['ПДн', 'законодательство'],
          },
          {
            id: 2,
            title: 'ИИ в судебной практике',
            content: 'Судебная коллегия по экономическим спорам ВС РФ...',
            source: 'Habr',
            quality_score: 7.8,
            created_at: new Date().toISOString(),
            tags: ['AI', 'суды'],
          },
        ])
      }
    } finally {
      setLoading(false)
    }
  }

  const handleApprove = async (id: number) => {
    setActionLoading(true)
    try {
      await apiMethods.approveDraft(id)
      setDrafts(drafts.filter(d => d.id !== id))
      setSelectedDraft(null)

      if (window.Telegram?.WebApp) {
        window.Telegram.WebApp.showAlert('Статья одобрена для публикации')
      }
    } catch (error) {
      console.error('Failed to approve draft:', error)
    } finally {
      setActionLoading(false)
    }
  }

  const handleReject = async (id: number) => {
    setActionLoading(true)
    try {
      await apiMethods.rejectDraft(id)
      setDrafts(drafts.filter(d => d.id !== id))
      setSelectedDraft(null)

      if (window.Telegram?.WebApp) {
        window.Telegram.WebApp.showAlert('Статья отклонена')
      }
    } catch (error) {
      console.error('Failed to reject draft:', error)
    } finally {
      setActionLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="w-16 h-16 border-4 border-primary border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
          <p className="text-muted-foreground">Загрузка...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50 p-4">
      <div className="max-w-7xl mx-auto space-y-4">
        {/* Header */}
        <div className="flex items-center gap-4 py-2">
          <Link href="/">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="w-5 h-5" />
            </Button>
          </Link>
          <div>
            <h1 className="text-2xl font-bold">Модерация контента</h1>
            <p className="text-sm text-muted-foreground">
              {drafts.length} {drafts.length === 1 ? 'статья' : 'статей'} ожидает проверки
            </p>
          </div>
        </div>

        {drafts.length === 0 ? (
          <Card>
            <CardContent className="py-12 text-center">
              <p className="text-muted-foreground">Нет черновиков для модерации</p>
            </CardContent>
          </Card>
        ) : !selectedDraft ? (
          /* List view */
          <div className="space-y-3">
            {drafts.map((draft) => (
              <Card
                key={draft.id}
                className="cursor-pointer hover:shadow-md transition-shadow"
                onClick={() => setSelectedDraft(draft)}
              >
                <CardContent className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <h3 className="font-semibold text-lg mb-1 line-clamp-2">
                        {draft.title}
                      </h3>
                      <p className="text-sm text-muted-foreground line-clamp-2 mb-2">
                        {draft.content}
                      </p>
                      <div className="flex flex-wrap gap-2 items-center text-xs">
                        <span className="px-2 py-1 bg-blue-100 text-blue-700 rounded">
                          {draft.source}
                        </span>
                        {draft.quality_score && (
                          <span className="px-2 py-1 bg-green-100 text-green-700 rounded">
                            ⭐ {draft.quality_score.toFixed(1)}
                          </span>
                        )}
                        {draft.tags?.map((tag) => (
                          <span
                            key={tag}
                            className="px-2 py-1 bg-gray-100 text-gray-700 rounded"
                          >
                            #{tag}
                          </span>
                        ))}
                      </div>
                    </div>
                    <ExternalLink className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          /* Detail view */
          <div className="space-y-4">
            <Button
              variant="ghost"
              onClick={() => setSelectedDraft(null)}
              className="mb-2"
            >
              <ArrowLeft className="w-4 h-4 mr-2" />
              Назад к списку
            </Button>

            <Card>
              <CardHeader>
                <CardTitle className="text-xl">{selectedDraft.title}</CardTitle>
                <div className="flex flex-wrap gap-2 mt-2">
                  <span className="text-xs px-2 py-1 bg-blue-100 text-blue-700 rounded">
                    {selectedDraft.source}
                  </span>
                  {selectedDraft.quality_score && (
                    <span className="text-xs px-2 py-1 bg-green-100 text-green-700 rounded">
                      Оценка: {selectedDraft.quality_score.toFixed(1)}/10
                    </span>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                {selectedDraft.ai_summary && (
                  <div className="p-3 bg-blue-50 rounded-lg border border-blue-200">
                    <p className="text-sm font-medium text-blue-900 mb-1">
                      AI Резюме:
                    </p>
                    <p className="text-sm text-blue-800">
                      {selectedDraft.ai_summary}
                    </p>
                  </div>
                )}

                <div>
                  <p className="text-sm font-medium mb-2">Содержание:</p>
                  <p className="text-sm leading-relaxed whitespace-pre-wrap">
                    {selectedDraft.content}
                  </p>
                </div>

                {selectedDraft.tags && selectedDraft.tags.length > 0 && (
                  <div>
                    <p className="text-sm font-medium mb-2">Теги:</p>
                    <div className="flex flex-wrap gap-2">
                      {selectedDraft.tags.map((tag) => (
                        <span
                          key={tag}
                          className="text-xs px-2 py-1 bg-gray-100 text-gray-700 rounded"
                        >
                          #{tag}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3 pt-4">
                  <Button
                    variant="destructive"
                    onClick={() => handleReject(selectedDraft.id)}
                    disabled={actionLoading}
                    className="w-full"
                  >
                    <X className="w-4 h-4 mr-2" />
                    Отклонить
                  </Button>
                  <Button
                    onClick={() => handleApprove(selectedDraft.id)}
                    disabled={actionLoading}
                    className="w-full"
                  >
                    <Check className="w-4 h-4 mr-2" />
                    Одобрить
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}
