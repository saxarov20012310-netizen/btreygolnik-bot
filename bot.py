#!/usr/bin/env python3
"""
Белый Треугольник | Автопилот
Telegram-бот для @btreygolnik — 3 поста в день автоматически.
"""

import os, sys, io, re, time, random, logging
import schedule, feedparser, requests
from PIL import Image, ImageDraw, ImageFont
from anthropic import Anthropic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
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

def _try_download(url, dest):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        if len(r.content) < 10000:
            return False
        with open(dest, "wb") as f:
            f.write(r.content)
        log.info(f"Font downloaded: {dest}")
        return True
    except Exception as e:
        log.warning(f"Font DL failed {url}: {e}")
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
        if os.path.exists(dest) and os.path.getsize(dest) > 10000:
            continue
        for url in urls:
            if _try_download(url, dest):
                break

_ensure_fonts()

def _font(paths, size):
    for p in paths:
        try:
            f = ImageFont.truetype(p, size)
            log.info(f"Font OK: {p} @ {size}px")
            return f
        except Exception:
            pass
    log.warning(f"All fonts failed @ {size}px")
    return ImageFont.load_default()

# ВАЖНО: DejaVu стоит до Lato — только DejaVu гарантирует кириллицу на Railway
BOLD_PATHS = [
    f"{FONT_DIR}/Inter-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/opentype/lato/Lato-Black.ttf",
    "/usr/share/fonts/truetype/lato/Lato-Black.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
REG_PATHS = [
    f"{FONT_DIR}/Inter-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/opentype/lato/Lato-Regular.ttf",
    "/usr/share/fonts/truetype/lato/Lato-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]

def load_fonts():
    return {
        "head": _font(BOLD_PATHS, 78),
        "sub":  _font(REG_PATHS,  24),
        "tag":  _font(REG_PATHS,  14),
        "hand": _font(BOLD_PATHS, 16),
    }

FONTS = load_fonts()

# ── 3 ТЕМЫ ОФОРМЛЕНИЯ (меняются рандомно) ────────────────────────────────────
THEMES = [
    # 1. Electric Blue
    {"BG": (7, 8, 16),  "ACCENT": (0, 200, 255),  "TRI": (0, 200, 255),
     "WHITE": (235, 238, 255), "MUTED": (88, 98, 130), "SEP": (20, 24, 46)},
    # 2. Neon Green
    {"BG": (6, 14, 9),  "ACCENT": (0, 230, 120),  "TRI": (0, 230, 120),
     "WHITE": (230, 250, 235), "MUTED": (80, 115, 90), "SEP": (18, 38, 24)},
    # 3. Violet
    {"BG": (11, 7, 18), "ACCENT": (185, 75, 255), "TRI": (185, 75, 255),
     "WHITE": (238, 232, 255), "MUTED": (110, 90, 145), "SEP": (32, 20, 52)},
]

# ── ПАРСИНГ НОВОСТЕЙ ──────────────────────────────────────────────────────────
def fetch_articles(n=8):
    articles = []
    random.shuffle(FEEDS)
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:3]:
                title   = e.get("title", "").strip()
                summary = re.sub(r"<[^>]+>", "", e.get("summary", e.get("description", ""))).strip()[:350]
                if title:
                    articles.append({"title": title, "summary": summary})
        except Exception as ex:
            log.warning(f"Feed error {url}: {ex}")
        if len(articles) >= n:
            break
    return articles[:n]

# ── ГЕНЕРАЦИЯ ПОСТА ───────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты — редактор Telegram-канала «Белый треугольник | Братство» (@btreygolnik).
Аудитория: молодые маркетологи 18-28 лет, которые следят за крипто-трендами,
viral-кейсами, growth hacking и product-led growth.
Стиль: уверенный, острый, без воды. Один конкретный инсайт в каждом посте.
Никогда не пиши «я думаю» или «возможно». Пиши как эксперт."""

def generate_post(articles):
    news_block = "\n".join(
        f"[{i+1}] {a['title']}\n{a['summary']}" for i, a in enumerate(articles)
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=900,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"""Свежие новости крипты и технологий:

{news_block}

Задача: выбери ОДНУ самую взрывную новость с маркетинговой точки зрения и напиши пост.

Формат:
СТРОКА 1: эмодзи + цепляющий заголовок (не раскрывает всё сразу, создаёт интригу)
СТРОКА 2: пустая
СТРОКИ 3-5: суть новости + почему важно для маркетолога (3-4 предложения)
СТРОКА 6: пустая
СТРОКА 7: один конкретный вывод, начиная с ключевого слова (без markdown)
СТРОКА 8: «Маркетологи, записывайте.»

Только текст поста. Без пояснений. Без markdown-звёздочек. 150-220 слов.
"""}]
    )
    full_text = resp.content[0].text.strip()
    lines = [l for l in full_text.split("\n") if l.strip()]
    raw_head = lines[0] if lines else "МАРКЕТИНГОВЫЙ КЕЙС"
    clean_head = re.sub(r"[^\w\s.,!?-]", "", raw_head).strip()
    words = clean_head.split()
    if len(words) > 7:
        clean_head = " ".join(words[:7])
    body_lines = [l for l in lines[1:] if l.strip()]
    sub = body_lines[0][:80] + ("…" if body_lines and len(body_lines[0]) > 80 else "") if body_lines else ""
    return full_text, clean_head.upper(), sub

# ── ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЯ ─────────────────────────────────────────────────────
def wrap_text(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def create_image(headline, subtext=""):
    W, H = 1280, 720
    T = random.choice(THEMES)
    BG    = T["BG"]
    BLUE  = T["ACCENT"]
    TRI   = T["TRI"]
    WHITE = T["WHITE"]
    MUTED = T["MUTED"]
    SEP   = T["SEP"]

    img = Image.new("RGBA", (W, H), BG + (255,))

    # Фоновый треугольник (прозрачный)
    tri = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td  = ImageDraw.Draw(tri)
    apex = (1060, 30)
    td.polygon([apex, (700, 740), (1420, 740)], fill=TRI + (6,))
    td.line([apex, (700,  740)], fill=TRI + (100,), width=2)
    td.line([apex, (1420, 740)], fill=TRI + (18,),  width=1)
    img = Image.alpha_composite(img, tri)

    # Свечение у вершины
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(glow)
    cx, cy = apex
    for r, a in [(36, 4), (22, 14), (12, 45), (6, 150)]:
        gd.ellipse([cx-r, cy-r, cx+r, cy+r], fill=TRI + (a,))
    img = Image.alpha_composite(img, glow)

    draw = ImageDraw.Draw(img)
    LM   = 64
    MAXW = W - LM * 2 - 100

    # Верхняя полоска
    draw.rectangle([0, 0, W, 4], fill=BLUE + (255,))

    # Метка
    draw.text((LM, 24), "БЕЛЫЙ ТРЕУГОЛЬНИК · МАРКЕТИНГ · @btreygolnik",
              font=FONTS["tag"], fill=MUTED + (255,))
    draw.line([(LM, 52), (W - LM, 52)], fill=SEP + (255,), width=1)

    # Заголовок
    y = 72
    hl = wrap_text(draw, headline, FONTS["head"], MAXW)
    for i, line in enumerate(hl[:3]):
        clr = BLUE + (255,) if i == len(hl[:3]) - 1 else WHITE + (255,)
        draw.text((LM, y), line, font=FONTS["head"], fill=clr)
        y += 94

    # Акцентная черта
    y += 12
    draw.rectangle([LM, y, LM + 50, y + 4], fill=BLUE + (255,))
    y += 22

    # Подзаголовок
    if subtext:
        for line in wrap_text(draw, subtext, FONTS["sub"], MAXW)[:2]:
            draw.text((LM, y), line, font=FONTS["sub"], fill=MUTED + (255,))
            y += 34

    # Нижняя полоса
    draw.line([(LM, H - 56), (W - LM, H - 56)], fill=SEP + (255,), width=1)
    draw.text((LM, H - 40), "@btreygolnik", font=FONTS["hand"], fill=BLUE + (255,))
    tag  = "маркетинг без воды"
    bbox = draw.textbbox((0, 0), tag, font=FONTS["tag"])
    draw.text((W - LM - (bbox[2] - bbox[0]), H - 38), tag,
              font=FONTS["tag"], fill=MUTED + (255,))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return buf.read()

# ── ОТПРАВКА В TELEGRAM ───────────────────────────────────────────────────────
def send_photo(caption, image_bytes):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    r = requests.post(
        url,
        data={"chat_id": CHANNEL, "caption": caption},
        files={"photo": ("post.png", image_bytes, "image/png")},
        timeout=30,
    )
    return r.json()

# ── ОСНОВНАЯ ЗАДАЧА ───────────────────────────────────────────────────────────
def job():
    log.info("Запуск поста...")
    try:
        articles = fetch_articles()
        if not articles:
            log.warning("Нет статей, пропускаю")
            return
        post_text, headline, subtext = generate_post(articles)
        log.info(f"Заголовок: {headline}")
        image_bytes = create_image(headline, subtext)
        result = send_photo(post_text, image_bytes)
        if result.get("ok"):
            log.info("Пост опубликован!")
        else:
            log.error(f"Ошибка Telegram: {result}")
    except Exception:
        log.exception("Ошибка в job()")

# ── РАСПИСАНИЕ (UTC; Москва = UTC+3) ─────────────────────────────────────────
schedule.every().day.at("06:00").do(job)
schedule.every().day.at("11:00").do(job)
schedule.every().day.at("17:00").do(job)

if __name__ == "__main__":
    log.info("Бот Белый треугольник запущен")
    log.info(f"Канал: {CHANNEL}")
    job()
    while True:
        schedule.run_pending()
        time.sleep(60)
