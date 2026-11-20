{\rtf1\ansi\ansicpg1251\cocoartf2869
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\paperw11900\paperh16840\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx566\tx1133\tx1700\tx2267\tx2834\tx3401\tx3968\tx4535\tx5102\tx5669\tx6236\tx6803\pardirnatural\partightenfactor0

\f0\fs24 \cf0 """\
AURATECH \'97 AI-\uc0\u1089 \u1090 \u1080 \u1083 \u1080 \u1089 \u1090  (lite MVP \u1074  \u1086 \u1076 \u1085 \u1086 \u1084  \u1092 \u1072 \u1081 \u1083 \u1077 ).\
\uc0\u1050 \u1080 \u1086 \u1089 \u1082 : /   \u1040 \u1076 \u1084 \u1080 \u1085 \u1082 \u1072 : /admin   \u1047 \u1076 \u1086 \u1088 \u1086 \u1074 \u1100 \u1077 : /healthz\
\uc0\u1041 \u1077 \u1079  GIGACHAT_AUTH_KEY \u1088 \u1072 \u1073 \u1086 \u1090 \u1072 \u1077 \u1090  \u1074  \u1088 \u1077 \u1078 \u1080 \u1084 \u1077  \u1087 \u1088 \u1072 \u1074 \u1080 \u1083 ; \u1089  \u1082 \u1083 \u1102 \u1095 \u1086 \u1084  \u1074 \u1082 \u1083 \u1102 \u1095 \u1072 \u1077 \u1090  \u1048 \u1048 .\
"""\
import hashlib, io, json, logging, os, random, re, sqlite3, threading, time, uuid\
from pathlib import Path\
\
import httpx\
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile\
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse\
from PIL import Image, ImageDraw, ImageFont\
\
# ---------------------------------------------------------------- \uc0\u1082 \u1086 \u1085 \u1092 \u1080 \u1075  ----\
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "auratech-admin")\
SECRET = os.environ.get("SECRET_KEY", "auratech-secret")\
GC_KEY = os.environ.get("GIGACHAT_AUTH_KEY", "").strip()\
GC_SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")\
GC_MODEL = os.environ.get("GIGACHAT_MODEL", "GigaChat-2-Max")\
GC_FALLBACK = os.environ.get("GIGACHAT_FALLBACK_MODEL", "GigaChat-Pro")\
GC_VERIFY = os.environ.get("GIGACHAT_VERIFY_SSL", "true").lower() != "false"\
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))\
MEDIA = DATA_DIR / "media"\
(MEDIA / "products").mkdir(parents=True, exist_ok=True)\
(MEDIA / "photos").mkdir(parents=True, exist_ok=True)\
(MEDIA / "gen").mkdir(parents=True, exist_ok=True)\
DB_PATH = DATA_DIR / "auratech.db"\
logging.basicConfig(level=logging.INFO)\
log = logging.getLogger("auratech")\
\
\
class UserError(Exception):\
    pass\
\
\
class GCError(Exception):\
    pass\
\
\
def db():\
    c = sqlite3.connect(DB_PATH, timeout=30)\
    c.row_factory = sqlite3.Row\
    return c\
\
\
def init_db():\
    c = db()\
    c.execute("PRAGMA journal_mode=WAL")\
    c.executescript("""\
    CREATE TABLE IF NOT EXISTS archetypes(\
      aid TEXT PRIMARY KEY, name TEXT, desc TEXT, gender TEXT,\
      age_from INTEGER, age_to INTEGER, height_from INTEGER, height_to INTEGER,\
      body TEXT, color_type TEXT, styles TEXT, rec_colors TEXT, avoid_colors TEXT);\
    CREATE TABLE IF NOT EXISTS products(\
      sku TEXT PRIMARY KEY, name TEXT, brand TEXT, category TEXT, gender TEXT,\
      sizes TEXT, colors TEXT, price REAL, season TEXT, styles TEXT, occasions TEXT,\
      cut TEXT, location TEXT, stock INTEGER, margin REAL, photo TEXT, comp TEXT);\
    CREATE TABLE IF NOT EXISTS sessions(\
      sid TEXT PRIMARY KEY, created REAL, status TEXT, result TEXT);\
    CREATE TABLE IF NOT EXISTS events(\
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, sid TEXT, event TEXT, payload TEXT);\
    """)\
    c.commit()\
    c.close()\
\
\
init_db()\
\
EVENTS = \{"questionnaire_started", "questionnaire_completed", "outfit_shown",\
          "where_clicked", "upsell_added"\}\
\
\
def log_event(conn, sid, event, payload=None):\
    conn.execute("INSERT INTO events(ts,sid,event,payload) VALUES (?,?,?,?)",\
                 (time.time(), sid, event, json.dumps(payload or \{\}, ensure_ascii=False)))\
\
\
def cleanup_photos(ttl=15 * 60):\
    now = time.time()\
    for f in (MEDIA / "photos").iterdir():\
        try:\
            if f.is_file() and now - f.stat().st_mtime > ttl:\
                f.unlink()\
        except OSError:\
            pass\
\
\
# ------------------------------------------------------------ \uc0\u1091 \u1090 \u1080 \u1083 \u1080 \u1090 \u1099  ----\
def tags(s):\
    return [t.strip().lower() for t in re.split(r"[,;/]", s or "") if t.strip()]\
\
\
def norm(c):\
    return str(c).strip().lower().replace("\uc0\u1105 ", "\u1077 ")\
\
\
COLOR_FAMILY = \{\
    "\uc0\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ": "\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081 ", "\u1073 \u1077 \u1083 \u1099 \u1081 ": "\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081 ", "\u1089 \u1077 \u1088 \u1099 \u1081 ": "\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081 ",\
    "\uc0\u1075 \u1088 \u1072 \u1092 \u1080 \u1090 \u1086 \u1074 \u1099 \u1081 ": "\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081 ", "\u1073 \u1077 \u1078 \u1077 \u1074 \u1099 \u1081 ": "\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081 ", "\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ": "\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081 ",\
    "\uc0\u1084 \u1086 \u1083 \u1086 \u1095 \u1085 \u1099 \u1081 ": "\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081 ", "\u1093 \u1072 \u1082 \u1080 ": "\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081 ",\
    "\uc0\u1089 \u1080 \u1085 \u1080 \u1081 ": "\u1089 \u1080 \u1085 \u1080 \u1081 ", "\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ": "\u1089 \u1080 \u1085 \u1080 \u1081 ",\
    "\uc0\u1079 \u1077 \u1083 \u1077 \u1085 \u1099 \u1081 ": "\u1079 \u1077 \u1083 \u1077 \u1085 \u1099 \u1081 ", "\u1086 \u1083 \u1080 \u1074 \u1082 \u1086 \u1074 \u1099 \u1081 ": "\u1079 \u1077 \u1083 \u1077 \u1085 \u1099 \u1081 ", "\u1080 \u1079 \u1091 \u1084 \u1088 \u1091 \u1076 \u1085 \u1099 \u1081 ": "\u1079 \u1077 \u1083 \u1077 \u1085 \u1099 \u1081 ",\
    "\uc0\u1082 \u1088 \u1072 \u1089 \u1085 \u1099 \u1081 ": "\u1082 \u1088 \u1072 \u1089 \u1085 \u1099 \u1081 ", "\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 ": "\u1082 \u1088 \u1072 \u1089 \u1085 \u1099 \u1081 ", "\u1074 \u1080 \u1085 \u1085 \u1099 \u1081 ": "\u1082 \u1088 \u1072 \u1089 \u1085 \u1099 \u1081 ",\
    "\uc0\u1086 \u1088 \u1072 \u1085 \u1078 \u1077 \u1074 \u1099 \u1081 ": "\u1086 \u1088 \u1072 \u1085 \u1078 \u1077 \u1074 \u1099 \u1081 ", "\u1088 \u1099 \u1078 \u1080 \u1081 ": "\u1086 \u1088 \u1072 \u1085 \u1078 \u1077 \u1074 \u1099 \u1081 ",\
    "\uc0\u1078 \u1077 \u1083 \u1090 \u1099 \u1081 ": "\u1078 \u1077 \u1083 \u1090 \u1099 \u1081 ", "\u1079 \u1086 \u1083 \u1086 \u1090 \u1080 \u1089 \u1090 \u1099 \u1081 ": "\u1078 \u1077 \u1083 \u1090 \u1099 \u1081 ",\
    "\uc0\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ": "\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ", "\u1087 \u1091 \u1076 \u1088 \u1086 \u1074 \u1099 \u1081 ": "\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ",\
    "\uc0\u1092 \u1080 \u1086 \u1083 \u1077 \u1090 \u1086 \u1074 \u1099 \u1081 ": "\u1092 \u1080 \u1086 \u1083 \u1077 \u1090 \u1086 \u1074 \u1099 \u1081 ", "\u1089 \u1080 \u1088 \u1077 \u1085 \u1077 \u1074 \u1099 \u1081 ": "\u1092 \u1080 \u1086 \u1083 \u1077 \u1090 \u1086 \u1074 \u1099 \u1081 ",\
    "\uc0\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ": "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ", "\u1096 \u1086 \u1082 \u1086 \u1083 \u1072 \u1076 \u1085 \u1099 \u1081 ": "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ",\
\}\
\
\
def colors_ok(a, b):\
    fa = COLOR_FAMILY.get(norm(a), norm(a))\
    fb = COLOR_FAMILY.get(norm(b), norm(b))\
    return fa == fb or fa == "\uc0\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081 " or fb == "\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081 "\
\
\
def palette_ok(set_a, set_b):\
    return any(colors_ok(x, y) for x in set_a for y in set_b)\
\
\
LETTERS = ["XS", "S", "M", "L", "XL", "XXL"]\
\
\
def size_variants(size):\
    s = str(size).strip().upper()\
    if s in LETTERS:\
        i = LETTERS.index(s)\
        out = [s] + [LETTERS[j] for j in (i + 1, i - 1, i + 2, i - 2) if 0 <= j < len(LETTERS)]\
        return out\
    if s.isdigit():\
        n = int(s)\
        return [s, str(n - 1), str(n + 1), str(n - 2), str(n + 2)]\
    return [s]\
\
\
def has_size(p, size, cat):\
    try:\
        sizes = json.loads(p["sizes"] or "[]")\
    except Exception:\
        sizes = []\
    raw = str(size).strip()\
    if cat == "\uc0\u1072 \u1082 \u1089 \u1077 \u1089 \u1089 \u1091 \u1072 \u1088 " or not sizes:\
        return True, (sizes[0] if sizes else "OS"), True\
    for cand in size_variants(raw):\
        if cand in sizes:\
            return True, cand, (cand == raw or cand == raw.upper())\
    return False, None, False\
\
\
def _font(size):\
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf"):\
        try:\
            return ImageFont.truetype(name, size)\
        except Exception:\
            pass\
    try:\
        return ImageFont.load_default(size=size)\
    except TypeError:\
        return ImageFont.load_default()\
\
\
CAT_BG = \{"\uc0\u1074 \u1077 \u1088 \u1093 ": "#dfe7f5", "\u1085 \u1080 \u1079 ": "#e7f0df", "\u1074 \u1077 \u1088 \u1093 \u1085 \u1103 \u1103  \u1086 \u1076 \u1077 \u1078 \u1076 \u1072 ": "#e8e3f5",\
          "\uc0\u1087 \u1083 \u1072 \u1090 \u1100 \u1077 ": "#f7e3ee", "\u1086 \u1073 \u1091 \u1074 \u1100 ": "#f5e9df", "\u1072 \u1082 \u1089 \u1077 \u1089 \u1089 \u1091 \u1072 \u1088 ": "#f5f2df"\}\
\
\
def make_placeholder(path, sku, name, cat):\
    path.parent.mkdir(parents=True, exist_ok=True)\
    img = Image.new("RGB", (480, 600), CAT_BG.get(cat, "#eef1f6"))\
    d = ImageDraw.Draw(img)\
    d.rectangle([10, 10, 469, 589], outline="#ffffff", width=3)\
    d.text((24, 24), "AURATECH", fill="#8a8a8a", font=_font(16))\
    f1, f2 = _font(28), _font(18)\
    d.text(((480 - d.textlength(sku, font=f1)) / 2, 262), sku, fill="#1c1c1c", font=f1)\
    t2 = name[:30]\
    d.text(((480 - min(d.textlength(t2, font=f2), 400)) / 2, 312), t2, fill="#444444", font=f2)\
    img.save(path, "JPEG", quality=88)\
\
\
# ------------------------------------------------------------- GigaChat ----\
_gc_token = \{"tok": None, "exp": 0\}\
_gc_lock = threading.Lock()\
_gc_cache = \{\}\
AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"\
API_URL = "https://gigachat.devices.sberbank.ru/api/v1"\
\
\
def gc_available():\
    return bool(GC_KEY)\
\
\
def _cache_get(k):\
    v = _gc_cache.get(k)\
    return v[1] if v and time.time() - v[0] < 3600 else None\
\
\
def _cache_put(k, v):\
    if len(_gc_cache) > 400:\
        _gc_cache.clear()\
    _gc_cache[k] = (time.time(), v)\
\
\
def _token():\
    with _gc_lock:\
        if _gc_token["tok"] and time.time() < _gc_token["exp"] - 60:\
            return _gc_token["tok"]\
    r = httpx.post(AUTH_URL,\
                   headers=\{"Authorization": "Basic " + GC_KEY,\
                            "RqUID": str(uuid.uuid4()),\
                            "Content-Type": "application/x-www-form-urlencoded",\
                            "Accept": "application/json"\},\
                   data=\{"scope": GC_SCOPE\}, verify=GC_VERIFY, timeout=30)\
    r.raise_for_status()\
    tok = r.json()["access_token"]\
    with _gc_lock:\
        _gc_token["tok"], _gc_token["exp"] = tok, time.time() + 25 * 60\
    return tok\
\
\
def _retry(fn, attempts=3):\
    delay = 1.0\
    last = None\
    for i in range(attempts):\
        try:\
            return fn()\
        except GCError:\
            raise\
        except Exception as e:\
            last = e\
            if i == attempts - 1:\
                break\
            time.sleep(delay)\
            delay *= 2\
    raise GCError(f"GigaChat \uc0\u1085 \u1077 \u1076 \u1086 \u1089 \u1090 \u1091 \u1087 \u1077 \u1085  \u1087 \u1086 \u1089 \u1083 \u1077  \{attempts\} \u1087 \u1086 \u1087 \u1099 \u1090 \u1086 \u1082  (\{type(last).__name__\}: \{last\})")\
\
\
def _auth_headers(tok):\
    return \{"Authorization": "Bearer " + tok, "Content-Type": "application/json",\
            "Accept": "application/json"\}\
\
\
def gc_chat(messages, temperature=0.2, max_tokens=1200):\
    payload = \{"messages": messages, "temperature": temperature, "max_tokens": max_tokens\}\
    key = "chat:" + hashlib.sha256(\
        (json.dumps(payload, sort_keys=True, ensure_ascii=False) + GC_MODEL).encode()).hexdigest()\
    hit = _cache_get(key)\
    if hit is not None:\
        return hit\
\
    def call():\
        tok = _token()\
        r = httpx.post(API_URL + "/chat/completions", headers=_auth_headers(tok),\
                       json=payload, verify=GC_VERIFY, timeout=90)\
        if r.status_code == 401:\
            with _gc_lock:\
                _gc_token["tok"] = None\
            raise RuntimeError("401 token expired")\
        r.raise_for_status()\
        return r.json()["choices"][0]["message"].get("content") or ""\
\
    text = _retry(call)\
    _cache_put(key, text)\
    return text\
\
\
def _extract_json(text):\
    m = re.search(r"\\\{.*\\\}", text or "", re.S)\
    if not m:\
        raise ValueError("JSON \uc0\u1085 \u1077  \u1085 \u1072 \u1081 \u1076 \u1077 \u1085 ")\
    return json.loads(m.group(0))\
\
\
def gc_json(messages, validate=None, attempts=2):\
    msgs = list(messages)\
    for _ in range(attempts):\
        text = gc_chat(msgs, temperature=0.1)\
        try:\
            data = _extract_json(text)\
            if validate:\
                validate(data)\
            return data\
        except Exception:\
            msgs = msgs + [\
                \{"role": "assistant", "content": text[:800]\},\
                \{"role": "user", "content": "\uc0\u1042 \u1077 \u1088 \u1085 \u1080  \u1058 \u1054 \u1051 \u1068 \u1050 \u1054  \u1082 \u1086 \u1088 \u1088 \u1077 \u1082 \u1090 \u1085 \u1099 \u1081  JSON \u1073 \u1077 \u1079  \u1087 \u1086 \u1103 \u1089 \u1085 \u1077 \u1085 \u1080 \u1081  \u1080  markdown."\},\
            ]\
    raise GCError("GigaChat \uc0\u1085 \u1077  \u1074 \u1077 \u1088 \u1085 \u1091 \u1083  \u1074 \u1072 \u1083 \u1080 \u1076 \u1085 \u1099 \u1081  JSON")\
\
\
PHOTO_PROMPT = """\uc0\u1058 \u1099  \u1072 \u1089 \u1089 \u1080 \u1089 \u1090 \u1077 \u1085 \u1090  \u1089 \u1090 \u1080 \u1083 \u1080 \u1089 \u1090 \u1072  \u1086 \u1092 \u1083 \u1072 \u1081 \u1085 -\u1084 \u1072 \u1075 \u1072 \u1079 \u1080 \u1085 \u1072  \u1086 \u1076 \u1077 \u1078 \u1076 \u1099 . \u1053 \u1072  \u1092 \u1086 \u1090 \u1086  \u1095 \u1077 \u1083 \u1086 \u1074 \u1077 \u1082 ; \u1079 \u1072 \u1076 \u1072 \u1095 \u1072  \'97 \u1087 \u1086 \u1084 \u1086 \u1095 \u1100  \u1087 \u1086 \u1076 \u1086 \u1073 \u1088 \u1072 \u1090 \u1100  \u1086 \u1076 \u1077 \u1078 \u1076 \u1091 .\
\uc0\u1042 \u1077 \u1088 \u1085 \u1080  \u1058 \u1054 \u1051 \u1068 \u1050 \u1054  JSON \u1073 \u1077 \u1079  \u1087 \u1086 \u1103 \u1089 \u1085 \u1077 \u1085 \u1080 \u1081 :\
\{"\uc0\u1090 \u1077 \u1083 \u1086 \u1089 \u1083 \u1086 \u1078 \u1077 \u1085 \u1080 \u1077 ": "<\u1086 \u1076 \u1085 \u1086  \u1080 \u1079 : \u1072 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 , \u1085 \u1086 \u1088 \u1084 \u1086 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 , \u1089 \u1087 \u1086 \u1088 \u1090 \u1080 \u1074 \u1085 \u1086 \u1077 , \u1082 \u1088 \u1091 \u1087 \u1085 \u1086 \u1077 >",\
 "\uc0\u1090 \u1086 \u1085 _\u1074 \u1086 \u1083 \u1086 \u1089 ": "<\u1082 \u1088 \u1072 \u1090 \u1082 \u1086 >", "\u1090 \u1086 \u1085 _\u1082 \u1086 \u1078 \u1080 ": "<\u1082 \u1088 \u1072 \u1090 \u1082 \u1086 >",\
 "\uc0\u1094 \u1074 \u1077 \u1090 \u1086 \u1090 \u1080 \u1087 ": "<\u1086 \u1076 \u1085 \u1086  \u1080 \u1079 : \u1074 \u1077 \u1089 \u1085 \u1072 , \u1083 \u1077 \u1090 \u1086 , \u1086 \u1089 \u1077 \u1085 \u1100 , \u1079 \u1080 \u1084 \u1072 >", "\u1089 \u1090 \u1080 \u1083 \u1100 ": "<1-3 \u1089 \u1083 \u1086 \u1074 \u1072 >", "\u1091 \u1074 \u1077 \u1088 \u1077 \u1085 \u1085 \u1086 \u1089 \u1090 \u1100 ": <0-100>\}\
\uc0\u1047 \u1040 \u1055 \u1056 \u1045 \u1065 \u1045 \u1053 \u1054  \u1086 \u1087 \u1088 \u1077 \u1076 \u1077 \u1083 \u1103 \u1090 \u1100  \u1088 \u1072 \u1089 \u1091 , \u1101 \u1090 \u1085 \u1080 \u1095 \u1085 \u1086 \u1089 \u1090 \u1100 , \u1079 \u1076 \u1086 \u1088 \u1086 \u1074 \u1100 \u1077 , \u1083 \u1080 \u1095 \u1085 \u1086 \u1089 \u1090 \u1100 , \u1080 \u1084 \u1103 , \u1090 \u1086 \u1095 \u1085 \u1099 \u1081  \u1074 \u1086 \u1079 \u1088 \u1072 \u1089 \u1090 . \u1053 \u1045  \u1086 \u1087 \u1080 \u1089 \u1099 \u1074 \u1072 \u1081  \u1083 \u1080 \u1094 \u1086 .\
\uc0\u1045 \u1089 \u1083 \u1080  \u1095 \u1077 \u1083 \u1086 \u1074 \u1077 \u1082 \u1072  \u1074  \u1086 \u1076 \u1077 \u1078 \u1076 \u1077  \u1085 \u1077 \u1090  \'97 \u1074 \u1077 \u1088 \u1085 \u1080  \{"\u1086 \u1096 \u1080 \u1073 \u1082 \u1072 ": "<\u1087 \u1088 \u1080 \u1095 \u1080 \u1085 \u1072 >"\}."""\
\
\
def gc_analyze_photo(path):\
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()\
    ck = "vis:" + digest\
    hit = _cache_get(ck)\
    if hit is not None:\
        return hit\
\
    def upload():\
        tok = _token()\
        with open(path, "rb") as f:\
            r = httpx.post(API_URL + "/files", headers=\{"Authorization": "Bearer " + tok\},\
                           files=\{"file": (os.path.basename(path), f, "image/jpeg")\},\
                           data=\{"purpose": "general"\}, verify=GC_VERIFY, timeout=90)\
        r.raise_for_status()\
        return r.json()["id"]\
\
    def call():\
        fid = upload()\
        tok = _token()\
        r = httpx.post(API_URL + "/chat/completions", headers=_auth_headers(tok),\
                       json=\{"messages": [\{"role": "user", "content": PHOTO_PROMPT,\
                                           "attachments": [fid]\}],\
                             "temperature": 0.1, "max_tokens": 400\},\
                       verify=GC_VERIFY, timeout=90)\
        r.raise_for_status()\
        return r.json()["choices"][0]["message"].get("content") or ""\
\
    data = _extract_json(_retry(call))\
    _cache_put(ck, data)\
    return data\
\
\
def gc_image(description):\
    key = "img:" + hashlib.sha256(description.encode()).hexdigest()\
    hit = _cache_get(key)\
    if hit is not None:\
        return hit\
\
    def call():\
        tok = _token()\
        payload = \{"messages": [\{"role": "user", "content": "\uc0\u1053 \u1072 \u1088 \u1080 \u1089 \u1091 \u1081 : " + description\}],\
                   "function_call": "auto", "temperature": 0.7\}\
        file_id = None\
        for _ in range(4):\
            r = httpx.post(API_URL + "/chat/completions", headers=_auth_headers(_token()),\
                           json=payload, verify=GC_VERIFY, timeout=120)\
            r.raise_for_status()\
            msg = r.json()["choices"][0]["message"]\
            content = msg.get("content") or ""\
            m = re.search(r'<img[^>]+src="([\\w\\-]\{8,80\})"', content)\
            if m:\
                file_id = m.group(1)\
                break\
            atts = msg.get("attachments") or []\
            if atts:\
                file_id = atts[0]\
                break\
            fc = msg.get("function_call")\
            if fc and fc.get("name") == "text2image":\
                args = fc.get("args")\
                if not isinstance(args, str):\
                    args = json.dumps(args or \{\}, ensure_ascii=False)\
                payload["messages"].append(\{"role": "assistant", "content": content,\
                                            "function_call": \{"name": "text2image", "args": args\}\})\
                payload["messages"].append(\{"role": "function", "name": "text2image", "content": ""\})\
                continue\
            break\
        if not file_id:\
            raise GCError("\uc0\u1052 \u1086 \u1076 \u1077 \u1083 \u1100  \u1085 \u1077  \u1074 \u1077 \u1088 \u1085 \u1091 \u1083 \u1072  \u1080 \u1079 \u1086 \u1073 \u1088 \u1072 \u1078 \u1077 \u1085 \u1080 \u1077 ")\
        r = httpx.get(API_URL + f"/files/\{file_id\}/content",\
                      headers=\{"Authorization": "Bearer " + _token()\},\
                      verify=GC_VERIFY, timeout=120)\
        r.raise_for_status()\
        return r.content\
\
    data = _retry(call)\
    _cache_put(key, data)\
    return data\
\
\
# ------------------------------------------------------------ \uc0\u1076 \u1077 \u1084 \u1086 -\u1076 \u1072 \u1085 \u1085 \u1099 \u1077  ----\
CT_COLORS = \{\
    "\uc0\u1074 \u1077 \u1089 \u1085 \u1072 ": (["\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ", "\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ", "\u1086 \u1083 \u1080 \u1074 \u1082 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 "], ["\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 "]),\
    "\uc0\u1083 \u1077 \u1090 \u1086 ": (["\u1089 \u1077 \u1088 \u1099 \u1081 ", "\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ", "\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ", "\u1089 \u1080 \u1085 \u1080 \u1081 "], ["\u1086 \u1088 \u1072 \u1085 \u1078 \u1077 \u1074 \u1099 \u1081 ", "\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 "]),\
    "\uc0\u1086 \u1089 \u1077 \u1085 \u1100 ": (["\u1073 \u1077 \u1078 \u1077 \u1074 \u1099 \u1081 ", "\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 ", "\u1086 \u1083 \u1080 \u1074 \u1082 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 "], ["\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ", "\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 "]),\
    "\uc0\u1079 \u1080 \u1084 \u1072 ": (["\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1073 \u1077 \u1083 \u1099 \u1081 ", "\u1082 \u1088 \u1072 \u1089 \u1085 \u1099 \u1081 ", "\u1089 \u1080 \u1085 \u1080 \u1081 "], ["\u1086 \u1088 \u1072 \u1085 \u1078 \u1077 \u1074 \u1099 \u1081 ", "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 "]),\
\}\
\
ARCH_SPECS = [\
    ("A01", "\uc0\u1044 \u1077 \u1083 \u1086 \u1074 \u1072 \u1103  \u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", 25, 45, 155, 180, "\u1085 \u1086 \u1088 \u1084 \u1086 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1086 \u1089 \u1077 \u1085 \u1100 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 , \u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1087 \u1088 \u1103 \u1084 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 , \u1091 \u1084 \u1077 \u1088 \u1077 \u1085 \u1085 \u1072 \u1103  \u1087 \u1088 \u1080 \u1090 \u1072 \u1083 \u1077 \u1085 \u1085 \u1086 \u1089 \u1090 \u1100 "),\
    ("A02", "\uc0\u1044 \u1077 \u1083 \u1086 \u1074 \u1072 \u1103  \u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", 25, 45, 170, 195, "\u1085 \u1086 \u1088 \u1084 \u1086 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1079 \u1080 \u1084 \u1072 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 , \u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1087 \u1088 \u1103 \u1084 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 , \u1087 \u1080 \u1076 \u1078 \u1072 \u1082 "),\
    ("A03", "\uc0\u1043 \u1086 \u1088 \u1086 \u1076 \u1089 \u1082 \u1086 \u1081  \u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", 20, 40, 150, 178, "\u1072 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1083 \u1077 \u1090 \u1086 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 , \u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ", "\u1089 \u1074 \u1086 \u1073 \u1086 \u1076 \u1085 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 , \u1089 \u1083 \u1086 \u1080 "),\
    ("A04", "\uc0\u1043 \u1086 \u1088 \u1086 \u1076 \u1089 \u1082 \u1086 \u1081  \u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", 20, 40, 168, 192, "\u1089 \u1087 \u1086 \u1088 \u1090 \u1080 \u1074 \u1085 \u1086 \u1077 ", "\u1074 \u1077 \u1089 \u1085 \u1072 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 , \u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ", "\u1089 \u1074 \u1086 \u1073 \u1086 \u1076 \u1085 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 "),\
    ("A05", "\uc0\u1057 \u1087 \u1086 \u1088 \u1090 \u1080 \u1074 \u1085 \u1099 \u1081  \u1089 \u1090 \u1080 \u1083 \u1100 ", "\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", 18, 35, 150, 180, "\u1089 \u1087 \u1086 \u1088 \u1090 \u1080 \u1074 \u1085 \u1086 \u1077 ", "\u1074 \u1077 \u1089 \u1085 \u1072 ", "\u1089 \u1087 \u1086 \u1088 \u1090 , \u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1082 \u1086 \u1084 \u1092 \u1086 \u1088 \u1090 \u1085 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 "),\
    ("A06", "\uc0\u1057 \u1087 \u1086 \u1088 \u1090 \u1080 \u1074 \u1085 \u1099 \u1081  \u1089 \u1090 \u1080 \u1083 \u1100 ", "\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", 18, 35, 170, 195, "\u1089 \u1087 \u1086 \u1088 \u1090 \u1080 \u1074 \u1085 \u1086 \u1077 ", "\u1083 \u1077 \u1090 \u1086 ", "\u1089 \u1087 \u1086 \u1088 \u1090 , \u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1082 \u1086 \u1084 \u1092 \u1086 \u1088 \u1090 \u1085 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 "),\
    ("A07", "\uc0\u1056 \u1086 \u1084 \u1072 \u1085 \u1090 \u1080 \u1082 \u1072 ", "\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", 18, 35, 150, 175, "\u1072 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1074 \u1077 \u1089 \u1085 \u1072 ", "\u1088 \u1086 \u1084 \u1072 \u1085 \u1090 \u1080 \u1082 \u1072 , \u1074 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081 ", "\u1089 \u1090 \u1088 \u1091 \u1103 \u1097 \u1080 \u1077 \u1089 \u1103  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 "),\
    ("A08", "\uc0\u1041 \u1086 \u1093 \u1086 ", "\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", 25, 45, 150, 185, "", "\u1086 \u1089 \u1077 \u1085 \u1100 ", "\u1073 \u1086 \u1093 \u1086 , \u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1084 \u1085 \u1086 \u1075 \u1086 \u1089 \u1083 \u1086 \u1081 \u1085 \u1086 \u1089 \u1090 \u1100 , \u1089 \u1074 \u1086 \u1073 \u1086 \u1076 \u1085 \u1099 \u1081  \u1082 \u1088 \u1086 \u1081 "),\
    ("A09", "\uc0\u1052 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ", "\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", 25, 45, 150, 180, "\u1072 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1079 \u1080 \u1084 \u1072 ", "\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 , \u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1095 \u1080 \u1089 \u1090 \u1099 \u1077  \u1083 \u1080 \u1085 \u1080 \u1080 "),\
    ("A10", "\uc0\u1052 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ", "\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", 25, 45, 170, 195, "\u1085 \u1086 \u1088 \u1084 \u1086 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1079 \u1080 \u1084 \u1072 ", "\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 , \u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1095 \u1080 \u1089 \u1090 \u1099 \u1077  \u1083 \u1080 \u1085 \u1080 \u1080 "),\
    ("A11", "\uc0\u1042 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081  \u1074 \u1099 \u1093 \u1086 \u1076 ", "\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", 25, 55, 150, 185, "\u1085 \u1086 \u1088 \u1084 \u1086 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1079 \u1080 \u1084 \u1072 ", "\u1074 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081 , \u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1101 \u1083 \u1077 \u1075 \u1072 \u1085 \u1090 \u1085 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 "),\
    ("A12", "\uc0\u1042 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081  \u1074 \u1099 \u1093 \u1086 \u1076 ", "\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", 30, 55, 170, 195, "\u1085 \u1086 \u1088 \u1084 \u1086 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1086 \u1089 \u1077 \u1085 \u1100 ", "\u1074 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081 , \u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1101 \u1083 \u1077 \u1075 \u1072 \u1085 \u1090 \u1085 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 "),\
    ("A13", "\uc0\u1057 \u1090 \u1088 \u1080 \u1090 -\u1089 \u1090 \u1072 \u1081 \u1083 ", "\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", 16, 30, 150, 180, "", "\u1083 \u1077 \u1090 \u1086 ", "\u1089 \u1090 \u1088 \u1080 \u1090 , \u1089 \u1087 \u1086 \u1088 \u1090 ", "\u1086 \u1074 \u1077 \u1088 \u1089 \u1072 \u1081 \u1079 , \u1089 \u1083 \u1086 \u1080 "),\
    ("A14", "\uc0\u1057 \u1090 \u1088 \u1080 \u1090 -\u1089 \u1090 \u1072 \u1081 \u1083 ", "\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", 16, 30, 165, 195, "\u1089 \u1087 \u1086 \u1088 \u1090 \u1080 \u1074 \u1085 \u1086 \u1077 ", "\u1079 \u1080 \u1084 \u1072 ", "\u1089 \u1090 \u1088 \u1080 \u1090 , \u1089 \u1087 \u1086 \u1088 \u1090 ", "\u1086 \u1074 \u1077 \u1088 \u1089 \u1072 \u1081 \u1079 , \u1089 \u1083 \u1086 \u1080 "),\
    ("A15", "\uc0\u1055 \u1088 \u1077 \u1087 \u1087 \u1080 ", "\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", 18, 35, 150, 178, "\u1072 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1083 \u1077 \u1090 \u1086 ", "\u1087 \u1088 \u1077 \u1087 \u1087 \u1080 , \u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1072 \u1082 \u1082 \u1091 \u1088 \u1072 \u1090 \u1085 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 "),\
    ("A16", "\uc0\u1055 \u1088 \u1077 \u1087 \u1087 \u1080 ", "\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", 18, 35, 165, 190, "\u1085 \u1086 \u1088 \u1084 \u1086 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1074 \u1077 \u1089 \u1085 \u1072 ", "\u1087 \u1088 \u1077 \u1087 \u1087 \u1080 , \u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1072 \u1082 \u1082 \u1091 \u1088 \u1072 \u1090 \u1085 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 "),\
    ("A17", "\uc0\u1057 \u1082 \u1072 \u1085 \u1076 \u1080 \u1085 \u1072 \u1074 \u1089 \u1082 \u1080 \u1081  \u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ", "\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", 30, 50, 150, 185, "", "\u1086 \u1089 \u1077 \u1085 \u1100 ", "\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 , \u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1087 \u1088 \u1086 \u1089 \u1090 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 , \u1073 \u1072 \u1079 \u1072 "),\
    ("A18", "\uc0\u1057 \u1082 \u1072 \u1085 \u1076 \u1080 \u1085 \u1072 \u1074 \u1089 \u1082 \u1080 \u1081  \u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ", "\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", 30, 50, 165, 195, "", "\u1086 \u1089 \u1077 \u1085 \u1100 ", "\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 , \u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1087 \u1088 \u1086 \u1089 \u1090 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 , \u1073 \u1072 \u1079 \u1072 "),\
    ("A19", "\uc0\u1059 \u1085 \u1080 \u1074 \u1077 \u1088 \u1089 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081  \u1073 \u1072 \u1079 \u1080 \u1089 ", "\u1091 \u1085 \u1080 \u1089 \u1077 \u1082 \u1089 ", 20, 60, 150, 200, "", "", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 , \u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ", "\u1073 \u1072 \u1079 \u1086 \u1074 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 "),\
    ("A20", "\uc0\u1054 \u1090 \u1087 \u1091 \u1089 \u1082 \u1085 \u1086 \u1081  \u1073 \u1086 \u1093 \u1086 ", "\u1091 \u1085 \u1080 \u1089 \u1077 \u1082 \u1089 ", 20, 55, 150, 200, "", "", "\u1073 \u1086 \u1093 \u1086 , \u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1089 \u1074 \u1086 \u1073 \u1086 \u1076 \u1085 \u1099 \u1077  \u1089 \u1080 \u1083 \u1091 \u1101 \u1090 \u1099 , \u1083 \u1105 \u1085 "),\
]\
\
\
def I(n, b, p, c, st, oc, se):\
    return (n, b, p, c, st, oc, se)\
\
\
M_TOPS = [\
    I("\uc0\u1060 \u1091 \u1090 \u1073 \u1086 \u1083 \u1082 \u1072  \u1080 \u1079  \u1093 \u1083 \u1086 \u1087 \u1082 \u1072 ", "URBANO", 1990, "\u1073 \u1077 \u1083 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ,\u1089 \u1077 \u1088 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1089 \u1087 \u1086 \u1088 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1056 \u1091 \u1073 \u1072 \u1096 \u1082 \u1072  \u1086 \u1082 \u1089 \u1092 \u1086 \u1088 \u1076 ", "NORDVELL", 4590, "\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ,\u1073 \u1077 \u1083 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ,\u1087 \u1088 \u1077 \u1087 \u1087 \u1080 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1055 \u1086 \u1083 \u1086  \u1076 \u1078 \u1077 \u1088 \u1089 \u1080 ", "ATELIER 7", 3490, "\u1086 \u1083 \u1080 \u1074 \u1082 \u1086 \u1074 \u1099 \u1081 ,\u1089 \u1080 \u1085 \u1080 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1087 \u1088 \u1077 \u1087 \u1087 \u1080 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ,\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1057 \u1074 \u1080 \u1090 \u1077 \u1088  \u1080 \u1079  \u1084 \u1077 \u1088 \u1080 \u1085 \u1086 \u1089 \u1072 ", "KAVRA", 6900, "\u1073 \u1077 \u1078 \u1077 \u1074 \u1099 \u1081 ,\u1075 \u1088 \u1072 \u1092 \u1080 \u1090 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ,\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
    I("\uc0\u1061 \u1091 \u1076 \u1080  \u1086 \u1074 \u1077 \u1088 \u1089 \u1072 \u1081 \u1079 ", "STREETLINE", 4200, "\u1089 \u1077 \u1088 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1089 \u1087 \u1086 \u1088 \u1090 ,\u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
    I("\uc0\u1056 \u1091 \u1073 \u1072 \u1096 \u1082 \u1072  \u1080 \u1079 \u1086  \u1083 \u1100 \u1085 \u1072 ", "SOLMAR", 4900, "\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ,\u1086 \u1083 \u1080 \u1074 \u1082 \u1086 \u1074 \u1099 \u1081 ", "\u1073 \u1086 \u1093 \u1086 ,\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1086 \u1090 \u1087 \u1091 \u1089 \u1082 ", "\u1083 \u1077 \u1090 \u1086 "),\
]\
F_TOPS = [\
    I("\uc0\u1041 \u1083 \u1091 \u1079 \u1072  \u1096 \u1105 \u1083 \u1082 \u1086 \u1074 \u1072 \u1103 ", "LUMI", 5290, "\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ,\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1088 \u1086 \u1084 \u1072 \u1085 \u1090 \u1080 \u1082 \u1072 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ,\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1060 \u1091 \u1090 \u1073 \u1086 \u1083 \u1082 \u1072  \u1080 \u1079  \u1093 \u1083 \u1086 \u1087 \u1082 \u1072 ", "URBANO", 1790, "\u1073 \u1077 \u1083 \u1099 \u1081 ,\u1089 \u1077 \u1088 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1044 \u1078 \u1077 \u1084 \u1087 \u1077 \u1088  \u1090 \u1086 \u1085 \u1082 \u1086 \u1075 \u1086  \u1090 \u1088 \u1080 \u1082 \u1086 \u1090 \u1072 \u1078 \u1072 ", "KAVRA", 5600, "\u1073 \u1077 \u1078 \u1077 \u1074 \u1099 \u1081 ,\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ,\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
    I("\uc0\u1058 \u1086 \u1087  \u1072 \u1090 \u1083 \u1072 \u1089 \u1085 \u1099 \u1081 ", "LUMI", 3200, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ,\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 ", "\u1074 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081 ,\u1088 \u1086 \u1084 \u1072 \u1085 \u1090 \u1080 \u1082 \u1072 ", "\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1056 \u1091 \u1073 \u1072 \u1096 \u1082 \u1072  \u1086 \u1074 \u1077 \u1088 \u1089 \u1072 \u1081 \u1079 ", "NORDVELL", 3900, "\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ,\u1073 \u1077 \u1083 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1044 \u1078 \u1077 \u1084 \u1087 \u1077 \u1088  \u1089  \u1075 \u1086 \u1088 \u1083 \u1086 \u1084 ", "KAVRA", 4990, "\u1084 \u1086 \u1083 \u1086 \u1095 \u1085 \u1099 \u1081 ,\u1075 \u1088 \u1072 \u1092 \u1080 \u1090 \u1086 \u1074 \u1099 \u1081 ", "\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ,\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
]\
M_BOTTOMS = [\
    I("\uc0\u1063 \u1080 \u1085 \u1086 \u1089  \u1087 \u1088 \u1103 \u1084 \u1086 \u1075 \u1086  \u1082 \u1088 \u1086 \u1103 ", "NORDVELL", 4990, "\u1073 \u1077 \u1078 \u1077 \u1074 \u1099 \u1081 ,\u1089 \u1080 \u1085 \u1080 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1087 \u1088 \u1077 \u1087 \u1087 \u1080 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ,\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1041 \u1088 \u1102 \u1082 \u1080  \u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1095 \u1077 \u1089 \u1082 \u1080 \u1077 ", "ATELIER 7", 6900, "\u1075 \u1088 \u1072 \u1092 \u1080 \u1090 \u1086 \u1074 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1044 \u1078 \u1080 \u1085 \u1089 \u1099  straight", "DENIM LAB", 5490, "\u1089 \u1080 \u1085 \u1080 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1044 \u1078 \u1086 \u1075 \u1075 \u1077 \u1088 \u1099 ", "STREETLINE", 3900, "\u1089 \u1077 \u1088 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1089 \u1087 \u1086 \u1088 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
    I("\uc0\u1041 \u1088 \u1102 \u1082 \u1080  \u1083 \u1100 \u1085 \u1103 \u1085 \u1099 \u1077 ", "SOLMAR", 4400, "\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ,\u1086 \u1083 \u1080 \u1074 \u1082 \u1086 \u1074 \u1099 \u1081 ", "\u1073 \u1086 \u1093 \u1086 ,\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1086 \u1090 \u1087 \u1091 \u1089 \u1082 ", "\u1083 \u1077 \u1090 \u1086 "),\
    I("\uc0\u1050 \u1072 \u1088 \u1075 \u1086 ", "STREETLINE", 4700, "\u1093 \u1072 \u1082 \u1080 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
]\
F_BOTTOMS = [\
    I("\uc0\u1041 \u1088 \u1102 \u1082 \u1080  \u1087 \u1072 \u1083 \u1072 \u1094 \u1094 \u1086 ", "LUMI", 5900, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ,\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1074 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081 ", "\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ,\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1044 \u1078 \u1080 \u1085 \u1089 \u1099  mom-fit", "DENIM LAB", 4990, "\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ,\u1089 \u1080 \u1085 \u1080 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1070 \u1073 \u1082 \u1072  \u1084 \u1080 \u1076 \u1080  \u1087 \u1083 \u1080 \u1089 \u1089 \u1077 ", "LUMI", 5200, "\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 ,\u1086 \u1083 \u1080 \u1074 \u1082 \u1086 \u1074 \u1099 \u1081 ", "\u1088 \u1086 \u1084 \u1072 \u1085 \u1090 \u1080 \u1082 \u1072 ,\u1073 \u1086 \u1093 \u1086 ", "\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1041 \u1088 \u1102 \u1082 \u1080  \u1089 \u1086  \u1089 \u1090 \u1088 \u1077 \u1083 \u1082 \u1072 \u1084 \u1080 ", "ATELIER 7", 6400, "\u1075 \u1088 \u1072 \u1092 \u1080 \u1090 \u1086 \u1074 \u1099 \u1081 ,\u1089 \u1080 \u1085 \u1080 \u1081 ", "\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ,\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1064 \u1086 \u1088 \u1090 \u1099  \u1083 \u1100 \u1085 \u1103 \u1085 \u1099 \u1077 ", "SOLMAR", 2900, "\u1073 \u1077 \u1078 \u1077 \u1074 \u1099 \u1081 ,\u1073 \u1077 \u1083 \u1099 \u1081 ", "\u1073 \u1086 \u1093 \u1086 ,\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1086 \u1090 \u1087 \u1091 \u1089 \u1082 ", "\u1083 \u1077 \u1090 \u1086 "),\
]\
F_DRESSES = [\
    I("\uc0\u1055 \u1083 \u1072 \u1090 \u1100 \u1077 -\u1082 \u1086 \u1084 \u1073 \u1080 \u1085 \u1072 \u1094 \u1080 \u1103 ", "LUMI", 6900, "\u1075 \u1088 \u1072 \u1092 \u1080 \u1090 \u1086 \u1074 \u1099 \u1081 ,\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 ", "\u1074 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081 ,\u1088 \u1086 \u1084 \u1072 \u1085 \u1090 \u1080 \u1082 \u1072 ", "\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1057 \u1072 \u1088 \u1072 \u1092 \u1072 \u1085  \u1083 \u1100 \u1085 \u1103 \u1085 \u1086 \u1081 ", "SOLMAR", 5400, "\u1086 \u1083 \u1080 \u1074 \u1082 \u1086 \u1074 \u1099 \u1081 ,\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ", "\u1073 \u1086 \u1093 \u1086 ,\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1086 \u1090 \u1087 \u1091 \u1089 \u1082 ", "\u1083 \u1077 \u1090 \u1086 "),\
    I("\uc0\u1055 \u1083 \u1072 \u1090 \u1100 \u1077 -\u1092 \u1091 \u1090 \u1083 \u1103 \u1088 ", "ATELIER 7", 7900, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ,\u1089 \u1080 \u1085 \u1080 \u1081 ", "\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ,\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1055 \u1083 \u1072 \u1090 \u1100 \u1077  \u1084 \u1072 \u1082 \u1089 \u1080  \u1074  \u1094 \u1074 \u1077 \u1090 \u1086 \u1082 ", "SOLMAR", 6200, "\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ,\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ", "\u1088 \u1086 \u1084 \u1072 \u1085 \u1090 \u1080 \u1082 \u1072 ,\u1073 \u1086 \u1093 \u1086 ", "\u1086 \u1090 \u1087 \u1091 \u1089 \u1082 ,\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1083 \u1077 \u1090 \u1086 "),\
    I("\uc0\u1058 \u1088 \u1080 \u1082 \u1086 \u1090 \u1072 \u1078 \u1085 \u1086 \u1077  \u1087 \u1083 \u1072 \u1090 \u1100 \u1077 ", "KAVRA", 5600, "\u1089 \u1077 \u1088 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ,\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ,\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
]\
M_OUTER = [\
    I("\uc0\u1058 \u1088 \u1077 \u1085 \u1095  \u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1095 \u1077 \u1089 \u1082 \u1080 \u1081 ", "NORDVELL", 12900, "\u1073 \u1077 \u1078 \u1077 \u1074 \u1099 \u1081 ,\u1075 \u1088 \u1072 \u1092 \u1080 \u1090 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ,\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
    I("\uc0\u1055 \u1091 \u1093 \u1086 \u1074 \u1080 \u1082  \u1083 \u1105 \u1075 \u1082 \u1080 \u1081 ", "ARCTICA", 9900, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ,\u1089 \u1080 \u1085 \u1080 \u1081 ", "\u1089 \u1087 \u1086 \u1088 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1079 \u1080 \u1084 \u1072 "),\
    I("\uc0\u1041 \u1086 \u1084 \u1073 \u1077 \u1088 ", "STREETLINE", 7400, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ,\u1086 \u1083 \u1080 \u1074 \u1082 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
    I("\uc0\u1041 \u1083 \u1077 \u1081 \u1079 \u1077 \u1088 ", "ATELIER 7", 9900, "\u1089 \u1080 \u1085 \u1080 \u1081 ,\u1089 \u1077 \u1088 \u1099 \u1081 ", "\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ,\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ,\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1040 \u1085 \u1086 \u1088 \u1072 \u1082 ", "ARCTICA", 6900, "\u1093 \u1072 \u1082 \u1080 ,\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ", "\u1089 \u1087 \u1086 \u1088 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
]\
F_OUTER = [\
    I("\uc0\u1058 \u1088 \u1077 \u1085 \u1095 ", "NORDVELL", 11900, "\u1073 \u1077 \u1078 \u1077 \u1074 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ,\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
    I("\uc0\u1046 \u1072 \u1082 \u1077 \u1090  \u1090 \u1074 \u1080 \u1076 \u1086 \u1074 \u1099 \u1081 ", "ATELIER 7", 9900, "\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ,\u1089 \u1077 \u1088 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1087 \u1088 \u1077 \u1087 \u1087 \u1080 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ,\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
    I("\uc0\u1050 \u1091 \u1088 \u1090 \u1082 \u1072  \u1076 \u1078 \u1080 \u1085 \u1089 \u1086 \u1074 \u1072 \u1103 ", "DENIM LAB", 5900, "\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ,\u1089 \u1080 \u1085 \u1080 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
    I("\uc0\u1055 \u1072 \u1083 \u1100 \u1090 \u1086  \u1086 \u1074 \u1077 \u1088 \u1089 \u1072 \u1081 \u1079 ", "KAVRA", 14900, "\u1075 \u1088 \u1072 \u1092 \u1080 \u1090 \u1086 \u1074 \u1099 \u1081 ,\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ", "\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ,\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1079 \u1080 \u1084 \u1072 "),\
]\
M_SHOES = [\
    I("\uc0\u1054 \u1082 \u1089 \u1092 \u1086 \u1088 \u1076 \u1099  \u1082 \u1086 \u1078 \u1072 \u1085 \u1099 \u1077 ", "MARCONI", 8900, "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1050 \u1077 \u1076 \u1099  \u1073 \u1077 \u1083 \u1099 \u1077 ", "STREETLINE", 3990, "\u1073 \u1077 \u1083 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1051 \u1086 \u1092 \u1077 \u1088 \u1099 ", "MARCONI", 6900, "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ", "\u1087 \u1088 \u1077 \u1087 \u1087 \u1080 ,\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1050 \u1088 \u1086 \u1089 \u1089 \u1086 \u1074 \u1082 \u1080  \u1073 \u1077 \u1075 \u1086 \u1074 \u1099 \u1077 ", "VELOCITY", 7490, "\u1089 \u1077 \u1088 \u1099 \u1081 ,\u1089 \u1080 \u1085 \u1080 \u1081 ", "\u1089 \u1087 \u1086 \u1088 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1057 \u1072 \u1085 \u1076 \u1072 \u1083 \u1080 \u1080  \u1082 \u1086 \u1078 \u1072 \u1085 \u1099 \u1077 ", "SOLMAR", 3490, "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ", "\u1073 \u1086 \u1093 \u1086 ", "\u1086 \u1090 \u1087 \u1091 \u1089 \u1082 ", "\u1083 \u1077 \u1090 \u1086 "),\
    I("\uc0\u1041 \u1086 \u1090 \u1080 \u1085 \u1082 \u1080  \u1095 \u1077 \u1083 \u1089 \u1080 ", "MARCONI", 9900, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ,\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ,\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
]\
F_SHOES = [\
    I("\uc0\u1051 \u1086 \u1076 \u1086 \u1095 \u1082 \u1080 ", "MARCONI", 7900, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ,\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1074 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ,\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1050 \u1077 \u1076 \u1099  \u1073 \u1077 \u1083 \u1099 \u1077 ", "STREETLINE", 3590, "\u1073 \u1077 \u1083 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1041 \u1086 \u1090 \u1080 \u1083 \u1100 \u1086 \u1085 \u1099  \u1085 \u1072  \u1082 \u1072 \u1073 \u1083 \u1091 \u1082 \u1077 ", "MARCONI", 8900, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ,\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1074 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081 ", "\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 "),\
    I("\uc0\u1057 \u1072 \u1085 \u1076 \u1072 \u1083 \u1080 \u1080  \u1087 \u1083 \u1086 \u1089 \u1082 \u1080 \u1077 ", "SOLMAR", 3290, "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ,\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ", "\u1073 \u1086 \u1093 \u1086 ,\u1088 \u1086 \u1084 \u1072 \u1085 \u1090 \u1080 \u1082 \u1072 ", "\u1086 \u1090 \u1087 \u1091 \u1089 \u1082 ", "\u1083 \u1077 \u1090 \u1086 "),\
    I("\uc0\u1051 \u1086 \u1092 \u1077 \u1088 \u1099 ", "MARCONI", 6400, "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1087 \u1088 \u1077 \u1087 \u1087 \u1080 ,\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
]\
M_ACC = [\
    I("\uc0\u1056 \u1077 \u1084 \u1077 \u1085 \u1100  \u1082 \u1086 \u1078 \u1072 \u1085 \u1099 \u1081 ", "MARCONI", 2490, "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1057 \u1091 \u1084 \u1082 \u1072  \u1087 \u1086 \u1095 \u1090 \u1072 \u1083 \u1100 \u1086 \u1085 ", "URBANO", 5900, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1064 \u1072 \u1088 \u1092  \u1096 \u1077 \u1088 \u1089 \u1090 \u1103 \u1085 \u1086 \u1081 ", "KAVRA", 2990, "\u1089 \u1077 \u1088 \u1099 \u1081 ,\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1079 \u1080 \u1084 \u1072 "),\
    I("\uc0\u1054 \u1095 \u1082 \u1080  \u1089 \u1086 \u1083 \u1085 \u1094 \u1077 \u1079 \u1072 \u1097 \u1080 \u1090 \u1085 \u1099 \u1077 ", "SOLMAR", 3990, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1073 \u1086 \u1093 \u1086 ", "\u1086 \u1090 \u1087 \u1091 \u1089 \u1082 ", "\u1083 \u1077 \u1090 \u1086 "),\
    I("\uc0\u1050 \u1077 \u1087 \u1082 \u1072  \u1073 \u1077 \u1081 \u1089 \u1073 \u1086 \u1083 \u1082 \u1072 ", "STREETLINE", 1990, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ,\u1093 \u1072 \u1082 \u1080 ", "\u1089 \u1087 \u1086 \u1088 \u1090 ,\u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1043 \u1072 \u1083 \u1089 \u1090 \u1091 \u1082  \u1096 \u1105 \u1083 \u1082 \u1086 \u1074 \u1099 \u1081 ", "ATELIER 7", 3490, "\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 ,\u1089 \u1080 \u1085 \u1080 \u1081 ", "\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ,\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
]\
F_ACC = [\
    I("\uc0\u1055 \u1083 \u1072 \u1090 \u1086 \u1082  \u1096 \u1105 \u1083 \u1082 \u1086 \u1074 \u1099 \u1081 ", "LUMI", 2990, "\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ,\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ,\u1088 \u1086 \u1084 \u1072 \u1085 \u1090 \u1080 \u1082 \u1072 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ,\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1057 \u1091 \u1084 \u1082 \u1072 -\u1090 \u1086 \u1091 \u1090 ", "URBANO", 6900, "\u1073 \u1077 \u1078 \u1077 \u1074 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1056 \u1077 \u1084 \u1077 \u1085 \u1100  \u1082 \u1086 \u1078 \u1072 \u1085 \u1099 \u1081 ", "MARCONI", 2290, "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ,\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
    I("\uc0\u1064 \u1072 \u1088 \u1092  \u1082 \u1072 \u1096 \u1077 \u1084 \u1080 \u1088 \u1086 \u1074 \u1099 \u1081 ", "KAVRA", 3990, "\u1089 \u1077 \u1088 \u1099 \u1081 ,\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ", "\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ,\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1079 \u1080 \u1084 \u1072 "),\
    I("\uc0\u1054 \u1095 \u1082 \u1080  \u1089 \u1086 \u1083 \u1085 \u1094 \u1077 \u1079 \u1072 \u1097 \u1080 \u1090 \u1085 \u1099 \u1077 ", "SOLMAR", 3990, "\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ,\u1073 \u1086 \u1093 \u1086 ", "\u1086 \u1090 \u1087 \u1091 \u1089 \u1082 ", "\u1083 \u1077 \u1090 \u1086 "),\
    I("\uc0\u1041 \u1088 \u1086 \u1096 \u1100 ", "LUMI", 1990, "\u1079 \u1086 \u1083 \u1086 \u1090 \u1080 \u1089 \u1090 \u1099 \u1081 ", "\u1074 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081 ,\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 "),\
]\
\
TOP_SIZES = ["XS", "S", "M", "L", "XL", "XXL"]\
BOT_SIZES = [str(s) for s in range(40, 55, 2)]\
SHOE_SIZES = [str(s) for s in range(35, 47)]\
ACC_SIZES = ["OS"]\
CUT_BY_CAT = \{"\uc0\u1074 \u1077 \u1088 \u1093 ": "\u1087 \u1088 \u1103 \u1084 \u1086 \u1081 ", "\u1085 \u1080 \u1079 ": "\u1087 \u1088 \u1103 \u1084 \u1086 \u1081 ", "\u1087 \u1083 \u1072 \u1090 \u1100 \u1077 ": "\u1087 \u1088 \u1080 \u1090 \u1072 \u1083 \u1077 \u1085 \u1085 \u1099 \u1081 ",\
              "\uc0\u1074 \u1077 \u1088 \u1093 \u1085 \u1103 \u1103  \u1086 \u1076 \u1077 \u1078 \u1076 \u1072 ": "\u1089 \u1074 \u1086 \u1073 \u1086 \u1076 \u1085 \u1099 \u1081 ", "\u1086 \u1073 \u1091 \u1074 \u1100 ": "-", "\u1072 \u1082 \u1089 \u1077 \u1089 \u1089 \u1091 \u1072 \u1088 ": "-"\}\
\
\
def load_demo(conn):\
    rng = random.Random(42)\
    conn.execute("DELETE FROM archetypes")\
    conn.execute("DELETE FROM products")\
    for aid, name, g, af, at, hf, ht, body, ct, styles, sil in ARCH_SPECS:\
        rec, av = CT_COLORS.get(ct, (["\uc0\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1073 \u1077 \u1083 \u1099 \u1081 ", "\u1089 \u1077 \u1088 \u1099 \u1081 ", "\u1089 \u1080 \u1085 \u1080 \u1081 "], []))\
        who = \{"\uc0\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ": "\u1078 \u1077 \u1085 \u1097 \u1080 \u1085 ", "\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ": "\u1084 \u1091 \u1078 \u1095 \u1080 \u1085 "\}.get(g, "\u1074 \u1089 \u1077 \u1093 ")\
        desc = f"\{name\} \uc0\u1076 \u1083 \u1103  \{who\} \{af\}-\{at\} \u1083 \u1077 \u1090 : \{styles\}. \{sil\}. \u1058 \u1077 \u1083 \u1086 \u1089 \u1083 \u1086 \u1078 \u1077 \u1085 \u1080 \u1077 : \{body or '\u1083 \u1102 \u1073 \u1086 \u1077 '\}. \u1062 \u1074 \u1077 \u1090 \u1086 \u1090 \u1080 \u1087 : \{ct or '\u1083 \u1102 \u1073 \u1086 \u1081 '\}."\
        conn.execute(\
            "INSERT OR REPLACE INTO archetypes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",\
            (aid, name, desc, g, af, at, hf, ht, body, ct, styles, ", ".join(rec), ", ".join(av)))\
    skus = \{\}\
\
    def add(gender, cat, items, sizes, comp_src=None):\
        letter = "M" if gender == "\uc0\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 " else "F"\
        code = \{"\uc0\u1074 \u1077 \u1088 \u1093 ": "TOP", "\u1085 \u1080 \u1079 ": "BOT", "\u1087 \u1083 \u1072 \u1090 \u1100 \u1077 ": "DRS", "\u1074 \u1077 \u1088 \u1093 \u1085 \u1103 \u1103  \u1086 \u1076 \u1077 \u1078 \u1076 \u1072 ": "OUT",\
                "\uc0\u1086 \u1073 \u1091 \u1074 \u1100 ": "SHO", "\u1072 \u1082 \u1089 \u1077 \u1089 \u1089 \u1091 \u1072 \u1088 ": "ACC"\}[cat]\
        out = []\
        for i, (n, b, p, c, st, oc, se) in enumerate(items, start=1):\
            sku = f"\{letter\}-\{code\}-\{i:02d\}"\
            comp = comp_src[(i - 1) % len(comp_src)] if comp_src else ""\
            loc = f"\uc0\u1057 \u1077 \u1082 \u1094 \u1080 \u1103  \{rng.randint(1, 6)\} \'b7 \u1057 \u1090 \u1077 \u1083 \u1083 \u1072 \u1078  \{chr(65 + rng.randint(0, 3))\}\{rng.randint(1, 6)\}"\
            conn.execute(\
                "INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",\
                (sku, n, b, cat, gender, json.dumps(sizes), c, p, se, st, oc,\
                 CUT_BY_CAT[cat], loc, rng.randint(2, 14),\
                 round(rng.uniform(0.25, 0.55), 2), f"products/\{sku\}.jpg",\
                 json.dumps([comp]) if comp else "[]"))\
            make_placeholder(MEDIA / "products" / f"\{sku\}.jpg", sku, n, cat)\
            out.append(sku)\
        skus[(gender, cat)] = out\
\
    add("\uc0\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", "\u1074 \u1077 \u1088 \u1093 ", F_TOPS, TOP_SIZES)\
    add("\uc0\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", "\u1074 \u1077 \u1088 \u1093 ", M_TOPS, TOP_SIZES)\
    add("\uc0\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", "\u1085 \u1080 \u1079 ", F_BOTTOMS, BOT_SIZES)\
    add("\uc0\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", "\u1085 \u1080 \u1079 ", M_BOTTOMS, BOT_SIZES)\
    add("\uc0\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", "\u1087 \u1083 \u1072 \u1090 \u1100 \u1077 ", F_DRESSES, TOP_SIZES)\
    add("\uc0\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", "\u1074 \u1077 \u1088 \u1093 \u1085 \u1103 \u1103  \u1086 \u1076 \u1077 \u1078 \u1076 \u1072 ", F_OUTER, TOP_SIZES)\
    add("\uc0\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", "\u1074 \u1077 \u1088 \u1093 \u1085 \u1103 \u1103  \u1086 \u1076 \u1077 \u1078 \u1076 \u1072 ", M_OUTER, TOP_SIZES)\
    add("\uc0\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", "\u1086 \u1073 \u1091 \u1074 \u1100 ", F_SHOES, SHOE_SIZES, comp_src=skus[("\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", "\u1085 \u1080 \u1079 ")])\
    add("\uc0\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", "\u1086 \u1073 \u1091 \u1074 \u1100 ", M_SHOES, SHOE_SIZES, comp_src=skus[("\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", "\u1085 \u1080 \u1079 ")])\
    add("\uc0\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", "\u1072 \u1082 \u1089 \u1077 \u1089 \u1089 \u1091 \u1072 \u1088 ", F_ACC, ACC_SIZES, comp_src=skus[("\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", "\u1074 \u1077 \u1088 \u1093 ")])\
    add("\uc0\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", "\u1072 \u1082 \u1089 \u1077 \u1089 \u1089 \u1091 \u1072 \u1088 ", M_ACC, ACC_SIZES, comp_src=skus[("\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ", "\u1074 \u1077 \u1088 \u1093 ")])\
    conn.commit()\
    return len(ARCH_SPECS), 60\
\
\
# -------------------------------------------------------------- \uc0\u1087 \u1086 \u1076 \u1073 \u1086 \u1088  ----\
GENDERS = ("\uc0\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 ", "\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 ")\
BODIES = ("\uc0\u1072 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1085 \u1086 \u1088 \u1084 \u1086 \u1089 \u1090 \u1077 \u1085 \u1080 \u1095 \u1077 \u1089 \u1082 \u1086 \u1077 ", "\u1089 \u1087 \u1086 \u1088 \u1090 \u1080 \u1074 \u1085 \u1086 \u1077 ", "\u1082 \u1088 \u1091 \u1087 \u1085 \u1086 \u1077 ")\
OCC = ("\uc0\u1088 \u1072 \u1073 \u1086 \u1090 \u1072 ", "\u1087 \u1086 \u1074 \u1089 \u1077 \u1076 \u1085 \u1077 \u1074 \u1085 \u1086 \u1077 ", "\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ", "\u1086 \u1090 \u1087 \u1091 \u1089 \u1082 ")\
SEASONS = ("\uc0\u1074 \u1077 \u1089 \u1085 \u1072 ", "\u1083 \u1077 \u1090 \u1086 ", "\u1086 \u1089 \u1077 \u1085 \u1100 ", "\u1079 \u1080 \u1084 \u1072 ", "\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 ")\
FITS = ("\uc0\u1089 \u1074 \u1086 \u1073 \u1086 \u1076 \u1085 \u1072 \u1103 ", "\u1086 \u1073 \u1099 \u1095 \u1085 \u1072 \u1103 ", "\u1086 \u1073 \u1083 \u1077 \u1075 \u1072 \u1102 \u1097 \u1072 \u1103 ")\
STYLE_LIST = ("\uc0\u1082 \u1083 \u1072 \u1089 \u1089 \u1080 \u1082 \u1072 ", "\u1082 \u1101 \u1078 \u1091 \u1072 \u1083 ", "\u1076 \u1077 \u1083 \u1086 \u1074 \u1086 \u1081 ", "\u1089 \u1087 \u1086 \u1088 \u1090 ", "\u1088 \u1086 \u1084 \u1072 \u1085 \u1090 \u1080 \u1082 \u1072 ", "\u1073 \u1086 \u1093 \u1086 ",\
              "\uc0\u1084 \u1080 \u1085 \u1080 \u1084 \u1072 \u1083 \u1080 \u1079 \u1084 ", "\u1074 \u1077 \u1095 \u1077 \u1088 \u1085 \u1080 \u1081 ", "\u1089 \u1090 \u1088 \u1080 \u1090 ", "\u1087 \u1088 \u1077 \u1087 \u1087 \u1080 ")\
COLOR_LIST = ("\uc0\u1095 \u1077 \u1088 \u1085 \u1099 \u1081 ", "\u1073 \u1077 \u1083 \u1099 \u1081 ", "\u1089 \u1077 \u1088 \u1099 \u1081 ", "\u1075 \u1088 \u1072 \u1092 \u1080 \u1090 \u1086 \u1074 \u1099 \u1081 ", "\u1073 \u1077 \u1078 \u1077 \u1074 \u1099 \u1081 ", "\u1082 \u1088 \u1077 \u1084 \u1086 \u1074 \u1099 \u1081 ", "\u1089 \u1080 \u1085 \u1080 \u1081 ",\
              "\uc0\u1075 \u1086 \u1083 \u1091 \u1073 \u1086 \u1081 ", "\u1079 \u1077 \u1083 \u1077 \u1085 \u1099 \u1081 ", "\u1086 \u1083 \u1080 \u1074 \u1082 \u1086 \u1074 \u1099 \u1081 ", "\u1082 \u1088 \u1072 \u1089 \u1085 \u1099 \u1081 ", "\u1073 \u1086 \u1088 \u1076 \u1086 \u1074 \u1099 \u1081 ", "\u1088 \u1086 \u1079 \u1086 \u1074 \u1099 \u1081 ",\
              "\uc0\u1092 \u1080 \u1086 \u1083 \u1077 \u1090 \u1086 \u1074 \u1099 \u1081 ", "\u1078 \u1077 \u1083 \u1090 \u1099 \u1081 ", "\u1082 \u1086 \u1088 \u1080 \u1095 \u1085 \u1077 \u1074 \u1099 \u1081 ")\
\
\
def parse_q(gender, age, height, size_top, size_bottom, size_shoes, body_type,\
            occasion, season, budget_from, budget_to, needs, styles,\
            favorite_colors, avoid_colors, fit, restrictions):\
    try:\
        age = int(age)\
        height = int(height)\
    except Exception:\
        raise UserError("\uc0\u1042 \u1086 \u1079 \u1088 \u1072 \u1089 \u1090  \u1080  \u1088 \u1086 \u1089 \u1090  \'97 \u1094 \u1077 \u1083 \u1099 \u1077  \u1095 \u1080 \u1089 \u1083 \u1072 .")\
    if gender not in GENDERS:\
        raise UserError("\uc0\u1042 \u1099 \u1073 \u1077 \u1088 \u1080 \u1090 \u1077  \u1087 \u1086 \u1083 .")\
    if not 12 <= age <= 99:\
        raise UserError("\uc0\u1042 \u1086 \u1079 \u1088 \u1072 \u1089 \u1090  \u1076 \u1086 \u1083 \u1078 \u1077 \u1085  \u1073 \u1099 \u1090 \u1100  \u1086 \u1090  12 \u1076 \u1086  99.")\
    if not 120 <= height <= 220:\
        raise UserError("\uc0\u1056 \u1086 \u1089 \u1090  \u1076 \u1086 \u1083 \u1078 \u1077 \u1085  \u1073 \u1099 \u1090 \u1100  120\'96220 \u1089 \u1084 .")\
    if body_type not in BODIES:\
        raise UserError("\uc0\u1042 \u1099 \u1073 \u1077 \u1088 \u1080 \u1090 \u1077  \u1090 \u1077 \u1083 \u1086 \u1089 \u1083 \u1086 \u1078 \u1077 \u1085 \u1080 \u1077  \u1080 \u1079  \u1089 \u1087 \u1080 \u1089 \u1082 \u1072 .")\
    if occasion not in OCC:\
        raise UserError("\uc0\u1042 \u1099 \u1073 \u1077 \u1088 \u1080 \u1090 \u1077  \u1087 \u1086 \u1074 \u1086 \u1076  \u1080 \u1079  \u1089 \u1087 \u1080 \u1089 \u1082 \u1072 .")\
    if season not in SEASONS:\
        raise UserError("\uc0\u1042 \u1099 \u1073 \u1077 \u1088 \u1080 \u1090 \u1077  \u1089 \u1077 \u1079 \u1086 \u1085  \u1080 \u1079  \u1089 \u1087 \u1080 \u1089 \u1082 \u1072 .")\
    if fit not in FITS:\
        raise UserError("\uc0\u1042 \u1099 \u1073 \u1077 \u1088 \u1080 \u1090 \u1077  \u1087 \u1086 \u1089 \u1072 \u1076 \u1082 \u1091  \u1080 \u1079  \u1089 \u1087 \u1080 \u1089 \u1082 \u1072 .")\
    bfrom, bto = float(budget_from or 0), float(budget_to or 0)\
    if bto <= 0:\
        raise UserError("\uc0\u1059 \u1082 \u1072 \u1078 \u1080 \u1090 \u1077  \u1073 \u1102 \u1076 \u1078 \u1077 \u1090  \'ab\u1076 \u1086 \'bb.")\
    if bto < bfrom:\
        raise UserError("\uc0\u1041 \u1102 \u1076 \u1078 \u1077 \u1090  \'ab\u1076 \u1086 \'bb \u1076 \u1086 \u1083 \u1078 \u1077 \u1085  \u1073 \u1099 \u1090 \u1100  \u1085 \u1077  \u1084 \u1077 \u1085 \u1100 \u1096 \u1077  \'ab\u1086 \u1090 \'bb.")\
    return \{\
        "gender": gender, "age": age, "height": height,\
        "size_top": str(size_top).strip().upper(), "size_bottom": str(size_bottom).strip(),\
        "size_shoes": str(size_shoes).strip(), "body": body_type,\
        "occasion": occasion, "season": season, "budget_from": bfrom, "budget_to": bto,\
        "needs": needs, "styles": tags(styles), "fav_colors": tags(favorite_colors),\
        "avoid_colors": tags(avoid_colors), "fit": fit, "restrictions": restrictions,\
    \}\
\
\
def match_archetypes(conn, q, photo):\
    rows = conn.execute("SELECT * FROM archetypes").fetchall()\
    if not rows:\
        raise UserError("\uc0\u1041 \u1072 \u1079 \u1072  \u1090 \u1080 \u1087 \u1072 \u1078 \u1077 \u1081  \u1087 \u1091 \u1089 \u1090 \u1072 . \u1047 \u1072 \u1081 \u1076 \u1080 \u1090 \u1077  \u1074  /admin \u1080  \u1085 \u1072 \u1078 \u1084 \u1080 \u1090 \u1077  \'ab\u1044 \u1077 \u1084 \u1086 -\u1076 \u1072 \u1085 \u1085 \u1099 \u1077 \'bb.")\
    pool = [a for a in rows if a["gender"] in (q["gender"], "\uc0\u1091 \u1085 \u1080 \u1089 \u1077 \u1082 \u1089 ")] or list(rows)\
    W = \{"age": .14, "height": .08, "body": .22, "styles": .30, "colors": .16, "ct": .10\}\
    scored = []\
    for a in pool:\
        sc, reasons = 0.0, []\
        if a["age_from"] <= q["age"] <= a["age_to"]:\
            sc += W["age"]; reasons.append(f"\uc0\u1074 \u1086 \u1079 \u1088 \u1072 \u1089 \u1090  \{q['age']\} \u1074  \u1076 \u1080 \u1072 \u1087 \u1072 \u1079 \u1086 \u1085 \u1077  \{a['age_from']\}-\{a['age_to']\}")\
        elif a["age_from"] - 5 <= q["age"] <= a["age_to"] + 5:\
            sc += W["age"] / 2\
        if a["height_from"] <= q["height"] <= a["height_to"]:\
            sc += W["height"]; reasons.append("\uc0\u1088 \u1086 \u1089 \u1090  \u1087 \u1086 \u1076 \u1093 \u1086 \u1076 \u1080 \u1090  \u1090 \u1080 \u1087 \u1072 \u1078 \u1091 ")\
        elif a["height_from"] - 7 <= q["height"] <= a["height_to"] + 7:\
            sc += W["height"] / 2\
        body = (photo or \{\}).get("\uc0\u1090 \u1077 \u1083 \u1086 \u1089 \u1083 \u1086 \u1078 \u1077 \u1085 \u1080 \u1077 ") or q["body"]\
        if not a["body"]:\
            sc += W["body"] * .6\
        elif norm(body) == norm(a["body"]):\
            sc += W["body"]; reasons.append(f"\uc0\u1090 \u1077 \u1083 \u1086 \u1089 \u1083 \u1086 \u1078 \u1077 \u1085 \u1080 \u1077  \'ab\{a['body']\}\'bb \u1089 \u1086 \u1074 \u1087 \u1072 \u1076 \u1072 \u1077 \u1090 ")\
        else:\
            sc += W["body"] * .3\
        ast = set(tags(a["styles"]))\
        ust = set(q["styles"]) | (\{(photo or \{\}).get("\uc0\u1089 \u1090 \u1080 \u1083 \u1100 ", "").lower()\} - \{""\})\
        if ust:\
            ov = ast & ust\
            sc += W["styles"] * (len(ov) / len(ust)) if ov else W["styles"] * .05\
            if ov:\
                reasons.append("\uc0\u1089 \u1090 \u1080 \u1083 \u1080  \u1089 \u1086 \u1074 \u1087 \u1072 \u1076 \u1072 \u1102 \u1090 : " + ", ".join(sorted(ov)))\
        else:\
            sc += W["styles"] * .6\
        rec = set(tags(a["rec_colors"]))\
        fav = set(q["fav_colors"])\
        avoid = set(q["avoid_colors"]) | set(tags(a["avoid_colors"]))\
        if fav & rec:\
            sc += W["colors"]; reasons.append("\uc0\u1083 \u1102 \u1073 \u1080 \u1084 \u1099 \u1077  \u1094 \u1074 \u1077 \u1090 \u1072  \u1074 \u1093 \u1086 \u1076 \u1103 \u1090  \u1074  \u1088 \u1077 \u1082 \u1086 \u1084 \u1077 \u1085 \u1076 \u1086 \u1074 \u1072 \u1085 \u1085 \u1099 \u1077 ")\
        elif rec & avoid:\
            sc += W["colors"] * .1\
        else:\
            sc += W["colors"] * .6\
        ct = (photo or \{\}).get("\uc0\u1094 \u1074 \u1077 \u1090 \u1086 \u1090 \u1080 \u1087 ", "")\
        if not a["color_type"]:\
            sc += W["ct"] * .6\
        elif ct and norm(ct) == norm(a["color_type"]):\
            sc += W["ct"]; reasons.append(f"\uc0\u1094 \u1074 \u1077 \u1090 \u1086 \u1090 \u1080 \u1087  \'ab\{a['color_type']\}\'bb \u1089 \u1086 \u1074 \u1087 \u1072 \u1083  (\u1087 \u1086  \u1092 \u1086 \u1090 \u1086 )")\
        else:\
            sc += W["ct"] * .4\
        scored.append((round(100 * min(sc, 1.0)), a, reasons))\
    scored.sort(key=lambda t: -t[0])\
    top = scored[:3]\
    best = top[0]\
    reason = "; ".join(best[2]) or "\uc0\u1054 \u1073 \u1097 \u1080 \u1077  \u1087 \u1072 \u1088 \u1072 \u1084 \u1077 \u1090 \u1088 \u1099  \u1089 \u1086 \u1074 \u1087 \u1072 \u1076 \u1072 \u1102 \u1090 ."\
    ai_used = False\
    if gc_available() and len(top) > 1:\
        try:\
            cand = "\\n".join(f"- id=\{a['aid']\}: \{a['name']\}; \uc0\u1089 \u1090 \u1080 \u1083 \u1080 : \{a['styles']\}; "\
                             f"\uc0\u1094 \u1074 \u1077 \u1090 \u1072 : \{a['rec_colors']\}" for _, a, _ in top)\
            prof = (f"\uc0\u1087 \u1086 \u1083  \{q['gender']\}, \u1074 \u1086 \u1079 \u1088 \u1072 \u1089 \u1090  \{q['age']\}, \u1088 \u1086 \u1089 \u1090  \{q['height']\}, "\
                    f"\uc0\u1090 \u1077 \u1083 \u1086 \u1089 \u1083 \u1086 \u1078 \u1077 \u1085 \u1080 \u1077  \{q['body']\}, \u1089 \u1090 \u1080 \u1083 \u1080  \{', '.join(q['styles']) or '\'97'\}, "\
                    f"\uc0\u1087 \u1086 \u1074 \u1086 \u1076  \{q['occasion']\}")\
            data = gc_json([\
                \{"role": "system", "content": "\uc0\u1058 \u1099  \u1089 \u1090 \u1080 \u1083 \u1080 \u1089 \u1090  \u1086 \u1092 \u1083 \u1072 \u1081 \u1085 -\u1084 \u1072 \u1075 \u1072 \u1079 \u1080 \u1085 \u1072 . \u1054 \u1090 \u1074 \u1077 \u1095 \u1072 \u1081  \u1089 \u1090 \u1088 \u1086 \u1075 \u1086  JSON."\},\
                \{"role": "user", "content": (\
                    f"\uc0\u1055 \u1088 \u1086 \u1092 \u1080 \u1083 \u1100  \u1082 \u1083 \u1080 \u1077 \u1085 \u1090 \u1072 : \{prof\}\\n\u1050 \u1072 \u1085 \u1076 \u1080 \u1076 \u1072 \u1090 \u1099  \u1090 \u1080 \u1087 \u1072 \u1078 \u1077 \u1081 :\\n\{cand\}\\n"\
                    "\uc0\u1042 \u1099 \u1073 \u1077 \u1088 \u1080  \u1085 \u1072 \u1080 \u1073 \u1086 \u1083 \u1077 \u1077  \u1087 \u1086 \u1076 \u1093 \u1086 \u1076 \u1103 \u1097 \u1080 \u1081 . \u1042 \u1077 \u1088 \u1085 \u1080  \u1058 \u1054 \u1051 \u1068 \u1050 \u1054  JSON: "\
                    '\{"archetype_id": "<id \uc0\u1080 \u1079  \u1082 \u1072 \u1085 \u1076 \u1080 \u1076 \u1072 \u1090 \u1086 \u1074 >", "percent": <0-100>, '\
                    '"reason": "1-2 \uc0\u1087 \u1088 \u1077 \u1076 \u1083 \u1086 \u1078 \u1077 \u1085 \u1080 \u1103  \u1087 \u1088 \u1086 \u1089 \u1090 \u1099 \u1084 \u1080  \u1089 \u1083 \u1086 \u1074 \u1072 \u1084 \u1080 , \u1087 \u1086 \u1095 \u1077 \u1084 \u1091  \u1087 \u1086 \u1076 \u1093 \u1086 \u1076 \u1080 \u1090 "\}')\},\
            ], validate=_v_final)\
            if data["archetype_id"] in \{a["aid"] for _, a, _ in top\}:\
                best = next(t for t in top if t[1]["aid"] == data["archetype_id"])\
                reason = str(data["reason"])[:300]\
                try:\
                    best = (max(1, min(99, int(data.get("percent", best[0])))), best[1], best[2])\
                except Exception:\
                    pass\
                ai_used = True\
        except GCError as e:\
            log.warning("\uc0\u1060 \u1080 \u1085 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081  \u1074 \u1099 \u1073 \u1086 \u1088  \u1090 \u1080 \u1087 \u1072 \u1078 \u1072 : %s", e)\
    a = best[1]\
    return \{"aid": a["aid"], "name": a["name"], "percent": best[0], "reason": reason,\
            "styles": tags(a["styles"]), "rec_colors": tags(a["rec_colors"]),\
            "avoid_colors": tags(a["avoid_colors"]), "ai_used": ai_used\}\
\
\
def _v_final(d):\
    if not isinstance(d, dict) or not d.get("archetype_id") or "reason" not in d:\
        raise ValueError("bad final json")\
\
\
SIZE_FIELD = \{"\uc0\u1074 \u1077 \u1088 \u1093 ": "size_top", "\u1085 \u1080 \u1079 ": "size_bottom", "\u1086 \u1073 \u1091 \u1074 \u1100 ": "size_shoes",\
              "\uc0\u1074 \u1077 \u1088 \u1093 \u1085 \u1103 \u1103  \u1086 \u1076 \u1077 \u1078 \u1076 \u1072 ": "size_top", "\u1087 \u1083 \u1072 \u1090 \u1100 \u1077 ": "size_top"\}\
CATS = ("\uc0\u1074 \u1077 \u1088 \u1093 ", "\u1085 \u1080 \u1079 ", "\u1074 \u1077 \u1088 \u1093 \u1085 \u1103 \u1103  \u1086 \u1076 \u1077 \u1078 \u1076 \u1072 ", "\u1087 \u1083 \u1072 \u1090 \u1100 \u1077 ", "\u1086 \u1073 \u1091 \u1074 \u1100 ", "\u1072 \u1082 \u1089 \u1077 \u1089 \u1089 \u1091 \u1072 \u1088 ")\
\
\
def item_score(p, q, arch):\
    ast = set(arch["styles"]) | set(q["styles"])\
    pst = set(tags(p["styles"]))\
    style = len(ast & pst) / max(1, len(pst)) if pst else 0.4\
    cols = set(tags(p["colors"]))\
    rec = set(arch["rec_colors"]) | set(q["fav_colors"])\
    color = 1.0 if cols & rec else 0.4\
    if cols & set(q["avoid_colors"]) or cols & set(arch["avoid_colors"]):\
        color -= 0.5\
    occ = 1.0 if q["occasion"] in tags(p["occasions"]) else (\
        0.5 if not tags(p["occasions"]) else 0.15)\
    season_ok = (q["season"] in tags(p["season"]) or "\uc0\u1082 \u1088 \u1091 \u1075 \u1083 \u1099 \u1081  \u1075 \u1086 \u1076 " in tags(p["season"])\
                 or "\uc0\u1076 \u1077 \u1084 \u1080 \u1089 \u1077 \u1079 \u1086 \u1085 " in tags(p["season"]))\
    s = 30 * max(color, 0) + 30 * style + 20 * occ + 10 * (1.0 if season_ok else 0.4)\
    if p["margin"]:\
        s += 10 * min(1.0, p["margin"] / 0.6)\
    return s\
\
\
def build_outfit(conn, q, arch, notes):\
    cand = \{c: [] for c in CATS\}\
    for p in conn.execute("SELECT * FROM products WHERE stock>0 AND price<=?",\
                          (q["budget_to"],)).fetchall():\
        if p["gender"] not in (q["gender"], "\uc0\u1091 \u1085 \u1080 \u1089 \u1077 \u1082 \u1089 "):\
            continue\
        cat = p["category"]\
        field = SIZE_FIELD.get(cat)\
        if field:\
            ok, chosen, exact = has_size(p, q[field], cat)\
            if not ok:\
                continue\
        else:\
            chosen, exact = "OS", True\
        cand[cat].append(\{"p": p, "size": chosen, "exact": exact,\
                          "score": item_score(p, q, arch), "colors": set(tags(p["colors"]))\})\
    for lst in cand.values():\
        lst.sort(key=lambda x: -x["score"])\
\
    tops, bottoms = cand["\uc0\u1074 \u1077 \u1088 \u1093 "][:3], cand["\u1085 \u1080 \u1079 "][:3]\
    dresses, shoes = cand["\uc0\u1087 \u1083 \u1072 \u1090 \u1100 \u1077 "][:3], cand["\u1086 \u1073 \u1091 \u1074 \u1100 "][:3]\
    outer, accs = cand["\uc0\u1074 \u1077 \u1088 \u1093 \u1085 \u1103 \u1103  \u1086 \u1076 \u1077 \u1078 \u1076 \u1072 "][:2], cand["\u1072 \u1082 \u1089 \u1077 \u1089 \u1089 \u1091 \u1072 \u1088 "][:3]\
\
    def harmony(items):\
        return all(palette_ok(items[i]["colors"], items[i + 1]["colors"])\
                   for i in range(len(items) - 1))\
\
    combos = []\
    for t in tops:\
        for b in bottoms:\
            for s in shoes:\
                base = [t, b, s]\
                combos.append(base)\
                extras = [e for e in outer + accs if palette_ok(e["colors"], t["colors"])]\
                if extras:\
                    combos.append(base + [extras[0]])\
    for d in dresses:\
        for s in shoes:\
            combos.append([d, s])\
            extras = [e for e in outer + accs if palette_ok(e["colors"], d["colors"])]\
            if extras:\
                combos.append([d, s, extras[0]])\
    if not combos:\
        raise UserError("\uc0\u1055 \u1086 \u1076 \u1093 \u1086 \u1076 \u1103 \u1097 \u1080 \u1093  \u1090 \u1086 \u1074 \u1072 \u1088 \u1086 \u1074  \u1085 \u1077 \u1090 : \u1087 \u1088 \u1086 \u1074 \u1077 \u1088 \u1100 \u1090 \u1077  \u1087 \u1086 \u1083 /\u1088 \u1072 \u1079 \u1084 \u1077 \u1088  \u1080 \u1083 \u1080  \u1079 \u1072 \u1075 \u1088 \u1091 \u1079 \u1080 \u1090 \u1077  "\
                        "\uc0\u1076 \u1077 \u1084 \u1086 -\u1076 \u1072 \u1085 \u1085 \u1099 \u1077  \u1074  /admin.")\
\
    def combo_score(items):\
        total = sum(x["p"]["price"] for x in items)\
        avg = sum(x["score"] for x in items) / len(items)\
        bs = 1.0 if q["budget_from"] <= total <= q["budget_to"] else (\
            0.5 if total <= q["budget_to"] * 1.15 else 0.0)\
        cnt = \{3: 0.7, 4: 1.0, 5: 1.0\}.get(len(items), 0.4)\
        return 0.45 * avg + 25 * bs + 15 * (1.0 if harmony(items) else 0.5) + 10 * cnt, total\
\
    ranked = sorted(combos, key=lambda c: -combo_score(list(c))[0])\
    shortlist = ranked[:5]\
    pool = \{x["p"]["sku"]: x for lst in cand.values() for x in lst\}\
\
    def rule_reason(items):\
        st = sorted(\{s for x in items for s in tags(x["p"]["styles"])\}\
                    & (set(arch["styles"]) | set(q["styles"])))\
        total = round(sum(x["p"]["price"] for x in items))\
        return (f"\uc0\u1050 \u1086 \u1084 \u1087 \u1083 \u1077 \u1082 \u1090  \u1087 \u1086  \u1087 \u1088 \u1072 \u1074 \u1080 \u1083 \u1072 \u1084 : \u1089 \u1090 \u1080 \u1083 \u1080  \'ab\{', '.join(st[:3]) or '\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1072 \u1103  \u1073 \u1072 \u1079 \u1072 '\}\'bb "\
                f"\uc0\u1089 \u1086 \u1074 \u1087 \u1072 \u1076 \u1072 \u1102 \u1090  \u1089  \u1090 \u1080 \u1087 \u1072 \u1078 \u1086 \u1084  \'ab\{arch['name']\}\'bb, \u1094 \u1074 \u1077 \u1090 \u1072  \u1089 \u1086 \u1095 \u1077 \u1090 \u1072 \u1102 \u1090 \u1089 \u1103  \u1089  \u1088 \u1077 \u1082 \u1086 \u1084 \u1077 \u1085 \u1076 \u1086 \u1074 \u1072 \u1085 \u1085 \u1099 \u1084 \u1080 , "\
                f"\uc0\u1089 \u1091 \u1084 \u1084 \u1072  \{total\} \u8381  \u1087 \u1088 \u1080  \u1073 \u1102 \u1076 \u1078 \u1077 \u1090 \u1077  \{q['budget_from']:.0f\}-\{q['budget_to']:.0f\} \u8381 .")\
\
    chosen, reason, ai_used = ranked[0], rule_reason(ranked[0]), False\
    if gc_available():\
        try:\
            sl = [\{"sku_list": [x["p"]["sku"] for x in combo],\
                   "\uc0\u1087 \u1086 \u1079 \u1080 \u1094 \u1080 \u1080 ": [\{"sku": x["p"]["sku"], "\u1085 \u1072 \u1079 \u1074 \u1072 \u1085 \u1080 \u1077 ": x["p"]["name"],\
                                "\uc0\u1082 \u1072 \u1090 \u1077 \u1075 \u1086 \u1088 \u1080 \u1103 ": x["p"]["category"], "\u1094 \u1074 \u1077 \u1090 \u1072 ": x["p"]["colors"],\
                                "\uc0\u1089 \u1090 \u1080 \u1083 \u1080 ": x["p"]["styles"], "\u1094 \u1077 \u1085 \u1072 ": x["p"]["price"],\
                                "\uc0\u1088 \u1072 \u1079 \u1084 \u1077 \u1088 ": x["size"]\} for x in combo],\
                   "\uc0\u1089 \u1091 \u1084 \u1084 \u1072 ": round(sum(x["p"]["price"] for x in combo))\} for combo in shortlist]\
            data = gc_json([\
                \{"role": "system", "content": "\uc0\u1058 \u1099  \u1089 \u1090 \u1080 \u1083 \u1080 \u1089 \u1090  \u1086 \u1092 \u1083 \u1072 \u1081 \u1085 -\u1084 \u1072 \u1075 \u1072 \u1079 \u1080 \u1085 \u1072 . \u1054 \u1090 \u1074 \u1077 \u1095 \u1072 \u1081  \u1089 \u1090 \u1088 \u1086 \u1075 \u1086  JSON."\},\
                \{"role": "user", "content": (\
                    f"\uc0\u1055 \u1088 \u1086 \u1092 \u1080 \u1083 \u1100 : \u1087 \u1086 \u1083  \{q['gender']\}, \u1074 \u1086 \u1079 \u1088 \u1072 \u1089 \u1090  \{q['age']\}, \u1088 \u1072 \u1079 \u1084 \u1077 \u1088  \u1074 \u1077 \u1088 \u1093 \u1072  \{q['size_top']\}, "\
                    f"\uc0\u1085 \u1080 \u1079 \u1072  \{q['size_bottom']\}, \u1086 \u1073 \u1091 \u1074 \u1080  \{q['size_shoes']\}, \u1087 \u1086 \u1074 \u1086 \u1076  \{q['occasion']\}, "\
                    f"\uc0\u1073 \u1102 \u1076 \u1078 \u1077 \u1090  \{q['budget_from']:.0f\}-\{q['budget_to']:.0f\} \u8381 . "\
                    f"\uc0\u1058 \u1080 \u1087 \u1072 \u1078  \'ab\{arch['name']\}\'bb, \u1089 \u1090 \u1080 \u1083 \u1080 : \{', '.join(arch['styles'])\}, \u1094 \u1074 \u1077 \u1090 \u1072 : "\
                    f"\{', '.join(arch['rec_colors'])\}. \uc0\u1064 \u1086 \u1088 \u1090 \u1083 \u1080 \u1089 \u1090  (\u1090 \u1086 \u1083 \u1100 \u1082 \u1086  \u1101 \u1090 \u1080  SKU): "\
                    + json.dumps(sl, ensure_ascii=False)\
                    + ' \uc0\u1042 \u1099 \u1073 \u1077 \u1088 \u1080  \u1051 \u1059 \u1063 \u1064 \u1048 \u1049  \u1082 \u1086 \u1084 \u1087 \u1083 \u1077 \u1082 \u1090  (3-5 \u1087 \u1086 \u1079 \u1080 \u1094 \u1080 \u1081 ; \u1086 \u1073 \u1103 \u1079 \u1072 \u1090 \u1077 \u1083 \u1100 \u1085 \u1099  \u1074 \u1077 \u1088 \u1093 +\u1085 \u1080 \u1079  \u1080 \u1083 \u1080  \u1087 \u1083 \u1072 \u1090 \u1100 \u1077  \u1080  \u1086 \u1073 \u1091 \u1074 \u1100 ). '\
                      '\uc0\u1042 \u1077 \u1088 \u1085 \u1080  \u1058 \u1054 \u1051 \u1068 \u1050 \u1054  JSON: \{"sku_list": ["SKU",...], "reason": "1-2 \u1087 \u1088 \u1077 \u1076 \u1083 \u1086 \u1078 \u1077 \u1085 \u1080 \u1103 , \u1087 \u1086 \u1095 \u1077 \u1084 \u1091  \u1086 \u1073 \u1088 \u1072 \u1079  \u1087 \u1086 \u1076 \u1093 \u1086 \u1076 \u1080 \u1090 "\}')\},\
            ], validate=_v_pick)\
            items, skipped = _choice([str(s) for s in data["sku_list"]], pool, q["budget_to"])\
            if skipped:\
                notes.append("SKU \uc0\u1086 \u1090  \u1048 \u1048  \u1086 \u1090 \u1073 \u1088 \u1086 \u1096 \u1077 \u1085 \u1099  \u1087 \u1088 \u1086 \u1074 \u1077 \u1088 \u1082 \u1086 \u1081 : " + ", ".join(skipped))\
            chosen, reason, ai_used = items, str(data.get("reason", ""))[:300], True\
        except (GCError, ValueError) as e:\
            notes.append("\uc0\u1048 \u1048 -\u1087 \u1086 \u1076 \u1073 \u1086 \u1088  \u1086 \u1073 \u1088 \u1072 \u1079 \u1072  \u1085 \u1077 \u1076 \u1086 \u1089 \u1090 \u1091 \u1087 \u1077 \u1085 , \u1087 \u1088 \u1080 \u1084 \u1077 \u1085 \u1077 \u1085 \u1099  \u1087 \u1088 \u1072 \u1074 \u1080 \u1083 \u1072 : " + str(e)[:140])\
            chosen, reason, ai_used = ranked[0], rule_reason(ranked[0]), False\
\
    score, total = combo_score(chosen)\
    items_out = [\{\
        "sku": x["p"]["sku"], "name": x["p"]["name"], "brand": x["p"]["brand"],\
        "category": x["p"]["category"], "size": x["size"], "exact": x["exact"],\
        "price": x["p"]["price"], "location": x["p"]["location"] or "\'97",\
        "colors": x["p"]["colors"], "photo": x["p"]["photo"] or "",\
    \} for x in chosen]\
    return \{"items": items_out, "total": total,\
            "match": int(max(55, min(99, round(50 + score)))),\
            "reason": reason, "ai_used": ai_used\}\
\
\
def _v_pick(d):\
    if not isinstance(d, dict) or not isinstance(d.get("sku_list"), list) or not d["sku_list"]:\
        raise ValueError("bad pick json")\
\
\
def _choice(skus, pool, budget_to):\
    items, skipped, seen = [], [], set()\
    for s in skus[:5]:\
        s = s.strip()\
        c = pool.get(s)\
        if c is None or s in seen:\
            skipped.append(s)\
            continue\
        seen.add(s)\
        items.append(c)\
    cats = \{c["p"]["category"] for c in items\}\
    ok = ("\uc0\u1086 \u1073 \u1091 \u1074 \u1100 " in cats) and ("\u1087 \u1083 \u1072 \u1090 \u1100 \u1077 " in cats or \{"\u1074 \u1077 \u1088 \u1093 ", "\u1085 \u1080 \u1079 "\} <= cats)\
    if not ok or not 3 <= len(items) <= 5:\
        raise ValueError("\uc0\u1089 \u1086 \u1089 \u1090 \u1072 \u1074  \u1086 \u1073 \u1088 \u1072 \u1079 \u1072  \u1085 \u1077  \u1087 \u1086 \u1076 \u1093 \u1086 \u1076 \u1080 \u1090 ")\
    if sum(c["p"]["price"] for c in items) > budget_to * 1.2:\
        raise ValueError("\uc0\u1087 \u1088 \u1077 \u1074 \u1099 \u1096 \u1077 \u1085  \u1073 \u1102 \u1076 \u1078 \u1077 \u1090 ")\
    return items, skipped\
\
\
def upsell_items(conn, q, arch, outfit):\
    cap = max(q["budget_to"] * 0.30, 500)\
    in_outfit = \{i["sku"] for i in outfit["items"]\}\
    out_colors = \{norm(c) for i in outfit["items"] for c in tags(i["colors"])\}\
    comp = set()\
    for i in outfit["items"]:\
        try:\
            comp |= set(json.loads(conn.execute(\
                "SELECT comp FROM products WHERE sku=?", (i["sku"],)).fetchone()[0] or "[]"))\
        except Exception:\
            pass\
    pool = []\
    for p in conn.execute("SELECT * FROM products WHERE stock>0").fetchall():\
        if p["sku"] in in_outfit or p["category"] == "\uc0\u1087 \u1083 \u1072 \u1090 \u1100 \u1077 ":\
            continue\
        if p["gender"] not in (q["gender"], "\uc0\u1091 \u1085 \u1080 \u1089 \u1077 \u1082 \u1089 ") or p["price"] > cap:\
            continue\
        field = SIZE_FIELD.get(p["category"])\
        if field and not has_size(p, q[field], p["category"])[0]:\
            continue\
        s, why = 5.0, []\
        if p["sku"] in comp:\
            s += 40; why.append("\uc0\u1088 \u1077 \u1082 \u1086 \u1084 \u1077 \u1085 \u1076 \u1086 \u1074 \u1072 \u1085  \u1082 \u1072 \u1082  \u1076 \u1086 \u1087 \u1086 \u1083 \u1085 \u1077 \u1085 \u1080 \u1077  \u1082  \u1101 \u1090 \u1086 \u1084 \u1091  \u1086 \u1073 \u1088 \u1072 \u1079 \u1091 ")\
        cols = set(tags(p["colors"]))\
        if \{norm(c) for c in cols\} & out_colors:\
            s += 20; why.append("\uc0\u1089 \u1086 \u1095 \u1077 \u1090 \u1072 \u1077 \u1090 \u1089 \u1103  \u1089  \u1094 \u1074 \u1077 \u1090 \u1072 \u1084 \u1080  \u1086 \u1073 \u1088 \u1072 \u1079 \u1072 ")\
        ov = set(tags(p["styles"])) & (set(arch["styles"]) | set(q["styles"]))\
        if ov:\
            s += 12 * min(3, len(ov)); why.append("\uc0\u1089 \u1086 \u1074 \u1087 \u1072 \u1076 \u1072 \u1077 \u1090  \u1087 \u1086  \u1089 \u1090 \u1080 \u1083 \u1102  (\'ab" + "/".join(sorted(ov)) + "\'bb)")\
        if q["occasion"] in tags(p["occasions"]):\
            s += 8; why.append(f"\uc0\u1087 \u1086 \u1076 \u1093 \u1086 \u1076 \u1080 \u1090  \u1076 \u1083 \u1103  \u1087 \u1086 \u1074 \u1086 \u1076 \u1072  \'ab\{q['occasion']\}\'bb")\
        if p["margin"]:\
            s += 8 * min(1.0, p["margin"] / 0.6)\
        if \{norm(c) for c in cols\} & set(arch["avoid_colors"]):\
            s -= 25\
        pool.append((s, p, why))\
    pool.sort(key=lambda t: -t[0])\
    picked, per_cat = [], \{\}\
    for _s, p, why in pool:\
        capc = 1 if p["category"] in ("\uc0\u1072 \u1082 \u1089 \u1077 \u1089 \u1089 \u1091 \u1072 \u1088 ", "\u1086 \u1073 \u1091 \u1074 \u1100 ") else 2\
        if per_cat.get(p["category"], 0) >= capc:\
            continue\
        per_cat[p["category"]] = per_cat.get(p["category"], 0) + 1\
        picked.append(\{"sku": p["sku"], "name": p["name"], "price": p["price"],\
                       "photo": p["photo"] or "",\
                       "reason": "; ".join(why) or "\uc0\u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1099 \u1081  \u1074 \u1072 \u1088 \u1080 \u1072 \u1085 \u1090 , \u1089 \u1086 \u1095 \u1077 \u1090 \u1072 \u1077 \u1090 \u1089 \u1103  \u1089  \u1086 \u1073 \u1088 \u1072 \u1079 \u1086 \u1084 "\})\
        if len(picked) == 3:\
            break\
    return picked\
\
\
def pipeline(sid, q, photo_path):\
    conn = db()\
    notes = []\
    photo_info = None\
    if photo_path:\
        if gc_available():\
            try:\
                photo_info = gc_analyze_photo(photo_path)\
                if photo_info.get("\uc0\u1086 \u1096 \u1080 \u1073 \u1082 \u1072 "):\
                    notes.append("\uc0\u1060 \u1086 \u1090 \u1086  \u1085 \u1077  \u1080 \u1089 \u1087 \u1086 \u1083 \u1100 \u1079 \u1086 \u1074 \u1072 \u1085 \u1086 : " + str(photo_info["\u1086 \u1096 \u1080 \u1073 \u1082 \u1072 "])[:120])\
                    photo_info = None\
                else:\
                    notes.append("\uc0\u1060 \u1086 \u1090 \u1086  \u1091 \u1095 \u1090 \u1077 \u1085 \u1086 : \u1090 \u1077 \u1083 \u1086 \u1089 \u1083 \u1086 \u1078 \u1077 \u1085 \u1080 \u1077 , \u1094 \u1074 \u1077 \u1090 \u1086 \u1090 \u1080 \u1087  \u1080  \u1089 \u1090 \u1080 \u1083 \u1100 . \u1051 \u1080 \u1094 \u1086  \u1085 \u1077  \u1072 \u1085 \u1072 \u1083 \u1080 \u1079 \u1080 \u1088 \u1091 \u1077 \u1090 \u1089 \u1103 .")\
            except GCError as e:\
                notes.append("\uc0\u1060 \u1086 \u1090 \u1086 -\u1072 \u1085 \u1072 \u1083 \u1080 \u1079  \u1085 \u1077 \u1076 \u1086 \u1089 \u1090 \u1091 \u1087 \u1077 \u1085 : " + str(e)[:140])\
        else:\
            notes.append("\uc0\u1048 \u1048  \u1085 \u1077  \u1085 \u1072 \u1089 \u1090 \u1088 \u1086 \u1077 \u1085  \'97 \u1092 \u1086 \u1090 \u1086  \u1085 \u1077  \u1072 \u1085 \u1072 \u1083 \u1080 \u1079 \u1080 \u1088 \u1086 \u1074 \u1072 \u1083 \u1086 \u1089 \u1100 , \u1087 \u1086 \u1076 \u1073 \u1086 \u1088  \u1087 \u1086  \u1087 \u1088 \u1072 \u1074 \u1080 \u1083 \u1072 \u1084 .")\
    arch = match_archetypes(conn, q, photo_info)\
    outfit = build_outfit(conn, q, arch, notes)\
    ups = upsell_items(conn, q, arch, outfit)\
    ups_total = sum(u["price"] for u in ups)\
    if not conn.execute("SELECT 1 FROM events WHERE sid=? AND event='questionnaire_started'",\
                        (sid,)).fetchone():\
        log_event(conn, sid, "questionnaire_started")\
    log_event(conn, sid, "questionnaire_completed",\
              \{"ai_used": bool(arch.get("ai_used") or outfit.get("ai_used")),\
               "occasion": q["occasion"]\})\
    log_event(conn, sid, "outfit_shown", \{"arch": arch["name"], "total": outfit["total"],\
                                          "items": [i["sku"] for i in outfit["items"]]\})\
    result = \{"archetype": arch, "outfit": outfit, "upsells": ups, "notes": notes,\
              "ai_used": bool(arch.get("ai_used") or outfit.get("ai_used")),\
              "upsell_total": ups_total, "total_with_upsells": outfit["total"] + ups_total\}\
    conn.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?)",\
                 (sid, time.time(), "completed", json.dumps(result, ensure_ascii=False)))\
    conn.commit()\
    conn.close()\
    return result\
\
\
def render_lookboard(items, title, subtitle, out_path):\
    thumb, pad, cap_h, header = 320, 22, 52, 120\
    cols = 2 if len(items) <= 4 else 3\
    rows = -(-max(1, len(items)) // cols)\
    W = cols * (thumb + pad) + pad\
    H = header + rows * (thumb + cap_h + pad) + 70\
    img = Image.new("RGB", (W, H), "#f4f6fb")\
    d = ImageDraw.Draw(img)\
    d.text((pad, 36), title[:48], fill="#101528", font=_font(38))\
    if subtitle:\
        d.text((pad, 88), subtitle[:64], fill="#5a6473", font=_font(22))\
    for i, it in enumerate(items):\
        x = pad + (i % cols) * (thumb + pad)\
        y = header + (i // cols) * (thumb + cap_h + pad)\
        ph = Image.new("RGB", (thumb, thumb), "#e6ebf4")\
        path = MEDIA / it["photo"] if it.get("photo") else None\
        if path and path.exists():\
            try:\
                src = Image.open(path).convert("RGB")\
                src.thumbnail((thumb, thumb))\
                ph.paste(src, ((thumb - src.width) // 2, (thumb - src.height) // 2))\
            except Exception:\
                pass\
        img.paste(ph, (x, y))\
        d.text((x + 2, y + thumb + 8), f"\{it['sku']\} \'b7 \{it['category']\} \'b7 \{it['price']:.0f\} \uc0\u8381 ",\
               fill="#2a3244", font=_font(20))\
    d.text((pad, H - 44), "AURATECH \'b7 \uc0\u1083 \u1091 \u1082 \u1073 \u1086 \u1088 \u1076  \u1080 \u1079  \u1082 \u1072 \u1090 \u1072 \u1083 \u1086 \u1075 \u1072  \u1084 \u1072 \u1075 \u1072 \u1079 \u1080 \u1085 \u1072  (\u1090 \u1086 \u1095 \u1085 \u1099 \u1077  \u1092 \u1086 \u1090 \u1086  \u1090 \u1086 \u1074 \u1072 \u1088 \u1086 \u1074 )",\
           fill="#6b7280", font=_font(20))\
    img.save(out_path, "PNG")\
\
\
# ------------------------------------------------------------- \uc0\u1096 \u1072 \u1073 \u1083 \u1086 \u1085 \u1099  ----\
CSS = """\
:root\{--a:#2f6fed;--bg:#f4f6fb;--ink:#1c2333;--muted:#6b7280;--ok:#1a7f4e;--danger:#c0392b;--bd:#c9d2e3\}\
*\{box-sizing:border-box\}body\{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--ink);font-size:18px\}\
h1,h2,h3\{margin:6px 0 10px\}\
.top\{display:flex;gap:14px;align-items:center;padding:14px 20px;background:#101528;color:#fff;flex-wrap:wrap\}\
.brand\{font-weight:800;letter-spacing:2px;font-size:20px\}.tag\{opacity:.75\}\
.ai-off\{margin-left:auto;background:#7a2c2c;padding:6px 12px;border-radius:8px;font-size:13px\}\
main\{max-width:1000px;margin:0 auto;padding:16px\}\
.muted\{color:var(--muted)\}.small\{font-size:13px\}\
.error\{background:#fdecea;color:var(--danger);border:1px solid #f5c6c0;padding:10px 14px;border-radius:10px;margin:10px 0\}\
.msg\{background:#e8f7ee;color:var(--ok);border:1px solid #bfe6cf;padding:10px 14px;border-radius:10px;margin:10px 0\}\
.progress\{height:10px;background:#dde3ee;border-radius:6px;overflow:hidden;margin:12px 0 4px\}\
#bar\{height:100%;width:33%;background:var(--a);transition:width .3s\}\
fieldset\{border:0;padding:10px 0 0;margin:0\}\
.grid\{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px\}\
label.f\{display:flex;flex-direction:column;gap:6px;font-size:15px\}\
label.f input,select,textarea\{font-size:17px;padding:11px;border:1px solid var(--bd);border-radius:10px;background:#fff\}\
.blk\{margin:10px 0\}.lbl\{color:var(--muted);margin-bottom:6px;font-size:15px\}\
.chips\{display:flex;flex-wrap:wrap;gap:8px\}\
.chip\{border:1.5px solid var(--bd);background:#fff;border-radius:999px;padding:8px 14px;cursor:pointer;font-size:15px\}\
.chip.on\{background:var(--a);border-color:var(--a);color:#fff\}\
.pblock\{margin-top:12px;padding:12px;border:1px dashed var(--bd);border-radius:12px\}\
.consent\{display:flex;gap:10px;font-size:13px;color:var(--muted);margin-top:8px;align-items:flex-start\}\
#pprev\{max-height:160px;border-radius:10px;margin-top:8px;display:block\}\
.nav\{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap;align-items:center\}\
.btn\{font-size:17px;padding:12px 20px;border-radius:12px;border:0;background:var(--a);color:#fff;cursor:pointer;min-height:48px\}\
.btn.ghost\{background:#fff;color:var(--a);border:1.5px solid var(--a)\}\
.btn.danger\{background:var(--danger)\}\
.btn.small\{font-size:14px;padding:7px 12px;min-height:38px\}\
.btn:disabled\{opacity:.6;cursor:default\}\
.card\{background:#fff;border-radius:16px;padding:16px 18px;margin:12px 0;box-shadow:0 2px 10px rgba(15,25,50,.06)\}\
.loader\{display:flex;gap:12px;align-items:center;padding:24px;font-size:19px\}\
.spinner\{width:32px;height:32px;border:4px solid #dfe7f5;border-top-color:var(--a);border-radius:50%;animation:spin 1s linear infinite\}\
@keyframes spin\{to\{transform:rotate(360deg)\}\}\
.arc\{display:flex;gap:16px;align-items:center;flex-wrap:wrap\}\
.badge\{min-width:80px;height:80px;border-radius:50%;background:var(--ok);color:#fff;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;flex-shrink:0\}\
.note\{font-size:13px;color:var(--muted);margin:2px 0\}\
.aichip\{display:inline-block;background:#fff4d6;color:#8a6d1a;padding:4px 10px;border-radius:8px;font-size:13px\}\
.modes\{display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap\}\
.mode-btn\{border:1.5px solid var(--bd);background:#fff;border-radius:10px;padding:9px 14px;cursor:pointer;font-size:15px\}\
.mode-btn.active\{background:var(--a);border-color:var(--a);color:#fff\}\
.iwrap\{text-align:center\}.iwrap img\{max-width:100%;border-radius:12px\}\
.disc\{color:#8a6d1a;background:#fff8e6;padding:8px 12px;border-radius:8px;font-size:14px\}\
.tbl\{width:100%;border-collapse:collapse;margin-top:8px\}\
.tbl th,.tbl td\{border-bottom:1px solid #e4e9f2;padding:9px 7px;text-align:left;font-size:16px\}\
.tbl th\{color:var(--muted);font-weight:600\}\
.loc\{filter:blur(7px);user-select:none\}.loc.rev\{filter:none\}\
.tot\{margin:10px 0;font-size:17px\}\
.ugrid\{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px\}\
.up\{display:flex;gap:10px;border:1px solid #e4e9f2;border-radius:12px;padding:10px\}\
.up img\{width:100px;height:120px;object-fit:cover;border-radius:8px\}\
.why\{font-size:13px;color:var(--muted)\}\
.actions\{display:flex;gap:10px;margin:14px 0;flex-wrap:wrap\}\
.foot\{text-align:center;padding:16px;font-size:13px\}\
table.rep\{width:100%;border-collapse:collapse\}\
table.rep th,table.rep td\{border-bottom:1px solid #e4e9f2;padding:6px 8px;font-size:14px;text-align:left\}\
.mgrid\{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px\}\
.mg .num\{font-size:30px;font-weight:800;color:var(--a)\}\
@media print\{.top,.actions,.modes,.foot,.no-print,button\{display:none!important\}.loc\{filter:none!important\}.card\{box-shadow:none;border:1px solid #ddd\}\}\
"""\
\
JS = """\
const $=(s,r)=>(r||document).querySelector(s);\
const $$=(s,r)=>Array.from((r||document).querySelectorAll(s));\
const form=$('#wizard');\
const steps=$$('fieldset[data-step]');\
const labels=['\uc0\u1055 \u1072 \u1088 \u1072 \u1084 \u1077 \u1090 \u1088 \u1099 ','\u1062 \u1077 \u1083 \u1100 ','\u1057 \u1090 \u1080 \u1083 \u1100 '];\
let cur=0;\
function show(i)\{cur=i;steps.forEach((f,k)=>f.hidden=k!==i);\
 $('#bar').style.width=((i+1)*100/3)+'%';\
 $('#steplbl').textContent='\uc0\u1064 \u1072 \u1075  '+(i+1)+' \u1080 \u1079  3 \'97 '+labels[i];\
 $('#prev').hidden=i===0;$('#next').hidden=i===2;$('#go').hidden=i!==2;\
 window.scrollTo(\{top:0,behavior:'smooth'\});\}\
 $('#next').onclick=()=>\{let ok=true;\
 $$('fieldset[data-step="'+cur+'"] input, fieldset[data-step="'+cur+'"] select, fieldset[data-step="'+cur+'"] textarea').forEach(el=>\{if(!el.checkValidity())\{ok=false;el.reportValidity();\}\});\
 if(ok)show(Math.min(cur+1,2));\};\
 $('#prev').onclick=()=>show(Math.max(cur-1,0));\
$$('.chips').forEach(g=>\{g.addEventListener('click',e=>\{const b=e.target.closest('.chip');if(!b)return;\
 b.classList.toggle('on');\
 const inp=document.querySelector('input[name="'+g.dataset.input+'"]');\
 inp.value=$$('.chip.on',g).map(x=>x.dataset.value).join(',');\});\});\
 $('#photo').addEventListener('change',()=>\{const f=$('#photo').files[0];const p=$('#pprev');\
 if(f)\{p.src=URL.createObjectURL(f);p.hidden=false;\}else p.hidden=true;\});\
let started=false;\
form.addEventListener('change',()=>\{if(started)return;started=true;\
 const fd=new FormData();fd.append('session_id',form.session_id.value);\
 fd.append('event','questionnaire_started');fetch('/api/event',\{method:'POST',body:fd\});\});\
const err=$('#err'),loader=$('#loader');\
form.addEventListener('submit',e=>\{e.preventDefault();err.hidden=true;\
 if($('#photo').files[0]&&!form.photo_consent.checked)\{err.textContent='\uc0\u1044 \u1083 \u1103  \u1092 \u1086 \u1090 \u1086  \u1085 \u1091 \u1078 \u1085 \u1086  \u1089 \u1086 \u1075 \u1083 \u1072 \u1089 \u1080 \u1077  \u1085 \u1072  \u1086 \u1073 \u1088 \u1072 \u1073 \u1086 \u1090 \u1082 \u1091  (152-\u1060 \u1047 ).';err.hidden=false;return;\}\
 loader.hidden=false;form.hidden=true;\
 fetch('/api/recommend',\{method:'POST',body:new FormData(form)\})\
 .then(r=>r.text().then(t=>(\{ok:r.ok,t\})))\
 .then((\{ok,t\})=>\{if(!ok)\{let d='\uc0\u1054 \u1096 \u1080 \u1073 \u1082 \u1072  \u1087 \u1086 \u1076 \u1073 \u1086 \u1088 \u1072 ';try\{d=JSON.parse(t).detail||d\}catch(e)\{\}throw new Error(d);\}\
  document.getElementById('result').innerHTML=t;window.scrollTo(\{top:0\});\})\
 .catch(ex=>\{err.textContent=ex.message;err.hidden=false;form.hidden=false;\})\
 .finally(()=>\{loader.hidden=true;\});\
\});\
window.AU=\{\
 mode(sid,mode,btn)\{$$('.mode-btn').forEach(b=>b.classList.remove('active'));btn.classList.add('active');\
  $('#illustration-note').hidden=mode!=='ai';$('#img-msg').hidden=true;$('#img-spinner').hidden=false;\
  fetch('/api/render/'+sid+'/'+mode).then(r=>\{if(!r.ok)return r.json().then(j=>\{throw new Error(j.detail||'\uc0\u1054 \u1096 \u1080 \u1073 \u1082 \u1072 ')\});return r.blob();\})\
  .then(b=>\{$('#lookboard-img').src=URL.createObjectURL(b);\})\
  .catch(e=>\{$('#img-msg').textContent=e.message;$('#img-msg').hidden=false;\})\
  .finally(()=>\{$('#img-spinner').hidden=true;\});\},\
 where(sid,btn)\{const fd=new FormData();fd.append('session_id',sid);fd.append('event','where_clicked');\
  fetch('/api/event',\{method:'POST',body:fd\});\
  $$('.loc').forEach(td=>td.classList.add('rev'));btn.disabled=true;btn.textContent='\uc0\u1055 \u1086 \u1082 \u1072 \u1079 \u1072 \u1085 \u1086  \u10003 ';\},\
 upsell(sid,sku,price,btn)\{const fd=new FormData();fd.append('session_id',sid);fd.append('sku',sku);\
  fetch('/api/upsell',\{method:'POST',body:fd\}).then(r=>\{if(r.ok)\{btn.disabled=true;btn.textContent='\uc0\u10003  \u1076 \u1086 \u1073 \u1072 \u1074 \u1083 \u1077 \u1085 \u1086 ';\
   const box=$('#result-box');const s=parseFloat(box.dataset.upsell||'0')+price;const o=parseFloat(box.dataset.outfit||'0');\
   box.dataset.upsell=s;$('#upsell-sum').textContent=Math.round(s)+' \uc0\u8381 ';$('#grand-total').textContent=Math.round(o+s)+' \u8381 ';\}\});\},\
 del(sid)\{fetch('/api/delete/'+sid,\{method:'POST'\}).then(()=>\{\
  document.getElementById('result').innerHTML="<div class='card'>\uc0\u1052 \u1086 \u1080  \u1076 \u1072 \u1085 \u1085 \u1099 \u1077  \u1091 \u1076 \u1072 \u1083 \u1077 \u1085 \u1099 .</div>";\
  setTimeout(()=>location.reload(),1200);\});\},\
 restart(sid)\{fetch('/api/delete/'+sid,\{method:'POST'\}).catch(()=>\{\}).finally(()=>location.reload());\},\
 print()\{window.print();\}\
\};\
show(0);\
"""\
\
INDEX_TEMPLATE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">\
<meta name="viewport" content="width=device-width, initial-scale=1">\
<title>AURATECH \'97 AI-\uc0\u1089 \u1090 \u1080 \u1083 \u1080 \u1089 \u1090 </title><style>__CSS__</style></head><body>\
<header class="top"><div class="brand">AURATECH</div>\
<div class="tag">AI-\uc0\u1089 \u1090 \u1080 \u1083 \u1080 \u1089 \u1090  \u1086 \u1092 \u1083 \u1072 \u1081 \u1085 -\u1084 \u1072 \u1075 \u1072 \u1079 \u1080 \u1085 \u1072 </div>__AI_BANNER__</header>\
<main>\
<div id="err" class="error" hidden></div>\
<form id="wizard" autocomplete="off">\
<input type="hidden" name="session_id" value="__SID__">\
<div class="progress"><div id="bar"></div></div>\
<div id="steplbl" class="muted"></div>\
<fieldset data-step="0">\
<h2>\uc0\u1064 \u1072 \u1075  1. \u1055 \u1072 \u1088 \u1072 \u1084 \u1077 \u1090 \u1088 \u1099 </h2>\
<div class="grid">\
 <label class="f">\uc0\u1055 \u1086 \u1083 <select name="gender"><option>\u1078 \u1077 \u1085 \u1089 \u1082 \u1080 \u1081 </option><option>\u1084 \u1091 \u1078 \u1089 \u1082 \u1086 \u1081 </option></select></label>\
 <label class="f">\uc0\u1042 \u1086 \u1079 \u1088 \u1072 \u1089 \u1090 <input type="number" name="age" min="12" max="99" value="30" required></label>\
 <label class="f">\uc0\u1056 \u1086 \u1089 \u1090 , \u1089 \u1084 <input type="number" name="height" min="120" max="220" value="170" required></label>\
 <label class="f">\uc0\u1056 \u1072 \u1079 \u1084 \u1077 \u1088  \u1074 \u1077 \u1088 \u1093 \u1072 <select name="size_top">__OPT_TOP__</select></label>\
 <label class="f">\uc0\u1056 \u1072 \u1079 \u1084 \u1077 \u1088  \u1085 \u1080 \u1079 \u1072 <select name="size_bottom">__OPT_BOT__</select></label>\
 <label class="f">\uc0\u1056 \u1072 \u1079 \u1084 \u1077 \u1088  \u1086 \u1073 \u1091 \u1074 \u1080 <select name="size_shoes">__OPT_SHOE__</select></label>\
 <label class="f">\uc0\u1058 \u1077 \u1083 \u1086 \u1089 \u1083 \u1086 \u1078 \u1077 \u1085 \u1080 \u1077 <select name="body_type">__OPT_BODY__</select></label>\
</div>\
</fieldset>\
<fieldset data-step="1" hidden>\
<h2>\uc0\u1064 \u1072 \u1075  2. \u1062 \u1077 \u1083 \u1100 </h2>\
<div class="grid">\
 <label class="f">\uc0\u1055 \u1086 \u1074 \u1086 \u1076 <select name="occasion">__OPT_OCC__</select></label>\
 <label class="f">\uc0\u1057 \u1077 \u1079 \u1086 \u1085 <select name="season">__OPT_SEASON__</select></label>\
 <label class="f">\uc0\u1041 \u1102 \u1076 \u1078 \u1077 \u1090  \u1086 \u1090 , \u8381 <input type="number" name="budget_from" min="0" step="500" value="5000" required></label>\
 <label class="f">\uc0\u1041 \u1102 \u1076 \u1078 \u1077 \u1090  \u1076 \u1086 , \u8381 <input type="number" name="budget_to" min="500" step="500" value="30000" required></label>\
 <label class="f">\uc0\u1063 \u1090 \u1086  \u1085 \u1091 \u1078 \u1085 \u1086  \u1076 \u1086 \u1082 \u1091 \u1087 \u1080 \u1090 \u1100  (\u1085 \u1077 \u1086 \u1073 \u1103 \u1079 \u1072 \u1090 \u1077 \u1083 \u1100 \u1085 \u1086 )<textarea name="needs" rows="2"></textarea></label>\
</div>\
</fieldset>\
<fieldset data-step="2" hidden>\
<h2>\uc0\u1064 \u1072 \u1075  3. \u1057 \u1090 \u1080 \u1083 \u1100 </h2>\
<div class="blk"><div class="lbl">\uc0\u1057 \u1090 \u1080 \u1083 \u1080 </div>\
 <div class="chips" data-input="styles">__CHIPS_STYLES__</div>\
 <input type="hidden" name="styles"></div>\
<div class="blk"><div class="lbl">\uc0\u1051 \u1102 \u1073 \u1080 \u1084 \u1099 \u1077  \u1094 \u1074 \u1077 \u1090 \u1072 </div>\
 <div class="chips" data-input="favorite_colors">__CHIPS_FAV__</div>\
 <input type="hidden" name="favorite_colors"></div>\
<div class="blk"><div class="lbl">\uc0\u1053 \u1077 \u1078 \u1077 \u1083 \u1072 \u1090 \u1077 \u1083 \u1100 \u1085 \u1099 \u1077  \u1094 \u1074 \u1077 \u1090 \u1072 </div>\
 <div class="chips" data-input="avoid_colors">__CHIPS_AVOID__</div>\
 <input type="hidden" name="avoid_colors"></div>\
<div class="grid">\
 <label class="f">\uc0\u1055 \u1086 \u1089 \u1072 \u1076 \u1082 \u1072 <select name="fit">__OPT_FIT__</select></label>\
 <label class="f">\uc0\u1054 \u1075 \u1088 \u1072 \u1085 \u1080 \u1095 \u1077 \u1085 \u1080 \u1103 <textarea name="restrictions" rows="2"></textarea></label>\
</div>\
<div class="pblock">\
 <label class="f">\uc0\u1060 \u1086 \u1090 \u1086  (\u1085 \u1077 \u1086 \u1073 \u1103 \u1079 \u1072 \u1090 \u1077 \u1083 \u1100 \u1085 \u1086  \'97 \u1087 \u1086 \u1084 \u1086 \u1075 \u1072 \u1077 \u1090  \u1090 \u1086 \u1095 \u1085 \u1077 \u1077  \u1087 \u1086 \u1076 \u1086 \u1073 \u1088 \u1072 \u1090 \u1100  \u1094 \u1074 \u1077 \u1090 \u1086 \u1074 \u1091 \u1102  \u1075 \u1072 \u1084 \u1084 \u1091 )\
  <input type="file" name="photo" id="photo" accept="image/jpeg,image/png,image/webp"></label>\
 <img id="pprev" alt="" hidden>\
 <label class="consent"><input type="checkbox" name="photo_consent">\
  \uc0\u1057 \u1086 \u1075 \u1083 \u1072 \u1089 \u1077 \u1085 (-\u1085 \u1072 ) \u1085 \u1072  \u1086 \u1073 \u1088 \u1072 \u1073 \u1086 \u1090 \u1082 \u1091  \u1079 \u1072 \u1075 \u1088 \u1091 \u1078 \u1077 \u1085 \u1085 \u1086 \u1075 \u1086  \u1092 \u1086 \u1090 \u1086  \u1076 \u1083 \u1103  \u1087 \u1086 \u1076 \u1073 \u1086 \u1088 \u1072  \u1086 \u1076 \u1077 \u1078 \u1076 \u1099  (152-\u1060 \u1047 ). \u1060 \u1086 \u1090 \u1086  \u1091 \u1076 \u1072 \u1083 \u1103 \u1077 \u1090 \u1089 \u1103 \
  \uc0\u1089 \u1088 \u1072 \u1079 \u1091  \u1087 \u1086 \u1089 \u1083 \u1077  \u1087 \u1086 \u1076 \u1073 \u1086 \u1088 \u1072  \u1080 \u1083 \u1080  \u1095 \u1077 \u1088 \u1077 \u1079  15 \u1084 \u1080 \u1085 \u1091 \u1090 . \u1051 \u1080 \u1094 \u1086  \u1085 \u1077  \u1072 \u1085 \u1072 \u1083 \u1080 \u1079 \u1080 \u1088 \u1091 \u1077 \u1090 \u1089 \u1103 , \u1083 \u1080 \u1095 \u1085 \u1086 \u1089 \u1090 \u1100  \u1085 \u1077  \u1088 \u1072 \u1089 \u1087 \u1086 \u1079 \u1085 \u1072 \u1105 \u1090 \u1089 \u1103 .</label>\
</div>\
</fieldset>\
<div class="nav">\
 <button type="button" id="prev" class="btn ghost" hidden>\uc0\u1053 \u1072 \u1079 \u1072 \u1076 </button>\
 <button type="button" id="next" class="btn">\uc0\u1044 \u1072 \u1083 \u1077 \u1077 </button>\
 <button type="submit" id="go" class="btn" hidden>\uc0\u1055 \u1086 \u1076 \u1086 \u1073 \u1088 \u1072 \u1090 \u1100  \u1086 \u1073 \u1088 \u1072 \u1079 </button>\
</div>\
</form>\
<div id="loader" class="loader" hidden><div class="spinner"></div><div>\uc0\u1057 \u1086 \u1073 \u1080 \u1088 \u1072 \u1077 \u1084  \u1074 \u1072 \u1096  \u1086 \u1073 \u1088 \u1072 \u1079 \'85 \u1076 \u1086  \u1084 \u1080 \u1085 \u1091 \u1090 \u1099 </div></div>\
<div id="result"></div>\
<footer class="foot muted">AURATECH MVP \'b7 \uc0\u1092 \u1086 \u1090 \u1086  \u1085 \u1077  \u1093 \u1088 \u1072 \u1085 \u1080 \u1090 \u1089 \u1103  \u1076 \u1086 \u1083 \u1100 \u1096 \u1077  \u1089 \u1077 \u1089 \u1089 \u1080 \u1080  \'b7 \u1083 \u1080 \u1094 \u1086  \u1085 \u1077  \u1072 \u1085 \u1072 \u1083 \u1080 \u1079 \u1080 \u1088 \u1091 \u1077 \u1090 \u1089 \u1103 </footer>\
</main>\
<script>__JS__</script>\
</body></html>"""\
\
\
def render_index(sid):\
    def opts(items):\
        return "".join(f"<option>\{s\}</option>" for s in items)\
\
    def chips(items):\
        return "".join(f"<button type='button' class='chip' data-value='\{s\}'>\{s\}</button>"\
                       for s in items)\
\
    html = INDEX_TEMPLATE\
    html = html.replace("__CSS__", CSS).replace("__JS__", JS)\
    html = html.replace("__AI_BANNER__", "" if gc_available() else\
                        "<div class='ai-off'>\uc0\u1048 \u1048  \u1074 \u1099 \u1082 \u1083 \u1102 \u1095 \u1077 \u1085 : \u1085 \u1077  \u1079 \u1072 \u1076 \u1072 \u1085  GIGACHAT_AUTH_KEY \'97 \u1088 \u1077 \u1078 \u1080 \u1084  \u1087 \u1088 \u1072 \u1074 \u1080 \u1083 </div>")\
    html = html.replace("__SID__", sid)\
    html = html.replace("__OPT_TOP__", opts(TOP_SIZES))\
    html = html.replace("__OPT_BOT__", opts(BOT_SIZES))\
    html = html.replace("__OPT_SHOE__", opts(SHOE_SIZES))\
    html = html.replace("__OPT_BODY__", opts(BODIES))\
    html = html.replace("__OPT_OCC__", opts(OCC))\
    html = html.replace("__OPT_SEASON__", opts(SEASONS))\
    html = html.replace("__OPT_FIT__", opts(FITS))\
    html = html.replace("__CHIPS_STYLES__", chips(STYLE_LIST))\
    html = html.replace("__CHIPS_FAV__", chips(COLOR_LIST))\
    html = html.replace("__CHIPS_AVOID__", chips(COLOR_LIST))\
    return html\
\
\
def result_html(sid, r):\
    items = "".join(\
        f"<tr><td>\{i['sku']\}</td><td>\{i['name']\} <span class='muted'>\{i['brand']\}</span></td>"\
        f"<td>\{i['size']\}\{'*' if not i['exact'] else ''\}</td>"\
        f"<td>\{i['price']:.0f\} \uc0\u8381 </td><td class='loc'>\{i['location']\}</td></tr>"\
        for i in r["outfit"]["items"])\
    ups = "".join(\
        f"<div class='up'><img src='/media/\{u['photo']\}' alt=''>"\
        f"<div><b>\{u['name']\}</b><div class='muted'>\{u['sku']\} \'b7 \{u['price']:.0f\} \uc0\u8381 </div>"\
        f"<div class='why'>\{u['reason']\}</div>"\
        f"<div class='nav'><button class='btn small no-print' "\
        f"onclick=\\"AU.upsell('\{sid\}','\{u['sku']\}',\{u['price']:.0f\},this)\\">+ \uc0\u1074  \u1087 \u1088 \u1080 \u1084 \u1077 \u1088 \u1082 \u1091 </button></div></div></div>"\
        for u in r["upsells"])\
    notes = "".join(f"<p class='note'>\{n\}</p>" for n in r["notes"])\
    chip = "" if r["ai_used"] else "<p><span class='aichip'>\uc0\u1055 \u1086 \u1076 \u1073 \u1086 \u1088  \u1073 \u1077 \u1079  \u1048 \u1048  (\u1087 \u1086  \u1087 \u1088 \u1072 \u1074 \u1080 \u1083 \u1072 \u1084 )</span></p>"\
    return f"""\
<div id="result-box" data-upsell="\{r['upsell_total']:.0f\}" data-outfit="\{r['outfit']['total']:.0f\}">\
<section class="card arc">\
 <div class="badge">\{r['archetype']['percent']\}%</div>\
 <div><h2>\uc0\u1042 \u1072 \u1096  \u1090 \u1080 \u1087 \u1072 \u1078 : \{r['archetype']['name']\}</h2>\
 <p>\{r['archetype']['reason']\}</p>\{notes\}\{chip\}</div>\
</section>\
<section class="card">\
 <div class="modes no-print">\
  <button class="mode-btn active" onclick="AU.mode('\{sid\}','lb',this)">\uc0\u1051 \u1091 \u1082 \u1073 \u1086 \u1088 \u1076  (\u1090 \u1086 \u1095 \u1085 \u1099 \u1081 )</button>\
  <button class="mode-btn" onclick="AU.mode('\{sid\}','ai',this)">\uc0\u1048 \u1083 \u1083 \u1102 \u1089 \u1090 \u1088 \u1072 \u1094 \u1080 \u1103  (GigaChat)</button>\
 </div>\
 <div class="iwrap"><div id="img-spinner" class="spinner" hidden></div>\
  <img id="lookboard-img" src="/api/render/\{sid\}/lb" alt="\uc0\u1054 \u1073 \u1088 \u1072 \u1079 "></div>\
 <p id="illustration-note" class="disc" hidden>\uc0\u1048 \u1083 \u1083 \u1102 \u1089 \u1090 \u1088 \u1072 \u1094 \u1080 \u1103  \u1089 \u1075 \u1077 \u1085 \u1077 \u1088 \u1080 \u1088 \u1086 \u1074 \u1072 \u1085 \u1072  \u1048 \u1048  \'97 \u1101 \u1090 \u1086  \u1085 \u1077  \u1090 \u1086 \u1095 \u1085 \u1072 \u1103  \u1087 \u1088 \u1080 \u1084 \u1077 \u1088 \u1082 \u1072 .</p>\
 <div id="img-msg" class="error" hidden></div>\
</section>\
<section class="card">\
 <h3>\uc0\u1054 \u1073 \u1088 \u1072 \u1079  \'97 \u1089 \u1086 \u1074 \u1087 \u1072 \u1076 \u1077 \u1085 \u1080 \u1077  \{r['outfit']['match']\}%</h3>\
 <p class="muted">\{r['outfit']['reason']\}</p>\
 <table class="tbl"><thead><tr><th>SKU</th><th>\uc0\u1053 \u1072 \u1079 \u1074 \u1072 \u1085 \u1080 \u1077 </th><th>\u1056 \u1072 \u1079 \u1084 \u1077 \u1088 </th><th>\u1062 \u1077 \u1085 \u1072 </th>\
 <th class="loc">\uc0\u1043 \u1076 \u1077  \u1074 \u1079 \u1103 \u1090 \u1100 </th></tr></thead><tbody>\{items\}</tbody></table>\
 <p class="muted small">* \'97 \uc0\u1073 \u1083 \u1080 \u1078 \u1072 \u1081 \u1096 \u1080 \u1081  \u1076 \u1086 \u1089 \u1090 \u1091 \u1087 \u1085 \u1099 \u1081  \u1088 \u1072 \u1079 \u1084 \u1077 \u1088 </p>\
 <div class="tot">\uc0\u1057 \u1091 \u1084 \u1084 \u1072  \u1086 \u1073 \u1088 \u1072 \u1079 \u1072 : <b>\{r['outfit']['total']:.0f\} \u8381 </b> \'b7 \u1089  \u1076 \u1086 \u1087 \u1088 \u1086 \u1076 \u1072 \u1078 \u1072 \u1084 \u1080 :\
  <b id="grand-total">\{r['total_with_upsells']:.0f\} \uc0\u8381 </b></div>\
 <button class="btn ghost no-print" onclick="AU.where('\{sid\}',this)">\uc0\u1055 \u1086 \u1082 \u1072 \u1079 \u1072 \u1090 \u1100 , \u1075 \u1076 \u1077  \u1074 \u1079 \u1103 \u1090 \u1100 </button>\
</section>\
<section class="card">\
 <h3>\uc0\u1044 \u1086 \u1087 \u1088 \u1086 \u1076 \u1072 \u1078 \u1080  \'97 \u1087 \u1088 \u1080 \u1088 \u1086 \u1089 \u1090  \u1082  \u1095 \u1077 \u1082 \u1091 : <span id="upsell-sum">\{r['upsell_total']:.0f\} \u8381 </span></h3>\
 <div class="ugrid">\{ups\}</div>\
</section>\
<section class="actions no-print">\
 <button class="btn" onclick="AU.print()">\uc0\u1056 \u1072 \u1089 \u1087 \u1077 \u1095 \u1072 \u1090 \u1072 \u1090 \u1100 </button>\
 <button class="btn ghost" onclick="AU.restart('\{sid\}')">\uc0\u1047 \u1072 \u1085 \u1086 \u1074 \u1086 </button>\
 <button class="btn danger" onclick="AU.del('\{sid\}')">\uc0\u1059 \u1076 \u1072 \u1083 \u1080 \u1090 \u1100  \u1084 \u1086 \u1080  \u1076 \u1072 \u1085 \u1085 \u1099 \u1077 </button>\
</section>\
</div>"""\
\
\
ADMIN_LOGIN = """<!doctype html><html lang="ru"><head><meta charset="utf-8">\
<meta name="viewport" content="width=device-width, initial-scale=1">\
<title>AURATECH \'97 \uc0\u1074 \u1093 \u1086 \u1076 </title><style>__CSS__</style></head><body>\
<main style="display:flex;justify-content:center;padding-top:10vh">\
<section class="card" style="width:min(420px,92vw)"><h1>AURATECH \'b7 \uc0\u1072 \u1076 \u1084 \u1080 \u1085 \u1082 \u1072 </h1>\
__ERR__\
<form method="post" action="/admin/login">\
<label class="f">\uc0\u1055 \u1072 \u1088 \u1086 \u1083 \u1100 <input type="password" name="password" required autofocus></label>\
<div class="nav"><button class="btn">\uc0\u1042 \u1086 \u1081 \u1090 \u1080 </button></div>\
</form>\
<p class="muted small">\uc0\u1055 \u1072 \u1088 \u1086 \u1083 \u1100  \u1079 \u1072 \u1076 \u1072 \u1105 \u1090 \u1089 \u1103  \u1087 \u1077 \u1088 \u1077 \u1084 \u1077 \u1085 \u1085 \u1086 \u1081  \u1086 \u1082 \u1088 \u1091 \u1078 \u1077 \u1085 \u1080 \u1103  ADMIN_PASSWORD.</p>\
</section></main></body></html>"""\
\
ADMIN_PANEL = """<!doctype html><html lang="ru"><head><meta charset="utf-8">\
<meta name="viewport" content="width=device-width, initial-scale=1">\
<title>AURATECH \'97 \uc0\u1072 \u1076 \u1084 \u1080 \u1085 -\u1087 \u1072 \u1085 \u1077 \u1083 \u1100 </title><style>__CSS__</style></head><body>\
<header class="top"><div class="brand">AURATECH</div><div class="tag">\uc0\u1072 \u1076 \u1084 \u1080 \u1085 -\u1087 \u1072 \u1085 \u1077 \u1083 \u1100 </div>\
<nav style="margin-left:auto;display:flex;gap:12px;align-items:center">\
<a href="/" style="color:#dfe7ff">\uc0\u1050 \u1080 \u1086 \u1089 \u1082 </a>\
<form method="post" action="/admin/logout" style="display:inline">\
<button class="btn ghost small">\uc0\u1042 \u1099 \u1093 \u1086 \u1076 </button></form></nav></header>\
<main>\
__MSG__\
<section class="card"><h2>\uc0\u1057 \u1090 \u1072 \u1090 \u1091 \u1089 </h2>\
<p>\uc0\u1058 \u1080 \u1087 \u1072 \u1078 \u1077 \u1081 : <b>__NARCH__</b> \'b7 \u1058 \u1086 \u1074 \u1072 \u1088 \u1086 \u1074 : <b>__NPROD__</b> \'b7 GigaChat: __AI__</p>\
<div class="nav">\
<form method="post" action="/admin/demo"><button class="btn">\uc0\u1047 \u1072 \u1075 \u1088 \u1091 \u1079 \u1080 \u1090 \u1100  \u1076 \u1077 \u1084 \u1086 -\u1076 \u1072 \u1085 \u1085 \u1099 \u1077  (20 \u1090 \u1080 \u1087 \u1072 \u1078 \u1077 \u1081  + 60 \u1090 \u1086 \u1074 \u1072 \u1088 \u1086 \u1074 )</button></form>\
<form method="post" action="/admin/reset" onsubmit="return confirm('\uc0\u1059 \u1076 \u1072 \u1083 \u1080 \u1090 \u1100  \u1074 \u1089 \u1077  \u1076 \u1072 \u1085 \u1085 \u1099 \u1077 ?')">\
<button class="btn danger">\uc0\u1054 \u1095 \u1080 \u1089 \u1090 \u1080 \u1090 \u1100  \u1074 \u1089 \u1105 </button></form>\
</div>\
<p class="muted small">\uc0\u1055 \u1077 \u1088 \u1077 \u1084 \u1077 \u1085 \u1085 \u1099 \u1077  \u1086 \u1082 \u1088 \u1091 \u1078 \u1077 \u1085 \u1080 \u1103 : ADMIN_PASSWORD, SECRET_KEY, GIGACHAT_AUTH_KEY,\
GIGACHAT_SCOPE, GIGACHAT_MODEL, GIGACHAT_VERIFY_SSL. \uc0\u1055 \u1086 \u1089 \u1083 \u1077  \u1079 \u1072 \u1075 \u1088 \u1091 \u1079 \u1082 \u1080  \u1076 \u1077 \u1084 \u1086 -\u1076 \u1072 \u1085 \u1085 \u1099 \u1093  \u1072 \u1085 \u1082 \u1077 \u1090 \u1072 \
\uc0\u1085 \u1072  \u1075 \u1083 \u1072 \u1074 \u1085 \u1086 \u1081  \u1089 \u1090 \u1088 \u1072 \u1085 \u1080 \u1094 \u1077  \u1085 \u1072 \u1095 \u1085 \u1105 \u1090  \u1074 \u1099 \u1076 \u1072 \u1074 \u1072 \u1090 \u1100  \u1086 \u1073 \u1088 \u1072 \u1079 \u1099 .</p>\
</section>\
__STATS__\
</main></body></html>"""\
\
\
def is_admin(request: Request) -> bool:\
    token = hashlib.sha256((SECRET + "admin").encode()).hexdigest()\
    return request.cookies.get("auratech_admin", "") == token\
\
\
def admin_stats(conn):\
    evs = conn.execute("SELECT * FROM events ORDER BY ts DESC").fetchall()\
    sids = \{e["sid"] for e in evs\}\
    completed = \{e["sid"] for e in evs if e["event"] == "questionnaire_completed"\}\
    shown = \{e["sid"] for e in evs if e["event"] == "outfit_shown"\}\
    where = \{e["sid"] for e in evs if e["event"] == "where_clicked"\}\
    arch, skus, totals, ups = \{\}, \{\}, [], 0\
    for e in evs:\
        try:\
            p = json.loads(e["payload"] or "\{\}")\
        except Exception:\
            p = \{\}\
        if e["event"] == "outfit_shown":\
            arch[p.get("arch", "?")] = arch.get(p.get("arch", "?"), 0) + 1\
            if p.get("total"):\
                totals.append(float(p["total"]))\
            for s in p.get("items") or []:\
                skus[s] = skus.get(s, 0) + 1\
        elif e["event"] == "upsell_added":\
            ups += 1\
    pct = lambda n, d: round(100 * n / d) if d else 0  # noqa: E731\
\
    def table(rows, empty):\
        if not rows:\
            return f"<p class='muted'>\{empty\}</p>"\
        return "<table class='rep'>" + "".join(f"<tr><td>\{k\}</td><td>\{v\}</td></tr>"\
                                               for k, v in rows) + "</table>"\
\
    recent = ("<table class='rep'><tr><th>\uc0\u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 </th><th>\u1089 \u1077 \u1089 \u1089 \u1080 \u1103 </th></tr>" +\
              "".join(f"<tr><td>\{e['event']\}</td><td>\{(e['sid'] or '')[:8]\}</td></tr>"\
                      for e in evs[:12]) + "</table>") if evs else "<p class='muted'>\uc0\u1087 \u1086 \u1082 \u1072  \u1085 \u1077 \u1090  \u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1081 </p>"\
    return f"""\
<div class="mgrid">\
 <div class="card mg"><div class="num">\{len(sids)\}</div><div class="muted">\uc0\u1089 \u1077 \u1089 \u1089 \u1080 \u1081 </div></div>\
 <div class="card mg"><div class="num">\{pct(len(completed), len(sids))\}%</div><div class="muted">\uc0\u1079 \u1072 \u1074 \u1077 \u1088 \u1096 \u1080 \u1083 \u1080  \u1072 \u1085 \u1082 \u1077 \u1090 \u1091 </div></div>\
 <div class="card mg"><div class="num">\{pct(len(shown), len(sids))\}%</div><div class="muted">\uc0\u1076 \u1086 \u1096 \u1083 \u1080  \u1076 \u1086  \u1086 \u1073 \u1088 \u1072 \u1079 \u1072 </div></div>\
 <div class="card mg"><div class="num">\{pct(len(where), len(sids))\}%</div><div class="muted">\uc0\u1072 \u1082 \u1090 \u1080 \u1074 \u1072 \u1094 \u1080 \u1103  (\'ab\u1075 \u1076 \u1077  \u1074 \u1079 \u1103 \u1090 \u1100 \'bb)</div></div>\
 <div class="card mg"><div class="num">\{round(sum(totals) / len(totals)) if totals else 0\} \uc0\u8381 </div><div class="muted">\u1089 \u1088 \u1077 \u1076 \u1085 \u1103 \u1103  \u1089 \u1091 \u1084 \u1084 \u1072  \u1086 \u1073 \u1088 \u1072 \u1079 \u1072 </div></div>\
 <div class="card mg"><div class="num">\{ups\}</div><div class="muted">\uc0\u1076 \u1086 \u1087 \u1088 \u1086 \u1076 \u1072 \u1078  \u1076 \u1086 \u1073 \u1072 \u1074 \u1083 \u1077 \u1085 \u1086 </div></div>\
</div>\
<section class="card"><h3>\uc0\u1058 \u1086 \u1087 -\u1086 \u1073 \u1088 \u1072 \u1079 \u1099 </h3>\{table(sorted(arch.items(), key=lambda x: -x[1])[:5], '\u1087 \u1086 \u1082 \u1072  \u1085 \u1077 \u1090  \u1076 \u1072 \u1085 \u1085 \u1099 \u1093 ')\}</section>\
<section class="card"><h3>\uc0\u1058 \u1086 \u1087 -SKU</h3>\{table(sorted(skus.items(), key=lambda x: -x[1])[:8], '\u1087 \u1086 \u1082 \u1072  \u1085 \u1077 \u1090  \u1076 \u1072 \u1085 \u1085 \u1099 \u1093 ')\}</section>\
<section class="card"><h3>\uc0\u1055 \u1086 \u1089 \u1083 \u1077 \u1076 \u1085 \u1080 \u1077  \u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1103 </h3>\{recent\}</section>"""\
\
\
# --------------------------------------------------------------- \uc0\u1088 \u1086 \u1091 \u1090 \u1099  ----\
app = FastAPI(title="AURATECH \'97 AI-\uc0\u1089 \u1090 \u1080 \u1083 \u1080 \u1089 \u1090 ", docs_url=None, redoc_url=None)\
\
\
@app.get("/healthz")\
def healthz():\
    return \{"ok": True, "ai": gc_available()\}\
\
\
@app.get("/", response_class=HTMLResponse)\
def index():\
    cleanup_photos()\
    return render_index(uuid.uuid4().hex[:16])\
\
\
@app.post("/api/event")\
def api_event(session_id: str = Form(...), event: str = Form(...),\
              payload: str = Form("\{\}")):\
    if event not in EVENTS:\
        raise HTTPException(status_code=400, detail="\uc0\u1053 \u1077 \u1080 \u1079 \u1074 \u1077 \u1089 \u1090 \u1085 \u1086 \u1077  \u1089 \u1086 \u1073 \u1099 \u1090 \u1080 \u1077 ")\
    try:\
        data = json.loads(payload or "\{\}")\
    except Exception:\
        data = \{\}\
    conn = db()\
    log_event(conn, session_id, event, data)\
    conn.commit()\
    conn.close()\
    return \{"ok": True\}\
\
\
@app.post("/api/recommend")\
def recommend(session_id: str = Form(...), gender: str = Form(...),\
              age: int = Form(...), height: int = Form(...),\
              size_top: str = Form(...), size_bottom: str = Form(...),\
              size_shoes: str = Form(...), body_type: str = Form(...),\
              occasion: str = Form(...), season: str = Form(...),\
              budget_from: float = Form(0), budget_to: float = Form(...),\
              needs: str = Form(""), styles: str = Form(""),\
              favorite_colors: str = Form(""), avoid_colors: str = Form(""),\
              fit: str = Form("\uc0\u1086 \u1073 \u1099 \u1095 \u1085 \u1072 \u1103 "), restrictions: str = Form(""),\
              photo: UploadFile | None = File(None), photo_consent: str = Form("")):\
    try:\
        q = parse_q(gender, age, height, size_top, size_bottom, size_shoes, body_type,\
                    occasion, season, budget_from, budget_to, needs, styles,\
                    favorite_colors, avoid_colors, fit, restrictions)\
    except UserError as e:\
        return JSONResponse(status_code=400, content=\{"detail": str(e)\})\
\
    photo_path = None\
    if photo is not None and photo.filename:\
        if photo_consent != "on":\
            return JSONResponse(status_code=400, content=\{"detail":\
                "\uc0\u1044 \u1083 \u1103  \u1079 \u1072 \u1075 \u1088 \u1091 \u1079 \u1082 \u1080  \u1092 \u1086 \u1090 \u1086  \u1085 \u1091 \u1078 \u1085 \u1086  \u1089 \u1086 \u1075 \u1083 \u1072 \u1089 \u1080 \u1077  \u1085 \u1072  \u1086 \u1073 \u1088 \u1072 \u1073 \u1086 \u1090 \u1082 \u1091  \u1087 \u1077 \u1088 \u1089 \u1086 \u1085 \u1072 \u1083 \u1100 \u1085 \u1099 \u1093  \u1076 \u1072 \u1085 \u1085 \u1099 \u1093  (152-\u1060 \u1047 ). "\
                "\uc0\u1054 \u1090 \u1084 \u1077 \u1090 \u1100 \u1090 \u1077  \u1095 \u1077 \u1082 \u1073 \u1086 \u1082 \u1089  \u1080 \u1083 \u1080  \u1091 \u1073 \u1077 \u1088 \u1080 \u1090 \u1077  \u1092 \u1086 \u1090 \u1086 ."\})\
        ext = os.path.splitext(photo.filename)[1].lower()\
        if ext not in \{".jpg", ".jpeg", ".png", ".webp"\}:\
            return JSONResponse(status_code=400,\
                                content=\{"detail": "\uc0\u1060 \u1086 \u1090 \u1086  \u1076 \u1086 \u1083 \u1078 \u1085 \u1086  \u1073 \u1099 \u1090 \u1100  JPG, PNG \u1080 \u1083 \u1080  WebP."\})\
        data = photo.file.read()\
        if len(data) > 10 * 1024 * 1024:\
            return JSONResponse(status_code=400,\
                                content=\{"detail": "\uc0\u1060 \u1086 \u1090 \u1086  \u1073 \u1086 \u1083 \u1100 \u1096 \u1077  10 \u1052 \u1041 . \u1057 \u1086 \u1078 \u1084 \u1080 \u1090 \u1077  \u1092 \u1072 \u1081 \u1083 ."\})\
        photo_path = MEDIA / "photos" / f"\{session_id\}\{ext\}"\
        photo_path.write_bytes(data)\
\
    try:\
        result = pipeline(session_id, q, str(photo_path) if photo_path else None)\
    except UserError as e:\
        return JSONResponse(status_code=400, content=\{"detail": str(e)\})\
    except Exception:\
        log.exception("recommend: \uc0\u1074 \u1085 \u1091 \u1090 \u1088 \u1077 \u1085 \u1085 \u1103 \u1103  \u1086 \u1096 \u1080 \u1073 \u1082 \u1072 ")\
        return JSONResponse(status_code=500, content=\{"detail":\
            "\uc0\u1042 \u1085 \u1091 \u1090 \u1088 \u1077 \u1085 \u1085 \u1103 \u1103  \u1086 \u1096 \u1080 \u1073 \u1082 \u1072  \u1087 \u1086 \u1076 \u1073 \u1086 \u1088 \u1072 . \u1055 \u1086 \u1087 \u1088 \u1086 \u1073 \u1091 \u1081 \u1090 \u1077  \u1077 \u1097 \u1105  \u1088 \u1072 \u1079 ."\})\
    finally:\
        if photo_path:\
            try:\
                photo_path.unlink()\
            except OSError:\
                pass\
    return HTMLResponse(result_html(session_id, result))\
\
\
@app.get("/api/render/\{sid\}/\{mode\}")\
def render_image(sid: str, mode: str):\
    conn = db()\
    row = conn.execute("SELECT result FROM sessions WHERE sid=?", (sid,)).fetchone()\
    conn.close()\
    if row is None or not row["result"]:\
        raise HTTPException(status_code=404, detail="\uc0\u1057 \u1077 \u1089 \u1089 \u1080 \u1103  \u1085 \u1077  \u1085 \u1072 \u1081 \u1076 \u1077 \u1085 \u1072  \u1080 \u1083 \u1080  \u1091 \u1076 \u1072 \u1083 \u1077 \u1085 \u1072 ")\
    if mode not in ("lb", "ai"):\
        raise HTTPException(status_code=400, detail="\uc0\u1053 \u1077 \u1080 \u1079 \u1074 \u1077 \u1089 \u1090 \u1085 \u1099 \u1081  \u1088 \u1077 \u1078 \u1080 \u1084 ")\
    r = json.loads(row["result"])\
    out = MEDIA / "gen" / f"\{sid\}_\{mode\}.png"\
    if not out.exists():\
        title = f"\uc0\u1042 \u1072 \u1096  \u1086 \u1073 \u1088 \u1072 \u1079  \'b7 \u1090 \u1080 \u1087 \u1072 \u1078  \'ab\{r['archetype']['name']\}\'bb"\
        sub = f"\uc0\u1057 \u1086 \u1074 \u1087 \u1072 \u1076 \u1077 \u1085 \u1080 \u1077  \{r['archetype']['percent']\}% \'b7 \u1089 \u1091 \u1084 \u1084 \u1072  \{r['outfit']['total']:.0f\} \u8381 "\
        if mode == "lb":\
            render_lookboard(r["outfit"]["items"], title, sub, str(out))\
        else:\
            if not gc_available():\
                return JSONResponse(status_code=502, content=\{"detail":\
                    "\uc0\u1048 \u1083 \u1083 \u1102 \u1089 \u1090 \u1088 \u1072 \u1094 \u1080 \u1103  \u1085 \u1077 \u1076 \u1086 \u1089 \u1090 \u1091 \u1087 \u1085 \u1072 : \u1085 \u1077  \u1079 \u1072 \u1076 \u1072 \u1085  GIGACHAT_AUTH_KEY."\})\
            try:\
                parts = [f"\{i['category']\} (\{', '.join(tags(i['colors'])[:2])\})"\
                         for i in r["outfit"]["items"]]\
                prompt = ("\uc0\u1060 \u1086 \u1090 \u1086 \u1088 \u1077 \u1072 \u1083 \u1080 \u1089 \u1090 \u1080 \u1095 \u1085 \u1072 \u1103  \u1087 \u1083 \u1086 \u1089 \u1082 \u1072 \u1103  \u1088 \u1072 \u1089 \u1082 \u1083 \u1072 \u1076 \u1082 \u1072  \u1086 \u1076 \u1077 \u1078 \u1076 \u1099  (flat lay, \u1074 \u1080 \u1076  \u1089 \u1074 \u1077 \u1088 \u1093 \u1091 ) "\
                          "\uc0\u1085 \u1072  \u1089 \u1074 \u1077 \u1090 \u1083 \u1086 \u1084  \u1085 \u1077 \u1081 \u1090 \u1088 \u1072 \u1083 \u1100 \u1085 \u1086 \u1084  \u1092 \u1086 \u1085 \u1077 , \u1089 \u1090 \u1080 \u1083 \u1100  \u1082 \u1072 \u1090 \u1072 \u1083 \u1086 \u1075 \u1072  \u1084 \u1072 \u1075 \u1072 \u1079 \u1080 \u1085 \u1072  \u1086 \u1076 \u1077 \u1078 \u1076 \u1099 : "\
                          + "; ".join(parts) + ". \uc0\u1045 \u1076 \u1080 \u1085 \u1072 \u1103  \u1094 \u1074 \u1077 \u1090 \u1086 \u1074 \u1072 \u1103  \u1075 \u1072 \u1084 \u1084 \u1072 . \u1057 \u1090 \u1088 \u1086 \u1075 \u1086  \u1073 \u1077 \u1079  \u1083 \u1102 \u1076 \u1077 \u1081 , "\
                          "\uc0\u1073 \u1077 \u1079  \u1083 \u1080 \u1094 , \u1073 \u1077 \u1079  \u1083 \u1086 \u1075 \u1086 \u1090 \u1080 \u1087 \u1086 \u1074  \u1080  \u1090 \u1077 \u1082 \u1089 \u1090 \u1072 .")\
                out.write_bytes(gc_image(prompt))\
            except GCError as e:\
                return JSONResponse(status_code=502, content=\{"detail": str(e)\})\
    return FileResponse(out, media_type="image/png")\
\
\
@app.post("/api/upsell")\
def upsell_add(session_id: str = Form(...), sku: str = Form(...)):\
    conn = db()\
    row = conn.execute("SELECT result FROM sessions WHERE sid=?", (session_id,)).fetchone()\
    if row is None or not row["result"]:\
        conn.close()\
        raise HTTPException(status_code=404, detail="\uc0\u1057 \u1077 \u1089 \u1089 \u1080 \u1103  \u1085 \u1077  \u1085 \u1072 \u1081 \u1076 \u1077 \u1085 \u1072 ")\
    found = next((u for u in json.loads(row["result"]).get("upsells", [])\
                  if u["sku"] == sku), None)\
    if found is None:\
        conn.close()\
        raise HTTPException(status_code=404, detail="\uc0\u1044 \u1086 \u1087 \u1088 \u1086 \u1076 \u1072 \u1078 \u1072  \u1085 \u1077  \u1085 \u1072 \u1081 \u1076 \u1077 \u1085 \u1072 ")\
    log_event(conn, session_id, "upsell_added",\
              \{"sku": sku, "price": found["price"], "name": found["name"]\})\
    conn.commit()\
    conn.close()\
    return \{"ok": True, "price": found["price"]\}\
\
\
@app.post("/api/delete/\{sid\}")\
def delete_data(sid: str):\
    conn = db()\
    conn.execute("DELETE FROM sessions WHERE sid=?", (sid,))\
    conn.commit()\
    conn.close()\
    for mode in ("lb", "ai"):\
        (MEDIA / "gen" / f"\{sid\}_\{mode\}.png").unlink(missing_ok=True)\
    for f in (MEDIA / "photos").iterdir():\
        if f.name.startswith(sid):\
            f.unlink(missing_ok=True)\
    return \{"ok": True\}\
\
\
@app.get("/admin", response_class=HTMLResponse)\
def admin_page(request: Request):\
    cleanup_photos()\
    if not is_admin(request):\
        html = ADMIN_LOGIN.replace("__CSS__", CSS)\
        html = html.replace("__ERR__", "<div class='error'>\uc0\u1053 \u1077 \u1074 \u1077 \u1088 \u1085 \u1099 \u1081  \u1087 \u1072 \u1088 \u1086 \u1083 \u1100 .</div>"\
                            if request.query_params.get("err") else "")\
        return HTMLResponse(html)\
    conn = db()\
    n_arch = conn.execute("SELECT COUNT(*) c FROM archetypes").fetchone()["c"]\
    n_prod = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]\
    stats = admin_stats(conn)\
    conn.close()\
    msg_param = request.query_params.get("msg")\
    html = ADMIN_PANEL.replace("__CSS__", CSS)\
    html = html.replace("__MSG__", f"<div class='msg'>\{msg_param\}</div>" if msg_param else "")\
    html = html.replace("__NARCH__", str(n_arch)).replace("__NPROD__", str(n_prod))\
    html = html.replace("__AI__", "<b class='ok'>\uc0\u1087 \u1086 \u1076 \u1082 \u1083 \u1102 \u1095 \u1105 \u1085 </b>" if gc_available()\
                        else "<b class='warn' style='color:#b7791f'>\uc0\u1082 \u1083 \u1102 \u1095  \u1085 \u1077  \u1079 \u1072 \u1076 \u1072 \u1085  \'97 \u1088 \u1077 \u1078 \u1080 \u1084  \u1087 \u1088 \u1072 \u1074 \u1080 \u1083 </b>")\
    html = html.replace("__STATS__", stats)\
    return HTMLResponse(html)\
\
\
@app.post("/admin/login")\
def admin_login(request: Request, password: str = Form("")):\
    if password != ADMIN_PASSWORD:\
        return RedirectResponse("/admin?err=1", status_code=303)\
    resp = RedirectResponse("/admin", status_code=303)\
    resp.set_cookie("auratech_admin",\
                    hashlib.sha256((SECRET + "admin").encode()).hexdigest(),\
                    httponly=True, samesite="lax", max_age=8 * 3600)\
    return resp\
\
\
@app.post("/admin/logout")\
def admin_logout():\
    resp = RedirectResponse("/admin", status_code=303)\
    resp.delete_cookie("auratech_admin")\
    return resp\
\
\
@app.post("/admin/demo")\
def admin_demo(request: Request):\
    if not is_admin(request):\
        return RedirectResponse("/admin", status_code=303)\
    conn = db()\
    na, np_ = load_demo(conn)\
    conn.commit()\
    conn.close()\
    return RedirectResponse(f"/admin?msg=\uc0\u1047 \u1072 \u1075 \u1088 \u1091 \u1078 \u1077 \u1085 \u1086 : \{na\} \u1090 \u1080 \u1087 \u1072 \u1078 \u1077 \u1081 , \{np_\} \u1090 \u1086 \u1074 \u1072 \u1088 \u1086 \u1074 ", status_code=303)\
\
\
@app.post("/admin/reset")\
def admin_reset(request: Request):\
    if not is_admin(request):\
        return RedirectResponse("/admin", status_code=303)\
    conn = db()\
    for t in ("archetypes", "products", "events", "sessions"):\
        conn.execute(f"DELETE FROM \{t\}")\
    conn.commit()\
    conn.close()\
    return RedirectResponse("/admin?msg=\uc0\u1044 \u1072 \u1085 \u1085 \u1099 \u1077  \u1086 \u1095 \u1080 \u1097 \u1077 \u1085 \u1099 ", status_code=303)}