'use client'

import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { apiMethods } from '@/lib/api'
import { formatNumber } from '@/lib/utils'
import { BarChart3, FileText, TrendingUp, Users, ArrowRight } from 'lucide-react'
import Link from 'next/link'

interface DashboardStats {
  total_drafts: number
  total_published: number
  avg_quality_score: number
  total_views: number
  total_reactions: number
  engagement_rate: number
  articles_today: number
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadStats()
  }, [])

  const loadStats = async () => {
    try {
      const response = await apiMethods.getDashboardStats()
      setStats(response.data)
    } catch (error) {
      console.error('Failed to load stats:', error)
      // Use mock data for development
      setStats({
        total_drafts: 12,
        total_published: 145,
        avg_quality_score: 8.2,
        total_views: 15420,
        total_reactions: 892,
        engagement_rate: 5.8,
        articles_today: 3,
      })
    } finally {
      setLoading(false)
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
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white p-4">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="text-center py-6">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">
            Legal AI News
          </h1>
          <p className="text-gray-600">
            AI-driven news aggregation and analytics
          </p>
        </div>

        {/* Quick Stats */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <FileText className="w-4 h-4" />
                Черновики
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">{stats?.total_drafts || 0}</div>
              <p className="text-xs text-muted-foreground mt-1">
                Требуют модерации
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <BarChart3 className="w-4 h-4" />
                Опубликовано
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">{stats?.total_published || 0}</div>
              <p className="text-xs text-green-600 mt-1">
                +{stats?.articles_today || 0} сегодня
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <Users className="w-4 h-4" />
                Просмотры
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">
                {formatNumber(stats?.total_views || 0)}
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                Всего просмотров
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <TrendingUp className="w-4 h-4" />
                Вовлеченность
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">
                {stats?.engagement_rate?.toFixed(1) || '0.0'}%
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {formatNumber(stats?.total_reactions || 0)} реакций
              </p>
            </CardContent>
          </Card>
        </div>

        {/* Quick Actions */}
        <div className="grid md:grid-cols-2 gap-4">
          <Card className="hover:shadow-lg transition-shadow cursor-pointer">
            <Link href="/drafts">
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <span>Модерация контента</span>
                  <ArrowRight className="w-5 h-5 text-primary" />
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground mb-4">
                  Проверьте и одобрите новые статьи, собранные AI
                </p>
                <div className="flex items-center gap-2">
                  <div className="flex-1 bg-gray-200 rounded-full h-2">
                    <div
                      className="bg-primary h-2 rounded-full"
                      style={{ width: '60%' }}
                    ></div>
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {stats?.total_drafts || 0} ожидают
                  </span>
                </div>
              </CardContent>
            </Link>
          </Card>

          <Card className="hover:shadow-lg transition-shadow cursor-pointer">
            <Link href="/analytics">
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <span>Аналитика</span>
                  <ArrowRight className="w-5 h-5 text-primary" />
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground mb-4">
                  Детальная статистика по публикациям и источникам
                </p>
                <div className="flex gap-4">
                  <div>
                    <div className="text-2xl font-bold text-green-600">
                      {stats?.avg_quality_score?.toFixed(1) || '0.0'}
                    </div>
                    <p className="text-xs text-muted-foreground">Ср. качество</p>
                  </div>
                  <div>
                    <div className="text-2xl font-bold text-blue-600">
                      {stats?.total_published || 0}
                    </div>
                    <p className="text-xs text-muted-foreground">Статей</p>
                  </div>
                </div>
              </CardContent>
            </Link>
          </Card>
        </div>

        {/* Additional Actions */}
        <Card>
          <CardHeader>
            <CardTitle>Быстрые действия</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-3">
              <Link href="/settings">
                <Button variant="outline" className="w-full">
                  Настройки
                </Button>
              </Link>
              <Link href="/published">
                <Button variant="outline" className="w-full">
                  Опубликованное
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
