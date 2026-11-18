"""Classroom museum: aiohttp HTTP + WebSocket realtime server.
Run: python server.py ; requirements: pip install -r requirements.txt
"""
import asyncio, json, os, re, secrets, time
from pathlib import Path
from aiohttp import web
ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv('MUSEUM_DATA', ROOT / 'rooms.json'))
rooms = {}
clients = {}
lock = asyncio.Lock()
MAX_PLAYERS=36
TEAM_LIMITS={1:5,2:5,3:5,4:5,5:5,6:5,7:6}


def save():
    tmp=DATA.with_suffix('.tmp')
    tmp.write_text(json.dumps(rooms,ensure_ascii=False),encoding='utf-8')
    tmp.replace(DATA)


def public(room):
    return {k:v for k,v in room.items() if k not in ('secret','playerSecrets')}


def error(message,status=400):
    return web.json_response({'error':message},status=status)

async def broadcast(code):
    msg=json.dumps({'type':'state','state':public(rooms[code])},ensure_ascii=False)
    for ws in list(clients.get(code,set())):
        try: await ws.send_str(msg)
        except Exception: clients[code].discard(ws)

async def create(req):
    async with lock:
        for _ in range(100):
            code=str(secrets.randbelow(9000)+1000)
            if code not in rooms: break
        else:return error('אין קודים פנויים',503)
        secret=secrets.token_urlsafe(32)
        rooms[code]={'step':0,'created':int(time.time()),'participants':{},'responses':{},'vaultOpen':False,'secret':secret,'playerSecrets':{}}
        save()
        return web.json_response({'room':code,'secret':secret,'state':public(rooms[code])})

async def join(req):
    try: p=await req.json()
    except:return error('בקשה לא תקינה')
    code=str(p.get('room',''))
    name=str(p.get('name','')).strip()[:26]
    team=p.get('team')
    if not re.fullmatch(r'[0-9]{4}',code):return error('קוד חדר לא תקין')
    if not name or not isinstance(team,int) or team not in range(1,8):return error('יש להזין שם וצוות')
    async with lock:
        r=rooms.get(code)
        if not r:return error('החדר אינו קיים',404)
        if len(r['participants'])>=MAX_PLAYERS:return error('החדר מלא',409)
        if sum(int(v['team'])==team for v in r['participants'].values())>=TEAM_LIMITS[team]:return error('הקבוצה מלאה. בחרי קבוצה אחרת.',409)
        uid='p'+secrets.token_hex(9)
        secret=secrets.token_urlsafe(24)
        r['participants'][uid]={'name':name,'team':team,'joined':int(time.time())}
        r['playerSecrets'][uid]=secret
        save()
        await broadcast(code)
        return web.json_response({'uid':uid,'secret':secret})

async def patch(req):
    try:p=await req.json()
    except:return error('בקשה לא תקינה')
    code=str(p.get('room',''))
    path=str(p.get('path',''))
    val=p.get('value')
    secret=p.get('secret','')
    async with lock:
        r=rooms.get(code)
        if not r:return error('החדר אינו קיים',404)
        parts=path.split('/')
        is_host=secrets.compare_digest(str(secret),r['secret'])
        if is_host:
            if path != 'step' or type(val)!=int or not 0<=val<=29:return error('פעולה לא מורשית',403)
            r['step']=val
        else:
            if len(parts)==4 and parts[0]=='responses' and parts[1] in map(str,range(5)) and parts[3] in ('choice','word','reflection','sequence','contribution','decrypt','question'):
                i,uid,field=parts[1],parts[2],parts[3]
                if r['playerSecrets'].get(uid)!=secret:return error('פעולה לא מורשית',403)
                if field=='choice' and (type(val)!=int or not 0<=val<=4):return error('תשובה שגויה')
                if field=='sequence' and (type(val)!=int or not 1<=val<=6):return error('תחנה לא תקינה')
                if field=='contribution' and val!='done':return error('אישור לא תקין')
                if field=='decrypt' and (not isinstance(val,str) or len(val)>1):return error('אות לא תקינה')
                if field=='question' and (not isinstance(val,str) or len(val)>160):return error('אורך השאלה אינו תקין')
                if field=='word' and (not isinstance(val,str) or len(val)>40):return error('אורך הטקסט אינו תקין')
                if field=='reflection' and (not isinstance(val,str) or len(val)>180):return error('אורך הטקסט אינו תקין')
                r['responses'].setdefault(i,{}).setdefault(uid,{})[field]=val
            elif len(parts)==3 and parts[0]=='participants' and parts[2]=='finalValue':
                uid=parts[1]
                if r['playerSecrets'].get(uid)!=secret:return error('פעולה לא מורשית',403)
                if val not in ['מנהיגות','התמדה','אומץ','סקרנות','יצירה']:return error('ערך לא תקין')
                r['participants'][uid]['finalValue']=val
            else:return error('פעולה לא מורשית',403)
        save()
        await broadcast(code)
    return web.json_response({'ok':True})

