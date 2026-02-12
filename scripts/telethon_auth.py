"""One-time Telethon authorization script."""
import os
import asyncio
from telethon import TelegramClient

API_ID = int(os.environ.get('TELEGRAM_API_ID', 0))
API_HASH = os.environ.get('TELEGRAM_API_HASH', '')
SESSION = os.environ.get('TELEGRAM_SESSION', 'geopulse_vox')

async def main():
    client = TelegramClient(f'/app/sessions/{SESSION}', API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    print(f'Authorized as: {me.first_name} (id={me.id})')
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
