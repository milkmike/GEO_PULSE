import asyncio
from telethon import TelegramClient

async def main():
    client = TelegramClient(
        'sessions/geopulse_vox',
        39887137,
        '3d94816c91c3dc0142b90865c4577ad7'
    )
    await client.start()
    me = await client.get_me()
    name = me.first_name or ''
    print('SUCCESS: Authorized as ' + name + ' id=' + str(me.id))
    await client.disconnect()

asyncio.run(main())
