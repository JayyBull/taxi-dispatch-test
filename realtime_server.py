from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from pathlib import Path
import json,time,threading,queue,os,secrets,hashlib,hmac
import psycopg
from psycopg.rows import dict_row

HOST='0.0.0.0'; PORT=int(os.environ.get('PORT','8765')); BASE=Path(__file__).resolve().parent
DATABASE_URL=os.environ.get('DATABASE_URL','').strip(); SESSION_TTL=86400
lock=threading.Lock(); driver_clients={}; admin_clients=[]
DEFAULT_DRIVERS={'101':('John Smith','1010'),'103':('Sarah Jones','1030'),'107':('Mark Davis','1070'),'109':('Tom Wilson','1090'),'112':('Paul Turner','1120'),'115':('Steve Clark','1150'),'118':('Lisa Brown','1180'),'120':('Dan Harris','1200')}

def db(): return psycopg.connect(DATABASE_URL,row_factory=dict_row,autocommit=False)
def hash_pin(pin,salt=None):
    salt=salt or secrets.token_hex(16); digest=hashlib.pbkdf2_hmac('sha256',str(pin).encode(),salt.encode(),120000).hex(); return salt,digest
def token_hash(token): return hashlib.sha256(token.encode()).hexdigest()
def jdump(v): return json.dumps(v,separators=(',',':'))
def payload_row(row):
    p=dict(row.get('payload') or {}); p.update({k:row.get(k) for k in ('booking_ref','driver_callsign','status') if row.get(k) is not None})
    for k in ('driver_name','pickup','destination','pickup_time'):
        if row.get(k) is not None:p[k]=row[k]
    if row.get('fare') is not None:p['fare']=float(row['fare'])
    if row.get('expires_at') is not None:p['expires_at']=row['expires_at'].timestamp()
    return p

def startup():
    if not DATABASE_URL: raise SystemExit('STARTUP CHECK FAILED: DATABASE_URL is required')
    try:
        with db() as c:
            c.execute('SELECT 1')
            c.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
            applied={r['version'] for r in c.execute('SELECT version FROM schema_migrations').fetchall()}
            for f in sorted((BASE/'migrations').glob('*.sql')):
                if f.name in applied: continue
                c.execute(f.read_text()); c.execute('INSERT INTO schema_migrations(version) VALUES (%s)',(f.name,))
        with db() as c:
            n=c.execute('SELECT count(*) AS n FROM drivers').fetchone()['n']
            if n==0:
                for call,(name,pin) in DEFAULT_DRIVERS.items():
                    s,h=hash_pin(pin); c.execute('INSERT INTO drivers(callsign,name,pin_salt,pin_hash,enabled) VALUES (%s,%s,%s,%s,true)',(call,name,s,h))
        with db() as c:
            c.execute("DELETE FROM driver_sessions WHERE expires_at <= now()")
            c.execute("UPDATE driver_offers SET status='timed_out',responded_at=now() WHERE status='offered' AND expires_at<=now()")
        print('Startup checks passed: PostgreSQL reachable, migrations current, persistent state ready.')
    except Exception as e: raise SystemExit(f'STARTUP CHECK FAILED: {e}')

def verify_pin(call,pin):
    with db() as c:d=c.execute('SELECT pin_salt,pin_hash,enabled FROM drivers WHERE callsign=%s',(call,)).fetchone()
    if not d or not d['enabled']:return False
    _,candidate=hash_pin(pin,d['pin_salt']); return hmac.compare_digest(candidate,d['pin_hash'])
def emit(q,e,d):
    try:q.put_nowait((e,d))
    except:pass
def bd(c,e,d):
    with lock:qs=list(driver_clients.get(str(c),[]))
    for q in qs:emit(q,e,d)
def ba(e,d):
    with lock:qs=list(admin_clients)
    for q in qs:emit(q,e,d)
def history(c,ref,call,old,new,source='driver',details=None):
    c.execute('INSERT INTO job_status_history(booking_ref,driver_callsign,from_status,to_status,source,details) VALUES (%s,%s,%s,%s,%s,%s::jsonb)',(ref,call,old,new,source,jdump(details or {})))

def expire_loop():
    while True:
        try:
            expired=[]
            with db() as c:
                rows=c.execute("SELECT id,booking_ref,driver_callsign,payload FROM driver_offers WHERE status='offered' AND expires_at<=now() FOR UPDATE SKIP LOCKED").fetchall()
                for r in rows:
                    c.execute("UPDATE driver_offers SET status='timed_out',responded_at=now() WHERE id=%s",(r['id'],)); expired.append(r)
                c.execute('DELETE FROM driver_sessions WHERE expires_at<=now()')
            for o in expired:
                bd(o['driver_callsign'],'offer-closed',{'booking_ref':o['booking_ref'],'reason':'Timed out'}); ba('offer-response',{'booking_ref':o['booking_ref'],'driver_callsign':o['driver_callsign'],'action':'timeout'})
        except Exception as e: print('expiry worker:',e)
        time.sleep(.5)

