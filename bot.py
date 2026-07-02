#!/usr/bin/env python3
"""
Белый Треугольник | Автопилот
3 поста в день для @btreygolnik
Картинки: Pollinations.ai (бесплатный AI) → PIL текст-оверлей
"""

import os, sys, io, re, time, random, logging, urllib.parse
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
FONT_DIR = "/tmp/btfonts"

def _dl(url, dest):
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and len(r.content) > 10000:
            open(dest, "wb").write(r.content)
            return True
    except:
        pass
    return False

def _ensure_fonts():
    os.makedirs(FONT_DIR, exist_ok=True)
    for name, url in {
        "Inter-Bold.ttf":    "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Bold.ttf",
        "Inter-Regular.ttf": "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Regular.ttf",
    }.items():
        dest = f"{FONT_DIR}/{name}"
        if not (os.path.exists(dest) and os.path.getsize(dest) > 10000):
            _dl(url, dest)

_ensure_fonts()

BOLD_PATHS = [
    f"{FONT_DIR}/Inter-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]
REG_PATHS = [
    f"{FONT_DIR}/Inter-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]

def _fnt(paths, size):
    for p in paths:
        try:
            f = ImageFont.truetype(p, size)
            log.info(f"Font OK: {p} @{size}")
            return f
        except:
            pass
    log.warning(f"Font fallback @{size}")
    return ImageFont.load_default()

# ── RSS ───────────────────────────────────────────────────────────────────────
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
            log.warning(f"Feed {url}: {ex}")
        if len(articles) >= n:
            break
    return articles[:n]
# ── CLAUDE: ГЕНЕРАЦИЯ ПОСТА + ВИЗУАЛА ─────────────────────────────────────────
SYSTEM_PROMPT = """Ты — редактор Telegram-канала «Белый треугольник | Братство» (@btreygolnik).
Аудитория: маркетологи 18-28 лет, следят за крипто-трендами и growth hacking.
Стиль: уверенный, острый, без воды. Один инсайт — один пост.
Никогда не пиши «я думаю» или «возможно»."""

def generate_post(articles):
    news = "\n".join(f"[{i+1}] {a['title']}\n{a['summary']}" for i, a in enumerate(articles))
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1100,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"""Свежие новости:
{news}

Выбери ОДНУ самую взрывную новость. Напиши пост и данные для картинки.

=== ТЕКСТ ПОСТА ===
[Эмодзи + заголовок]

[3-4 предложения: суть новости + почему это важно и что с этим делать]

[Короткий вывод одной фразой — каждый раз разной, без шаблонных обращений]

=== ВИЗУАЛ ===
СТРОКА1: [2-3 слова КАПСОМ по-русски, главная мысль]
СТРОКА2: [1-2 слова КАПСОМ по-русски, ударная фраза]
ОПИСАНИЕ: [одно предложение, суть новости, можно по-русски]
ЦИФРА1: [метрика, например +900%]
МЕТКА1: [2-3 слова]
ЦИФРА2: [метрика, например $160M]
МЕТКА2: [2-3 слова]
ПРОМПТ: [5-8 слов EN для генерации фона, тема поста, без текста]

Строго два раздела."""}]
    )
    raw = resp.content[0].text.strip()

    post_text, vis_text = raw, ""
    if "=== ТЕКСТ ПОСТА ===" in raw and "=== ВИЗУАЛ ===" in raw:
        parts     = raw.split("=== ВИЗУАЛ ===")
        post_text = parts[0].replace("=== ТЕКСТ ПОСТА ===", "").strip()
        vis_text  = parts[1].strip()

    def ex(key, default):
        m = re.search(rf"^{key}:\s*(.+)$", vis_text, re.MULTILINE)
        return m.group(1).strip() if m else default

    vis = {
        "line1":  ex("СТРОКА1",  "КРИПТА СЕГОДНЯ"),
        "line2":  ex("СТРОКА2",  "НОВЫЙ ТРЕНД"),
        "desc":   ex("ОПИСАНИЕ", "Тренд, который меняет правила игры"),
        "s1v":    ex("ЦИФРА1",   "+300%"),
        "s1l":    ex("МЕТКА1",   "рост"),
        "s2v":    ex("ЦИФРА2",   "$50M"),
        "s2l":    ex("МЕТКА2",   "объём"),
        "prompt": ex("ПРОМПТ",   "dark futuristic blockchain technology glowing blue"),
    }
    return post_text, vis
