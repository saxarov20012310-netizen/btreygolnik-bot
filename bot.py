#!/usr/bin/env python3
"""
Белый Треугольник | Автопилот — 3 поста в день для @btreygolnik
"""
import os, sys, io, re, time, random, logging
import schedule, feedparser, requests
from PIL import Image, ImageDraw, ImageFont
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("btbot")

TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHANNEL  = os.environ.get("CHANNEL_ID", "@btreygolnik")
ANT_KEY  = os.environ["ANTHROPIC_API_KEY"]
client   = Anthropic(api_key=ANT_KEY)

FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://www.theblockcrypto.com/rss.xml",
    "https://cryptopotato.com/feed/",
]

# ── ШРИФТЫ ────────────────────────────────────────────────────────────────────
FONT_DIR = "/tmp/botfonts"

def _try_dl(url, dest):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        if len(r.content) < 10000: return False
        open(dest, "wb").write(r.content)
        log.info(f"Font DL OK: {dest}")
        return True
    except Exception as e:
        log.warning(f"Font DL fail {url}: {e}")
        return False

def _ensure_fonts():
    os.makedirs(FONT_DIR, exist_ok=True)
    for name, urls in {
        "Inter-Bold.ttf": [
            "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Bold.ttf",
            "https://github.com/google/fonts/raw/main/ofl/inter/static/Inter_24pt-Bold.ttf",
        ],
        "Inter-Regular.ttf": [
            "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Regular.ttf",
            "https://github.com/google/fonts/raw/main/ofl/inter/static/Inter_24pt-Regular.ttf",
        ],
    }.items():
        dest = f"{FONT_DIR}/{name}"
        if os.path.exists(dest) and os.path.getsize(dest) > 10000: continue
        for u in urls:
            if _try_dl(u, dest): break

_ensure_fonts()

def _font(paths, size):
    for p in paths:
        try:
            f = ImageFont.truetype(p, size)
            log.info(f"Font OK: {p} @ {size}")
            return f
        except: pass
    log.warning(f"Font fallback default @ {size}")
    return ImageFont.load_default()

BOLD = [f"{FONT_DIR}/Inter-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/opentype/lato/Lato-Black.ttf",
        "/usr/share/fonts/truetype/lato/Lato-Black.ttf"]
REG  = [f"{FONT_DIR}/Inter-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/opentype/lato/Lato-Regular.ttf",
        "/usr/share/fonts/truetype/lato/Lato-Regular.ttf"]

def load_fonts():
    return {
        "h1":   _font(BOLD, 82),
        "stat": _font(BOLD, 52),
        "sub":  _font(REG,  20),
        "tag":  _font(REG,  12),
        "hand": _font(BOLD, 14),
        "mono": _font(REG,  13),
    }
FONTS = load_fonts()

# ── RSS ───────────────────────────────────────────────────────────────────────
def fetch_articles(n=8):
    arts = []
    random.shuffle(FEEDS)
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:3]:
                t = e.get("title","").strip()
                s = re.sub(r"<[^>]+>","",e.get("summary",e.get("description",""))).strip()[:350]
                if t: arts.append({"title":t,"summary":s})
        except Exception as ex: log.warning(f"Feed {url}: {ex}")
        if len(arts) >= n: break
    return arts[:n]

# ── ГЕНЕРАЦИЯ ─────────────────────────────────────────────────────────────────
SYSTEM = """Ты — редактор Telegram-канала «Белый треугольник | Братство» (@btreygolnik).
Аудитория: молодые маркетологи 18-28 лет, которые следят за крипто-трендами,
viral-кейсами, growth hacking и product-led growth.
Стиль: уверенный, острый, без воды. Один конкретный инсайт в каждом посте.
Пиши как эксперт-практик. Без «возможно» и «я думаю»."""

