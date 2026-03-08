"""
NewsBot — агрегатор новостей для городского паблика.
Читает Telegram-каналы через Telethon (StringSession),
управление через отдельный бот (aiogram 3).
"""

import asyncio
import logging
import os
import json
import re
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    BufferedInputFile,
)
from telethon import TelegramClient
from telethon.sessions import StringSession
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ──────────────────────────────────────────────────────
BOT_TOKEN      = os.environ["BOT_TOKEN"]
ADMIN_ID       = int(os.environ["ADMIN_ID"])
TARGET_CHANNEL = os.environ["TARGET_CHANNEL"]
API_ID         = int(os.environ["API_ID"])
API_HASH       = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")

SOURCES_FILE = "sources.json"
SEEN_FILE    = "seen_posts.json"

# ── ХРАНИЛИЩЕ ─────────────────────────────────────────────────────────────────
def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

sources:  list[str]            = load_json(SOURCES_FILE, [])
seen_ids: dict[str, list[int]] = load_json(SEEN_FILE, {})

def save_sources(): save_json(SOURCES_FILE, sources)
def save_seen():    save_json(SEEN_FILE, seen_ids)

# ── AI-МОДЕЛИ (OpenRouter, бесплатные) ────────────────────────────────────────
FREE_MODELS = {
    "🦙 Llama 3.3 70B":   "meta-llama/llama-3.3-70b-instruct:free",
    "🔮 Mistral Nemo":     "mistralai/mistral-nemo:free",
    "💎 Gemma 3 12B":      "google/gemma-3-12b-it:free",
    "🌊 DeepSeek V3":      "deepseek/deepseek-chat-v3-5:free",
}

REPHRASE_PROMPT = (
    "Ты редактор молодёжного городского паблика. "
    "Перефразируй новость живым, дерзким языком Gen-Z (2026): "
    "короткие предложения, разговорный стиль, без канцелярита, "
    "можно добавить 1-2 уместных эмодзи. "
    "Верни ТОЛЬКО готовый текст, без пояснений.\n\nТекст:\n"
)

async def rephrase_text(text: str, model_key: str) -> str:
    model_id = FREE_MODELS[model_key]
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": REPHRASE_PROMPT + text}],
        "max_tokens": 600,
        "temperature": 0.8,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://newsbot.app",
                "X-Title": "CityNewsBot",
            },
            json=payload,
        )
        # Показываем подробную ошибку от OpenRouter
        if r.status_code != 200:
            try:
                err = r.json()
                raise ValueError(f"OpenRouter {r.status_code}: {err}")
            except Exception as e:
                raise ValueError(str(e))
        data = r.json()
        # Иногда OpenRouter возвращает ошибку внутри 200
        if "error" in data:
            raise ValueError(f"OpenRouter error: {data['error']}")
        return data["choices"][0]["message"]["content"].strip()

# ── TELETHON CLIENT ───────────────────────────────────────────────────────────
user_client: Optional[TelegramClient] = None

async def get_user_client() -> TelegramClient:
    global user_client
    if user_client is None or not user_client.is_connected():
        user_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
        await user_client.start()
    return user_client

# ── AIOGRAM ───────────────────────────────────────────────────────────────────
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp  = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

class EditState(StatesGroup):
    waiting_content = State()

class ScheduleState(StatesGroup):
    waiting_time = State()

class AddSource(StatesGroup):
    waiting_channel = State()

drafts:    dict[str, dict] = {}   # uid -> {text, photo_id}
scheduled: list[dict]      = []
scheduler  = AsyncIOScheduler()

# ── КЛАВИАТУРЫ ────────────────────────────────────────────────────────────────
def kb_main(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать",    callback_data=f"pub_now:{uid}"),
            InlineKeyboardButton(text="⏰ Отложить",         callback_data=f"pub_later:{uid}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Перефразировать", callback_data=f"rephrase:{uid}"),
            InlineKeyboardButton(text="✏️ Редактировать",   callback_data=f"edit:{uid}"),
        ],
        [InlineKeyboardButton(text="🗑 Пропустить",         callback_data=f"skip:{uid}")],
    ])