# ── AI ФОТОФОН: Pollinations.ai ───────────────────────────────────────────────
def generate_bg(vis_prompt: str) -> bytes | None:
    """Бесплатная AI-генерация фона — Pollinations.ai (без API-ключа)"""
    base_prompt = (
        f"dark cinematic wallpaper, {vis_prompt}, "
        "deep navy black background, glowing blue geometric shapes, "
        "professional marketing design, no text, no letters, no words, "
        "ultra sharp, 4K, dramatic lighting"
    )
    seed = random.randint(1000, 99999)
    encoded = urllib.parse.quote(base_prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1280&height=720&nologo=true&seed={seed}&model=flux"
    )
    log.info(f"AI фон: запрос к Pollinations (seed={seed})...")
    try:
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        if r.headers.get("content-type", "").startswith("image"):
            log.info("AI фон получен")
            return r.content
    except Exception as e:
        log.warning(f"Pollinations ошибка: {e}")
    return None

def _solid_bg() -> Image.Image:
    """Запасной фон — градиент тёмно-синего"""
    img = Image.new("RGB", (1280, 720), (5, 5, 15))
    draw = ImageDraw.Draw(img)
    for i in range(200):
        alpha = int(18 * (1 - i / 200))
        x = 640 + i * 3
        draw.line([(x, 0), (x - 400, 720)], fill=(0, 80 + i, 180, alpha), width=2)
    return img

# ── НАЛОЖЕНИЕ ТЕКСТА НА ФОТО ──────────────────────────────────────────────────
def _fit_size(draw, text, paths, start, max_w, min_size=44):
    """Подбирает размер шрифта, чтобы строка влезла в max_w"""
    size = start
    f = _fnt(paths, size)
    while size > min_size and draw.textbbox((0, 0), text, font=f)[2] > max_w:
        size -= 4
        f = _fnt(paths, size)
    return size

def _wrap(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
            cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines
def overlay_text(img: Image.Image, vis: dict) -> Image.Image:
    W, H = 1280, 720
    img = img.convert("RGBA")

    # Тёмная плашка слева — гарантирует контраст на любом фоне
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd   = ImageDraw.Draw(card)
    cd.rounded_rectangle([28, 18, 690, 700], radius=18, fill=(4, 5, 18, 195))
    img = Image.alpha_composite(img, card)

    # Мягкий градиент поверх плашки
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(grad)
    for x in range(700):
        a = int(60 * (1 - x / 700) ** 1.5)
        gd.line([(x, 0), (x, H)], fill=(2, 3, 10, a))
    img = Image.alpha_composite(img, grad)

    # Треугольник-акцент (правая часть)
    tri = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td  = ImageDraw.Draw(tri)
    apex = (1060, 20)
    td.polygon([apex, (680, 740), (1440, 740)], fill=(0, 195, 255, 10))
    td.line([apex, (680, 740)],  fill=(0, 195, 255, 45), width=2)
    td.line([apex, (1440, 740)], fill=(0, 195, 255, 25), width=1)
    for r, a in [(40, 60), (70, 35), (100, 18)]:
        cx, cy = apex
        td.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(0, 195, 255, a), width=1)
    td.ellipse([apex[0]-5, apex[1]-5, apex[0]+5, apex[1]+5], fill=(0, 220, 255, 220))
    td.polygon([(55, 620), (30, 665), (80, 665)], fill=(0, 195, 255, 18), outline=(0, 195, 255, 80))
    img = Image.alpha_composite(img, tri)

    draw = ImageDraw.Draw(img)
    BLK  = (0, 0, 0)
    ELEC = (0,   210, 255)
    GOLD = (255, 205,   0)
    WHT  = (255, 255, 255)
    MUTED= (180, 185, 210)
    SEP  = ( 50,  60, 110)
    LM   = 64

    MAXW = 690 - LM - 20   # ширина текста внутри плашки

    # Заголовки: общий размер, чтобы обе строки влезли в плашку
    h_size = min(
        _fit_size(draw, vis["line1"], BOLD_PATHS, 82, MAXW),
        _fit_size(draw, vis["line2"], BOLD_PATHS, 82, MAXW),
    )
    f_h1   = _fnt(BOLD_PATHS, h_size)
    f_h2   = f_h1
    f_desc = _fnt(REG_PATHS,  22)
    f_stat = _fnt(BOLD_PATHS, 52)
    f_lbl  = _fnt(REG_PATHS,  15)
    f_tag  = _fnt(REG_PATHS,  14)
    f_hand = _fnt(BOLD_PATHS, 17)
    draw.text((LM, 26), "БЕЛЫЙ ТРЕУГОЛЬНИК  \u00b7  МАРКЕТИНГ  \u00b7  @btreygolnik",
              font=f_tag, fill=MUTED, stroke_width=1, stroke_fill=BLK)
    draw.line([(LM, 52), (W - LM - 100, 52)], fill=SEP, width=1)

    y = 84
    draw.text((LM, y), vis["line1"], font=f_h1, fill=WHT,
              stroke_width=2, stroke_fill=BLK)
    y += h_size + 14
    draw.text((LM, y), vis["line2"], font=f_h2, fill=ELEC,
              stroke_width=2, stroke_fill=BLK)
    y += h_size + 26

    for line in _wrap(draw, vis["desc"], f_desc, MAXW)[:3]:
        draw.text((LM, y), line, font=f_desc, fill=MUTED,
                  stroke_width=1, stroke_fill=BLK)
        y += 32

    # Статистика прижата к низу плашки — композиция не разваливается
    sy = 500
    draw.line([(LM, sy), (560, sy)], fill=SEP, width=1)
    sy += 20
    # Вторая колонка сдвигается, если первая цифра широкая
    w1 = draw.textbbox((0, 0), vis["s1v"], font=f_stat)[2]
    x2 = max(LM + 220, LM + w1 + 48)
    draw.text((LM, sy), vis["s1v"], font=f_stat, fill=GOLD,
              stroke_width=2, stroke_fill=BLK)
    draw.text((x2, sy), vis["s2v"], font=f_stat, fill=WHT,
              stroke_width=2, stroke_fill=BLK)
    sy += 62
    draw.text((LM, sy), vis["s1l"].upper(), font=f_lbl, fill=MUTED,
              stroke_width=1, stroke_fill=BLK)
    draw.text((x2, sy), vis["s2l"].upper(), font=f_lbl, fill=MUTED,
              stroke_width=1, stroke_fill=BLK)
    draw.line([(x2 - 22, sy - 54), (x2 - 22, sy + 14)], fill=SEP, width=1)

    draw.line([(LM, H - 50), (W - LM - 100, H - 50)], fill=SEP, width=1)
    draw.text((LM, H - 34), "@btreygolnik", font=f_hand, fill=ELEC,
              stroke_width=1, stroke_fill=BLK)
    draw.text((480, H - 32), "маркетинг без воды", font=f_tag, fill=MUTED,
              stroke_width=1, stroke_fill=BLK)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    return Image.open(buf)
