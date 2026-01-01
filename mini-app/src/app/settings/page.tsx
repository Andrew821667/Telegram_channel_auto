'use client'

import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { apiMethods } from '@/lib/api'
import { ArrowLeft, Save } from 'lucide-react'
import Link from 'next/link'

interface SystemSettings {
  sources: Record<string, boolean>
  llm_models: {
    analysis_model: string
    generation_model: string
    ranking_model: string
  }
  dalle: {
    enabled: boolean
    model: string
    quality: string
    size: string
  }
  auto_publish: {
    enabled: boolean
    max_per_day: number
    schedule: string[]
  }
  filtering: {
    min_quality_score: number
    min_content_length: number
    similarity_threshold: number
  }
  budget: {
    daily_limit: number
    warning_threshold: number
  }
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<SystemSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    loadSettings()
  }, [])

  const loadSettings = async () => {
    try {
      const response = await apiMethods.getSettings()
      setSettings(response.data)
    } catch (error) {
      console.error('Failed to load settings:', error)
      // Mock data
      setSettings({
        sources: {
          google_news: true,
          habr: true,
          perplexity: false,
          telegram: true,
        },
        llm_models: {
          analysis_model: 'gpt-4o-mini',
          generation_model: 'gpt-4o',
          ranking_model: 'gpt-4o-mini',
        },
        dalle: {
          enabled: true,
          model: 'dall-e-3',
          quality: 'standard',
          size: '1024x1024',
        },
        auto_publish: {
          enabled: true,
          max_per_day: 5,
          schedule: ['09:00', '14:00', '18:00'],
        },
        filtering: {
          min_quality_score: 7.0,
          min_content_length: 300,
          similarity_threshold: 0.85,
        },
        budget: {
          daily_limit: 50,
          warning_threshold: 80,
        },
      })
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    if (!settings) return

    setSaving(true)
    try {
      await apiMethods.updateSettings(settings)
      if (window.Telegram?.WebApp) {
        window.Telegram.WebApp.showPopup({
          message: 'Настройки сохранены'
        })
      }
    } catch (error) {
      console.error('Failed to save settings:', error)
    } finally {
      setSaving(false)
    }
  }

  const toggleSource = (source: string) => {
    if (!settings) return
    setSettings({
      ...settings,
      sources: {
        ...settings.sources,
        [source]: !settings.sources[source],
      },
    })
  }

  const updateDalle = (key: string, value: any) => {
    if (!settings) return
    setSettings({
      ...settings,
      dalle: {
        ...settings.dalle,
        [key]: value,
      },
    })
  }

  const updateAutoPublish = (key: string, value: any) => {
    if (!settings) return
    setSettings({
      ...settings,
      auto_publish: {
        ...settings.auto_publish,
        [key]: value,
      },
    })
  }

  const updateFiltering = (key: string, value: any) => {
    if (!settings) return
    setSettings({
      ...settings,
      filtering: {
        ...settings.filtering,
        [key]: value,
      },
    })
  }

  const updateBudget = (key: string, value: any) => {
    if (!settings) return
    setSettings({
      ...settings,
      budget: {
        ...settings.budget,
        [key]: value,
      },
    })
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
        <div className="flex items-center justify-between py-2">
          <div className="flex items-center gap-4">
            <Link href="/">
              <Button variant="ghost" size="icon">
                <ArrowLeft className="w-5 h-5" />
              </Button>
            </Link>
            <h1 className="text-2xl font-bold">Настройки системы</h1>
          </div>
          <Button onClick={handleSave} disabled={saving}>
            <Save className="w-4 h-4 mr-2" />
            Сохранить
          </Button>
        </div>

        {/* Sources */}
        <Card>
          <CardHeader>
            <CardTitle>Источники новостей</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {Object.entries(settings?.sources || {}).map(([source, enabled]) => (
              <div key={source} className="flex items-center justify-between">
                <span className="capitalize">
                  {source.replace('_', ' ').replace('google news', 'Google News')}
                </span>
                <button
                  onClick={() => toggleSource(source)}
                  className={`w-12 h-6 rounded-full transition-colors ${
                    enabled ? 'bg-green-500' : 'bg-gray-300'
                  }`}
                >
                  <div
                    className={`w-5 h-5 bg-white rounded-full shadow transition-transform ${
                      enabled ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>
            ))}
          </CardContent>
        </Card>

        {/* LLM Models */}
        <Card>
          <CardHeader>
            <CardTitle>Модели LLM</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="text-sm font-medium mb-2 block">
                Модель для анализа
              </label>
              <select
                value={settings?.llm_models.analysis_model}
                onChange={(e) =>
                  setSettings({
                    ...settings!,
                    llm_models: {
                      ...settings!.llm_models,
                      analysis_model: e.target.value,
                    },
                  })
                }
                className="w-full p-2 border rounded"
              >
                <option value="gpt-4o-mini">GPT-4o Mini</option>
                <option value="gpt-4o">GPT-4o</option>
                <option value="gpt-4">GPT-4</option>
              </select>
            </div>

            <div>
              <label className="text-sm font-medium mb-2 block">
                Модель для генерации
              </label>
              <select
                value={settings?.llm_models.generation_model}
                onChange={(e) =>
                  setSettings({
                    ...settings!,
                    llm_models: {
                      ...settings!.llm_models,
                      generation_model: e.target.value,
                    },
                  })
                }
                className="w-full p-2 border rounded"
              >
                <option value="gpt-4o-mini">GPT-4o Mini</option>
                <option value="gpt-4o">GPT-4o</option>
                <option value="gpt-4">GPT-4</option>
              </select>
            </div>

            <div>
              <label className="text-sm font-medium mb-2 block">
                Модель для ранжирования
              </label>
              <select
                value={settings?.llm_models.ranking_model}
                onChange={(e) =>
                  setSettings({
                    ...settings!,
                    llm_models: {
                      ...settings!.llm_models,
                      ranking_model: e.target.value,
                    },
                  })
                }
                className="w-full p-2 border rounded"
              >
                <option value="gpt-4o-mini">GPT-4o Mini</option>
                <option value="gpt-4o">GPT-4o</option>
                <option value="gpt-4">GPT-4</option>
              </select>
            </div>
          </CardContent>
        </Card>

        {/* DALL-E */}
        <Card>
          <CardHeader>
            <CardTitle>DALL-E генерация</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <span>Включено</span>
              <button
                onClick={() => updateDalle('enabled', !settings?.dalle.enabled)}
                className={`w-12 h-6 rounded-full transition-colors ${
                  settings?.dalle.enabled ? 'bg-green-500' : 'bg-gray-300'
                }`}
              >
                <div
                  className={`w-5 h-5 bg-white rounded-full shadow transition-transform ${
                    settings?.dalle.enabled ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            <div>
              <label className="text-sm font-medium mb-2 block">Качество</label>
              <select
                value={settings?.dalle.quality}
                onChange={(e) => updateDalle('quality', e.target.value)}
                className="w-full p-2 border rounded"
              >
                <option value="standard">Standard</option>
                <option value="hd">HD</option>
              </select>
            </div>

            <div>
              <label className="text-sm font-medium mb-2 block">Размер</label>
              <select
                value={settings?.dalle.size}
                onChange={(e) => updateDalle('size', e.target.value)}
                className="w-full p-2 border rounded"
              >
                <option value="1024x1024">1024x1024</option>
                <option value="1024x1792">1024x1792</option>
                <option value="1792x1024">1792x1024</option>
              </select>
            </div>
          </CardContent>
        </Card>

        {/* Auto Publish */}
        <Card>
          <CardHeader>
            <CardTitle>Автопубликация</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <span>Включено</span>
              <button
                onClick={() =>
                  updateAutoPublish('enabled', !settings?.auto_publish.enabled)
                }
                className={`w-12 h-6 rounded-full transition-colors ${
                  settings?.auto_publish.enabled ? 'bg-green-500' : 'bg-gray-300'
                }`}
              >
                <div
                  className={`w-5 h-5 bg-white rounded-full shadow transition-transform ${
                    settings?.auto_publish.enabled
                      ? 'translate-x-6'
                      : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            <div>
              <label className="text-sm font-medium mb-2 block">
                Макс. статей в день
              </label>
              <input
                type="number"
                value={settings?.auto_publish.max_per_day}
                onChange={(e) =>
                  updateAutoPublish('max_per_day', parseInt(e.target.value))
                }
                className="w-full p-2 border rounded"
                min="1"
                max="20"
              />
            </div>
          </CardContent>
        </Card>

        {/* Filtering */}
        <Card>
          <CardHeader>
            <CardTitle>Фильтрация</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="text-sm font-medium mb-2 block">
                Мин. оценка качества: {settings?.filtering.min_quality_score}
              </label>
              <input
                type="range"
                value={settings?.filtering.min_quality_score}
                onChange={(e) =>
                  updateFiltering('min_quality_score', parseFloat(e.target.value))
                }
                className="w-full"
                min="0"
                max="10"
                step="0.1"
              />
            </div>

            <div>
              <label className="text-sm font-medium mb-2 block">
                Мин. длина контента (символов)
              </label>
              <input
                type="number"
                value={settings?.filtering.min_content_length}
                onChange={(e) =>
                  updateFiltering('min_content_length', parseInt(e.target.value))
                }
                className="w-full p-2 border rounded"
                min="100"
                max="5000"
                step="50"
              />
            </div>

            <div>
              <label className="text-sm font-medium mb-2 block">
                Порог схожести: {settings?.filtering.similarity_threshold}
              </label>
              <input
                type="range"
                value={settings?.filtering.similarity_threshold}
                onChange={(e) =>
                  updateFiltering('similarity_threshold', parseFloat(e.target.value))
                }
                className="w-full"
                min="0"
                max="1"
                step="0.05"
              />
            </div>
          </CardContent>
        </Card>

        {/* Budget */}
        <Card>
          <CardHeader>
            <CardTitle>Бюджет API</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="text-sm font-medium mb-2 block">
                Дневной лимит ($)
              </label>
              <input
                type="number"
                value={settings?.budget.daily_limit}
                onChange={(e) =>
                  updateBudget('daily_limit', parseInt(e.target.value))
                }
                className="w-full p-2 border rounded"
                min="1"
                max="1000"
              />
            </div>

            <div>
              <label className="text-sm font-medium mb-2 block">
                Порог предупреждения (%)
              </label>
              <input
                type="number"
                value={settings?.budget.warning_threshold}
                onChange={(e) =>
                  updateBudget('warning_threshold', parseInt(e.target.value))
                }
                className="w-full p-2 border rounded"
                min="50"
                max="100"
                step="5"
              />
            </div>
          </CardContent>
        </Card>

        <div className="pb-4">
          <Button onClick={handleSave} disabled={saving} className="w-full">
            <Save className="w-4 h-4 mr-2" />
            Сохранить все настройки
          </Button>
        </div>
      </div>
    </div>
  )
}