def kb_models(uid: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=name, callback_data=f"model:{uid}:{name}")]
            for name in FREE_MODELS]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"back:{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_after_rephrase(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать",         callback_data=f"pub_now:{uid}"),
            InlineKeyboardButton(text="⏰ Отложить",              callback_data=f"pub_later:{uid}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Перефразировать ещё",  callback_data=f"rephrase:{uid}"),
            InlineKeyboardButton(text="✏️ Редактировать",        callback_data=f"edit:{uid}"),
        ],
        [InlineKeyboardButton(text="🗑 Пропустить", callback_data=f"skip:{uid}")],
    ])

def kb_after_edit(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub_now:{uid}"),
            InlineKeyboardButton(text="⏰ Отложить",     callback_data=f"pub_later:{uid}"),
        ],
        [InlineKeyboardButton(text="🗑 Пропустить", callback_data=f"skip:{uid}")],
    ])

# ── УТИЛИТЫ ───────────────────────────────────────────────────────────────────
def uid_key(channel: str, msg_id: int) -> str:
    return f"{channel}_{msg_id}"

async def publish_post(text: str, photo_id: Optional[str] = None):
    if photo_id:
        await bot.send_photo(TARGET_CHANNEL, photo=photo_id, caption=text)
    else:
        await bot.send_message(TARGET_CHANNEL, text)

# ── ПАРСЕР КАНАЛОВ ────────────────────────────────────────────────────────────
async def fetch_new_posts():
    if not sources:
        log.info("Нет источников — пропускаю.")
        return
    client = await get_user_client()
    new_posts = []

    for ch in sources:
        seen = seen_ids.get(ch, [])
        try:
            entity = await client.get_entity(ch)
            async for msg in client.iter_messages(entity, limit=10):
                if msg.id in seen:
                    continue
                text = msg.text or msg.message or ""
                photo_bytes = None
                if msg.photo:
                    photo_bytes = await client.download_media(msg.photo, bytes)
                if not text and not photo_bytes:
                    seen.append(msg.id)
                    continue
                new_posts.append({
                    "uid":         uid_key(ch, msg.id),
                    "channel":     ch,
                    "text":        text,
                    "photo_bytes": photo_bytes,
                })
                seen.append(msg.id)
            seen_ids[ch] = seen[-200:]
        except Exception as e:
            log.warning(f"Ошибка при чтении {ch}: {e}")

    save_seen()
    log.info(f"Новых постов: {len(new_posts)}")
    for post in new_posts:
        await send_post_to_admin(post)
        await asyncio.sleep(1.5)

async def send_post_to_admin(post: dict):
    uid    = post["uid"]
    text   = post["text"] or ""
    header = f"📬 <b>Новый пост из {post['channel']}</b>\n\n"
    full   = header + text if text else header + "📷 <i>Только фото</i>"

    drafts[uid] = {"text": text, "photo_id": None}
    kb = kb_main(uid)

    if post.get("photo_bytes"):
        file = BufferedInputFile(post["photo_bytes"], filename="photo.jpg")
        msg  = await bot.send_photo(ADMIN_ID, photo=file, caption=full, reply_markup=kb)
        drafts[uid]["photo_id"] = msg.photo[-1].file_id
    else:
        await bot.send_message(ADMIN_ID, full, reply_markup=kb)

