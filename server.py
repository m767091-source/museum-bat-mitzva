"""Classroom museum: aiohttp HTTP + WebSocket realtime server.
Run: python server.py ; requirements: pip install -r requirements.txt
"""
import asyncio, json, os, re, secrets, time
from io import BytesIO
import qrcode
from pathlib import Path
from aiohttp import web
ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv('MUSEUM_DATA', ROOT / 'rooms.json'))
rooms = {}
clients = {}
lock = asyncio.Lock()
MAX_PLAYERS=35
MAX_TEAM=5


def save():
    DATA.parent.mkdir(parents=True,exist_ok=True)
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
    if not name or type(team) is not int or team not in range(1,8):return error('יש להזין שם וצוות')
    async with lock:
        r=rooms.get(code)
        if not r:return error('החדר אינו קיים',404)
        if len(r['participants'])>=MAX_PLAYERS:return error('החדר מלא',409)
        if sum(int(v['team'])==team for v in r['participants'].values())>=MAX_TEAM:return error('הקבוצה מלאה. בחרי קבוצה אחרת.',409)
        uid='p'+secrets.token_hex(9)
        secret=secrets.token_urlsafe(24)
        seat=sum(int(v['team'])==team for v in r['participants'].values())
        r['participants'][uid]={'name':name,'team':team,'joined':int(time.time()),'seat':seat}
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
            if path != 'step':return error('פעולה לא מורשית',403)
            code_input = val.get('code') if isinstance(val,dict) else None
            target = val.get('step') if isinstance(val,dict) else val
            if type(target)!=int or not 0<=target<=31:return error('שלב לא תקין',400)
            # Do not let a teacher's Next button accidentally skip the final puzzle.
            if target>=28 and r['step']<=27 and not r.get('vaultOpen',False):
                if code_input!='147':return error('יש לפענח את קוד הכספת תחילה',403)
                r['vaultOpen']=True
            if target==0:r['vaultOpen']=False
            r['step']=target
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

async def backup(req):
    """Host-only backup, including credentials needed to restore the same classroom."""
    try: payload=await req.json()
    except Exception:return error('בקשה לא תקינה')
    code=str(payload.get('room',''))
    async with lock:
        r=rooms.get(code)
        if not r:return error('החדר אינו קיים',404)
        if not secrets.compare_digest(str(payload.get('secret','')),r['secret']):return error('פעולה לא מורשית',403)
        return web.json_response({'version':1,'room':code,'data':r,'exported':int(time.time())})