class H(BaseHTTPRequestHandler):
    def cors(self):
        origin=self.headers.get('Origin',''); self.send_header('Access-Control-Allow-Origin',origin if origin else '*'); self.send_header('Vary','Origin'); self.send_header('Access-Control-Allow-Credentials','true'); self.send_header('Access-Control-Allow-Headers','Content-Type'); self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
    def hdr(self,code=200,ctype='application/json'):self.send_response(code);self.cors();self.send_header('Content-Type',ctype);self.end_headers()
    def do_OPTIONS(self):self.hdr(204)
    def body(self):
        n=int(self.headers.get('Content-Length','0') or 0)
        try:return json.loads(self.rfile.read(n) or b'{}')
        except:return {}
    def js(self,o,code=200,cookie=None):
        b=json.dumps(o,default=str).encode();self.send_response(code);self.cors();self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers();self.wfile.write(b)
    def cookie_token(self):
        for p in self.headers.get('Cookie','').split(';'):
            if p.strip().startswith('driver_session='):return p.strip().split('=',1)[1]
        return ''
    def auth_driver(self):
        raw=self.cookie_token()
        if not raw:return None
        th=token_hash(raw)
        with db() as c:
            s=c.execute('SELECT driver_callsign FROM driver_sessions WHERE token_hash=%s AND expires_at>now()',(th,)).fetchone()
            if not s:return None
            c.execute('UPDATE driver_sessions SET last_seen_at=now() WHERE token_hash=%s',(th,))
            return str(s['driver_callsign'])
    def require_driver(self):
        call=self.auth_driver()
        if not call:self.js({'error':'authentication required'},401);return None
        return call
    def sse(self,q,initial=None):
        self.send_response(200);self.cors();self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.send_header('Connection','keep-alive');self.end_headers()
        if initial:
            for e,d in initial:self.wfile.write(f'event: {e}\ndata: {json.dumps(d,default=str)}\n\n'.encode())
        try:
            while True:
                try:e,d=q.get(timeout=15);self.wfile.write(f'event: {e}\ndata: {json.dumps(d,default=str)}\n\n'.encode())
                except queue.Empty:self.wfile.write(b': keepalive\n\n')
                self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=='/health':
            try:
                with db() as c:
                    c.execute('SELECT 1'); jobs=c.execute("SELECT count(*) n FROM bookings WHERE status<>'Completed'").fetchone()['n']
                return self.js({'ok':True,'service':'taxi-dispatch-postgres','database':'ok','jobs':jobs})
            except Exception as e:return self.js({'ok':False,'database':'error','error':str(e)},503)
        if u.path=='/api/driver/me':
            call=self.require_driver()
            if not call:return
            with db() as c:
                d=c.execute('SELECT callsign,name,status FROM drivers WHERE callsign=%s',(call,)).fetchone(); rows=c.execute("SELECT * FROM bookings WHERE driver_callsign=%s AND status<>'Completed' ORDER BY updated_at DESC",(call,)).fetchall()
            return self.js({'authenticated':True,'callsign':call,'name':d['name'],'status':d['status'],'jobs':[payload_row(r) for r in rows]})
        if u.path=='/api/admin/events':
            q=queue.Queue();
            with lock:admin_clients.append(q)
            try:return self.sse(q)
            finally:
                with lock:
                    if q in admin_clients:admin_clients.remove(q)
        if u.path=='/api/driver/events':
            call=self.require_driver()
            if not call:return
            q=queue.Queue()
            with db() as c:
                offers=c.execute("SELECT booking_ref,driver_callsign,status,payload,expires_at FROM driver_offers WHERE driver_callsign=%s AND status='offered' AND expires_at>now() ORDER BY created_at",(call,)).fetchall(); jobs=c.execute("SELECT * FROM bookings WHERE driver_callsign=%s AND status<>'Completed' ORDER BY updated_at DESC",(call,)).fetchall()
            initial=[('offer',payload_row(o)) for o in offers]+[('job-assigned',payload_row(j)) for j in jobs]
            with lock:driver_clients.setdefault(call,[]).append(q)
            try:return self.sse(q,initial)
            finally:
                with lock:
                    if q in driver_clients.get(call,[]):driver_clients[call].remove(q)
        if u.path in ('/','/index.html'):
            try:b=(BASE/'index.html').read_bytes();self.send_response(200);self.cors();self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();return self.wfile.write(b)
            except:return self.js({'error':'dispatch portal missing'},404)
        if u.path in ('/driver','/driver.html'):
            try:b=(BASE/'driver.html').read_bytes();self.send_response(200);self.cors();self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();return self.wfile.write(b)
            except:return self.js({'error':'driver portal missing'},404)
        if u.path=='/health':
            try:
                with db() as c:c.execute('SELECT 1').fetchone()
                return self.js({'ok':True,'database':'connected'})
            except Exception as e:return self.js({'ok':False,'database':'unavailable'},503)
        return self.js({'error':'not found'},404)
    def do_POST(self):
        u=urlparse(self.path);d=self.body()
        if u.path=='/api/driver/login':
            call=str(d.get('callsign','')).strip();pin=str(d.get('pin','')).strip()
            if not verify_pin(call,pin):return self.js({'error':'Invalid callsign or PIN'},401)
            raw=secrets.token_urlsafe(32);th=token_hash(raw)
            with db() as c:
                name=c.execute('SELECT name FROM drivers WHERE callsign=%s',(call,)).fetchone()['name'];c.execute('INSERT INTO driver_sessions(token_hash,driver_callsign,expires_at) VALUES (%s,%s,now()+(%s * interval \'1 second\'))',(th,call,SESSION_TTL))
            return self.js({'ok':True,'callsign':call,'name':name},200,f'driver_session={raw}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}')
        if u.path=='/api/driver/logout':
            raw=self.cookie_token();call=self.auth_driver()
            with db() as c:
                if raw:c.execute('DELETE FROM driver_sessions WHERE token_hash=%s',(token_hash(raw),))
                if call:c.execute("UPDATE drivers SET status='Offline' WHERE callsign=%s",(call,))
            if call:ba('driver-status',{'driver_callsign':call,'status':'Offline'})
            return self.js({'ok':True},200,'driver_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0')
        if u.path=='/api/admin/drivers/sync':
            call=str(d.get('callsign','')).strip();name=str(d.get('name','Driver '+call)).strip();pin=str(d.get('pin','')).strip();enabled=bool(d.get('enabled',True))
            if not call:return self.js({'error':'callsign required'},400)
            with db() as c:
                old=c.execute('SELECT callsign FROM drivers WHERE callsign=%s',(call,)).fetchone()
                if old:
                    if pin:s,h=hash_pin(pin);c.execute('UPDATE drivers SET name=%s,pin_salt=%s,pin_hash=%s,enabled=%s WHERE callsign=%s',(name,s,h,enabled,call))
                    else:c.execute('UPDATE drivers SET name=%s,enabled=%s WHERE callsign=%s',(name,enabled,call))
                elif pin:
                    s,h=hash_pin(pin);c.execute('INSERT INTO drivers(callsign,name,pin_salt,pin_hash,enabled) VALUES (%s,%s,%s,%s,%s)',(call,name,s,h,enabled))
                else:return self.js({'error':'PIN required for a new driver'},400)
            return self.js({'ok':True,'callsign':call})
        if u.path=='/api/offers':
            ref=str(d.get('booking_ref',''));call=str(d.get('driver_callsign',''));timeout=max(5,int(d.get('timeout_seconds',30)))
            if not ref or not call:return self.js({'error':'booking_ref and driver_callsign required'},400)
            with db() as c:
                drv=c.execute('SELECT name,enabled FROM drivers WHERE callsign=%s',(call,)).fetchone()
                if not drv or not drv['enabled']:return self.js({'error':'driver is not authorised for offers'},403)
                c.execute("UPDATE driver_offers SET status='closed',responded_at=now() WHERE booking_ref=%s AND status='offered'",(ref,))
                row=c.execute("INSERT INTO driver_offers(booking_ref,driver_callsign,status,timeout_seconds,payload,expires_at) VALUES (%s,%s,'offered',%s,%s::jsonb,now()+(%s * interval '1 second')) RETURNING expires_at",(ref,call,timeout,jdump(d),timeout)).fetchone()
            out=dict(d);out.update(status='offered',expires_at=row['expires_at'].timestamp());bd(call,'offer',out);return self.js({'ok':True,'offer':out},201)
        if u.path.startswith('/api/offers/') and u.path.endswith('/respond'):
            call=self.require_driver()
            if not call:return
            ref=u.path.split('/')[3];action=d.get('action')
            if action not in ('accept','decline'):return self.js({'error':'action must be accept or decline'},400)
            with db() as c:
                o=c.execute("SELECT * FROM driver_offers WHERE booking_ref=%s AND status='offered' ORDER BY created_at DESC LIMIT 1 FOR UPDATE",(ref,)).fetchone()
                if not o:return self.js({'error':'offer not found'},404)
                if o['driver_callsign']!=call:return self.js({'error':'offer is not assigned to this authenticated driver'},403)
                if o['expires_at'].timestamp()<time.time():return self.js({'error':'offer expired'},409)
                c.execute("UPDATE driver_offers SET status=%s,responded_at=now() WHERE id=%s",('accepted' if action=='accept' else 'declined',o['id']))
                if action=='accept':
                    p=dict(o['payload'] or {}); old=c.execute('SELECT status FROM bookings WHERE booking_ref=%s',(ref,)).fetchone();oldst=old['status'] if old else None
                    c.execute("INSERT INTO bookings(booking_ref,driver_callsign,driver_name,pickup,destination,pickup_time,fare,status,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,'Assigned',%s::jsonb) ON CONFLICT(booking_ref) DO UPDATE SET driver_callsign=excluded.driver_callsign,driver_name=excluded.driver_name,pickup=excluded.pickup,destination=excluded.destination,pickup_time=excluded.pickup_time,fare=excluded.fare,status='Assigned',payload=excluded.payload",(ref,call,p.get('driver_name'),p.get('pickup',''),p.get('destination',''),p.get('pickup_time'),p.get('fare') or None,jdump(p)))
                    c.execute("UPDATE drivers SET status='Busy' WHERE callsign=%s",(call,));history(c,ref,call,oldst,'Assigned','offer',{'action':'accept'})
                    j=c.execute('SELECT * FROM bookings WHERE booking_ref=%s',(ref,)).fetchone()
            bd(call,'offer-closed',{'booking_ref':ref,'reason':action.title()});ba('offer-response',{'booking_ref':ref,'driver_callsign':call,'action':action})
            if action=='accept':out=payload_row(j);bd(call,'job-assigned',out);ba('job-status',out);ba('driver-status',{'driver_callsign':call,'status':'Busy'})
            return self.js({'ok':True})
        if u.path.startswith('/api/jobs/') and u.path.endswith('/status'):
            call=self.require_driver()
            if not call:return
            ref=u.path.split('/')[3];new=d.get('status');allowed={'Assigned':'En Route','En Route':'Arrived','Arrived':'Passenger On Board','Passenger On Board':'Completed'}
            with db() as c:
                j=c.execute('SELECT * FROM bookings WHERE booking_ref=%s FOR UPDATE',(ref,)).fetchone()
                if not j:return self.js({'error':'job not found'},404)
                if str(j['driver_callsign'])!=call:return self.js({'error':'job belongs to another driver'},403)
                old=j['status']
                if allowed.get(old)!=new:return self.js({'error':'invalid status transition'},409)
                c.execute('UPDATE bookings SET status=%s WHERE booking_ref=%s',(new,ref));c.execute('UPDATE drivers SET status=%s WHERE callsign=%s',('Available' if new=='Completed' else 'Busy',call));history(c,ref,call,old,new)
                out=payload_row(c.execute('SELECT * FROM bookings WHERE booking_ref=%s',(ref,)).fetchone())
            bd(call,'job-status',out);ba('job-status',out);ba('driver-status',{'driver_callsign':call,'status':'Available' if new=='Completed' else 'Busy'})
            if new=='Completed':bd(call,'job-closed',{'booking_ref':ref,'reason':'Completed'})
            return self.js({'ok':True,'job':out})
        if u.path=='/api/driver/status':
            call=self.require_driver()
            if not call:return
            st=d.get('status')
            if st not in ('Available','Busy','Offline'):return self.js({'error':'invalid driver status'},400)
            with db() as c:c.execute('UPDATE drivers SET status=%s WHERE callsign=%s',(st,call))
            ba('driver-status',{'driver_callsign':call,'status':st});return self.js({'ok':True,'driver_callsign':call,'status':st})
        if u.path.startswith('/api/offers/') and u.path.endswith('/close'):
            ref=u.path.split('/')[3]
            with db() as c:
                o=c.execute("UPDATE driver_offers SET status='closed',responded_at=now() WHERE booking_ref=%s AND status='offered' RETURNING driver_callsign",(ref,)).fetchone()
            if o:bd(o['driver_callsign'],'offer-closed',{'booking_ref':ref,'reason':d.get('reason','Closed')})
            return self.js({'ok':True})
        return self.js({'error':'not found'},404)
    def log_message(self,*a):pass

startup();threading.Thread(target=expire_loop,daemon=True).start();print(f'PostgreSQL taxi dispatch server running on http://localhost:{PORT}');ThreadingHTTPServer((HOST,PORT),H).serve_forever()
