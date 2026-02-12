import asyncio
from telethon import TelegramClient

async def main():
    client = TelegramClient(
        'sessions/geopulse_vox',
        REDACTED_TG_ID,
        'REDACTED_TG_HASH'
    )
    await client.start()
    me = await client.get_me()
    name = me.first_name or ''
    print('SUCCESS: Authorized as ' + name + ' id=' + str(me.id))
    await client.disconnect()

asyncio.run(main())
