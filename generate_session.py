"""
Запусти этот скрипт ОДИН РАЗ локально.
Он авторизует тебя в Telegram и выдаст SESSION_STRING —
длинную строку, которую нужно вставить в переменные Railway.

Установка:
    pip install telethon python-dotenv

Запуск:
    python generate_session.py
"""

import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv()

API_ID   = int(input("Введи API_ID: ").strip())
API_HASH = input("Введи API_HASH: ").strip()

async def main():
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        session_string = client.session.save()
        print("\n" + "="*60)
        print("✅ SESSION_STRING (скопируй в Railway → Variables):")
        print("="*60)
        print(session_string)
        print("="*60)
        print("\nВажно: держи эту строку в секрете — это доступ к аккаунту!")

asyncio.run(main())
