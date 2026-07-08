#!/usr/bin/env python3
"""
Белый Треугольник | Автопилот
3 поста в день для @btreygolnik
Картинки: чередование стилей — живое фото из статьи / фотореалистичный AI-кадр /
фирменный шаблон (Pollinations.ai → PIL текст-оверлей)
"""

import os, sys, io, re, json, time, random, logging, urllib.parse
import html as html_mod
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
# Inter лежит в репозитории (fonts/) — не зависим ни от скачивания, ни от системы
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

BOLD_PATHS = [
    os.path.join(FONT_DIR, "Inter-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]
REG_PATHS = [
    os.path.join(FONT_DIR, "Inter-Regular.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]

def _fnt(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    # Битмап-fallback не умеет кириллицу — лучше не публиковать пост вовсе
    raise RuntimeError(f"Не найден ни один шрифт: {paths}")

# ── RSS ───────────────────────────────────────────────────────────────────────
def _entry_image(e) -> str | None:
    """Достаёт URL картинки статьи из RSS-записи (media:content, enclosure, <img>)"""
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key) or []:
            u = m.get("url")
            if u:
                return u
    for l in e.get("links") or []:
        if l.get("rel") == "enclosure" and str(l.get("type", "")).startswith("image"):
            return l.get("href")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', e.get("summary", e.get("description", "")))
    return m.group(1) if m else None

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
                    articles.append({"title": title, "summary": summary,
                                     "image": _entry_image(e)})
        except Exception as ex:
            log.warning(f"Feed {url}: {ex}")
        if len(articles) >= n:
            break
    return articles[:n]
# ── ПАМЯТЬ: ПОСЛЕДНИЕ ПОСТЫ ───────────────────────────────────────────────────
# На Railway с volume история переживает редеплой (/data), иначе живёт до рестарта
HISTORY_PATH = os.environ.get(
    "HISTORY_PATH",
    "/data/post_history.json" if os.path.isdir("/data")
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "post_history.json")
)
HISTORY_LIMIT = 15

def load_history():
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_history_entry(post_text, vis):
    hist = load_history()
    hist.append({
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "headline": f"{vis['line1']} / {vis['line2']}",
        "text": post_text[:300],
    })
    hist = hist[-HISTORY_LIMIT:]
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning(f"История не сохранилась: {e}")

# ── CLAUDE: ГЕНЕРАЦИЯ ПОСТА + ВИЗУАЛА ─────────────────────────────────────────
SYSTEM_PROMPT = """Ты — автор Telegram-канала «Белый треугольник | Братство» (@btreygolnik).
Аудитория: обычные ребята 18-28 лет. Про крипту они почти НИЧЕГО не знают —
им просто любопытно, что там происходит и при чём тут их деньги.
Пиши как друг в переписке: на «ты», просто, живо, можно с лёгким юмором и иронией.
Представь, что пересказываешь новость приятелю, который спросил «ну чё там у криптанов?».
Любой термин (стейблкоин, ETF, халвинг, альткоин, майнинг...) либо не используй,
либо тут же объясняй по-человечески — в скобках или через простое сравнение из жизни.
Без канцелярита, без «данный», «осуществляет», «динамика показателей».
Одна новость — одна простая мысль: что случилось и почему тебе не всё равно.
Пиши только по-русски. Отвечай ПЛОСКИМ ТЕКСТОМ без Markdown: никаких #, **, *, ---.
Разделы помечай ровно так: === ТЕКСТ ПОСТА === и === ВИЗУАЛ === — не заменяй эти маркеры ничем."""

def _strip_md(text: str) -> str:
    """Telegram-подпись не рендерит Markdown — убираем его следы"""
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)      # заголовки
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)          # жирный/курсив
    text = re.sub(r"^[-_=]{3,}\s*$", "", text, flags=re.MULTILINE)  # ---
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# Форматы постов — каждый раз случайный, чтобы лента не выглядела конвейером
POST_FORMATS = [
    "Классика: эмодзи + простой цепляющий заголовок, затем 3-4 предложения — что "
    "случилось и почему обычному человеку не всё равно, в конце вывод одной фразой.",
    "Как другу: перескажи новость так, будто пишешь приятелю в личку — «короче, "
    "смотри, что произошло...». Живо, с эмоцией, в конце — что ты сам об этом думаешь.",
    "Список: эмодзи + заголовок, затем «Что нужно знать:» и 3 коротких простых пункта "
    "(каждый с новой строки, начинай с эмодзи ▪️ или ⚡), финальная фраза-вывод.",
    "Вопрос-крючок: начни с вопроса, который задал бы новичок («А правда, что...?», "
    "«Слышал, что...?»), ответь просто и по фактам, закончи выводом.",
    "Цифра дня: начни с самой впечатляющей цифры новости (крупно, в первой строке), "
    "затем объясни на пальцах — много это или мало, с чем сравнить, что это меняет.",
    "Ликбез: новость одним предложением, затем разбери простыми словами термин или "
    "явление, которое за ней стоит — «что это вообще такое и почему все о нём говорят».",
]

def generate_post(articles):
    news = "\n".join(f"[{i+1}] {a['title']}\n{a['summary']}" for i, a in enumerate(articles))
    fmt = random.choice(POST_FORMATS)

    hist_block = ""
    history = load_history()
    if history:
        recent = "\n".join(f"- [{h['date']}] {h['text']}" for h in history[-7:])
        hist_block = f"""Последние посты канала:
{recent}

Не повторяй темы из этих постов и не противоречь им. Если новость уже была освещена —
возьми другую или найди новый угол. Если ситуация изменилась (был рост, стало падение) —
прямо скажи об этом развороте, а не делай вид, что прошлого поста не было.

"""
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1100,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"""{hist_block}Свежие новости:
{news}

Выбери ОДНУ новость — самую громкую и понятную новичку: деньги, рекорды, скандалы,
известные имена и компании. Узкотехнические (протоколы, апдейты сетей, EIP) пропускай,
если из них нельзя сделать простую человеческую историю. Напиши пост и данные для картинки.

Формат поста в этот раз: {fmt}
Первая строка поста — всегда цепляющий заголовок. Объём — до 900 знаков.

=== ТЕКСТ ПОСТА ===
[пост строго в указанном формате]

=== ВИЗУАЛ ===
НОМЕР: [число — номер выбранной новости из списка, например 3]
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
    return _parse_response(resp.content[0].text.strip())

def _parse_response(raw: str):
    # Отделяем пост от блока визуала — модель иногда подменяет маркеры Markdown-заголовками
    m = re.search(r"^\W*={0,3}\s*ВИЗУАЛ\s*={0,3}\s*$", raw, re.MULTILINE)
    if not m:
        m = re.search(r"^\W*СТРОКА1", raw, re.MULTILINE)  # последний рубеж
    if m:
        post_text, vis_text = raw[:m.start()], raw[m.start():]
    else:
        post_text, vis_text = raw, ""

    post_text = re.sub(r"^\W*={0,3}\s*ТЕКСТ ПОСТА\s*={0,3}\s*$", "", post_text, flags=re.MULTILINE)
    post_text = _strip_md(post_text)[:1024]

    def ex(key, default):
        # терпим Markdown вокруг ключа: **СТРОКА1:** значение
        m = re.search(rf"^[\*#\s]*{key}[\*\s]*:[\*\s]*(.+)$", vis_text, re.MULTILINE)
        return m.group(1).strip(" *") if m else default

    idx_m = re.search(r"\d+", ex("НОМЕР", ""))
    vis = {
        "idx":    int(idx_m.group()) - 1 if idx_m else None,  # 0-based индекс статьи
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
def _pollinations(prompt: str) -> bytes | None:
    seed = random.randint(1000, 99999)
    encoded = urllib.parse.quote(prompt)
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

def generate_bg(vis_prompt: str) -> bytes | None:
    """Фон для фирменного шаблона — тёмный, геометрия, под плашку"""
    return _pollinations(
        f"dark cinematic wallpaper, {vis_prompt}, "
        "deep navy black background, glowing blue geometric shapes, "
        "professional marketing design, no text, no letters, no words, "
        "ultra sharp, 4K, dramatic lighting"
    )

# Стили «живых» кадров — тоже ротация, чтобы фото не были однотипными
PHOTO_LOOKS = [
    "shot on professional camera, cinematic lighting, shallow depth of field",
    "dramatic macro close-up, studio lighting, dark background",
    "night city scene, neon reflections, rain on glass",
    "top-down flat lay composition, hard directional light",
    "documentary photography, natural light, candid moment",
    "epic wide angle, golden hour light, atmospheric haze",
]

def generate_photo_bg(vis_prompt: str) -> bytes | None:
    """«Живой» кадр — как редакционное фото к новости, без плашек и графики"""
    look = random.choice(PHOTO_LOOKS)
    return _pollinations(
        f"photorealistic editorial news photograph, {vis_prompt}, {look}, "
        "rich detail, realistic textures, no text, no letters, no watermark, no logo"
    )

# ── ЖИВЫЕ КАРТИНКИ: фото статьи / AI-фото + водяной знак ─────────────────────
def download_article_image(url: str) -> Image.Image | None:
    """Скачивает оригинальную картинку новости; мелкие превью отбрасываем"""
    try:
        r = requests.get(url, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; btbot/1.0)"})
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        img.load()
        if img.width < 500 or img.height < 280:
            log.info(f"Картинка статьи слишком мелкая ({img.width}x{img.height}) — пропуск")
            return None
        img = img.convert("RGB")
        if img.width > 1600:
            img = img.resize((1600, int(img.height * 1600 / img.width)))
        return img
    except Exception as e:
        log.warning(f"Картинка статьи не скачалась: {e}")
        return None

def _watermark(img: Image.Image) -> Image.Image:
    """Маленькая подпись канала в углу — единственный брендинг «живых» постов"""
    img = img.convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = _fnt(BOLD_PATHS, max(18, img.width // 55))
    text = "@btreygolnik"
    bbox = d.textbbox((0, 0), text, font=f)
    x = img.width - bbox[2] + bbox[0] - 24
    y = img.height - bbox[3] + bbox[1] - 20
    d.text((x, y), text, font=f, fill=(255, 255, 255, 200),
           stroke_width=2, stroke_fill=(0, 0, 0, 150))
    return Image.alpha_composite(img, layer).convert("RGB")

def _img_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

def create_article_image(url: str) -> bytes | None:
    img = download_article_image(url)
    return _img_bytes(_watermark(img)) if img else None

def create_photo_image(vis: dict) -> bytes | None:
    bg = generate_photo_bg(vis.get("prompt", "crypto market news"))
    if not bg:
        return None
    try:
        img = Image.open(io.BytesIO(bg)).resize((1280, 720))
    except Exception as e:
        log.warning(f"Не удалось открыть AI-фото: {e}")
        return None
    return _img_bytes(_watermark(img))

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
    draw.text((LM, 26), "БЕЛЫЙ ТРЕУГОЛЬНИК  \u00b7  @btreygolnik",
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
def html_caption(text: str) -> str:
    """Первая строка — жирный заголовок (как у живых каналов), остальное как есть"""
    text = text.strip()
    head, sep, rest = text.partition("\n")
    return f"<b>{html_mod.escape(head.strip())}</b>{sep}{html_mod.escape(rest)}"

def send_photo(caption: str, image_bytes: bytes, parse_mode: str | None = None) -> dict:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    data = {"chat_id": CHANNEL, "caption": caption}
    if parse_mode:
        data["parse_mode"] = parse_mode
    r = requests.post(
        url,
        data=data,
        files={"photo": ("post.png", image_bytes, "image/png")},
        timeout=30,
    )
    return r.json()

# ── ВЫБОР СТИЛЯ КАРТИНКИ ──────────────────────────────────────────────────────
def pick_style(has_article_img: bool) -> str:
    """40% фирменный шаблон, остальное — «живые»: фото статьи или AI-кадр"""
    r = random.random()
    if r < 0.40:
        return "branded"
    if r < 0.75 and has_article_img:
        return "article"
    return "photo"

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

        idx = vis.get("idx")
        art = articles[idx] if idx is not None and 0 <= idx < len(articles) else None
        art_img_url = (art or {}).get("image")

        style = pick_style(bool(art_img_url))
        image_bytes = None
        if style == "article":
            image_bytes = create_article_image(art_img_url)
            if not image_bytes:
                style = "photo"
        if style == "photo" and not image_bytes:
            image_bytes = create_photo_image(vis)
            if not image_bytes:
                style = "branded"
        if not image_bytes:
            image_bytes = create_image(vis)
        log.info(f"Стиль картинки: {style}")

        result = send_photo(html_caption(post_text), image_bytes, parse_mode="HTML")
        if not result.get("ok"):
            log.warning(f"HTML-подпись не прошла ({result}), пробую без разметки")
            result = send_photo(post_text, image_bytes)
        if result.get("ok"):
            log.info("Пост опубликован!")
            save_history_entry(post_text, vis)
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