async def restore(req):
    """Restore a teacher-held classroom backup after an ephemeral-disk restart."""
    try: payload=await req.json()
    except Exception:return error('בקשה לא תקינה')
    backup_data=payload.get('backup')
    if not isinstance(backup_data,dict) or backup_data.get('version')!=1:return error('קובץ גיבוי לא תקין')
    code=str(backup_data.get('room',''))
    r=backup_data.get('data')
    if not re.fullmatch(r'[0-9]{4}',code) or not isinstance(r,dict):return error('קובץ גיבוי לא תקין')
    if not all(k in r for k in ('secret','playerSecrets','participants','responses','step','vaultOpen')):return error('קובץ גיבוי חסר נתונים')
    if not isinstance(r['secret'],str) or len(r['secret'])<24 or not isinstance(r['participants'],dict) or not isinstance(r['playerSecrets'],dict) or not isinstance(r['responses'],dict) or type(r['step'])!=int or not 0<=r['step']<=31 or type(r['vaultOpen'])!=bool:return error('קובץ גיבוי לא תקין')
    if len(r['participants'])>MAX_PLAYERS or set(r['participants'])!=set(r['playerSecrets']):return error('נתוני המשתתפות אינם תקינים')
    # A restored room must respect the same classroom limits as new joins.
    team_counts={i:0 for i in range(1,8)}
    for participant in r['participants'].values():
        if not isinstance(participant,dict) or type(participant.get('team'))!=int or participant['team'] not in team_counts:
            return error('נתוני הקבוצות בגיבוי אינם תקינים')
        team_counts[participant['team']]+=1
        if team_counts[participant['team']]>MAX_TEAM:return error('בגיבוי יש קבוצה גדולה מהמותר')
    # A backup is untrusted input: validate identity and response data before restoring.
    for uid,participant in r['participants'].items():
        if (not isinstance(uid,str) or not re.fullmatch(r'p[0-9a-f]{18}',uid)
            or not isinstance(participant.get('name'),str)
            or not 1<=len(participant['name'].strip())<=26
            or not isinstance(r['playerSecrets'].get(uid),str)
            or len(r['playerSecrets'][uid])<20):
            return error('פרטי משתתפת בגיבוי אינם תקינים')
    for stage,answers in r['responses'].items():
        if stage not in map(str,range(5)) or not isinstance(answers,dict):
            return error('נתוני תשובות בגיבוי אינם תקינים')
        for uid,fields in answers.items():
            if uid not in r['participants'] or not isinstance(fields,dict):
                return error('נתוני תשובות בגיבוי אינם תקינים')
            if any(k not in ('choice','word','reflection','sequence','contribution','decrypt','question') for k in fields):
                return error('נתוני תשובות בגיבוי אינם תקינים')
            for field, value in fields.items():
                if field == 'choice' and (type(value) is not int or not 0 <= value <= 4):
                    return error('בחירה לא תקינה בגיבוי')
                if field == 'sequence' and (type(value) is not int or not 1 <= value <= 6):
                    return error('מספר רצף לא תקין בגיבוי')
                if field == 'contribution' and value != 'done':
                    return error('אישור לא תקין בגיבוי')
                limits = {'word':40, 'reflection':180, 'decrypt':1, 'question':160}
                if field in limits and (not isinstance(value,str) or len(value)>limits[field]):
                    return error('טקסט לא תקין בגיבוי')
    if r['step'] >= 28 and not r['vaultOpen']:
        return error('שלב הכספת אינו תואם למצב המשחק')
    if not secrets.compare_digest(str(payload.get('secret','')),r['secret']):return error('פעולה לא מורשית',403)
    async with lock:
        if code in rooms:return error('החדר כבר קיים. לא ניתן לדרוס חדר פעיל.',409)
        rooms[code]=r
        save()
    return web.json_response({'ok':True,'room':code,'state':public(r)})

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

async def classroom_qr(req):
    """Generate the room QR locally, without third-party CDN access."""
    code=req.query.get('room','')
    if not re.fullmatch(r'[0-9]{4}',code) or code not in rooms:
        return error('החדר אינו קיים',404)
    proto=req.headers.get('X-Forwarded-Proto',req.scheme).split(',')[0].strip()
    if proto not in ('http','https'): proto='https'
    url=f"{proto}://{req.host}/?role=player&room={code}"
    img=qrcode.make(url)
    buf=BytesIO();img.save(buf,format='PNG')
    return web.Response(body=buf.getvalue(),content_type='image/png',headers={'Cache-Control':'no-store'})

async def health(req):
    return web.json_response({'ok':True,'rooms':len(rooms)})

async def app_startup(app):
    global rooms
    if DATA.exists():
        try: rooms=json.loads(DATA.read_text(encoding='utf-8'))
        except Exception:rooms={}

@web.middleware
async def fresh_app_assets(req, handler):
    response = await handler(req)
    # A classroom must never mix an old game script with a newly deployed server.
    if req.path in ('/', '/app.js', '/style.css'):
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

app=web.Application(client_max_size=65536, middlewares=[fresh_app_assets])
app.on_startup.append(app_startup)
app.router.add_post('/api/create',create)
app.router.add_post('/api/join',join)
app.router.add_post('/api/patch',patch)
app.router.add_post('/api/backup',backup)
app.router.add_post('/api/restore',restore)
app.router.add_get('/api/health',health)
app.router.add_get('/api/qr',classroom_qr)
app.router.add_get('/ws',websocket)
app.router.add_get('/',lambda req:web.FileResponse(ROOT/'index.html'))
app.router.add_get('/app.js',lambda req:web.FileResponse(ROOT/'app.js'))
app.router.add_get('/style.css',lambda req:web.FileResponse(ROOT/'style.css'))
app.router.add_static('/videos',ROOT/'videos',show_index=False)
app.router.add_static('/scenes',ROOT/'scenes',show_index=False)
if __name__=='__main__':web.run_app(app,host='0.0.0.0',port=int(os.getenv('PORT','8080')))
