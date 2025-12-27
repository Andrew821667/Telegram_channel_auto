# Telegram Channels Setup Guide

Этот гайд поможет настроить чтение публичных Telegram каналов для агрегации новостей.

## 📋 Что нужно

1. **Telegram аккаунт** (ваш личный номер телефона)
2. **Telegram API credentials** (API ID и API Hash)
3. **Список публичных каналов** для мониторинга

---

## 🔑 Шаг 1: Получение Telegram API credentials

### 1.1 Перейдите на https://my.telegram.org

### 1.2 Войдите с вашим номером телефона
- Введите номер в международном формате: `+7 XXX XXX XX XX`
- Вам придет код подтверждения в Telegram

### 1.3 Перейдите в "API development tools"

### 1.4 Создайте приложение
- **App title:** `AI News Aggregator` (любое название)
- **Short name:** `ai_news` (любое)
- **Platform:** `Other`
- Нажмите **Create application**

### 1.5 Скопируйте данные
Вы получите:
- **App api_id:** например `12345678`
- **App api_hash:** например `0123456789abcdef0123456789abcdef`

⚠️ **ВАЖНО:** Никому не передавайте эти данные!

---

## ⚙️ Шаг 2: Настройка .env файла

Добавьте в файл `.env`:

```bash
# Telegram API (для чтения каналов)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
TELEGRAM_SESSION_NAME=news_fetcher
TELEGRAM_CHANNELS_ENABLED=false  # Сначала false, включите после авторизации
TELEGRAM_CHANNELS=ai_newz,data_science_etc,ai_machinelearning_big_data,legal_tech_russia
```

### Популярные Russian AI/Tech каналы:
- `ai_newz` - AI новости
- `data_science_etc` - Data Science
- `ai_machinelearning_big_data` - ML и Big Data
- `legal_tech_russia` - LegalTech Russia
- `ai_digest` - AI Digest
- `ml_engineering` - ML Engineering

---

## 🔐 Шаг 3: Первичная авторизация

### 3.1 Запустите скрипт авторизации

**Вне Docker:**
```bash
cd /home/user/Telegram_channel_auto
python scripts/telegram_auth.py
```

**Внутри Docker:**
```bash
docker compose exec app python scripts/telegram_auth.py
```

### 3.2 Введите код подтверждения
- Скрипт попросит ввести код, который придет в Telegram
- Введите код и нажмите Enter

### 3.3 Проверьте результат
Вы увидите:
```
✅ Successfully authorized!

Session file created: news_fetcher.session

Logged in as:
  Name: Ваше Имя
  Username: @your_username
  Phone: +7 XXX XXX XX XX
```

### 3.4 Включите Telegram channels

Откройте `.env` и измените:
```bash
TELEGRAM_CHANNELS_ENABLED=true
```

---

## 🐳 Шаг 4: Перезапуск Docker

### 4.1 Пересоберите контейнеры
```bash
docker compose down
docker compose build --no-cache app
docker compose up -d
```

### 4.2 Проверьте логи
```bash
docker compose logs -f celery_worker
```

Вы должны увидеть:
```
fetching_telegram_channel channel=ai_newz
telegram_message_fetched channel=ai_newz message_id=12345 views=1234
telegram_fetch_complete channel=ai_newz articles_count=10
```

---

## 📝 Примечания

### Session файл
- Файл `news_fetcher.session` создается при первой авторизации
- Хранит ваш Telegram session (как cookies)
- **НЕ КОММИТЬТЕ** в Git! (добавлен в .gitignore)
- Если удалите - нужно будет авторизоваться заново

### Безопасность
- API credentials дают доступ к вашему Telegram аккаунту
- Используйте только для чтения публичных каналов
- Не используйте для спама или нарушения ToS Telegram

### Ограничения
- Можно читать **только публичные каналы** (с username)
- Приватные каналы не поддерживаются
- Rate limits: ~20-30 каналов одновременно

### Telegram ToS
Использование соответствует Telegram ToS:
- Чтение публичной информации
- Не спам, не массовые действия
- Для личного news aggregator

---

## 🔧 Troubleshooting

### Ошибка: "telegram_api_not_configured"
✅ **Решение:** Проверьте `.env` - добавлены ли `TELEGRAM_API_ID` и `TELEGRAM_API_HASH`

### Ошибка: "telegram_not_authorized"
✅ **Решение:** Запустите `python scripts/telegram_auth.py`

### Ошибка: "telegram_channel_not_found"
✅ **Решение:** Проверьте username канала (без @)
- ✅ Правильно: `ai_newz`
- ❌ Неправильно: `@ai_newz`

### Session файл исчез
✅ **Решение:** Запустите авторизацию заново

---

## ✅ Готово!

Telegram channels теперь интегрированы в ваш news aggregator!

**Итого источников:**
- Google News RSS (RU + EN)
- Perplexity Real-Time Search
- 5 Russian RSS sources
- Hacker News
- Reddit (3 subreddits)
- ArXiv (2 categories)
- Medium (2 tags)
- **Telegram Channels** (сколько угодно) 🆕

**~130-150 статей в день** из разных источников! 🚀