def create_image(vis: dict) -> bytes:
    bg_bytes = generate_bg(vis.get("prompt", "dark tech crypto blockchain"))
    if bg_bytes:
        try:
            bg = Image.open(io.BytesIO(bg_bytes)).resize((1280, 720))
        except Exception as e:
            log.warning(f"Не удалось открыть AI-фон: {e}")
            bg = _solid_bg()
    else:
        bg = _solid_bg()
    result = overlay_text(bg, vis)
    buf = io.BytesIO()
    result.save(buf, "PNG")
    buf.seek(0)
    return buf.read()

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def send_photo(caption: str, image_bytes: bytes) -> dict:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    r = requests.post(
        url,
        data={"chat_id": CHANNEL, "caption": caption},
        files={"photo": ("post.png", image_bytes, "image/png")},
        timeout=30,
    )
    return r.json()

# ── JOB ───────────────────────────────────────────────────────────────────────
def job():
    log.info("Запуск поста...")
    try:
        articles = fetch_articles()
        if not articles:
            log.warning("Нет статей")
            return
        post_text, vis = generate_post(articles)
        log.info(f"Визуал: {vis['line1']} / {vis['line2']}")
        image_bytes = create_image(vis)
        result = send_photo(post_text, image_bytes)
        if result.get("ok"):
            log.info("Пост опубликован!")
        else:
            log.error(f"Telegram: {result}")
    except Exception:
        log.exception("Ошибка в job()")

# 09:00 МСК = 06:00 UTC | 14:00 МСК = 11:00 UTC | 20:00 МСК = 17:00 UTC
schedule.every().day.at("06:00").do(job)
schedule.every().day.at("11:00").do(job)
schedule.every().day.at("17:00").do(job)

if __name__ == "__main__":
    log.info("Бот Белый треугольник запущен")
    log.info(f"Канал: {CHANNEL}")
    log.info("Расписание: 09:00 / 14:00 / 20:00 МСК")
    job()
    while True:
        schedule.run_pending()
        time.sleep(60)
