"""
AURATECH — AI-стилист (lite MVP в одном файле).
Киоск: /   Админка: /admin   Здоровье: /healthz
Без GIGACHAT_AUTH_KEY работает в режиме правил; с ключом включает ИИ.
"""
import hashlib, io, json, logging, os, random, re, sqlite3, threading, time, uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- конфиг ----
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "auratech-admin")
SECRET = os.environ.get("SECRET_KEY", "auratech-secret")
GC_KEY = os.environ.get("GIGACHAT_AUTH_KEY", "").strip()
GC_SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GC_MODEL = os.environ.get("GIGACHAT_MODEL", "GigaChat-2-Max")
GC_FALLBACK = os.environ.get("GIGACHAT_FALLBACK_MODEL", "GigaChat-Pro")
GC_VERIFY = os.environ.get("GIGACHAT_VERIFY_SSL", "true").lower() != "false"
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
MEDIA = DATA_DIR / "media"
(MEDIA / "products").mkdir(parents=True, exist_ok=True)
(MEDIA / "photos").mkdir(parents=True, exist_ok=True)
(MEDIA / "gen").mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "auratech.db"
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("auratech")


class UserError(Exception):
    pass


class GCError(Exception):
    pass


def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = db()
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS archetypes(
      aid TEXT PRIMARY KEY, name TEXT, desc TEXT, gender TEXT,
      age_from INTEGER, age_to INTEGER, height_from INTEGER, height_to INTEGER,
      body TEXT, color_type TEXT, styles TEXT, rec_colors TEXT, avoid_colors TEXT);
    CREATE TABLE IF NOT EXISTS products(
      sku TEXT PRIMARY KEY, name TEXT, brand TEXT, category TEXT, gender TEXT,
      sizes TEXT, colors TEXT, price REAL, season TEXT, styles TEXT, occasions TEXT,
      cut TEXT, location TEXT, stock INTEGER, margin REAL, photo TEXT, comp TEXT);
    CREATE TABLE IF NOT EXISTS sessions(
      sid TEXT PRIMARY KEY, created REAL, status TEXT, result TEXT);
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, sid TEXT, event TEXT, payload TEXT);
    """)
    c.commit()
    c.close()


init_db()

EVENTS = {"questionnaire_started", "questionnaire_completed", "outfit_shown",
          "where_clicked", "upsell_added"}


def log_event(conn, sid, event, payload=None):
    conn.execute("INSERT INTO events(ts,sid,event,payload) VALUES (?,?,?,?)",
                 (time.time(), sid, event, json.dumps(payload or {}, ensure_ascii=False)))


def cleanup_photos(ttl=15 * 60):
    now = time.time()
    for f in (MEDIA / "photos").iterdir():
        try:
            if f.is_file() and now - f.stat().st_mtime > ttl:
                f.unlink()
        except OSError:
            pass


# ------------------------------------------------------------ утилиты ----
def tags(s):
    return [t.strip().lower() for t in re.split(r"[,;/]", s or "") if t.strip()]


def norm(c):
    return str(c).strip().lower().replace("ё", "е")


COLOR_FAMILY = {
    "черный": "нейтральный", "белый": "нейтральный", "серый": "нейтральный",
    "графитовый": "нейтральный", "бежевый": "нейтральный", "кремовый": "нейтральный",
    "молочный": "нейтральный", "хаки": "нейтральный",
    "синий": "синий", "голубой": "синий",
    "зеленый": "зеленый", "оливковый": "зеленый", "изумрудный": "зеленый",
    "красный": "красный", "бордовый": "красный", "винный": "красный",
    "оранжевый": "оранжевый", "рыжий": "оранжевый",
    "желтый": "желтый", "золотистый": "желтый",
    "розовый": "розовый", "пудровый": "розовый",
    "фиолетовый": "фиолетовый", "сиреневый": "фиолетовый",
    "коричневый": "коричневый", "шоколадный": "коричневый",
}


def colors_ok(a, b):
    fa = COLOR_FAMILY.get(norm(a), norm(a))
    fb = COLOR_FAMILY.get(norm(b), norm(b))
    return fa == fb or fa == "нейтральный" or fb == "нейтральный"


def palette_ok(set_a, set_b):
    return any(colors_ok(x, y) for x in set_a for y in set_b)


LETTERS = ["XS", "S", "M", "L", "XL", "XXL"]


def size_variants(size):
    s = str(size).strip().upper()
    if s in LETTERS:
        i = LETTERS.index(s)
        out = [s] + [LETTERS[j] for j in (i + 1, i - 1, i + 2, i - 2) if 0 <= j < len(LETTERS)]
        return out
    if s.isdigit():
        n = int(s)
        return [s, str(n - 1), str(n + 1), str(n - 2), str(n + 2)]
    return [s]


def has_size(p, size, cat):
    try:
        sizes = json.loads(p["sizes"] or "[]")
    except Exception:
        sizes = []
    raw = str(size).strip()
    if cat == "аксессуар" or not sizes:
        return True, (sizes[0] if sizes else "OS"), True
    for cand in size_variants(raw):
        if cand in sizes:
            return True, cand, (cand == raw or cand == raw.upper())
    return False, None, False


def _font(size):
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


CAT_BG = {"верх": "#dfe7f5", "низ": "#e7f0df", "верхняя одежда": "#e8e3f5",
          "платье": "#f7e3ee", "обувь": "#f5e9df", "аксессуар": "#f5f2df"}


def make_placeholder(path, sku, name, cat):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (480, 600), CAT_BG.get(cat, "#eef1f6"))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 469, 589], outline="#ffffff", width=3)
    d.text((24, 24), "AURATECH", fill="#8a8a8a", font=_font(16))
    f1, f2 = _font(28), _font(18)
    d.text(((480 - d.textlength(sku, font=f1)) / 2, 262), sku, fill="#1c1c1c", font=f1)
    t2 = name[:30]
    d.text(((480 - min(d.textlength(t2, font=f2), 400)) / 2, 312), t2, fill="#444444", font=f2)
    img.save(path, "JPEG", quality=88)


# ------------------------------------------------------------- GigaChat ----
_gc_token = {"tok": None, "exp": 0}
_gc_lock = threading.Lock()
_gc_cache = {}
AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://gigachat.devices.sberbank.ru/api/v1"


def gc_available():
    return bool(GC_KEY)


def _cache_get(k):
    v = _gc_cache.get(k)
    return v[1] if v and time.time() - v[0] < 3600 else None


def _cache_put(k, v):
    if len(_gc_cache) > 400:
        _gc_cache.clear()
    _gc_cache[k] = (time.time(), v)


def _token():
    with _gc_lock:
        if _gc_token["tok"] and time.time() < _gc_token["exp"] - 60:
            return _gc_token["tok"]
    r = httpx.post(AUTH_URL,
                   headers={"Authorization": "Basic " + GC_KEY,
                            "RqUID": str(uuid.uuid4()),
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Accept": "application/json"},
                   data={"scope": GC_SCOPE}, verify=GC_VERIFY, timeout=30)
    r.raise_for_status()
    tok = r.json()["access_token"]
    with _gc_lock:
        _gc_token["tok"], _gc_token["exp"] = tok, time.time() + 25 * 60
    return tok


def _retry(fn, attempts=3):
    delay = 1.0
    last = None
    for i in range(attempts):
        try:
            return fn()
        except GCError:
            raise
        except Exception as e:
            last = e
            if i == attempts - 1:
                break
            time.sleep(delay)
            delay *= 2
    raise GCError(f"GigaChat недоступен после {attempts} попыток ({type(last).__name__}: {last})")


def _auth_headers(tok):
    return {"Authorization": "Bearer " + tok, "Content-Type": "application/json",
            "Accept": "application/json"}


def gc_chat(messages, temperature=0.2, max_tokens=1200):
    payload = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    key = "chat:" + hashlib.sha256(
        (json.dumps(payload, sort_keys=True, ensure_ascii=False) + GC_MODEL).encode()).hexdigest()
    hit = _cache_get(key)
    if hit is not None:
        return hit

    def call():
        tok = _token()
        r = httpx.post(API_URL + "/chat/completions", headers=_auth_headers(tok),
                       json=payload, verify=GC_VERIFY, timeout=90)
        if r.status_code == 401:
            with _gc_lock:
                _gc_token["tok"] = None
            raise RuntimeError("401 token expired")
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content") or ""

    text = _retry(call)
    _cache_put(key, text)
    return text


def _extract_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("JSON не найден")
    return json.loads(m.group(0))


def gc_json(messages, validate=None, attempts=2):
    msgs = list(messages)
    for _ in range(attempts):
        text = gc_chat(msgs, temperature=0.1)
        try:
            data = _extract_json(text)
            if validate:
                validate(data)
            return data
        except Exception:
            msgs = msgs + [
                {"role": "assistant", "content": text[:800]},
                {"role": "user", "content": "Верни ТОЛЬКО корректный JSON без пояснений и markdown."},
            ]
    raise GCError("GigaChat не вернул валидный JSON")


PHOTO_PROMPT = """Ты ассистент стилиста офлайн-магазина одежды. На фото человек; задача — помочь подобрать одежду.
Верни ТОЛЬКО JSON без пояснений:
{"телосложение": "<одно из: астеническое, нормостеническое, спортивное, крупное>",
 "тон_волос": "<кратко>", "тон_кожи": "<кратко>",
 "цветотип": "<одно из: весна, лето, осень, зима>", "стиль": "<1-3 слова>", "уверенность": <0-100>}
