import os
#!/usr/bin/env python3
import asyncio, time, os, sys
sys.path.insert(0, '/opt/cis-thermometer')

CHANNELS = [
    'zakonkz', 'ztb_qaz', 'kozachkow', 'vlastkz', 'nehabar',
    'Zanamiviehali', 'yedilov_online', 'qazaqstantv', 'astana_arnasy',
    'RedKazakh', 'respublikaKZmediaNEWS', 'centralasiamedia',
    'podrobno', 'daryo_uz', 'kun_uz', 'uzbekistan24tv',
    'news_am_channel', 'ReportAzNews', 'publika_ge',
    'belapartisan', 'charter97', 'motolkohelp', 'belta_telegramm',
    'newsmaker_md', 'jurnaltv', 'khovar', 'turkmen_portal',
    'kloopnews', 'kabar_kg',
]

async def main():
    from telethon import TelegramClient
    from telethon.tl.functions.channels import JoinChannelRequest

    client = TelegramClient(
        '/opt/cis-thermometer/sessions/geopulse_vox',
        int(os.environ.get('TELEGRAM_API_ID', '0')),
        os.environ.get('TELEGRAM_API_HASH', '')
    )
    await client.start()

    joined = 0
    failed = 0
    for ch in CHANNELS:
        try:
            print(f'  @{ch}...', end=' ', flush=True)
            await client(JoinChannelRequest(ch))
            print('OK')
            joined += 1
        except Exception as e:
            err = str(e)
            if 'already' in err.lower() or 'participant' in err.lower():
                print('(already)')
                joined += 1
            elif 'flood' in err.lower():
                wait = 60
                try:
                    wait = int(''.join(c for c in err.split('of ')[-1].split(' ')[0] if c.isdigit()))
                except:
                    pass
                print(f'FLOOD {wait}s')
                if wait <= 120:
                    time.sleep(wait + 5)
                    try:
                        await client(JoinChannelRequest(ch))
                        print(f'    retry OK')
                        joined += 1
                    except Exception as e2:
                        print(f'    retry FAIL: {e2}')
                        failed += 1
                else:
                    failed += 1
            else:
                print(f'FAIL: {str(e)[:80]}')
                failed += 1
        time.sleep(8)

    await client.disconnect()
    print(f'\nResult: {joined} joined, {failed} failed')

asyncio.run(main())