# ── КОМАНДЫ ───────────────────────────────────────────────────────────────────
@router.message(Command("start"))
async def cmd_start(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer(
        "👋 <b>NewsBot запущен!</b>\n\n"
        "Команды:\n"
        "  /sources — список источников\n"
        "  /add — добавить канал\n"
        "  /remove — удалить канал\n"
        "  /fetch — проверить посты прямо сейчас\n"
        "  /scheduled — отложенные публикации\n\n"
        "Бот автоматически проверяет каналы каждые 15 минут 🔄"
    )

@router.message(Command("sources"))
async def cmd_sources(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    if not sources:
        await msg.answer("📋 Источников пока нет.\nДобавь через /add")
        return
    text = f"📋 <b>Источники ({len(sources)}):</b>\n"
    text += "\n".join(f"  {i+1}. <code>{s}</code>" for i, s in enumerate(sources))
    await msg.answer(text)

@router.message(Command("add"))
async def cmd_add(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer("Введи username канала:\nНапример: <code>@citylife</code>")
    await state.set_state(AddSource.waiting_channel)

@router.message(AddSource.waiting_channel)
async def process_add(msg: Message, state: FSMContext):
    ch = msg.text.strip()
    if not ch.startswith("@"):
        ch = "@" + ch
    if ch in sources:
        await msg.answer(f"⚠️ <code>{ch}</code> уже в списке.")
    else:
        sources.append(ch)
        save_sources()
        await msg.answer(f"✅ Добавлен: <code>{ch}</code>\nВсего источников: {len(sources)}")
    await state.clear()

@router.message(Command("remove"))
async def cmd_remove(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    if not sources:
        await msg.answer("📋 Список источников пуст.")
        return
    rows = [[InlineKeyboardButton(text=f"🗑 {s}", callback_data=f"del_src:{s}")]
            for s in sources]
    await msg.answer("Выбери канал для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data.startswith("del_src:"))
async def cb_del_src(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    ch = call.data.split(":", 1)[1]
    if ch in sources:
        sources.remove(ch)
        save_sources()
    await call.message.edit_text(f"✅ Удалён: <code>{ch}</code>\nОсталось: {len(sources)}")
    await call.answer()

@router.message(Command("fetch"))
async def cmd_fetch(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer("🔍 Проверяю каналы...")
    await fetch_new_posts()
    if not sources:
        await msg.answer("⚠️ Нет источников. Добавь через /add")

@router.message(Command("scheduled"))
async def cmd_scheduled(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    active = [s for s in scheduled if s["run_at"] > datetime.now()]
    if not active:
        await msg.answer("📭 Отложенных публикаций нет.")
        return
    lines = []
    for i, s in enumerate(active):
        t = s["run_at"].strftime("%d.%m.%Y %H:%M")
        preview = (s.get("text") or "📷 Фото")[:50]
        lines.append(f"{i+1}. <b>{t}</b>\n    {preview}...")
    await msg.answer("⏰ <b>Отложенные:</b>\n\n" + "\n\n".join(lines))

# ── КОЛБЭКИ ───────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("pub_now:"))
async def cb_pub_now(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    uid = call.data.split(":", 1)[1]
    d = drafts.get(uid, {})
    try:
        await publish_post(d.get("text", ""), d.get("photo_id"))
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer("✅ <b>Опубликовано!</b>")
        drafts.pop(uid, None)
    except Exception as e:
        await call.message.answer(f"❌ Ошибка: {e}")
    await call.answer()

@router.callback_query(F.data.startswith("pub_later:"))
async def cb_pub_later(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    uid = call.data.split(":", 1)[1]
    await state.set_state(ScheduleState.waiting_time)
    await state.update_data(uid=uid)
    await call.message.answer(
        "⏰ <b>Введи время публикации:</b>\n\n"
        "Только время (сегодня): <code>15:30</code>\n"
        "Дата и время: <code>25.03 18:00</code>"
    )
    await call.answer()

@router.message(ScheduleState.waiting_time)
async def process_schedule_time(msg: Message, state: FSMContext):
    text = msg.text.strip()
    now  = datetime.now()
    try:
        if re.match(r"^\d{1,2}:\d{2}$", text):
            h, m   = map(int, text.split(":"))
            run_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if run_at <= now:
                run_at += timedelta(days=1)
        elif re.match(r"^\d{2}\.\d{2} \d{2}:\d{2}$", text):
            run_at = datetime.strptime(f"{now.year}.{text}", "%Y.%d.%m %H:%M")
        else:
            raise ValueError
    except ValueError:
        await msg.answer("❌ Неверный формат.\nПопробуй: <code>15:30</code> или <code>25.03 18:00</code>")
        return

    d      = drafts.get(uid, {})
    job_id = f"sched_{datetime.now().timestamp()}"

    async def job(t=d.get("text",""), p=d.get("photo_id")):
        await publish_post(t, p)

    scheduler.add_job(job, "date", run_date=run_at, id=job_id)
    scheduled.append({"text": d.get("text",""), "photo_id": d.get("photo_id"), "run_at": run_at, "job_id": job_id})
    await msg.answer(f"✅ <b>Запланировано</b> на {run_at.strftime('%d.%m.%Y в %H:%M')}")
    await state.clear()

@router.callback_query(F.data.startswith("rephrase:"))
async def cb_rephrase(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    if not OPENROUTER_KEY:
        await call.answer("⚠️ OPENROUTER_KEY не задан!", show_alert=True)
        return
    uid = call.data.split(":", 1)[1]
    await call.message.edit_reply_markup(reply_markup=kb_models(uid))
    await call.answer()

@router.callback_query(F.data.startswith("model:"))
async def cb_model_select(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    parts = call.data.split(":", 2)
    uid, model_name = parts[1], parts[2]
    d        = drafts.get(uid, {})
    original = d.get("text", "")

    if not original:
        await call.answer("Нет текста для перефразирования.", show_alert=True)
        return

    await call.answer("⏳ Генерирую...")
    status = await call.message.answer(f"🔄 Перефразирую через <b>{model_name}</b>...")

    try:
        new_text = await rephrase_text(original, model_name)
        drafts[uid]["text"] = new_text
        await status.delete()
        kb = kb_after_rephrase(uid)
        if d.get("photo_id"):
            try:
                await call.message.edit_caption(caption=new_text, reply_markup=kb)
            except Exception:
                await call.message.answer(new_text, reply_markup=kb)
        else:
            try:
                await call.message.edit_text(new_text, reply_markup=kb)
            except Exception:
                await call.message.answer(new_text, reply_markup=kb)
    except Exception as e:
        await status.edit_text(f"❌ Ошибка при перефразировании: {e}")

@router.callback_query(F.data.startswith("back:"))
async def cb_back(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    uid = call.data.split(":", 1)[1]
    await call.message.edit_reply_markup(reply_markup=kb_main(uid))
    await call.answer()

@router.callback_query(F.data.startswith("edit:"))
async def cb_edit(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    uid = call.data.split(":", 1)[1]
    await state.set_state(EditState.waiting_content)
    await state.update_data(uid=uid)
    await call.message.answer(
        "✏️ <b>Режим редактирования</b>\n\n"
        "Отправь новый текст, или фото с подписью.\n"
        "Только текст — фото из оригинала сохранится."
    )
    await call.answer()

@router.message(EditState.waiting_content)
async def process_edit(msg: Message, state: FSMContext):
    data = await state.get_data()
    uid  = data.get("uid", "edit")
    d    = drafts.get(uid, {})

    if msg.photo:
        d["photo_id"] = msg.photo[-1].file_id
        d["text"]     = msg.caption or d.get("text", "")
    elif msg.text:
        d["text"] = msg.text
    drafts[uid] = d

    kb = kb_after_edit(uid)
    if d.get("photo_id"):
        await msg.answer_photo(photo=d["photo_id"], caption=d.get("text",""), reply_markup=kb)
    else:
        await msg.answer(d.get("text",""), reply_markup=kb)
    await state.clear()

@router.callback_query(F.data.startswith("skip:"))
async def cb_skip(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Пропущено 🗑")

# ── ЗАПУСК ────────────────────────────────────────────────────────────────────
async def main():
    scheduler.add_job(fetch_new_posts, "interval", minutes=15, id="auto_fetch")
    scheduler.start()
    log.info("🚀 NewsBot запущен. Автопроверка каждые 15 минут.")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
