# 📰 NewsBot — городской агрегатор новостей

Telegram-бот для ведения городского паблика: собирает посты из каналов,
даёт перефразировать через AI, редактировать и публиковать — сразу или отложенно.

---

## ⚙️ Как запустить

### 1. Получи токены

| Что нужно | Где взять |
|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → /newbot |
| `ADMIN_ID` | [@userinfobot](https://t.me/userinfobot) |
| `API_ID` + `API_HASH` | [my.telegram.org](https://my.telegram.org) → API development tools |
| `OPENROUTER_KEY` | [openrouter.ai](https://openrouter.ai) → Keys (бесплатно) |

### 2. Первый запуск (авторизация Telethon)

Telethon требует разовой авторизации как обычный пользователь.
Сделай это **локально** до деплоя на Railway:

```bash
pip install -r requirements.txt
cp .env.example .env
# Заполни .env реальными значениями

python bot.py
# При первом запуске Telethon попросит номер телефона и код из Telegram
# После этого появится файл newsbot_session.session
```

### 3. Деплой на Railway

1. Загрузи проект на GitHub (без файла `.env` и `*.session`!)
2. Зайди на [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. В разделе **Variables** добавь все переменные из `.env.example`
4. Загрузи файл сессии Telethon:
   - В Railway → Files (или через Railway CLI: `railway files upload newsbot_session.session`)
   - Или используй `SESSION_STRING` — строковую сессию (см. ниже)

#### Альтернатива: строковая сессия (удобнее для Railway)

```python
# Запусти локально один раз:
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print(client.session.save())
# Скопируй вывод → добавь в Railway как SESSION_NAME (строку)
```

Затем в `bot.py` замени:
```python
TelegramClient(SESSION_NAME, API_ID, API_HASH)
# на:
TelegramClient(StringSession(SESSION_NAME), API_ID, API_HASH)
```

---

## 🤖 Команды бота

| Команда | Что делает |
|---|---|
| `/start` | Приветствие |
| `/add` | Добавить канал-источник |
| `/remove` | Удалить канал |
| `/sources` | Список каналов |
| `/fetch` | Проверить новые посты вручную |
| `/scheduled` | Список отложенных публикаций |

---

## 🔄 Как работает

1. Каждые **15 минут** бот проверяет добавленные каналы
2. Новые посты (текст + фото) прилетают тебе в личку
3. Для каждого поста кнопки:
   - **✅ Опубликовать** — сразу в канал
   - **⏰ Отложить** — введи время в формате `15:30` или `25.03 18:00`
   - **🔄 Перефразировать** — выбираешь AI-модель, получаешь Gen-Z версию
   - **✏️ Редактировать** — отправляешь свой текст или фото
   - **🗑 Пропустить** — игнор

---

## 🆓 Бесплатные AI-модели (OpenRouter)

- 🦙 **Llama 3.3 70B** — отличное качество
- 🔮 **Mistral 7B** — быстрый
- 💎 **Gemma 3 27B** — от Google
- 🌊 **DeepSeek R1** — мощный reasoning

---

## 📁 Структура

```
newsbot/
├── bot.py              # Основной код
├── requirements.txt    # Зависимости
├── Procfile            # Для Railway
├── .env.example        # Пример переменных
├── sources.json        # Список каналов (создаётся автоматически)
└── seen_posts.json     # Просмотренные посты (создаётся автоматически)
```