def generate_post(articles):
    news = "\n".join(f"[{i+1}] {a['title']}\n{a['summary']}" for i,a in enumerate(articles))
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=1100, system=SYSTEM,
        messages=[{"role":"user","content":f"""Новости:

{news}

Выбери ОДНУ самую взрывную и напиши:

=== ТЕКСТ ПОСТА ===
(эмодзи + заголовок, затем 3-4 предложения, затем вывод, затем «Маркетологи, записывайте.» — 150-220 слов, без markdown-звёздочек)

=== ВИЗУАЛ ===
СТРОКА1: (3-4 слова — первая часть заголовка, всё заглавными, без эмодзи)
СТРОКА2: (2-3 слова — вторая часть, ударная, всё заглавными)
ОПИСАНИЕ: (одно предложение — суть кейса, 10-15 слов)
ЦИФРА1: (ключовая метрика, например +900% или $160M)
МЕТКА1: (3-6 слов что означает цифра)
ЦИФРА2: (вторая метрика)
МЕТКА2: (3-6 слов что означает)
ЦЕПОЧКА: (flywheel 4-5 шагов через →, например: Продукт → Вирус → Деньги → Рост)
"""}])
    text = resp.content[0].text.strip()

    # Разбираем секции
    post_part   = text.split("=== ВИЗУАЛ ===")[0].replace("=== ТЕКСТ ПОСТА ===","").strip()
    visual_part = text.split("=== ВИЗУАЛ ===")[1].strip() if "=== ВИЗУАЛ ===" in text else ""

    def ex(key, default=""):
        for line in visual_part.split("\n"):
            if line.strip().startswith(key + ":"):
                return line.split(":",1)[1].strip()
        return default

    vis = {
        "line1": ex("СТРОКА1","МАРКЕТИНГОВЫЙ").upper(),
        "line2": ex("СТРОКА2","КЕЙС.").upper(),
        "desc":  ex("ОПИСАНИЕ",""),
        "s1v":   ex("ЦИФРА1",""),
        "s1l":   ex("МЕТКА1",""),
        "s2v":   ex("ЦИФРА2",""),
        "s2l":   ex("МЕТКА2",""),
        "chain": ex("ЦЕПОЧКА",""),
    }
    return post_part, vis

# ── ИЗОБРАЖЕНИЕ ───────────────────────────────────────────────────────────────
def wrap(draw, text, font, max_w):
    words = text.split(); lines, cur = [], ""
    for w in words:
        test = (cur+" "+w).strip()
        if draw.textbbox((0,0),test,font=font)[2] <= max_w: cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines

def create_image(vis):
    W, H  = 1280, 720
    BG    = (5,   5,   9)
    ELEC  = (0,  195, 255)
    GOLD  = (255, 196,  0)
    WHT   = (238, 240, 248)
    DIM   = (50,  53,  70)
    DTXT  = (105, 110, 135)

    img  = Image.new("RGBA",(W,H),BG+(255,))

    # Диагональный луч снизу
    bm = Image.new("RGBA",(W,H),(0,0,0,0)); bd=ImageDraw.Draw(bm)
    for i in range(80):
        a = max(0,18-i//4); o=i*3
        bd.line([(0,H-o),(W,H-W-o)],fill=(0,195,255,a))
    img = Image.alpha_composite(img,bm)

    # Большой белый треугольник справа
    apex=(1068,50); bl=(648,700); br=(1380,700)
    tr=Image.new("RGBA",(W,H),(0,0,0,0)); td=ImageDraw.Draw(tr)
    td.polygon([apex,bl,br],fill=(255,255,255,14))
    td.line([apex,bl],fill=(225,230,255,190),width=2)
    td.line([apex,br],fill=(225,230,255,80), width=2)
    img=Image.alpha_composite(img,tr)

    # Маленький треугольник верх-лево
    sa=Image.new("RGBA",(W,H),(0,0,0,0)); sd=ImageDraw.Draw(sa)
    pts=[(64,102),(28,170),(100,170)]
    sd.polygon(pts,fill=(0,195,255,18))
    sd.line(pts+[pts[0]],fill=(0,195,255,160),width=1)
    img=Image.alpha_composite(img,sa)

    # Дуги от вершины
    ar=Image.new("RGBA",(W,H),(0,0,0,0)); ad=ImageDraw.Draw(ar)
    cx,cy=apex
    for r,a in [(75,50),(145,32),(225,18),(315,10)]:
        ad.arc([cx-r,cy-r,cx+r,cy+r],125,218,fill=(0,195,255,a))
    img=Image.alpha_composite(img,ar)

    # Точка на вершине
    gl=Image.new("RGBA",(W,H),(0,0,0,0)); gd=ImageDraw.Draw(gl)
    for r,a in [(22,8),(14,26),(8,65),(4,175)]:
        gd.ellipse([cx-r,cy-r,cx+r,cy+r],fill=(0,215,255,a))
    img=Image.alpha_composite(img,gl)

    draw=ImageDraw.Draw(img)
    LM=62; MAXW=560

    # Тег
    draw.text((LM,48),"БЕЛЫЙ ТРЕУГОЛЬНИК  /  МАРКЕТИНГ",font=FONTS["tag"],fill=DIM+(255,))
    draw.line([(LM,70),(550,70)],fill=DIM+(255,),width=1)

    # Заголовок: строка 1 белая, строка 2 голубая
    y=88
    for ln,color in [(vis["line1"],WHT),(vis["line2"],ELEC)]:
        for part in wrap(draw,ln,FONTS["h1"],MAXW)[:2]:
            draw.text((LM,y),part,font=FONTS["h1"],fill=color+(255,)); y+=90

    # Описание
    y+=6
    draw.text((LM,y),"...",font=FONTS["sub"],fill=ELEC+(255,)); y+=28
    for part in wrap(draw,vis["desc"],FONTS["sub"],MAXW)[:2]:
        draw.text((LM,y),part,font=FONTS["sub"],fill=DTXT+(255,)); y+=26

    # Статы
    y+=20
    draw.line([(LM,y),(550,y)],fill=DIM+(255,),width=1); y+=16
    col2=LM+250

    if vis["s1v"]:
        draw.text((LM,y),vis["s1v"],font=FONTS["stat"],fill=GOLD+(255,))
    if vis["s2v"]:
        draw.text((col2,y),vis["s2v"],font=FONTS["stat"],fill=WHT+(255,))
    y+=62

    if vis["s1l"]:
        draw.text((LM,y),vis["s1l"],font=FONTS["tag"],fill=DTXT+(255,))
    if vis["s2l"]:
        draw.text((col2,y),vis["s2l"],font=FONTS["tag"],fill=DTXT+(255,))
    if vis["s1v"] and vis["s2v"]:
        draw.line([(col2-20,y-50),(col2-20,y+18)],fill=DIM+(255,),width=1)
    y+=22

    # Цепочка
    if vis["chain"]:
        y+=6
        draw.text((LM,y),"↑  "+vis["chain"],font=FONTS["mono"],fill=DTXT+(200,))

    # Нижняя полоса
    draw.line([(LM,636),(W-LM,636)],fill=DIM+(255,),width=1)
    draw.text((LM,650),"@btreygolnik",font=FONTS["hand"],fill=ELEC+(255,))
    tag="Product flywheel  >  рекламный бюджет"
    bbox=draw.textbbox((0,0),tag,font=FONTS["tag"])
    draw.text((W-LM-(bbox[2]-bbox[0]),652),tag,font=FONTS["tag"],fill=DTXT+(255,))

    buf=io.BytesIO()
    img.convert("RGB").save(buf,"PNG")
    buf.seek(0)
    return buf.read()

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def send_photo(caption, img_bytes):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    r=requests.post(url,
        data={"chat_id":CHANNEL,"caption":caption},
        files={"photo":("post.png",img_bytes,"image/png")},timeout=30)
    return r.json()

# ── JOB ───────────────────────────────────────────────────────────────────────
def job():
    log.info("Запуск поста...")
    try:
        arts=fetch_articles()
        if not arts: log.warning("Нет статей"); return
        post_text, vis = generate_post(arts)
        log.info(f"Заголовок: {vis['line1']} / {vis['line2']}")
        img_bytes=create_image(vis)
        res=send_photo(post_text, img_bytes)
        if res.get("ok"): log.info("Пост опубликован!")
        else: log.error(f"Ошибка TG: {res}")
    except Exception: log.exception("Ошибка в job()")

# ── РАСПИСАНИЕ ────────────────────────────────────────────────────────────────
schedule.every().day.at("06:00").do(job)
schedule.every().day.at("11:00").do(job)
schedule.every().day.at("17:00").do(job)

if __name__ == "__main__":
    log.info(f"Бот запущен. Канал: {CHANNEL}")
    job()
    while True:
        schedule.run_pending()
        time.sleep(60)
