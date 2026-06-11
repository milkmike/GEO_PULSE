import os
import asyncio
from telethon import TelegramClient

async def main():
    client = TelegramClient(
        'sessions/geopulse_vox',
        int(os.environ.get('TELEGRAM_API_ID', '0')),
        os.environ.get('TELEGRAM_API_HASH', '')
    )
    await client.start()
    me = await client.get_me()
    name = me.first_name or ''
    print('SUCCESS: Authorized as ' + name + ' id=' + str(me.id))
    await client.disconnect()

asyncio.run(main())