async def websocket(req):
    code=req.query.get('room','')
    if code not in rooms:return error('החדר אינו קיים',404)
    ws=web.WebSocketResponse(heartbeat=25,max_msg_size=2048)
    await ws.prepare(req)
    clients.setdefault(code,set()).add(ws)
    await ws.send_json({'type':'state','state':public(rooms[code])})
    try:
        async for _ in ws:pass
    finally: clients[code].discard(ws)
    return ws

async def backup(req):
    code=req.query.get('room','')
    secret=req.headers.get('X-Host-Secret','')
    async with lock:
        r=rooms.get(code)
        if not r or not secrets.compare_digest(secret,r['secret']):
            return error('אין הרשאת מנחה',403)
        payload={'format':'museum-backup-v1','room':code,'data':r}
        return web.json_response(payload,headers={'Content-Disposition':f'attachment; filename="museum-{code}-backup.json"','Cache-Control':'no-store'})

async def restore(req):
    try: payload=await req.json()
    except: return error('קובץ גיבוי לא תקין')
    if payload.get('format')!='museum-backup-v1' or not isinstance(payload.get('data'),dict):return error('מבנה גיבוי לא תקין')
    code=str(payload.get('room',''))
    r=payload['data']
    if not re.fullmatch(r'[0-9]{4}',code) or not isinstance(r.get('participants'),dict) or not isinstance(r.get('playerSecrets'),dict) or not isinstance(r.get('secret'),str) or not isinstance(r.get('responses'),dict):return error('נתוני גיבוי לא תקינים')
    if len(r['participants'])>MAX_PLAYERS or not isinstance(r.get('step'),int) or not 0<=r['step']<=29:return error('גיבוי חורג ממגבלות המשחק')
    for n in range(1,8):
        if sum(v.get('team')==n for v in r['participants'].values() if isinstance(v,dict))>TEAM_LIMITS[n]:return error('חלוקת קבוצות שגויה')
    async with lock:
        if code in rooms:return error('קוד החדר כבר פעיל; לא שוחזר דבר',409)
        rooms[code]=r
        save()
    return web.json_response({'room':code,'secret':r['secret'],'restored':True})

async def health(req):
    return web.json_response({'ok':True,'rooms':len(rooms)})

async def app_startup(app):
    global rooms
    if DATA.exists():
        try: rooms=json.loads(DATA.read_text(encoding='utf-8'))
        except Exception:rooms={}

app=web.Application(client_max_size=65536)
app.on_startup.append(app_startup)
app.router.add_post('/api/create',create)
app.router.add_post('/api/join',join)
app.router.add_post('/api/patch',patch)
app.router.add_get('/api/health',health)
app.router.add_get('/api/backup',backup)
app.router.add_post('/api/restore',restore)
app.router.add_get('/ws',websocket)
app.router.add_get('/',lambda req:web.FileResponse(ROOT/'index.html'))
app.router.add_get('/app.js',lambda req:web.FileResponse(ROOT/'app.js'))
app.router.add_get('/style.css',lambda req:web.FileResponse(ROOT/'style.css'))
app.router.add_static('/videos',ROOT/'videos',show_index=False)
if __name__=='__main__':web.run_app(app,host='0.0.0.0',port=int(os.getenv('PORT','8080')))
