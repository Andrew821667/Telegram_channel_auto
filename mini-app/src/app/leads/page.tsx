'use client'

import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { apiMethods } from '@/lib/api'
import { formatNumber } from '@/lib/utils'
import { Target, Users, TrendingUp, DollarSign, ArrowLeft, BarChart3 } from 'lucide-react'
import Link from 'next/link'
import { LeadAnalytics, LeadROI } from '@/types'

interface LeadAnalyticsResponse {
  success: boolean
  data: LeadAnalytics
  roi: LeadROI
  period_days: number
}

export default function LeadsAnalyticsPage() {
  const [analytics, setAnalytics] = useState<LeadAnalytics | null>(null)
  const [roi, setRoi] = useState<LeadROI | null>(null)
  const [loading, setLoading] = useState(true)
  const [period, setPeriod] = useState(30)

  useEffect(() => {
    loadAnalytics()
  }, [period])

  const loadAnalytics = async () => {
    try {
      console.log('[Leads] Loading analytics from API')
      const response = await apiMethods.getLeadAnalytics(period)
      console.log('[Leads] API response:', response.data)

      if (response.data.success) {
        setAnalytics(response.data.data)
        setRoi(response.data.roi)
      }
    } catch (error: any) {
      console.error('[Leads] Failed to load analytics:', error)

      // Mock data for development
      if (process.env.NODE_ENV === 'development') {
        console.warn('[Leads] Using mock data (development mode)')
        setAnalytics({
          period_days: 30,
          overview: {
            total_leads: 47,
            qualified_leads: 23,
            converted_leads: 8,
            completed_magnet: 31,
            qualification_rate: 48.9,
            conversion_rate: 17.0,
            magnet_completion_rate: 66.0,
            avg_lead_score: 68.2,
            with_email: 29,
            with_phone: 12,
            with_company: 18
          },
          daily_stats: [
            { date: '2026-01-01', new_leads: 3, completed_magnet: 2, qualified: 1, avg_score: 72 },
            { date: '2026-01-02', new_leads: 5, completed_magnet: 3, qualified: 2, avg_score: 68 },
            { date: '2026-01-03', new_leads: 2, completed_magnet: 1, qualified: 1, avg_score: 75 }
          ],
          top_leads: [
            { user_id: 1, username: 'john_doe', full_name: 'John Doe', email: 'john@example.com', company: 'Tech Corp', lead_score: 85, expertise_level: 'expert', business_focus: 'corporate', created_at: '2026-01-01' },
            { user_id: 2, username: 'jane_smith', full_name: 'Jane Smith', email: 'jane@lawfirm.com', company: 'Legal Partners', lead_score: 82, expertise_level: 'expert', business_focus: 'law_firm', created_at: '2026-01-02' }
          ],
          sources_stats: [
            { source: 'law_firm', count: 18, avg_score: 78.5, completed_rate: 72.2 },
            { source: 'corporate', count: 15, avg_score: 71.2, completed_rate: 60.0 },
            { source: 'consulting', count: 8, avg_score: 65.8, completed_rate: 50.0 }
          ]
        })

        setRoi({
          period_days: 30,
          costs: { api_cost: 15.50, total_cost: 15.50 },
          revenue: { total_leads: 47, quality_leads: 23, assumed_lead_value: 500, estimated_revenue: 11500 },
          metrics: { profit: 11484.50, roi_percent: 740.6, cost_per_lead: 0.33, cost_per_quality_lead: 0.67, avg_lead_score: 68.2 }
        })
      }
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="w-16 h-16 border-4 border-primary border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
          <p className="text-muted-foreground">Загрузка аналитики лидов...</p>
        </div>
      </div>
    )
  }

  const overview = analytics?.overview

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white p-4">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center gap-4">
          <Link href="/">
            <Button variant="outline" size="sm">
              <ArrowLeft className="w-4 h-4 mr-2" />
              Назад
            </Button>
          </Link>
          <div>
            <h1 className="text-3xl font-bold text-gray-900">
              Аналитика лидов
            </h1>
            <p className="text-gray-600">
              ROI лид-магнита и метрики конверсии
            </p>
          </div>
        </div>

        {/* Period Selector */}
        <div className="flex gap-2">
          {[7, 30, 90].map(days => (
            <Button
              key={days}
              variant={period === days ? "default" : "outline"}
              size="sm"
              onClick={() => setPeriod(days)}
            >
              {days} дней
            </Button>
          ))}
        </div>

        {/* Overview Stats */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <Users className="w-4 h-4" />
                Всего лидов
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">{overview?.total_leads || 0}</div>
              <p className="text-xs text-green-600 mt-1">
                +{overview?.completed_magnet || 0} магнит
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <Target className="w-4 h-4" />
                Квалифицированные
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">{overview?.qualified_leads || 0}</div>
              <p className="text-xs text-muted-foreground mt-1">
                {overview?.qualification_rate?.toFixed(1) || '0.0'}% конверсии
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <BarChart3 className="w-4 h-4" />
                Средний скор
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">{overview?.avg_lead_score?.toFixed(1) || '0.0'}</div>
              <p className="text-xs text-muted-foreground mt-1">
                из 100 баллов
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <DollarSign className="w-4 h-4" />
                ROI
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold text-green-600">
                {roi?.metrics?.roi_percent?.toFixed(1) || '0.0'}%
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                ₽{roi?.metrics?.profit?.toLocaleString() || '0'} прибыли
              </p>
            </CardContent>
          </Card>
        </div>

        {/* ROI Details */}
        {roi && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <DollarSign className="w-5 h-5" />
                Детальный ROI лид-магнита
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid md:grid-cols-3 gap-6">
                <div>
                  <h4 className="font-semibold text-red-600 mb-2">Затраты</h4>
                  <div className="space-y-1 text-sm">
                    <div className="flex justify-between">
                      <span>API (OpenAI/Perplexity):</span>
                      <span>${roi.costs.api_cost.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between font-semibold">
                      <span>Итого:</span>
                      <span>${roi.costs.total_cost.toFixed(2)}</span>
                    </div>
                  </div>
                </div>

                <div>
                  <h4 className="font-semibold text-green-600 mb-2">Доходы</h4>
                  <div className="space-y-1 text-sm">
                    <div className="flex justify-between">
                      <span>Качественных лидов:</span>
                      <span>{roi.revenue.quality_leads}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Ценность лида:</span>
                      <span>₽{roi.revenue.assumed_lead_value}</span>
                    </div>
                    <div className="flex justify-between font-semibold">
                      <span>Ожидаемый доход:</span>
                      <span>₽{roi.revenue.estimated_revenue.toLocaleString()}</span>
                    </div>
                  </div>
                </div>

                <div>
                  <h4 className="font-semibold text-blue-600 mb-2">Метрики</h4>
                  <div className="space-y-1 text-sm">
                    <div className="flex justify-between">
                      <span>Прибыль:</span>
                      <span>₽{roi.metrics.profit.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Стоимость лида:</span>
                      <span>${roi.metrics.cost_per_lead.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Качественного:</span>
                      <span>${roi.metrics.cost_per_quality_lead.toFixed(2)}</span>
                    </div>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Top Leads */}
        {analytics?.top_leads && analytics.top_leads.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Target className="w-5 h-5" />
                Топ лидов по скорингу
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {analytics.top_leads.slice(0, 5).map((lead, index) => (
                  <div key={lead.user_id} className="flex items-center justify-between p-4 border rounded-lg">
                    <div className="flex items-center gap-4">
                      <div className="w-8 h-8 bg-primary text-primary-foreground rounded-full flex items-center justify-center font-bold">
                        {index + 1}
                      </div>
                      <div>
                        <div className="font-semibold">{lead.full_name || lead.username}</div>
                        <div className="text-sm text-muted-foreground">
                          {lead.company && `${lead.company} • `}
                          {lead.email}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {lead.business_focus} • {lead.expertise_level}
                        </div>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-2xl font-bold text-green-600">
                        {lead.lead_score}
                      </div>
                      <div className="text-xs text-muted-foreground">скор</div>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Sources Analysis */}
        {analytics?.sources_stats && analytics.sources_stats.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <TrendingUp className="w-5 h-5" />
                Анализ по источникам
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {analytics.sources_stats.map((source) => (
                  <div key={source.source} className="flex items-center justify-between p-4 border rounded-lg">
                    <div>
                      <div className="font-semibold capitalize">{source.source.replace('_', ' ')}</div>
                      <div className="text-sm text-muted-foreground">
                        {source.count} лидов
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-lg font-semibold">
                        {source.avg_score.toFixed(1)} ср. скор
                      </div>
                      <div className="text-sm text-green-600">
                        {source.completed_rate.toFixed(1)}% завершили
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}