#!/usr/bin/env python3
"""
Telegram Authorization Script
Используйте этот скрипт для первичной авторизации в Telegram API.
Запускается ОДИН РАЗ для создания session файла.
"""

import asyncio
import os
import sys
from pathlib import Path

# Добавляем путь к app для импорта settings
sys.path.insert(0, str(Path(__file__).parent.parent))

from telethon import TelegramClient
from app.config import settings


async def main():
    """Авторизация в Telegram API."""
    
    print("=" * 60)
    print("Telegram API Authorization")
    print("=" * 60)
    
    # Проверяем наличие API credentials
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("\n❌ ERROR: Telegram API credentials not configured!")
        print("\nПожалуйста, добавьте в .env файл:")
        print("  TELEGRAM_API_ID=your_api_id")
        print("  TELEGRAM_API_HASH=your_api_hash")
        print("\nКак получить:")
        print("  1. Перейдите на https://my.telegram.org")
        print("  2. Войдите с вашим номером телефона")
        print("  3. Перейдите в 'API development tools'")
        print("  4. Создайте приложение")
        print("  5. Скопируйте API ID и API Hash")
        return
    
    print(f"\nAPI ID: {settings.telegram_api_id}")
    print(f"API Hash: {settings.telegram_api_hash[:8]}...")
    print(f"Session name: {settings.telegram_session_name}")
    
    # Создаем клиент
    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash
    )
    
    print("\n📱 Connecting to Telegram...")
    await client.start()
    
    print("✅ Successfully authorized!")
    print(f"\nSession file created: {settings.telegram_session_name}.session")
    
    # Получаем информацию о пользователе
    me = await client.get_me()
    print(f"\nLogged in as:")
    print(f"  Name: {me.first_name} {me.last_name or ''}")
    print(f"  Username: @{me.username or 'N/A'}")
    print(f"  Phone: {me.phone or 'N/A'}")
    
    print("\n✅ Authorization complete!")
    print("\nТеперь можете включить Telegram channels в .env:")
    print("  TELEGRAM_CHANNELS_ENABLED=true")
    print("  TELEGRAM_CHANNELS=ai_newz,data_science_etc,ai_machinelearning_big_data")
    
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