ЗАПРЕЩЕНО определять расу, этничность, здоровье, личность, имя, точный возраст. НЕ описывай лицо.
Если человека в одежде нет — верни {"ошибка": "<причина>"}."""


def gc_analyze_photo(path):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    ck = "vis:" + digest
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    def upload():
        tok = _token()
        with open(path, "rb") as f:
            r = httpx.post(API_URL + "/files", headers={"Authorization": "Bearer " + tok},
                           files={"file": (os.path.basename(path), f, "image/jpeg")},
                           data={"purpose": "general"}, verify=GC_VERIFY, timeout=90)
        r.raise_for_status()
        return r.json()["id"]

    def call():
        fid = upload()
        tok = _token()
        r = httpx.post(API_URL + "/chat/completions", headers=_auth_headers(tok),
                       json={"messages": [{"role": "user", "content": PHOTO_PROMPT,
                                           "attachments": [fid]}],
                             "temperature": 0.1, "max_tokens": 400},
                       verify=GC_VERIFY, timeout=90)
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content") or ""

    data = _extract_json(_retry(call))
    _cache_put(ck, data)
    return data


def gc_image(description):
    key = "img:" + hashlib.sha256(description.encode()).hexdigest()
    hit = _cache_get(key)
    if hit is not None:
        return hit

    def call():
        tok = _token()
        payload = {"messages": [{"role": "user", "content": "Нарисуй: " + description}],
                   "function_call": "auto", "temperature": 0.7}
        file_id = None
        for _ in range(4):
            r = httpx.post(API_URL + "/chat/completions", headers=_auth_headers(_token()),
                           json=payload, verify=GC_VERIFY, timeout=120)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            content = msg.get("content") or ""
            m = re.search(r'<img[^>]+src="([\w\-]{8,80})"', content)
            if m:
                file_id = m.group(1)
                break
            atts = msg.get("attachments") or []
            if atts:
                file_id = atts[0]
                break
            fc = msg.get("function_call")
            if fc and fc.get("name") == "text2image":
                args = fc.get("args")
                if not isinstance(args, str):
                    args = json.dumps(args or {}, ensure_ascii=False)
                payload["messages"].append({"role": "assistant", "content": content,
                                            "function_call": {"name": "text2image", "args": args}})
                payload["messages"].append({"role": "function", "name": "text2image", "content": ""})
                continue
            break
        if not file_id:
            raise GCError("Модель не вернула изображение")
        r = httpx.get(API_URL + f"/files/{file_id}/content",
                      headers={"Authorization": "Bearer " + _token()},
                      verify=GC_VERIFY, timeout=120)
        r.raise_for_status()
        return r.content

    data = _retry(call)
    _cache_put(key, data)
    return data


# ------------------------------------------------------------ демо-данные ----
CT_COLORS = {
    "весна": (["кремовый", "голубой", "оливковый", "коричневый"], ["черный", "бордовый"]),
    "лето": (["серый", "голубой", "розовый", "синий"], ["оранжевый", "бордовый"]),
    "осень": (["бежевый", "бордовый", "оливковый", "коричневый"], ["розовый", "голубой"]),
    "зима": (["черный", "белый", "красный", "синий"], ["оранжевый", "коричневый"]),
}

ARCH_SPECS = [
    ("A01", "Деловая классика", "женский", 25, 45, 155, 180, "нормостеническое", "осень", "классика, деловой", "прямые силуэты, умеренная приталенность"),
    ("A02", "Деловая классика", "мужской", 25, 45, 170, 195, "нормостеническое", "зима", "классика, деловой", "прямые силуэты, пиджак"),
    ("A03", "Городской кэжуал", "женский", 20, 40, 150, 178, "астеническое", "лето", "кэжуал, минимализм", "свободные силуэты, слои"),
    ("A04", "Городской кэжуал", "мужской", 20, 40, 168, 192, "спортивное", "весна", "кэжуал, минимализм", "свободные силуэты"),
    ("A05", "Спортивный стиль", "женский", 18, 35, 150, 180, "спортивное", "весна", "спорт, кэжуал", "комфортные силуэты"),
    ("A06", "Спортивный стиль", "мужской", 18, 35, 170, 195, "спортивное", "лето", "спорт, стрит", "комфортные силуэты"),
    ("A07", "Романтика", "женский", 18, 35, 150, 175, "астеническое", "весна", "романтика, вечерний", "струящиеся силуэты"),
    ("A08", "Бохо", "женский", 25, 45, 150, 185, "", "осень", "бохо, кэжуал", "многослойность, свободный крой"),
    ("A09", "Минимализм", "женский", 25, 45, 150, 180, "астеническое", "зима", "минимализм, деловой", "чистые линии"),
    ("A10", "Минимализм", "мужской", 25, 45, 170, 195, "нормостеническое", "зима", "минимализм, классика", "чистые линии"),
    ("A11", "Вечерний выход", "женский", 25, 55, 150, 185, "нормостеническое", "зима", "вечерний, классика", "элегантные силуэты"),
    ("A12", "Вечерний выход", "мужской", 30, 55, 170, 195, "нормостеническое", "осень", "вечерний, деловой", "элегантные силуэты"),
    ("A13", "Стрит-стайл", "женский", 16, 30, 150, 180, "", "лето", "стрит, спорт", "оверсайз, слои"),
    ("A14", "Стрит-стайл", "мужской", 16, 30, 165, 195, "спортивное", "зима", "стрит, спорт", "оверсайз, слои"),
    ("A15", "Преппи", "женский", 18, 35, 150, 178, "астеническое", "лето", "преппи, классика", "аккуратные силуэты"),
    ("A16", "Преппи", "мужской", 18, 35, 165, 190, "нормостеническое", "весна", "преппи, кэжуал", "аккуратные силуэты"),
    ("A17", "Скандинавский минимализм", "женский", 30, 50, 150, 185, "", "осень", "минимализм, кэжуал", "простые силуэты, база"),
    ("A18", "Скандинавский минимализм", "мужской", 30, 50, 165, 195, "", "осень", "минимализм, кэжуал", "простые силуэты, база"),
    ("A19", "Универсальный базис", "унисекс", 20, 60, 150, 200, "", "", "кэжуал, минимализм", "базовые силуэты"),
    ("A20", "Отпускной бохо", "унисекс", 20, 55, 150, 200, "", "", "бохо, кэжуал", "свободные силуэты, лён"),
]


def I(n, b, p, c, st, oc, se):
    return (n, b, p, c, st, oc, se)


M_TOPS = [
    I("Футболка из хлопка", "URBANO", 1990, "белый,черный,серый", "кэжуал,спорт", "повседневное", "круглый год"),
    I("Рубашка оксфорд", "NORDVELL", 4590, "голубой,белый", "классика,деловой,преппи", "работа", "круглый год"),
    I("Поло джерси", "ATELIER 7", 3490, "оливковый,синий", "кэжуал,преппи", "повседневное,работа", "круглый год"),
    I("Свитер из мериноса", "KAVRA", 6900, "бежевый,графитовый", "классика,минимализм", "работа,повседневное", "демисезон"),
    I("Худи оверсайз", "STREETLINE", 4200, "серый,черный", "спорт,стрит", "повседневное", "демисезон"),
    I("Рубашка изо льна", "SOLMAR", 4900, "кремовый,оливковый", "бохо,кэжуал", "отпуск", "лето"),
]
F_TOPS = [
    I("Блуза шёлковая", "LUMI", 5290, "кремовый,розовый", "классика,романтика", "работа,событие", "круглый год"),
    I("Футболка из хлопка", "URBANO", 1790, "белый,серый", "кэжуал", "повседневное", "круглый год"),
    I("Джемпер тонкого трикотажа", "KAVRA", 5600, "бежевый,голубой", "кэжуал,минимализм", "работа,повседневное", "демисезон"),
    I("Топ атласный", "LUMI", 3200, "черный,бордовый", "вечерний,романтика", "событие", "круглый год"),
    I("Рубашка оверсайз", "NORDVELL", 3900, "голубой,белый", "кэжуал,стрит", "повседневное", "круглый год"),
    I("Джемпер с горлом", "KAVRA", 4990, "молочный,графитовый", "минимализм,кэжуал", "повседневное", "демисезон"),
]
M_BOTTOMS = [
    I("Чинос прямого кроя", "NORDVELL", 4990, "бежевый,синий", "кэжуал,преппи", "повседневное,работа", "круглый год"),
    I("Брюки классические", "ATELIER 7", 6900, "графитовый,черный", "классика,деловой", "работа", "круглый год"),
    I("Джинсы straight", "DENIM LAB", 5490, "синий,черный", "кэжуал,стрит", "повседневное", "круглый год"),
    I("Джоггеры", "STREETLINE", 3900, "серый,черный", "спорт", "повседневное", "демисезон"),
    I("Брюки льняные", "SOLMAR", 4400, "кремовый,оливковый", "бохо,кэжуал", "отпуск", "лето"),
    I("Карго", "STREETLINE", 4700, "хаки,черный", "стрит", "повседневное", "круглый год"),
]
F_BOTTOMS = [
    I("Брюки палаццо", "LUMI", 5900, "черный,кремовый", "классика,вечерний", "событие,работа", "круглый год"),
    I("Джинсы mom-fit", "DENIM LAB", 4990, "голубой,синий", "кэжуал,стрит", "повседневное", "круглый год"),
    I("Юбка миди плиссе", "LUMI", 5200, "бордовый,оливковый", "романтика,бохо", "событие", "круглый год"),
    I("Брюки со стрелками", "ATELIER 7", 6400, "графитовый,синий", "деловой,классика", "работа", "круглый год"),
    I("Шорты льняные", "SOLMAR", 2900, "бежевый,белый", "бохо,кэжуал", "отпуск", "лето"),
]
F_DRESSES = [
    I("Платье-комбинация", "LUMI", 6900, "графитовый,бордовый", "вечерний,романтика", "событие", "круглый год"),
    I("Сарафан льняной", "SOLMAR", 5400, "оливковый,кремовый", "бохо,кэжуал", "отпуск", "лето"),
    I("Платье-футляр", "ATELIER 7", 7900, "черный,синий", "деловой,классика", "работа", "круглый год"),
    I("Платье макси в цветок", "SOLMAR", 6200, "розовый,голубой", "романтика,бохо", "отпуск,событие", "лето"),
    I("Трикотажное платье", "KAVRA", 5600, "серый,черный", "минимализм,кэжуал", "работа,повседневное", "круглый год"),
]
M_OUTER = [
    I("Тренч классический", "NORDVELL", 12900, "бежевый,графитовый", "классика,деловой", "повседневное,работа", "демисезон"),
    I("Пуховик лёгкий", "ARCTICA", 9900, "черный,синий", "спорт", "повседневное", "зима"),
    I("Бомбер", "STREETLINE", 7400, "черный,оливковый", "кэжуал,стрит", "повседневное", "демисезон"),
    I("Блейзер", "ATELIER 7", 9900, "синий,серый", "деловой,классика", "работа,событие", "круглый год"),
    I("Анорак", "ARCTICA", 6900, "хаки,голубой", "спорт", "повседневное", "демисезон"),
]
F_OUTER = [
    I("Тренч", "NORDVELL", 11900, "бежевый,черный", "классика,деловой", "повседневное,работа", "демисезон"),
    I("Жакет твидовый", "ATELIER 7", 9900, "розовый,серый", "классика,преппи", "работа,событие", "демисезон"),
    I("Куртка джинсовая", "DENIM LAB", 5900, "голубой,синий", "кэжуал,стрит", "повседневное", "демисезон"),
    I("Пальто оверсайз", "KAVRA", 14900, "графитовый,кремовый", "минимализм,классика", "повседневное", "зима"),
]
M_SHOES = [
    I("Оксфорды кожаные", "MARCONI", 8900, "коричневый,черный", "классика,деловой", "работа", "круглый год"),
    I("Кеды белые", "STREETLINE", 3990, "белый", "кэжуал,стрит", "повседневное", "круглый год"),
    I("Лоферы", "MARCONI", 6900, "коричневый", "преппи,кэжуал", "работа", "круглый год"),
    I("Кроссовки беговые", "VELOCITY", 7490, "серый,синий", "спорт", "повседневное", "круглый год"),
    I("Сандалии кожаные", "SOLMAR", 3490, "коричневый", "бохо", "отпуск", "лето"),
    I("Ботинки челси", "MARCONI", 9900, "черный,коричневый", "кэжуал,классика", "повседневное,работа", "демисезон"),
]
F_SHOES = [
    I("Лодочки", "MARCONI", 7900, "черный,розовый", "классика,вечерний", "работа,событие", "круглый год"),
    I("Кеды белые", "STREETLINE", 3590, "белый", "кэжуал", "повседневное", "круглый год"),
    I("Ботильоны на каблуке", "MARCONI", 8900, "черный,бордовый", "кэжуал,вечерний", "событие", "демисезон"),
    I("Сандалии плоские", "SOLMAR", 3290, "коричневый,кремовый", "бохо,романтика", "отпуск", "лето"),
    I("Лоферы", "MARCONI", 6400, "коричневый,черный", "преппи,деловой", "работа", "круглый год"),
]
M_ACC = [
    I("Ремень кожаный", "MARCONI", 2490, "коричневый,черный", "классика,кэжуал", "работа", "круглый год"),
    I("Сумка почтальон", "URBANO", 5900, "черный", "кэжуал", "повседневное", "круглый год"),
    I("Шарф шерстяной", "KAVRA", 2990, "серый,бордовый", "кэжуал,классика", "повседневное", "зима"),
    I("Очки солнцезащитные", "SOLMAR", 3990, "черный", "кэжуал,бохо", "отпуск", "лето"),
    I("Кепка бейсболка", "STREETLINE", 1990, "черный,хаки", "спорт,стрит", "повседневное", "круглый год"),
    I("Галстук шёлковый", "ATELIER 7", 3490, "бордовый,синий", "деловой,классика", "работа", "круглый год"),
]
F_ACC = [
    I("Платок шёлковый", "LUMI", 2990, "розовый,кремовый", "классика,романтика", "работа,событие", "круглый год"),
    I("Сумка-тоут", "URBANO", 6900, "бежевый,черный", "кэжуал,деловой", "работа", "круглый год"),
    I("Ремень кожаный", "MARCONI", 2290, "коричневый,черный", "кэжуал", "повседневное", "круглый год"),
    I("Шарф кашемировый", "KAVRA", 3990, "серый,голубой", "минимализм,кэжуал", "повседневное", "зима"),
    I("Очки солнцезащитные", "SOLMAR", 3990, "черный", "кэжуал,бохо", "отпуск", "лето"),
    I("Брошь", "LUMI", 1990, "золотистый", "вечерний,классика", "событие", "круглый год"),
]

TOP_SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
BOT_SIZES = [str(s) for s in range(40, 55, 2)]
SHOE_SIZES = [str(s) for s in range(35, 47)]
ACC_SIZES = ["OS"]
CUT_BY_CAT = {"верх": "прямой", "низ": "прямой", "платье": "приталенный",
              "верхняя одежда": "свободный", "обувь": "-", "аксессуар": "-"}


def load_demo(conn):
    rng = random.Random(42)
    conn.execute("DELETE FROM archetypes")
    conn.execute("DELETE FROM products")
    for aid, name, g, af, at, hf, ht, body, ct, styles, sil in ARCH_SPECS:
        rec, av = CT_COLORS.get(ct, (["черный", "белый", "серый", "синий"], []))
        who = {"женский": "женщин", "мужской": "мужчин"}.get(g, "всех")
        desc = f"{name} для {who} {af}-{at} лет: {styles}. {sil}. Телосложение: {body or 'любое'}. Цветотип: {ct or 'любой'}."
        conn.execute(
            "INSERT OR REPLACE INTO archetypes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, name, desc, g, af, at, hf, ht, body, ct, styles, ", ".join(rec), ", ".join(av)))
    skus = {}

    def add(gender, cat, items, sizes, comp_src=None):
        letter = "M" if gender == "мужской" else "F"
        code = {"верх": "TOP", "низ": "BOT", "платье": "DRS", "верхняя одежда": "OUT",
                "обувь": "SHO", "аксессуар": "ACC"}[cat]
        out = []
        for i, (n, b, p, c, st, oc, se) in enumerate(items, start=1):
            sku = f"{letter}-{code}-{i:02d}"
            comp = comp_src[(i - 1) % len(comp_src)] if comp_src else ""
            loc = f"Секция {rng.randint(1, 6)} · Стеллаж {chr(65 + rng.randint(0, 3))}{rng.randint(1, 6)}"
            conn.execute(
                "INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sku, n, b, cat, gender, json.dumps(sizes), c, p, se, st, oc,
                 CUT_BY_CAT[cat], loc, rng.randint(2, 14),
                 round(rng.uniform(0.25, 0.55), 2), f"products/{sku}.jpg",
                 json.dumps([comp]) if comp else "[]"))
            make_placeholder(MEDIA / "products" / f"{sku}.jpg", sku, n, cat)
            out.append(sku)
        skus[(gender, cat)] = out

    add("женский", "верх", F_TOPS, TOP_SIZES)
    add("мужской", "верх", M_TOPS, TOP_SIZES)
    add("женский", "низ", F_BOTTOMS, BOT_SIZES)
    add("мужской", "низ", M_BOTTOMS, BOT_SIZES)
    add("женский", "платье", F_DRESSES, TOP_SIZES)
    add("женский", "верхняя одежда", F_OUTER, TOP_SIZES)
    add("мужской", "верхняя одежда", M_OUTER, TOP_SIZES)
    add("женский", "обувь", F_SHOES, SHOE_SIZES, comp_src=skus[("женский", "низ")])
    add("мужской", "обувь", M_SHOES, SHOE_SIZES, comp_src=skus[("мужской", "низ")])
    add("женский", "аксессуар", F_ACC, ACC_SIZES, comp_src=skus[("женский", "верх")])
    add("мужской", "аксессуар", M_ACC, ACC_SIZES, comp_src=skus[("мужской", "верх")])
    conn.commit()
    return len(ARCH_SPECS), 60


# -------------------------------------------------------------- подбор ----
GENDERS = ("женский", "мужской")
BODIES = ("астеническое", "нормостеническое", "спортивное", "крупное")
OCC = ("работа", "повседневное", "событие", "отпуск")
SEASONS = ("весна", "лето", "осень", "зима", "демисезон")
FITS = ("свободная", "обычная", "облегающая")
STYLE_LIST = ("классика", "кэжуал", "деловой", "спорт", "романтика", "бохо",
              "минимализм", "вечерний", "стрит", "преппи")
COLOR_LIST = ("черный", "белый", "серый", "графитовый", "бежевый", "кремовый", "синий",
              "голубой", "зеленый", "оливковый", "красный", "бордовый", "розовый",
              "фиолетовый", "желтый", "коричневый")


def parse_q(gender, age, height, size_top, size_bottom, size_shoes, body_type,
            occasion, season, budget_from, budget_to, needs, styles,
            favorite_colors, avoid_colors, fit, restrictions):
    try:
        age = int(age)
        height = int(height)
    except Exception:
        raise UserError("Возраст и рост — целые числа.")
    if gender not in GENDERS:
        raise UserError("Выберите пол.")
    if not 12 <= age <= 99:
        raise UserError("Возраст должен быть от 12 до 99.")
    if not 120 <= height <= 220:
        raise UserError("Рост должен быть 120–220 см.")
    if body_type not in BODIES:
        raise UserError("Выберите телосложение из списка.")
    if occasion not in OCC:
        raise UserError("Выберите повод из списка.")
    if season not in SEASONS:
        raise UserError("Выберите сезон из списка.")
    if fit not in FITS:
        raise UserError("Выберите посадку из списка.")
    bfrom, bto = float(budget_from or 0), float(budget_to or 0)
    if bto <= 0:
        raise UserError("Укажите бюджет «до».")
    if bto < bfrom:
        raise UserError("Бюджет «до» должен быть не меньше «от».")
    return {
        "gender": gender, "age": age, "height": height,
        "size_top": str(size_top).strip().upper(), "size_bottom": str(size_bottom).strip(),
        "size_shoes": str(size_shoes).strip(), "body": body_type,
        "occasion": occasion, "season": season, "budget_from": bfrom, "budget_to": bto,
        "needs": needs, "styles": tags(styles), "fav_colors": tags(favorite_colors),
        "avoid_colors": tags(avoid_colors), "fit": fit, "restrictions": restrictions,
    }


def match_archetypes(conn, q, photo):
    rows = conn.execute("SELECT * FROM archetypes").fetchall()
    if not rows:
        raise UserError("База типажей пуста. Зайдите в /admin и нажмите «Демо-данные».")
    pool = [a for a in rows if a["gender"] in (q["gender"], "унисекс")] or list(rows)
    W = {"age": .14, "height": .08, "body": .22, "styles": .30, "colors": .16, "ct": .10}
    scored = []
    for a in pool:
        sc, reasons = 0.0, []
        if a["age_from"] <= q["age"] <= a["age_to"]:
            sc += W["age"]; reasons.append(f"возраст {q['age']} в диапазоне {a['age_from']}-{a['age_to']}")
        elif a["age_from"] - 5 <= q["age"] <= a["age_to"] + 5:
            sc += W["age"] / 2
        if a["height_from"] <= q["height"] <= a["height_to"]:
            sc += W["height"]; reasons.append("рост подходит типажу")
        elif a["height_from"] - 7 <= q["height"] <= a["height_to"] + 7:
            sc += W["height"] / 2
        body = (photo or {}).get("телосложение") or q["body"]
        if not a["body"]:
            sc += W["body"] * .6
        elif norm(body) == norm(a["body"]):
            sc += W["body"]; reasons.append(f"телосложение «{a['body']}» совпадает")
        else:
            sc += W["body"] * .3
        ast = set(tags(a["styles"]))
        ust = set(q["styles"]) | ({(photo or {}).get("стиль", "").lower()} - {""})
        if ust:
            ov = ast & ust
            sc += W["styles"] * (len(ov) / len(ust)) if ov else W["styles"] * .05
            if ov:
                reasons.append("стили совпадают: " + ", ".join(sorted(ov)))
        else:
            sc += W["styles"] * .6
        rec = set(tags(a["rec_colors"]))
        fav = set(q["fav_colors"])
        avoid = set(q["avoid_colors"]) | set(tags(a["avoid_colors"]))
        if fav & rec:
            sc += W["colors"]; reasons.append("любимые цвета входят в рекомендованные")
        elif rec & avoid:
            sc += W["colors"] * .1
        else:
            sc += W["colors"] * .6
        ct = (photo or {}).get("цветотип", "")
        if not a["color_type"]:
            sc += W["ct"] * .6
        elif ct and norm(ct) == norm(a["color_type"]):
            sc += W["ct"]; reasons.append(f"цветотип «{a['color_type']}» совпал (по фото)")
        else:
            sc += W["ct"] * .4
        scored.append((round(100 * min(sc, 1.0)), a, reasons))
    scored.sort(key=lambda t: -t[0])
    top = scored[:3]
    best = top[0]
    reason = "; ".join(best[2]) or "Общие параметры совпадают."
    ai_used = False
    if gc_available() and len(top) > 1:
        try:
            cand = "\n".join(f"- id={a['aid']}: {a['name']}; стили: {a['styles']}; "
                             f"цвета: {a['rec_colors']}" for _, a, _ in top)
            prof = (f"пол {q['gender']}, возраст {q['age']}, рост {q['height']}, "
                    f"телосложение {q['body']}, стили {', '.join(q['styles']) or '—'}, "
                    f"повод {q['occasion']}")
            data = gc_json([
                {"role": "system", "content": "Ты стилист офлайн-магазина. Отвечай строго JSON."},
                {"role": "user", "content": (
                    f"Профиль клиента: {prof}\nКандидаты типажей:\n{cand}\n"
                    "Выбери наиболее подходящий. Верни ТОЛЬКО JSON: "
                    '{"archetype_id": "<id из кандидатов>", "percent": <0-100>, '
                    '"reason": "1-2 предложения простыми словами, почему подходит"}')},
            ], validate=_v_final)
            if data["archetype_id"] in {a["aid"] for _, a, _ in top}:
                best = next(t for t in top if t[1]["aid"] == data["archetype_id"])
                reason = str(data["reason"])[:300]
                try:
                    best = (max(1, min(99, int(data.get("percent", best[0])))), best[1], best[2])
                except Exception:
                    pass
                ai_used = True
        except GCError as e:
            log.warning("Финальный выбор типажа: %s", e)
    a = best[1]
    return {"aid": a["aid"], "name": a["name"], "percent": best[0], "reason": reason,
            "styles": tags(a["styles"]), "rec_colors": tags(a["rec_colors"]),
            "avoid_colors": tags(a["avoid_colors"]), "ai_used": ai_used}


def _v_final(d):
    if not isinstance(d, dict) or not d.get("archetype_id") or "reason" not in d:
        raise ValueError("bad final json")


SIZE_FIELD = {"верх": "size_top", "низ": "size_bottom", "обувь": "size_shoes",
              "верхняя одежда": "size_top", "платье": "size_top"}
CATS = ("верх", "низ", "верхняя одежда", "платье", "обувь", "аксессуар")


def item_score(p, q, arch):
    ast = set(arch["styles"]) | set(q["styles"])
    pst = set(tags(p["styles"]))
    style = len(ast & pst) / max(1, len(pst)) if pst else 0.4
    cols = set(tags(p["colors"]))
    rec = set(arch["rec_colors"]) | set(q["fav_colors"])
    color = 1.0 if cols & rec else 0.4
    if cols & set(q["avoid_colors"]) or cols & set(arch["avoid_colors"]):
        color -= 0.5
    occ = 1.0 if q["occasion"] in tags(p["occasions"]) else (
        0.5 if not tags(p["occasions"]) else 0.15)
    season_ok = (q["season"] in tags(p["season"]) or "круглый год" in tags(p["season"])
                 or "демисезон" in tags(p["season"]))
    s = 30 * max(color, 0) + 30 * style + 20 * occ + 10 * (1.0 if season_ok else 0.4)
    if p["margin"]:
        s += 10 * min(1.0, p["margin"] / 0.6)
    return s


def build_outfit(conn, q, arch, notes):
    cand = {c: [] for c in CATS}
    for p in conn.execute("SELECT * FROM products WHERE stock>0 AND price<=?",
                          (q["budget_to"],)).fetchall():
        if p["gender"] not in (q["gender"], "унисекс"):
            continue
        cat = p["category"]
        field = SIZE_FIELD.get(cat)
        if field:
            ok, chosen, exact = has_size(p, q[field], cat)
            if not ok:
                continue
        else:
            chosen, exact = "OS", True
        cand[cat].append({"p": p, "size": chosen, "exact": exact,
                          "score": item_score(p, q, arch), "colors": set(tags(p["colors"]))})
    for lst in cand.values():
        lst.sort(key=lambda x: -x["score"])

    tops, bottoms = cand["верх"][:3], cand["низ"][:3]
    dresses, shoes = cand["платье"][:3], cand["обувь"][:3]
    outer, accs = cand["верхняя одежда"][:2], cand["аксессуар"][:3]

    def harmony(items):
        return all(palette_ok(items[i]["colors"], items[i + 1]["colors"])
                   for i in range(len(items) - 1))

    combos = []
    for t in tops:
        for b in bottoms:
            for s in shoes:
                base = [t, b, s]
                combos.append(base)
                extras = [e for e in outer + accs if palette_ok(e["colors"], t["colors"])]
                if extras:
                    combos.append(base + [extras[0]])
    for d in dresses:
        for s in shoes:
            combos.append([d, s])
            extras = [e for e in outer + accs if palette_ok(e["colors"], d["colors"])]
            if extras:
                combos.append([d, s, extras[0]])
    if not combos:
        raise UserError("Подходящих товаров нет: проверьте пол/размер или загрузите "
                        "демо-данные в /admin.")

    def combo_score(items):
        total = sum(x["p"]["price"] for x in items)
        avg = sum(x["score"] for x in items) / len(items)
        bs = 1.0 if q["budget_from"] <= total <= q["budget_to"] else (
            0.5 if total <= q["budget_to"] * 1.15 else 0.0)
        cnt = {3: 0.7, 4: 1.0, 5: 1.0}.get(len(items), 0.4)
        return 0.45 * avg + 25 * bs + 15 * (1.0 if harmony(items) else 0.5) + 10 * cnt, total

    ranked = sorted(combos, key=lambda c: -combo_score(list(c))[0])
    shortlist = ranked[:5]
    pool = {x["p"]["sku"]: x for lst in cand.values() for x in lst}

    def rule_reason(items):
        st = sorted({s for x in items for s in tags(x["p"]["styles"])}
                    & (set(arch["styles"]) | set(q["styles"])))
        total = round(sum(x["p"]["price"] for x in items))
        return (f"Комплект по правилам: стили «{', '.join(st[:3]) or 'нейтральная база'}» "
                f"совпадают с типажом «{arch['name']}», цвета сочетаются с рекомендованными, "
                f"сумма {total} ₽ при бюджете {q['budget_from']:.0f}-{q['budget_to']:.0f} ₽.")

    chosen, reason, ai_used = ranked[0], rule_reason(ranked[0]), False
    if gc_available():
        try:
            sl = [{"sku_list": [x["p"]["sku"] for x in combo],
                   "позиции": [{"sku": x["p"]["sku"], "название": x["p"]["name"],
                                "категория": x["p"]["category"], "цвета": x["p"]["colors"],
                                "стили": x["p"]["styles"], "цена": x["p"]["price"],
                                "размер": x["size"]} for x in combo],
                   "сумма": round(sum(x["p"]["price"] for x in combo))} for combo in shortlist]
            data = gc_json([
                {"role": "system", "content": "Ты стилист офлайн-магазина. Отвечай строго JSON."},
                {"role": "user", "content": (
                    f"Профиль: пол {q['gender']}, возраст {q['age']}, размер верха {q['size_top']}, "
                    f"низа {q['size_bottom']}, обуви {q['size_shoes']}, повод {q['occasion']}, "
                    f"бюджет {q['budget_from']:.0f}-{q['budget_to']:.0f} ₽. "
                    f"Типаж «{arch['name']}», стили: {', '.join(arch['styles'])}, цвета: "
                    f"{', '.join(arch['rec_colors'])}. Шортлист (только эти SKU): "
                    + json.dumps(sl, ensure_ascii=False)
                    + ' Выбери ЛУЧШИЙ комплект (3-5 позиций; обязательны верх+низ или платье и обувь). '
                      'Верни ТОЛЬКО JSON: {"sku_list": ["SKU",...], "reason": "1-2 предложения, почему образ подходит"}')},
            ], validate=_v_pick)
            items, skipped = _choice([str(s) for s in data["sku_list"]], pool, q["budget_to"])
            if skipped:
                notes.append("SKU от ИИ отброшены проверкой: " + ", ".join(skipped))
            chosen, reason, ai_used = items, str(data.get("reason", ""))[:300], True
        except (GCError, ValueError) as e:
            notes.append("ИИ-подбор образа недоступен, применены правила: " + str(e)[:140])
            chosen, reason, ai_used = ranked[0], rule_reason(ranked[0]), False

    score, total = combo_score(chosen)
    items_out = [{
        "sku": x["p"]["sku"], "name": x["p"]["name"], "brand": x["p"]["brand"],
        "category": x["p"]["category"], "size": x["size"], "exact": x["exact"],
        "price": x["p"]["price"], "location": x["p"]["location"] or "—",
        "colors": x["p"]["colors"], "photo": x["p"]["photo"] or "",
    } for x in chosen]
    return {"items": items_out, "total": total,
            "match": int(max(55, min(99, round(50 + score)))),
            "reason": reason, "ai_used": ai_used}


def _v_pick(d):
    if not isinstance(d, dict) or not isinstance(d.get("sku_list"), list) or not d["sku_list"]:
        raise ValueError("bad pick json")


def _choice(skus, pool, budget_to):
    items, skipped, seen = [], [], set()
    for s in skus[:5]:
        s = s.strip()
        c = pool.get(s)
        if c is None or s in seen:
            skipped.append(s)
            continue
        seen.add(s)
        items.append(c)
    cats = {c["p"]["category"] for c in items}
    ok = ("обувь" in cats) and ("платье" in cats or {"верх", "низ"} <= cats)
    if not ok or not 3 <= len(items) <= 5:
        raise ValueError("состав образа не подходит")
    if sum(c["p"]["price"] for c in items) > budget_to * 1.2:
        raise ValueError("превышен бюджет")
    return items, skipped


def upsell_items(conn, q, arch, outfit):
    cap = max(q["budget_to"] * 0.30, 500)
    in_outfit = {i["sku"] for i in outfit["items"]}
    out_colors = {norm(c) for i in outfit["items"] for c in tags(i["colors"])}
    comp = set()
    for i in outfit["items"]:
        try:
            comp |= set(json.loads(conn.execute(
                "SELECT comp FROM products WHERE sku=?", (i["sku"],)).fetchone()[0] or "[]"))
        except Exception:
            pass
    pool = []
    for p in conn.execute("SELECT * FROM products WHERE stock>0").fetchall():
        if p["sku"] in in_outfit or p["category"] == "платье":
            continue
        if p["gender"] not in (q["gender"], "унисекс") or p["price"] > cap:
            continue
        field = SIZE_FIELD.get(p["category"])
        if field and not has_size(p, q[field], p["category"])[0]:
            continue
        s, why = 5.0, []
        if p["sku"] in comp:
            s += 40; why.append("рекомендован как дополнение к этому образу")
        cols = set(tags(p["colors"]))
        if {norm(c) for c in cols} & out_colors:
            s += 20; why.append("сочетается с цветами образа")
        ov = set(tags(p["styles"])) & (set(arch["styles"]) | set(q["styles"]))
        if ov:
            s += 12 * min(3, len(ov)); why.append("совпадает по стилю («" + "/".join(sorted(ov)) + "»)")
        if q["occasion"] in tags(p["occasions"]):
            s += 8; why.append(f"подходит для повода «{q['occasion']}»")
        if p["margin"]:
            s += 8 * min(1.0, p["margin"] / 0.6)
        if {norm(c) for c in cols} & set(arch["avoid_colors"]):
            s -= 25
        pool.append((s, p, why))
    pool.sort(key=lambda t: -t[0])
    picked, per_cat = [], {}
    for _s, p, why in pool:
        capc = 1 if p["category"] in ("аксессуар", "обувь") else 2
        if per_cat.get(p["category"], 0) >= capc:
            continue
        per_cat[p["category"]] = per_cat.get(p["category"], 0) + 1
        picked.append({"sku": p["sku"], "name": p["name"], "price": p["price"],
                       "photo": p["photo"] or "",
                       "reason": "; ".join(why) or "нейтральный вариант, сочетается с образом"})
        if len(picked) == 3:
            break
    return picked


def pipeline(sid, q, photo_path):
    conn = db()
    notes = []
    photo_info = None
    if photo_path:
        if gc_available():
            try:
                photo_info = gc_analyze_photo(photo_path)
                if photo_info.get("ошибка"):
                    notes.append("Фото не использовано: " + str(photo_info["ошибка"])[:120])
                    photo_info = None
                else:
                    notes.append("Фото учтено: телосложение, цветотип и стиль. Лицо не анализируется.")
            except GCError as e:
                notes.append("Фото-анализ недоступен: " + str(e)[:140])
        else:
            notes.append("ИИ не настроен — фото не анализировалось, подбор по правилам.")
    arch = match_archetypes(conn, q, photo_info)
    outfit = build_outfit(conn, q, arch, notes)
    ups = upsell_items(conn, q, arch, outfit)
    ups_total = sum(u["price"] for u in ups)
    if not conn.execute("SELECT 1 FROM events WHERE sid=? AND event='questionnaire_started'",
                        (sid,)).fetchone():
        log_event(conn, sid, "questionnaire_started")
    log_event(conn, sid, "questionnaire_completed",
              {"ai_used": bool(arch.get("ai_used") or outfit.get("ai_used")),
               "occasion": q["occasion"]})
    log_event(conn, sid, "outfit_shown", {"arch": arch["name"], "total": outfit["total"],
                                          "items": [i["sku"] for i in outfit["items"]]})
    result = {"archetype": arch, "outfit": outfit, "upsells": ups, "notes": notes,
              "ai_used": bool(arch.get("ai_used") or outfit.get("ai_used")),
              "upsell_total": ups_total, "total_with_upsells": outfit["total"] + ups_total}
    conn.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?)",
                 (sid, time.time(), "completed", json.dumps(result, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return result


def render_lookboard(items, title, subtitle, out_path):
    thumb, pad, cap_h, header = 320, 22, 52, 120
    cols = 2 if len(items) <= 4 else 3
    rows = -(-max(1, len(items)) // cols)
    W = cols * (thumb + pad) + pad
    H = header + rows * (thumb + cap_h + pad) + 70
    img = Image.new("RGB", (W, H), "#f4f6fb")
    d = ImageDraw.Draw(img)
    d.text((pad, 36), title[:48], fill="#101528", font=_font(38))
    if subtitle:
        d.text((pad, 88), subtitle[:64], fill="#5a6473", font=_font(22))
    for i, it in enumerate(items):
        x = pad + (i % cols) * (thumb + pad)
        y = header + (i // cols) * (thumb + cap_h + pad)
        ph = Image.new("RGB", (thumb, thumb), "#e6ebf4")
        path = MEDIA / it["photo"] if it.get("photo") else None
        if path and path.exists():
            try:
                src = Image.open(path).convert("RGB")
                src.thumbnail((thumb, thumb))
                ph.paste(src, ((thumb - src.width) // 2, (thumb - src.height) // 2))
            except Exception:
                pass
        img.paste(ph, (x, y))
        d.text((x + 2, y + thumb + 8), f"{it['sku']} · {it['category']} · {it['price']:.0f} ₽",
               fill="#2a3244", font=_font(20))
    d.text((pad, H - 44), "AURATECH · лукборд из каталога магазина (точные фото товаров)",
           fill="#6b7280", font=_font(20))
    img.save(out_path, "PNG")


# ------------------------------------------------------------- шаблоны ----
CSS = """
:root{--a:#2f6fed;--bg:#f4f6fb;--ink:#1c2333;--muted:#6b7280;--ok:#1a7f4e;--danger:#c0392b;--bd:#c9d2e3}
*{box-sizing:border-box}body{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--ink);font-size:18px}
h1,h2,h3{margin:6px 0 10px}
.top{display:flex;gap:14px;align-items:center;padding:14px 20px;background:#101528;color:#fff;flex-wrap:wrap}
.brand{font-weight:800;letter-spacing:2px;font-size:20px}.tag{opacity:.75}
.ai-off{margin-left:auto;background:#7a2c2c;padding:6px 12px;border-radius:8px;font-size:13px}
main{max-width:1000px;margin:0 auto;padding:16px}
.muted{color:var(--muted)}.small{font-size:13px}
.error{background:#fdecea;color:var(--danger);border:1px solid #f5c6c0;padding:10px 14px;border-radius:10px;margin:10px 0}
.msg{background:#e8f7ee;color:var(--ok);border:1px solid #bfe6cf;padding:10px 14px;border-radius:10px;margin:10px 0}
.progress{height:10px;background:#dde3ee;border-radius:6px;overflow:hidden;margin:12px 0 4px}
#bar{height:100%;width:33%;background:var(--a);transition:width .3s}
fieldset{border:0;padding:10px 0 0;margin:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
label.f{display:flex;flex-direction:column;gap:6px;font-size:15px}
label.f input,select,textarea{font-size:17px;padding:11px;border:1px solid var(--bd);border-radius:10px;background:#fff}
.blk{margin:10px 0}.lbl{color:var(--muted);margin-bottom:6px;font-size:15px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{border:1.5px solid var(--bd);background:#fff;border-radius:999px;padding:8px 14px;cursor:pointer;font-size:15px}
.chip.on{background:var(--a);border-color:var(--a);color:#fff}
.pblock{margin-top:12px;padding:12px;border:1px dashed var(--bd);border-radius:12px}
.consent{display:flex;gap:10px;font-size:13px;color:var(--muted);margin-top:8px;align-items:flex-start}
#pprev{max-height:160px;border-radius:10px;margin-top:8px;display:block}
.nav{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap;align-items:center}
.btn{font-size:17px;padding:12px 20px;border-radius:12px;border:0;background:var(--a);color:#fff;cursor:pointer;min-height:48px}
.btn.ghost{background:#fff;color:var(--a);border:1.5px solid var(--a)}
.btn.danger{background:var(--danger)}
.btn.small{font-size:14px;padding:7px 12px;min-height:38px}
.btn:disabled{opacity:.6;cursor:default}
.card{background:#fff;border-radius:16px;padding:16px 18px;margin:12px 0;box-shadow:0 2px 10px rgba(15,25,50,.06)}
.loader{display:flex;gap:12px;align-items:center;padding:24px;font-size:19px}
.spinner{width:32px;height:32px;border:4px solid #dfe7f5;border-top-color:var(--a);border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.arc{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.badge{min-width:80px;height:80px;border-radius:50%;background:var(--ok);color:#fff;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;flex-shrink:0}
.note{font-size:13px;color:var(--muted);margin:2px 0}
.aichip{display:inline-block;background:#fff4d6;color:#8a6d1a;padding:4px 10px;border-radius:8px;font-size:13px}
.modes{display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.mode-btn{border:1.5px solid var(--bd);background:#fff;border-radius:10px;padding:9px 14px;cursor:pointer;font-size:15px}
.mode-btn.active{background:var(--a);border-color:var(--a);color:#fff}
.iwrap{text-align:center}.iwrap img{max-width:100%;border-radius:12px}
.disc{color:#8a6d1a;background:#fff8e6;padding:8px 12px;border-radius:8px;font-size:14px}
.tbl{width:100%;border-collapse:collapse;margin-top:8px}
.tbl th,.tbl td{border-bottom:1px solid #e4e9f2;padding:9px 7px;text-align:left;font-size:16px}
.tbl th{color:var(--muted);font-weight:600}
.loc{filter:blur(7px);user-select:none}.loc.rev{filter:none}
.tot{margin:10px 0;font-size:17px}
.ugrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px}
.up{display:flex;gap:10px;border:1px solid #e4e9f2;border-radius:12px;padding:10px}
.up img{width:100px;height:120px;object-fit:cover;border-radius:8px}
.why{font-size:13px;color:var(--muted)}
.actions{display:flex;gap:10px;margin:14px 0;flex-wrap:wrap}
.foot{text-align:center;padding:16px;font-size:13px}
table.rep{width:100%;border-collapse:collapse}
table.rep th,table.rep td{border-bottom:1px solid #e4e9f2;padding:6px 8px;font-size:14px;text-align:left}
.mgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.mg .num{font-size:30px;font-weight:800;color:var(--a)}
@media print{.top,.actions,.modes,.foot,.no-print,button{display:none!important}.loc{filter:none!important}.card{box-shadow:none;border:1px solid #ddd}}
"""

JS = """
const $=(s,r)=>(r||document).querySelector(s);
const $$=(s,r)=>Array.from((r||document).querySelectorAll(s));
const form=$('#wizard');
const steps=$$('fieldset[data-step]');
const labels=['Параметры','Цель','Стиль'];
let cur=0;
function show(i){cur=i;steps.forEach((f,k)=>f.hidden=k!==i);
 $('#bar').style.width=((i+1)*100/3)+'%';
 $('#steplbl').textContent='Шаг '+(i+1)+' из 3 — '+labels[i];
 $('#prev').hidden=i===0;$('#next').hidden=i===2;$('#go').hidden=i!==2;
 window.scrollTo({top:0,behavior:'smooth'});}
 $('#next').onclick=()=>{let ok=true;
 $$('fieldset[data-step="'+cur+'"] input, fieldset[data-step="'+cur+'"] select, fieldset[data-step="'+cur+'"] textarea').forEach(el=>{if(!el.checkValidity()){ok=false;el.reportValidity();}});
 if(ok)show(Math.min(cur+1,2));};
 $('#prev').onclick=()=>show(Math.max(cur-1,0));
$$('.chips').forEach(g=>{g.addEventListener('click',e=>{const b=e.target.closest('.chip');if(!b)return;
 b.classList.toggle('on');
 const inp=document.querySelector('input[name="'+g.dataset.input+'"]');
 inp.value=$$('.chip.on',g).map(x=>x.dataset.value).join(',');});});
 $('#photo').addEventListener('change',()=>{const f=$('#photo').files[0];const p=$('#pprev');
 if(f){p.src=URL.createObjectURL(f);p.hidden=false;}else p.hidden=true;});
let started=false;
form.addEventListener('change',()=>{if(started)return;started=true;
 const fd=new FormData();fd.append('session_id',form.session_id.value);
 fd.append('event','questionnaire_started');fetch('/api/event',{method:'POST',body:fd});});
const err=$('#err'),loader=$('#loader');
form.addEventListener('submit',e=>{e.preventDefault();err.hidden=true;
 if($('#photo').files[0]&&!form.photo_consent.checked){err.textContent='Для фото нужно согласие на обработку (152-ФЗ).';err.hidden=false;return;}
 loader.hidden=false;form.hidden=true;
 fetch('/api/recommend',{method:'POST',body:new FormData(form)})
 .then(r=>r.text().then(t=>({ok:r.ok,t})))
 .then(({ok,t})=>{if(!ok){let d='Ошибка подбора';try{d=JSON.parse(t).detail||d}catch(e){}throw new Error(d);}
  document.getElementById('result').innerHTML=t;window.scrollTo({top:0});})
 .catch(ex=>{err.textContent=ex.message;err.hidden=false;form.hidden=false;})
 .finally(()=>{loader.hidden=true;});
});
window.AU={
 mode(sid,mode,btn){$$('.mode-btn').forEach(b=>b.classList.remove('active'));btn.classList.add('active');
  $('#illustration-note').hidden=mode!=='ai';$('#img-msg').hidden=true;$('#img-spinner').hidden=false;
  fetch('/api/render/'+sid+'/'+mode).then(r=>{if(!r.ok)return r.json().then(j=>{throw new Error(j.detail||'Ошибка')});return r.blob();})
  .then(b=>{$('#lookboard-img').src=URL.createObjectURL(b);})
  .catch(e=>{$('#img-msg').textContent=e.message;$('#img-msg').hidden=false;})
  .finally(()=>{$('#img-spinner').hidden=true;});},
 where(sid,btn){const fd=new FormData();fd.append('session_id',sid);fd.append('event','where_clicked');
  fetch('/api/event',{method:'POST',body:fd});
  $$('.loc').forEach(td=>td.classList.add('rev'));btn.disabled=true;btn.textContent='Показано ✓';},
 upsell(sid,sku,price,btn){const fd=new FormData();fd.append('session_id',sid);fd.append('sku',sku);
  fetch('/api/upsell',{method:'POST',body:fd}).then(r=>{if(r.ok){btn.disabled=true;btn.textContent='✓ добавлено';
   const box=$('#result-box');const s=parseFloat(box.dataset.upsell||'0')+price;const o=parseFloat(box.dataset.outfit||'0');
   box.dataset.upsell=s;$('#upsell-sum').textContent=Math.round(s)+' ₽';$('#grand-total').textContent=Math.round(o+s)+' ₽';}});},
 del(sid){fetch('/api/delete/'+sid,{method:'POST'}).then(()=>{
  document.getElementById('result').innerHTML="<div class='card'>Мои данные удалены.</div>";
  setTimeout(()=>location.reload(),1200);});},
 restart(sid){fetch('/api/delete/'+sid,{method:'POST'}).catch(()=>{}).finally(()=>location.reload());},
 print(){window.print();}
};
show(0);
"""

INDEX_TEMPLATE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AURATECH — AI-стилист</title><style>__CSS__</style></head><body>
<header class="top"><div class="brand">AURATECH</div>
<div class="tag">AI-стилист офлайн-магазина</div>__AI_BANNER__</header>
<main>
<div id="err" class="error" hidden></div>
<form id="wizard" autocomplete="off">
<input type="hidden" name="session_id" value="__SID__">
<div class="progress"><div id="bar"></div></div>
<div id="steplbl" class="muted"></div>
<fieldset data-step="0">
<h2>Шаг 1. Параметры</h2>
<div class="grid">
 <label class="f">Пол<select name="gender"><option>женский</option><option>мужской</option></select></label>
 <label class="f">Возраст<input type="number" name="age" min="12" max="99" value="30" required></label>
 <label class="f">Рост, см<input type="number" name="height" min="120" max="220" value="170" required></label>
 <label class="f">Размер верха<select name="size_top">__OPT_TOP__</select></label>
 <label class="f">Размер низа<select name="size_bottom">__OPT_BOT__</select></label>
 <label class="f">Размер обуви<select name="size_shoes">__OPT_SHOE__</select></label>
 <label class="f">Телосложение<select name="body_type">__OPT_BODY__</select></label>
</div>
</fieldset>
<fieldset data-step="1" hidden>
<h2>Шаг 2. Цель</h2>
<div class="grid">
 <label class="f">Повод<select name="occasion">__OPT_OCC__</select></label>
 <label class="f">Сезон<select name="season">__OPT_SEASON__</select></label>
 <label class="f">Бюджет от, ₽<input type="number" name="budget_from" min="0" step="500" value="5000" required></label>
 <label class="f">Бюджет до, ₽<input type="number" name="budget_to" min="500" step="500" value="30000" required></label>
 <label class="f">Что нужно докупить (необязательно)<textarea name="needs" rows="2"></textarea></label>
</div>
</fieldset>
<fieldset data-step="2" hidden>
<h2>Шаг 3. Стиль</h2>
<div class="blk"><div class="lbl">Стили</div>
 <div class="chips" data-input="styles">__CHIPS_STYLES__</div>
 <input type="hidden" name="styles"></div>
<div class="blk"><div class="lbl">Любимые цвета</div>
 <div class="chips" data-input="favorite_colors">__CHIPS_FAV__</div>
 <input type="hidden" name="favorite_colors"></div>
<div class="blk"><div class="lbl">Нежелательные цвета</div>
 <div class="chips" data-input="avoid_colors">__CHIPS_AVOID__</div>
 <input type="hidden" name="avoid_colors"></div>
<div class="grid">
 <label class="f">Посадка<select name="fit">__OPT_FIT__</select></label>
 <label class="f">Ограничения<textarea name="restrictions" rows="2"></textarea></label>
</div>
<div class="pblock">
 <label class="f">Фото (необязательно — помогает точнее подобрать цветовую гамму)
  <input type="file" name="photo" id="photo" accept="image/jpeg,image/png,image/webp"></label>
 <img id="pprev" alt="" hidden>
 <label class="consent"><input type="checkbox" name="photo_consent">
  Согласен(-на) на обработку загруженного фото для подбора одежды (152-ФЗ). Фото удаляется
  сразу после подбора или через 15 минут. Лицо не анализируется, личность не распознаётся.</label>
</div>
</fieldset>
<div class="nav">
 <button type="button" id="prev" class="btn ghost" hidden>Назад</button>
 <button type="button" id="next" class="btn">Далее</button>
 <button type="submit" id="go" class="btn" hidden>Подобрать образ</button>
</div>
</form>
<div id="loader" class="loader" hidden><div class="spinner"></div><div>Собираем ваш образ… до минуты</div></div>
<div id="result"></div>
<footer class="foot muted">AURATECH MVP · фото не хранится дольше сессии · лицо не анализируется</footer>
</main>
<script>__JS__</script>
</body></html>"""


def render_index(sid):
    def opts(items):
        return "".join(f"<option>{s}</option>" for s in items)

    def chips(items):
        return "".join(f"<button type='button' class='chip' data-value='{s}'>{s}</button>"
                       for s in items)

    html = INDEX_TEMPLATE
    html = html.replace("__CSS__", CSS).replace("__JS__", JS)
    html = html.replace("__AI_BANNER__", "" if gc_available() else
                        "<div class='ai-off'>ИИ выключен: не задан GIGACHAT_AUTH_KEY — режим правил</div>")
    html = html.replace("__SID__", sid)
    html = html.replace("__OPT_TOP__", opts(TOP_SIZES))
    html = html.replace("__OPT_BOT__", opts(BOT_SIZES))
    html = html.replace("__OPT_SHOE__", opts(SHOE_SIZES))
    html = html.replace("__OPT_BODY__", opts(BODIES))
    html = html.replace("__OPT_OCC__", opts(OCC))
    html = html.replace("__OPT_SEASON__", opts(SEASONS))
    html = html.replace("__OPT_FIT__", opts(FITS))
    html = html.replace("__CHIPS_STYLES__", chips(STYLE_LIST))
    html = html.replace("__CHIPS_FAV__", chips(COLOR_LIST))
    html = html.replace("__CHIPS_AVOID__", chips(COLOR_LIST))
    return html


def result_html(sid, r):
    items = "".join(
        f"<tr><td>{i['sku']}</td><td>{i['name']} <span class='muted'>{i['brand']}</span></td>"
        f"<td>{i['size']}{'*' if not i['exact'] else ''}</td>"
        f"<td>{i['price']:.0f} ₽</td><td class='loc'>{i['location']}</td></tr>"
        for i in r["outfit"]["items"])
    ups = "".join(
        f"<div class='up'><img src='/media/{u['photo']}' alt=''>"
        f"<div><b>{u['name']}</b><div class='muted'>{u['sku']} · {u['price']:.0f} ₽</div>"
        f"<div class='why'>{u['reason']}</div>"
        f"<div class='nav'><button class='btn small no-print' "
        f"onclick=\"AU.upsell('{sid}','{u['sku']}',{u['price']:.0f},this)\">+ в примерку</button></div></div></div>"
        for u in r["upsells"])
    notes = "".join(f"<p class='note'>{n}</p>" for n in r["notes"])
    chip = "" if r["ai_used"] else "<p><span class='aichip'>Подбор без ИИ (по правилам)</span></p>"
    return f"""
<div id="result-box" data-upsell="{r['upsell_total']:.0f}" data-outfit="{r['outfit']['total']:.0f}">
<section class="card arc">
 <div class="badge">{r['archetype']['percent']}%</div>
 <div><h2>Ваш типаж: {r['archetype']['name']}</h2>
 <p>{r['archetype']['reason']}</p>{notes}{chip}</div>
</section>
<section class="card">
 <div class="modes no-print">
  <button class="mode-btn active" onclick="AU.mode('{sid}','lb',this)">Лукборд (точный)</button>
  <button class="mode-btn" onclick="AU.mode('{sid}','ai',this)">Иллюстрация (GigaChat)</button>
 </div>
 <div class="iwrap"><div id="img-spinner" class="spinner" hidden></div>
  <img id="lookboard-img" src="/api/render/{sid}/lb" alt="Образ"></div>
 <p id="illustration-note" class="disc" hidden>Иллюстрация сгенерирована ИИ — это не точная примерка.</p>
 <div id="img-msg" class="error" hidden></div>
</section>
<section class="card">
 <h3>Образ — совпадение {r['outfit']['match']}%</h3>
 <p class="muted">{r['outfit']['reason']}</p>
 <table class="tbl"><thead><tr><th>SKU</th><th>Название</th><th>Размер</th><th>Цена</th>
 <th class="loc">Где взять</th></tr></thead><tbody>{items}</tbody></table>
 <p class="muted small">* — ближайший доступный размер</p>
 <div class="tot">Сумма образа: <b>{r['outfit']['total']:.0f} ₽</b> · с допродажами:
  <b id="grand-total">{r['total_with_upsells']:.0f} ₽</b></div>
 <button class="btn ghost no-print" onclick="AU.where('{sid}',this)">Показать, где взять</button>
</section>
<section class="card">
 <h3>Допродажи — прирост к чеку: <span id="upsell-sum">{r['upsell_total']:.0f} ₽</span></h3>
 <div class="ugrid">{ups}</div>
</section>
<section class="actions no-print">
 <button class="btn" onclick="AU.print()">Распечатать</button>
 <button class="btn ghost" onclick="AU.restart('{sid}')">Заново</button>
 <button class="btn danger" onclick="AU.del('{sid}')">Удалить мои данные</button>
</section>
</div>"""


ADMIN_LOGIN = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AURATECH — вход</title><style>__CSS__</style></head><body>
<main style="display:flex;justify-content:center;padding-top:10vh">
<section class="card" style="width:min(420px,92vw)"><h1>AURATECH · админка</h1>
__ERR__
<form method="post" action="/admin/login">
<label class="f">Пароль<input type="password" name="password" required autofocus></label>
<div class="nav"><button class="btn">Войти</button></div>
</form>
<p class="muted small">Пароль задаётся переменной окружения ADMIN_PASSWORD.</p>
</section></main></body></html>"""

ADMIN_PANEL = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AURATECH — админ-панель</title><style>__CSS__</style></head><body>
<header class="top"><div class="brand">AURATECH</div><div class="tag">админ-панель</div>
<nav style="margin-left:auto;display:flex;gap:12px;align-items:center">
<a href="/" style="color:#dfe7ff">Киоск</a>
<form method="post" action="/admin/logout" style="display:inline">
<button class="btn ghost small">Выход</button></form></nav></header>
<main>
__MSG__
<section class="card"><h2>Статус</h2>
<p>Типажей: <b>__NARCH__</b> · Товаров: <b>__NPROD__</b> · GigaChat: __AI__</p>
<div class="nav">
<form method="post" action="/admin/demo"><button class="btn">Загрузить демо-данные (20 типажей + 60 товаров)</button></form>
<form method="post" action="/admin/reset" onsubmit="return confirm('Удалить все данные?')">
<button class="btn danger">Очистить всё</button></form>
</div>
<p class="muted small">Переменные окружения: ADMIN_PASSWORD, SECRET_KEY, GIGACHAT_AUTH_KEY,
GIGACHAT_SCOPE, GIGACHAT_MODEL, GIGACHAT_VERIFY_SSL. После загрузки демо-данных анкета
на главной странице начнёт выдавать образы.</p>
</section>
__STATS__
</main></body></html>"""


def is_admin(request: Request) -> bool:
    token = hashlib.sha256((SECRET + "admin").encode()).hexdigest()
    return request.cookies.get("auratech_admin", "") == token


def admin_stats(conn):
    evs = conn.execute("SELECT * FROM events ORDER BY ts DESC").fetchall()
    sids = {e["sid"] for e in evs}
    completed = {e["sid"] for e in evs if e["event"] == "questionnaire_completed"}
    shown = {e["sid"] for e in evs if e["event"] == "outfit_shown"}
    where = {e["sid"] for e in evs if e["event"] == "where_clicked"}
    arch, skus, totals, ups = {}, {}, [], 0
    for e in evs:
        try:
            p = json.loads(e["payload"] or "{}")
        except Exception:
            p = {}
        if e["event"] == "outfit_shown":
            arch[p.get("arch", "?")] = arch.get(p.get("arch", "?"), 0) + 1
            if p.get("total"):
                totals.append(float(p["total"]))
            for s in p.get("items") or []:
                skus[s] = skus.get(s, 0) + 1
        elif e["event"] == "upsell_added":
            ups += 1
    pct = lambda n, d: round(100 * n / d) if d else 0  # noqa: E731

    def table(rows, empty):
        if not rows:
            return f"<p class='muted'>{empty}</p>"
        return "<table class='rep'>" + "".join(f"<tr><td>{k}</td><td>{v}</td></tr>"
                                               for k, v in rows) + "</table>"

    recent = ("<table class='rep'><tr><th>событие</th><th>сессия</th></tr>" +
              "".join(f"<tr><td>{e['event']}</td><td>{(e['sid'] or '')[:8]}</td></tr>"
                      for e in evs[:12]) + "</table>") if evs else "<p class='muted'>пока нет событий</p>"
    return f"""
<div class="mgrid">
 <div class="card mg"><div class="num">{len(sids)}</div><div class="muted">сессий</div></div>
 <div class="card mg"><div class="num">{pct(len(completed), len(sids))}%</div><div class="muted">завершили анкету</div></div>
 <div class="card mg"><div class="num">{pct(len(shown), len(sids))}%</div><div class="muted">дошли до образа</div></div>
 <div class="card mg"><div class="num">{pct(len(where), len(sids))}%</div><div class="muted">активация («где взять»)</div></div>
 <div class="card mg"><div class="num">{round(sum(totals) / len(totals)) if totals else 0} ₽</div><div class="muted">средняя сумма образа</div></div>
 <div class="card mg"><div class="num">{ups}</div><div class="muted">допродаж добавлено</div></div>
</div>
<section class="card"><h3>Топ-образы</h3>{table(sorted(arch.items(), key=lambda x: -x[1])[:5], 'пока нет данных')}</section>
<section class="card"><h3>Топ-SKU</h3>{table(sorted(skus.items(), key=lambda x: -x[1])[:8], 'пока нет данных')}</section>
<section class="card"><h3>Последние события</h3>{recent}</section>"""


# --------------------------------------------------------------- роуты ----
app = FastAPI(title="AURATECH — AI-стилист", docs_url=None, redoc_url=None)


@app.get("/healthz")
def healthz():
    return {"ok": True, "ai": gc_available()}


@app.get("/", response_class=HTMLResponse)
def index():
    cleanup_photos()
    return render_index(uuid.uuid4().hex[:16])


@app.post("/api/event")
def api_event(session_id: str = Form(...), event: str = Form(...),
              payload: str = Form("{}")):
    if event not in EVENTS:
        raise HTTPException(status_code=400, detail="Неизвестное событие")
    try:
        data = json.loads(payload or "{}")
    except Exception:
        data = {}
    conn = db()
    log_event(conn, session_id, event, data)
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/recommend")
def recommend(session_id: str = Form(...), gender: str = Form(...),
              age: int = Form(...), height: int = Form(...),
              size_top: str = Form(...), size_bottom: str = Form(...),
              size_shoes: str = Form(...), body_type: str = Form(...),
              occasion: str = Form(...), season: str = Form(...),
              budget_from: float = Form(0), budget_to: float = Form(...),
              needs: str = Form(""), styles: str = Form(""),
              favorite_colors: str = Form(""), avoid_colors: str = Form(""),
              fit: str = Form("обычная"), restrictions: str = Form(""),
              photo: UploadFile | None = File(None), photo_consent: str = Form("")):
    try:
        q = parse_q(gender, age, height, size_top, size_bottom, size_shoes, body_type,
                    occasion, season, budget_from, budget_to, needs, styles,
                    favorite_colors, avoid_colors, fit, restrictions)
    except UserError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    photo_path = None
    if photo is not None and photo.filename:
        if photo_consent != "on":
            return JSONResponse(status_code=400, content={"detail":
                "Для загрузки фото нужно согласие на обработку персональных данных (152-ФЗ). "
                "Отметьте чекбокс или уберите фото."})
        ext = os.path.splitext(photo.filename)[1].lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            return JSONResponse(status_code=400,
                                content={"detail": "Фото должно быть JPG, PNG или WebP."})
        data = photo.file.read()
        if len(data) > 10 * 1024 * 1024:
            return JSONResponse(status_code=400,
                                content={"detail": "Фото больше 10 МБ. Сожмите файл."})
        photo_path = MEDIA / "photos" / f"{session_id}{ext}"
        photo_path.write_bytes(data)

    try:
        result = pipeline(session_id, q, str(photo_path) if photo_path else None)
    except UserError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})
    except Exception:
        log.exception("recommend: внутренняя ошибка")
        return JSONResponse(status_code=500, content={"detail":
            "Внутренняя ошибка подбора. Попробуйте ещё раз."})
    finally:
        if photo_path:
            try:
                photo_path.unlink()
            except OSError:
                pass
    return HTMLResponse(result_html(session_id, result))


@app.get("/api/render/{sid}/{mode}")
def render_image(sid: str, mode: str):
    conn = db()
    row = conn.execute("SELECT result FROM sessions WHERE sid=?", (sid,)).fetchone()
    conn.close()
    if row is None or not row["result"]:
        raise HTTPException(status_code=404, detail="Сессия не найдена или удалена")
    if mode not in ("lb", "ai"):
        raise HTTPException(status_code=400, detail="Неизвестный режим")
    r = json.loads(row["result"])
    out = MEDIA / "gen" / f"{sid}_{mode}.png"
    if not out.exists():
        title = f"Ваш образ · типаж «{r['archetype']['name']}»"
        sub = f"Совпадение {r['archetype']['percent']}% · сумма {r['outfit']['total']:.0f} ₽"
        if mode == "lb":
            render_lookboard(r["outfit"]["items"], title, sub, str(out))
        else:
            if not gc_available():
                return JSONResponse(status_code=502, content={"detail":
                    "Иллюстрация недоступна: не задан GIGACHAT_AUTH_KEY."})
            try:
                parts = [f"{i['category']} ({', '.join(tags(i['colors'])[:2])})"
                         for i in r["outfit"]["items"]]
                prompt = ("Фотореалистичная плоская раскладка одежды (flat lay, вид сверху) "
                          "на светлом нейтральном фоне, стиль каталога магазина одежды: "
                          + "; ".join(parts) + ". Единая цветовая гамма. Строго без людей, "
                          "без лиц, без логотипов и текста.")
                out.write_bytes(gc_image(prompt))
            except GCError as e:
                return JSONResponse(status_code=502, content={"detail": str(e)})
    return FileResponse(out, media_type="image/png")


@app.post("/api/upsell")
def upsell_add(session_id: str = Form(...), sku: str = Form(...)):
    conn = db()
    row = conn.execute("SELECT result FROM sessions WHERE sid=?", (session_id,)).fetchone()
    if row is None or not row["result"]:
        conn.close()
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    found = next((u for u in json.loads(row["result"]).get("upsells", [])
                  if u["sku"] == sku), None)
    if found is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Допродажа не найдена")
    log_event(conn, session_id, "upsell_added",
              {"sku": sku, "price": found["price"], "name": found["name"]})
    conn.commit()
    conn.close()
    return {"ok": True, "price": found["price"]}


@app.post("/api/delete/{sid}")
def delete_data(sid: str):
    conn = db()
    conn.execute("DELETE FROM sessions WHERE sid=?", (sid,))
    conn.commit()
    conn.close()
    for mode in ("lb", "ai"):
        (MEDIA / "gen" / f"{sid}_{mode}.png").unlink(missing_ok=True)
    for f in (MEDIA / "photos").iterdir():
        if f.name.startswith(sid):
            f.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    cleanup_photos()
    if not is_admin(request):
        html = ADMIN_LOGIN.replace("__CSS__", CSS)
        html = html.replace("__ERR__", "<div class='error'>Неверный пароль.</div>"
                            if request.query_params.get("err") else "")
        return HTMLResponse(html)
    conn = db()
    n_arch = conn.execute("SELECT COUNT(*) c FROM archetypes").fetchone()["c"]
    n_prod = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
    stats = admin_stats(conn)
    conn.close()
    msg_param = request.query_params.get("msg")
    html = ADMIN_PANEL.replace("__CSS__", CSS)
    html = html.replace("__MSG__", f"<div class='msg'>{msg_param}</div>" if msg_param else "")
    html = html.replace("__NARCH__", str(n_arch)).replace("__NPROD__", str(n_prod))
    html = html.replace("__AI__", "<b class='ok'>подключён</b>" if gc_available()
                        else "<b class='warn' style='color:#b7791f'>ключ не задан — режим правил</b>")
    html = html.replace("__STATS__", stats)
    return HTMLResponse(html)


@app.post("/admin/login")
def admin_login(request: Request, password: str = Form("")):
    if password != ADMIN_PASSWORD:
        return RedirectResponse("/admin?err=1", status_code=303)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie("auratech_admin",
                    hashlib.sha256((SECRET + "admin").encode()).hexdigest(),
                    httponly=True, samesite="lax", max_age=8 * 3600)
    return resp


@app.post("/admin/logout")
def admin_logout():
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie("auratech_admin")
    return resp


@app.post("/admin/demo")
def admin_demo(request: Request):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    conn = db()
    na, np_ = load_demo(conn)
    conn.commit()
    conn.close()
    return RedirectResponse(f"/admin?msg=Загружено: {na} типажей, {np_} товаров", status_code=303)


@app.post("/admin/reset")
def admin_reset(request: Request):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    conn = db()
    for t in ("archetypes", "products", "events", "sessions"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    return RedirectResponse("/admin?msg=Данные очищены", status_code=303)