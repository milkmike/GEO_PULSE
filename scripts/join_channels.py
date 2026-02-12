import asyncio
from telethon import TelegramClient
from telethon.tl.functions.channels import JoinChannelRequest

CHANNELS = [
    'newsam','armtoday','novostiarmenia','SputnikARM',
    'civilnetam','mediaam','hetqonline','azatutyun',
    'reportaz','trendnewsagency','azertacofficial',
    'caliber_az','oxikiresmi','haikiresmi',
    'meydantv','toplumtv','abzasmedia',
    'onlinerby','pul_1','nexta_live','bgmedia','tutby_official',
    'zerkala_io','nashaniva','radiosvaboda','bajby',
    'formula_news','interpressnews','mtavaritv',
    'sputnikgeorgia','tabula_ge','netgazeti','batumelebi','ocmedia',
    'akipress','sputnik_kyrgyzstan','kaktus_mediakg',
    'news24kg','kloopnews','azattykkg','tandyr_media','ExpressAsia',
    'tengrinews','zakon_kz','ztb_news','nur_kz_news',
    'atamekenbusiness','informburo_kz','kazinform_news',
    'RadioAzattyq','kursivm','orda_kz','factcheckkz',
    'the_steppe','kzexclusive','almatytoday','vlast_kz','ktknews',
    'tv8md','moldovatelegraph','wtfmoldova',
    'radiolibera','newsmakermd','zdg_md','nokta_md',
    'radioozodi','akhbor_tj','asiaplustj','payam_tj',
    'turkmennews','turkmenportal','hronikatm','azathabar','progres_tm',
    'gazetauz','kunuz_ru','podrobno_uz','qalampir_uz',
    'kunuzofficial','daryouz','uzbekistan24','anhor24',
    'ozodlik','hookreport','norma_uz','repost_uz',
]

async def main():
    client = TelegramClient('sessions/geopulse_vox', 39887137, '3d94816c91c3dc0142b90865c4577ad7')
    await client.start()
    me = await client.get_me()
    print('Logged in as: ' + (me.first_name or '') + ' id=' + str(me.id))

    joined = 0
    failed = []
    for ch in CHANNELS:
        try:
            await client(JoinChannelRequest(ch))
            joined += 1
            print('[+] ' + str(joined) + '/' + str(len(CHANNELS)) + ' @' + ch)
            await asyncio.sleep(2)
        except Exception as e:
            err = str(e)[:80]
            failed.append(ch + ': ' + err)
            print('[-] FAIL @' + ch + ': ' + err)
            await asyncio.sleep(3)

    print('')
    print('Done: ' + str(joined) + '/' + str(len(CHANNELS)))
    if failed:
        print('Failed (' + str(len(failed)) + '):')
        for f in failed:
            print('  ' + f)

    await client.disconnect()

asyncio.run(main())
