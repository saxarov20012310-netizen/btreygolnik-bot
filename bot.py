#!/usr/bin/env python3
"""
Белый Треугольник | Автопилот
Крипта @btreygolnik: дайджест + 3 поста в день (живое фото / AI-кадр / шаблон) + опрос
Оракул @orakul_app: гороскоп + карта дня + вечерняя рубрика (PIL-карточки)
"""

import os, sys, io, re, json, math, time, random, logging, urllib.parse
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
# Второй канал — Оракул. Тот же бот-аккаунт (админ обоих каналов).
TARO_CHANNEL = os.environ.get("TARO_CHANNEL_ID", "@orakul_app")
# Приложение Оракула — Mini App бот, НЕ канал: все CTA ведут сюда
TARO_APP     = os.environ.get("TARO_APP_LINK", "t.me/BotTaroOraclBot")
ANT_KEY  = os.environ["ANTHROPIC_API_KEY"]
client   = Anthropic(api_key=ANT_KEY)

def taro_link(source: str) -> str:
    """Deep-link в бота приложения с меткой источника — конверсия канал→бот измерима."""
    base = TARO_APP if TARO_APP.startswith("http") else f"https://{TARO_APP}"
    return f"{base}?start=ch_{source}"

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
# Символы зодиака (♈–♓) есть в DejaVu (Linux/Railway) и Segoe UI Symbol (Windows-тест)
SYM_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\seguisym.ttf",
    os.path.join(FONT_DIR, "Inter-Regular.ttf"),
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

=== ОПРОС ===
ВОПРОС: [простой провокационный вопрос аудитории по этой новости, до 100 символов]
ВАРИАНТ1: [короткий ответ, до 40 символов]
ВАРИАНТ2: [короткий ответ-противоположность, до 40 символов]

Строго три раздела."""}]
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
    poll = {"q": ex("ВОПРОС", ""), "o1": ex("ВАРИАНТ1", ""), "o2": ex("ВАРИАНТ2", "")}
    return post_text, vis, poll
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

def send_photo(caption: str, image_bytes: bytes, parse_mode: str | None = None,
               channel: str = CHANNEL, button: tuple[str, str] | None = None) -> dict:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    data = {"chat_id": channel, "caption": caption}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if button:
        data["reply_markup"] = json.dumps(
            {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]})
    r = requests.post(
        url,
        data=data,
        files={"photo": ("post.png", image_bytes, "image/png")},
        timeout=30,
    )
    return r.json()

def send_poll(question: str, options: list[str], channel: str = CHANNEL) -> dict:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPoll"
    r = requests.post(
        url,
        data={"chat_id": channel, "question": question[:300],
              "options": json.dumps(options[:10], ensure_ascii=False)},
        timeout=30,
    )
    return r.json()

def _assemble_caption(parts: list[str], limit: int = 1024) -> str:
    """Собирает подпись из блоков по приоритету: не влезающий блок отбрасывается целиком."""
    out = ""
    for p in parts:
        if not p:
            continue
        add = ("\n\n" if out else "") + p.strip()
        if len(out) + len(add) > limit:
            continue
        out += add
    return out

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
def job(with_poll: bool = False):
    log.info("Запуск поста...")
    try:
        articles = fetch_articles()
        if not articles:
            log.warning("Нет статей")
            return
        post_text, vis, poll = generate_post(articles)
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
            # Опрос по новости — раз в день, следом за дневным постом
            if with_poll and poll["q"] and poll["o1"] and poll["o2"]:
                pr = send_poll(poll["q"], [poll["o1"], poll["o2"]])
                log.info("Опрос опубликован!" if pr.get("ok") else f"Telegram (опрос): {pr}")
        else:
            log.error(f"Telegram: {result}")
    except Exception:
        log.exception("Ошибка в job()")

# ══════════════════════════════════════════════════════════════════════════════
# ОРАКУЛ · ГОРОСКОП ДНЯ  →  @orakul_app
# Тот же бот постит премиальные карточки-гороскопы. Один знак за пост, ротация 12.
# ══════════════════════════════════════════════════════════════════════════════

# Созвездия заданы в нормализованных координатах 0..1 (стилизованы, но узнаваемы).
ZODIAC = [
    {"ru": "Овен",     "sym": "♈", "dates": "21 марта — 19 апреля",   "element": "Огонь",  "ruler": "Марс",
     "stars": [(.15,.30),(.38,.22),(.60,.34),(.82,.55)], "edges": [(0,1),(1,2),(2,3)]},
    {"ru": "Телец",    "sym": "♉", "dates": "20 апреля — 20 мая",     "element": "Земля",  "ruler": "Венера",
     "stars": [(.20,.62),(.38,.50),(.52,.40),(.70,.30),(.85,.22),(.55,.60)], "edges": [(0,1),(1,2),(2,3),(3,4),(2,5)]},
    {"ru": "Близнецы", "sym": "♊", "dates": "21 мая — 20 июня",       "element": "Воздух", "ruler": "Меркурий",
     "stars": [(.30,.18),(.30,.80),(.68,.16),(.68,.82),(.30,.48),(.68,.50)], "edges": [(0,4),(4,1),(2,5),(5,3),(4,5)]},
    {"ru": "Рак",      "sym": "♋", "dates": "21 июня — 22 июля",      "element": "Вода",   "ruler": "Луна",
     "stars": [(.50,.22),(.50,.52),(.24,.72),(.78,.70)], "edges": [(0,1),(1,2),(1,3)]},
    {"ru": "Лев",      "sym": "♌", "dates": "23 июля — 22 августа",   "element": "Огонь",  "ruler": "Солнце",
     "stars": [(.18,.60),(.30,.42),(.46,.34),(.62,.40),(.74,.56),(.66,.74),(.40,.72)], "edges": [(0,1),(1,2),(2,3),(3,4),(4,5),(5,6),(6,0)]},
    {"ru": "Дева",     "sym": "♍", "dates": "23 августа — 22 сентября","element": "Земля", "ruler": "Меркурий",
     "stars": [(.20,.28),(.36,.40),(.50,.52),(.64,.44),(.60,.68),(.52,.82)], "edges": [(0,1),(1,2),(2,3),(2,4),(4,5)]},
    {"ru": "Весы",     "sym": "♎", "dates": "23 сентября — 22 октября","element": "Воздух", "ruler": "Венера",
     "stars": [(.50,.20),(.28,.44),(.72,.44),(.40,.70),(.60,.70)], "edges": [(0,1),(0,2),(1,3),(2,4),(3,4)]},
    {"ru": "Скорпион", "sym": "♏", "dates": "23 октября — 21 ноября", "element": "Вода",   "ruler": "Плутон",
     "stars": [(.16,.30),(.30,.34),(.44,.40),(.56,.50),(.64,.64),(.60,.78),(.74,.82)], "edges": [(0,1),(1,2),(2,3),(3,4),(4,5),(5,6)]},
    {"ru": "Стрелец",  "sym": "♐", "dates": "22 ноября — 21 декабря", "element": "Огонь",  "ruler": "Юпитер",
     "stars": [(.24,.66),(.44,.54),(.44,.34),(.62,.44),(.62,.66),(.80,.30)], "edges": [(0,1),(1,2),(1,3),(3,4),(3,5)]},
    {"ru": "Козерог",  "sym": "♑", "dates": "22 декабря — 19 января", "element": "Земля",  "ruler": "Сатурн",
     "stars": [(.22,.34),(.44,.24),(.72,.40),(.60,.66),(.36,.72)], "edges": [(0,1),(1,2),(2,3),(3,4),(4,0)]},
    {"ru": "Водолей",  "sym": "♒", "dates": "20 января — 18 февраля", "element": "Воздух", "ruler": "Уран",
     "stars": [(.16,.40),(.30,.54),(.44,.40),(.58,.54),(.72,.40),(.84,.54)], "edges": [(0,1),(1,2),(2,3),(3,4),(4,5)]},
    {"ru": "Рыбы",     "sym": "♓", "dates": "19 февраля — 20 марта",  "element": "Вода",   "ruler": "Нептун",
     "stars": [(.18,.30),(.32,.42),(.48,.50),(.66,.44),(.82,.32),(.50,.72)], "edges": [(0,1),(1,2),(2,3),(3,4),(2,5)]},
]

MONTHS_RU = ["января","февраля","марта","апреля","мая","июня",
             "июля","августа","сентября","октября","ноября","декабря"]

def _today_ru() -> str:
    t = time.localtime(time.time() + 3 * 3600)  # МСК
    return f"{t.tm_mday} {MONTHS_RU[t.tm_mon - 1]}"

# ── РОТАЦИЯ ЗНАКОВ + ИСТОРИЯ ГОРОСКОПОВ ───────────────────────────────────────
_DATA_DIR = "/data" if os.path.isdir("/data") else os.path.dirname(os.path.abspath(__file__))
TARO_STATE_PATH   = os.path.join(_DATA_DIR, "taro_state.json")
TARO_HISTORY_PATH = os.path.join(_DATA_DIR, "taro_history.json")

def next_sign() -> dict:
    """Берём следующий знак по кругу и сохраняем позицию — 3 поста/день = 3 знака."""
    try:
        with open(TARO_STATE_PATH, encoding="utf-8") as f:
            idx = json.load(f).get("idx", 0)
    except Exception:
        idx = 0
    sign = ZODIAC[idx % 12]
    try:
        with open(TARO_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"idx": (idx + 1) % 12}, f)
    except Exception as e:
        log.warning(f"taro_state не сохранился: {e}")
    return sign

def load_taro_history():
    try:
        with open(TARO_HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_taro_history(sign_ru, phrase, text):
    hist = load_taro_history()
    hist.append({"date": time.strftime("%Y-%m-%d %H:%M"), "sign": sign_ru,
                 "phrase": phrase, "text": text[:200]})
    hist = hist[-24:]
    try:
        with open(TARO_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning(f"taro_history не сохранилась: {e}")

# ── CLAUDE: ГЕНЕРАЦИЯ ГОРОСКОПА ───────────────────────────────────────────────
TARO_SYSTEM = """Ты — астролог и редактор Telegram-канала «Оракул» (@orakul_app).
Пишешь дневной гороскоп для одного знака зодиака. Аудитория — русскоязычные 20-40 лет,
верят в знаки, ищут поддержку и лёгкое руководство к действию на день.
Тон: тёплый, образный, уверенный, современный — не приторный, без канцелярита и клише
вроде «звёзды советуют». Говоришь по-делу и по-доброму, как мудрый друг. Только по-русски.
Отвечай ПЛОСКИМ ТЕКСТОМ без Markdown: никаких #, **, *, ---, списков и таблиц.
Разделы помечай ровно так: === ТЕКСТ ПОСТА === и === ВИЗУАЛ === — не заменяй эти маркеры."""

def generate_horoscope(sign: dict):
    hist_block = ""
    history = load_taro_history()
    same = [h for h in history if h.get("sign") == sign["ru"]][-2:]
    if same:
        prev = "\n".join(f"- [{h['date']}] {h['phrase']}: {h['text']}" for h in same)
        hist_block = (f"Прошлые гороскопы для «{sign['ru']}» (НЕ повторяй мысли, фразы и советы — дай свежий угол):\n{prev}\n\n")

    today = _today_ru()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=900,
        system=TARO_SYSTEM,
        messages=[{"role": "user", "content": f"""{hist_block}Знак: {sign['ru']} ({sign['element']}, управитель {sign['ruler']}).
Дата: сегодня, {today}.

Напиши гороскоп на сегодня для этого знака и данные для карточки-картинки.

=== ТЕКСТ ПОСТА ===
♈ {sign['ru']} — гороскоп на {today}
(замени эмодзи на подходящий знаку; заголовок оставь таким по смыслу)

[3 живых предложения: настроение дня, дела и отношения, на что опереться. Конкретно и тепло, без общих слов, суммарно до 280 символов.]

Совет дня: [одна ёмкая фраза]

=== ВИЗУАЛ ===
ФРАЗА: [ударная фраза дня, 2-4 слова, без точки — крупный акцент на карточке]
КАРТА: [суть прогноза для картинки, 2 коротких предложения, до 140 символов]
ЛЮБОВЬ: [одно слово — настрой в отношениях сегодня]
ДЕЛА: [одно слово — про работу и деньги]
НАСТРОЕНИЕ: [одно слово — общий тонус дня]

=== ВСЕ ЗНАКИ ===
[12 строк — по одной на каждый знак по порядку от Овна до Рыб, формат строго:
♈ Овен — подсказка дня в 3-4 словах
Каждая строка до 42 символов, без точки в конце, символ знака в начале строки.]

Строго три раздела."""}]
    )
    return _parse_horoscope(resp.content[0].text.strip(), sign)

def _parse_horoscope(raw: str, sign: dict):
    m = re.search(r"^\W*={0,3}\s*ВИЗУАЛ\s*={0,3}\s*$", raw, re.MULTILINE)
    if not m:
        m = re.search(r"^\W*ФРАЗА", raw, re.MULTILINE)
    if m:
        post_text, vis_text = raw[:m.start()], raw[m.start():]
    else:
        post_text, vis_text = raw, ""

    post_text = re.sub(r"^\W*={0,3}\s*ТЕКСТ ПОСТА\s*={0,3}\s*$", "", post_text, flags=re.MULTILINE)
    post_text = _strip_md(post_text)[:1024]

    def ex(key, default):
        mm = re.search(rf"^[\*#\s]*{key}[\*\s]*:[\*\s]*(.+)$", vis_text, re.MULTILINE)
        return mm.group(1).strip(" *") if mm else default

    vis = {
        "phrase": ex("ФРАЗА",      "День силы"),
        "card":   ex("КАРТА",      "Сегодня стоит довериться интуиции и сделать первый шаг."),
        "love":   ex("ЛЮБОВЬ",     "тепло"),
        "work":   ex("ДЕЛА",       "рост"),
        "mood":   ex("НАСТРОЕНИЕ", "ясность"),
    }
    # Однострочники всех 12 знаков (раздел ВСЕ ЗНАКИ идёт после ВИЗУАЛ → лежит в vis_text)
    sign_lines = [l.strip()[:46] for l in
                  re.findall(r"^[♈♉♊♋♌♍♎♏♐♑♒♓][^\n]{3,60}$", vis_text, re.MULTILINE)][:12]
    return post_text, vis, sign_lines

# ── ПРЕМИАЛЬНАЯ КАРТОЧКА ЗНАКА (без AI-фона — чистый дизайн) ───────────────────
CARD_W, CARD_H = 1080, 1350

# Палитры по стихии знака — акцент и градиент меняются, текстовые цвета едины
ELEMENT_PALETTES = {
    "Огонь":  {"top": (22, 9, 18),  "bot": (46, 18, 28),  "accent": (242, 166, 110),
               "hi": (255, 210, 160), "glow": (200, 80, 66)},
    "Земля":  {"top": (9, 14, 12),  "bot": (20, 34, 27),  "accent": (196, 182, 112),
               "hi": (230, 216, 152), "glow": (74, 128, 84)},
    "Воздух": {"top": (12, 12, 27), "bot": (26, 30, 56),  "accent": (168, 190, 235),
               "hi": (214, 228, 255), "glow": (100, 120, 200)},
    "Вода":   {"top": (8, 12, 26),  "bot": (15, 28, 52),  "accent": (118, 190, 212),
               "hi": (168, 228, 240), "glow": (58, 118, 178)},
}

# Ротация CTA — и в подписи, и на карточке (одна фраза на весь день)
CTAS_CAPTION = [
    "Полный личный расклад Таро — в приложении",
    "Задай свой вопрос картам — в приложении",
    "Твой личный прогноз уже ждёт — в приложении",
    "Открой свой расклад дня — в приложении",
]
CTAS_CARD = [
    "Личный расклад Таро — в приложении  →",
    "Задай свой вопрос картам  →",
    "Твой полный прогноз — в Оракуле  →",
    "Открой свой расклад дня  →",
]

def _doy() -> int:
    return int(time.strftime("%j"))

def _cta_caption() -> str:
    return CTAS_CAPTION[_doy() % len(CTAS_CAPTION)]

def _cta_card() -> str:
    return CTAS_CARD[_doy() % len(CTAS_CARD)]

def _sym_font(size):
    for p in SYM_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return _fnt(REG_PATHS, size)

def _ctext(draw, cx, y, text, font, fill, stroke=0, sfill=(0,0,0)):
    w = draw.textbbox((0, 0), text, font=font)[2]
    draw.text((cx - w / 2, y), text, font=font, fill=fill,
              stroke_width=stroke, stroke_fill=sfill)

def _draw_star(d, x, y, r, fill):
    """Четырёхлучевая звезда-блик."""
    d.polygon([(x, y - r), (x + r*0.28, y - r*0.28), (x + r, y),
               (x + r*0.28, y + r*0.28), (x, y + r), (x - r*0.28, y + r*0.28),
               (x - r, y), (x - r*0.28, y - r*0.28)], fill=fill)

def draw_horoscope_card(sign: dict, vis: dict) -> bytes:
    W, H = CARD_W, CARD_H
    pal    = ELEMENT_PALETTES[sign["element"]]
    z_idx  = next(i for i, s in enumerate(ZODIAC) if s["ru"] == sign["ru"])
    style  = (_doy() + z_idx) % 3           # 0 орбиты · 1 лучи · 2 горизонт
    seed   = int(time.strftime("%Y%m%d")) + z_idx  # каждый день фон другой
    GOLD   = pal["accent"]
    GOLD_HI= pal["hi"]
    WHT    = (245, 243, 250)
    MUTED  = (168, 162, 196)
    FAINT  = (120, 116, 150)
    SEP    = tuple(int(c * 0.38) for c in GOLD)
    BLK    = (0, 0, 0)

    # Фон — вертикальный космический градиент в палитре стихии
    img = Image.new("RGB", (W, H))
    px = img.load()
    top, bot = pal["top"], pal["bot"]
    for yy in range(H):
        t = yy / H
        px_row = (int(top[0] + (bot[0]-top[0])*t),
                  int(top[1] + (bot[1]-top[1])*t),
                  int(top[2] + (bot[2]-top[2])*t))
        for xx in range(W):
            px[xx, yy] = px_row
    img = img.convert("RGBA")

    # Радиальное свечение под созвездием + туманности (позиции меняются день ото дня)
    glow = Image.new("RGBA", (W, H), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    gcx, gcy = W//2, 340
    gc = pal["glow"]
    for r, a in [(430, 10), (330, 14), (230, 18), (140, 24)]:
        gd.ellipse([gcx-r, gcy-r, gcx+r, gcy+r], fill=gc + (a,))
    nrnd = random.Random(seed)
    for _ in range(2):
        bx, by = nrnd.randint(120, W-120), nrnd.randint(560, H-320)
        for r, a in [(240, 7), (160, 10), (100, 13)]:
            gd.ellipse([bx-r, by-r, bx+r, by+r], fill=gc + (a,))
    img = Image.alpha_composite(img, glow)

    draw = ImageDraw.Draw(img)
    rnd = random.Random(seed)  # звёздная пыль своя на каждый день

    # Звёздная пыль
    for _ in range(150):
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        s = rnd.random()
        rad = 1 if s < .7 else 2
        a = rnd.randint(30, 150)
        col = GOLD if s > .93 else (230, 230, 250)
        draw.ellipse([x-rad, y-rad, x+rad, y+rad], fill=col + (a,))

    # ── Стилевые вариации фона (меняются по дням — лента не повторяется) ──────
    if style == 1:
        # Лучи света из зенита
        deco = Image.new("RGBA", (W, H), (0,0,0,0))
        dd = ImageDraw.Draw(deco)
        for i in range(-3, 4):
            x_top = W//2 + i * 60
            x_bot = W//2 + i * 340
            dd.polygon([(x_top-14, -40), (x_top+14, -40), (x_bot+52, 760), (x_bot-52, 760)],
                       fill=GOLD + (9,))
        img = Image.alpha_composite(img, deco)
        draw = ImageDraw.Draw(img)
    elif style == 2:
        # Дуга-горизонт планеты внизу
        deco = Image.new("RGBA", (W, H), (0,0,0,0))
        dd = ImageDraw.Draw(deco)
        cy_p, r_p = H + 620, 780
        dd.ellipse([W//2-r_p, cy_p-r_p, W//2+r_p, cy_p+r_p], fill=tuple(int(c*0.55) for c in pal["bot"]) + (160,))
        for extra, a in [(0, 90), (10, 40), (22, 18)]:
            dd.ellipse([W//2-r_p-extra, cy_p-r_p-extra, W//2+r_p+extra, cy_p+r_p+extra],
                       outline=GOLD + (a,), width=2)
        img = Image.alpha_composite(img, deco)
        draw = ImageDraw.Draw(img)

    # ── Верхний тег + дата ────────────────────────────────────────────────────
    f_tag  = _fnt(REG_PATHS, 27)
    f_date = _fnt(REG_PATHS, 27)
    draw.text((70, 58), "ГОРОСКОП ДНЯ", font=f_tag, fill=GOLD)
    dt = _today_ru()
    dw = draw.textbbox((0,0), dt, font=f_date)[2]
    draw.text((W-70-dw, 58), dt, font=f_date, fill=MUTED)
    draw.line([(70, 104), (W-70, 104)], fill=SEP, width=1)

    # ── Созвездие + символ знака ──────────────────────────────────────────────
    box_x, box_y, box_w, box_h = 250, 150, 580, 380
    pts = [(box_x + sx*box_w, box_y + sy*box_h) for sx, sy in sign["stars"]]
    for a, b in sign["edges"]:
        draw.line([pts[a], pts[b]], fill=(GOLD[0], GOLD[1], GOLD[2], 90), width=2)
    for (x, y) in pts:
        draw.ellipse([x-14, y-14, x+14, y+14], fill=(GOLD[0], GOLD[1], GOLD[2], 40))
        _draw_star(draw, x, y, 8, GOLD_HI)

    # Круг-ореол с символом знака в центре
    ox, oy, orad = W//2, 340, 96
    for r, a in [(orad+34, 40), (orad+18, 70), (orad, 120)]:
        draw.ellipse([ox-r, oy-r, ox+r, oy+r], outline=(GOLD[0], GOLD[1], GOLD[2], a), width=2)
    draw.ellipse([ox-orad, oy-orad, ox+orad, oy+orad], fill=(18, 13, 34, 220))
    draw.ellipse([ox-orad, oy-orad, ox+orad, oy+orad], outline=GOLD, width=3)
    f_sym = _sym_font(120)
    sb = draw.textbbox((0,0), sign["sym"], font=f_sym)
    draw.text((ox-(sb[2]-sb[0])/2 - sb[0], oy-(sb[3]-sb[1])/2 - sb[1]),
              sign["sym"], font=f_sym, fill=GOLD_HI)

    # ── Имя знака + даты + стихия ─────────────────────────────────────────────
    y = 500
    f_name = _fnt(BOLD_PATHS, 96)
    _ctext(draw, W//2, y, sign["ru"].upper(), f_name, WHT, stroke=1, sfill=BLK)
    y += 118
    f_dates = _fnt(REG_PATHS, 31)
    _ctext(draw, W//2, y, sign["dates"], f_dates, MUTED)
    y += 46
    f_meta = _fnt(REG_PATHS, 25)
    _ctext(draw, W//2, y, f"{sign['element']}  ·  управитель {sign['ruler']}", f_meta, GOLD)
    y += 60

    # Тонкий орнамент-разделитель
    cx = W//2
    draw.line([(cx-160, y), (cx-24, y)], fill=SEP, width=1)
    draw.line([(cx+24, y), (cx+160, y)], fill=SEP, width=1)
    _draw_star(draw, cx, y, 7, GOLD)
    y += 34

    # ── Фраза дня ─────────────────────────────────────────────────────────────
    f_phrase = _fnt(BOLD_PATHS, 46)
    for line in _wrap(draw, vis["phrase"], f_phrase, W-200)[:2]:
        _ctext(draw, W//2, y, line, f_phrase, GOLD_HI)
        y += 58
    y += 18

    # ── Суть прогноза ─────────────────────────────────────────────────────────
    f_card = _fnt(REG_PATHS, 34)
    for line in _wrap(draw, vis["card"], f_card, W-200)[:4]:
        _ctext(draw, W//2, y, line, f_card, WHT)
        y += 48

    # ── Три блока: любовь / дела / настроение ─────────────────────────────────
    by = 1120
    draw.line([(70, by-30), (W-70, by-30)], fill=SEP, width=1)
    cols = [("ЛЮБОВЬ", vis["love"]), ("ДЕЛА", vis["work"]), ("НАСТРОЕНИЕ", vis["mood"])]
    f_bl = _fnt(REG_PATHS, 21)
    f_bv = _fnt(BOLD_PATHS, 32)
    seg = (W - 140) / 3
    for i, (lbl, val) in enumerate(cols):
        cxx = 70 + seg*i + seg/2
        _ctext(draw, cxx, by, lbl, f_bl, FAINT)
        _ctext(draw, cxx, by+30, val.capitalize(), f_bv, GOLD_HI)
        if i < 2:
            xln = 70 + seg*(i+1)
            draw.line([(xln, by), (xln, by+66)], fill=SEP, width=1)

    # ── CTA-плашка ────────────────────────────────────────────────────────────
    cy0, cy1 = H-120, H-52
    draw.rounded_rectangle([70, cy0, W-70, cy1], radius=16,
                           fill=(24, 17, 46, 255), outline=GOLD, width=2)
    f_cta = _fnt(BOLD_PATHS, 30)
    _ctext(draw, W//2, (cy0+cy1)//2 - 20, _cta_card(), f_cta, GOLD_HI)
    f_h = _fnt(REG_PATHS, 24)
    _ctext(draw, W//2, H-40, "@orakul_app", f_h, MUTED)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()

# ── JOB: ГОРОСКОП ─────────────────────────────────────────────────────────────
def job_taro():
    log.info("Запуск гороскопа...")
    try:
        sign = next_sign()
        log.info(f"Знак: {sign['ru']}")
        post_text, vis, sign_lines = generate_horoscope(sign)
        image_bytes = draw_horoscope_card(sign, vis)
        parts = [post_text]
        if len(sign_lines) == 12:
            parts.append("Сегодня у знаков:\n" + "\n".join(sign_lines))
        parts.append(f"Перешли тому, кто {sign['ru']} {sign['sym']}")
        caption = _assemble_caption(parts)
        result = send_photo(caption, image_bytes, channel=TARO_CHANNEL,
                            button=("🔮 Мой полный расклад — бесплатно", taro_link("goroskop")))
        if result.get("ok"):
            log.info(f"Гороскоп «{sign['ru']}» опубликован!")
            save_taro_history(sign["ru"], vis["phrase"], vis["card"])
        else:
            log.error(f"Telegram (таро): {result}")
    except Exception:
        log.exception("Ошибка в job_taro()")

# ══════════════════════════════════════════════════════════════════════════════
# РУБРИКА «КАРТА ДНЯ» — Старшие арканы Таро (14:30 МСК)
# ══════════════════════════════════════════════════════════════════════════════
ARCANA = [
    ("Шут", "0", "свобода · начало · доверие миру"),
    ("Маг", "I", "воля · действие · сила намерения"),
    ("Верховная Жрица", "II", "интуиция · тайна · внутренний голос"),
    ("Императрица", "III", "изобилие · забота · рост"),
    ("Император", "IV", "порядок · опора · ответственность"),
    ("Иерофант", "V", "традиция · учитель · смысл"),
    ("Влюблённые", "VI", "выбор · союз · сердце"),
    ("Колесница", "VII", "движение · победа · контроль"),
    ("Сила", "VIII", "мягкая сила · терпение · достоинство"),
    ("Отшельник", "IX", "тишина · поиск · мудрость"),
    ("Колесо Фортуны", "X", "поворот · шанс · перемены"),
    ("Справедливость", "XI", "равновесие · честность · закон причин"),
    ("Повешенный", "XII", "пауза · другой взгляд · отпускание"),
    ("Смерть", "XIII", "завершение · обновление · трансформация"),
    ("Умеренность", "XIV", "баланс · исцеление · золотая середина"),
    ("Дьявол", "XV", "соблазн · привязки · честность с собой"),
    ("Башня", "XVI", "освобождение · правда · внезапность"),
    ("Звезда", "XVII", "надежда · вдохновение · путеводный свет"),
    ("Луна", "XVIII", "интуиция · сны · неизвестность"),
    ("Солнце", "XIX", "радость · успех · ясность"),
    ("Суд", "XX", "пробуждение · итог · второй шанс"),
    ("Мир", "XXI", "целостность · завершение цикла · гармония"),
]
CARD_STATE_PATH = os.path.join(_DATA_DIR, "card_state.json")

def next_arcana():
    """Случайная карта, но без повторов последних 12."""
    try:
        with open(CARD_STATE_PATH, encoding="utf-8") as f:
            recent = json.load(f).get("recent", [])
    except Exception:
        recent = []
    pool = [i for i in range(len(ARCANA)) if i not in recent] or list(range(len(ARCANA)))
    idx = random.choice(pool)
    recent = (recent + [idx])[-12:]
    try:
        with open(CARD_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"recent": recent}, f)
    except Exception as e:
        log.warning(f"card_state не сохранился: {e}")
    return idx

def _split_vis(raw: str, first_key: str):
    m = re.search(r"^\W*={0,3}\s*ВИЗУАЛ\s*={0,3}\s*$", raw, re.MULTILINE)
    if not m:
        m = re.search(rf"^\W*{first_key}", raw, re.MULTILINE)
    if m:
        post_text, vis_text = raw[:m.start()], raw[m.start():]
    else:
        post_text, vis_text = raw, ""
    post_text = re.sub(r"^\W*={0,3}\s*ТЕКСТ ПОСТА\s*={0,3}\s*$", "", post_text, flags=re.MULTILINE)
    return _strip_md(post_text)[:1024], vis_text

def _vis_ex(vis_text: str, key: str, default: str) -> str:
    mm = re.search(rf"^[\*#\s]*{key}[\*\s]*:[\*\s]*(.+)$", vis_text, re.MULTILINE)
    return mm.group(1).strip(" *") if mm else default

def generate_card_post(name, roman, keywords):
    today = _today_ru()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=TARO_SYSTEM,
        messages=[{"role": "user", "content": f"""Рубрика «Карта дня». Сегодня, {today}, выпала карта «{name}» (аркан {roman}).
Ключевые слова карты: {keywords}.

Напиши пост и данные для карточки-картинки.

=== ТЕКСТ ПОСТА ===
🃏 Карта дня — {name}

[3-4 предложения: что эта карта значит именно сегодня: какая энергия у дня, в чём шанс, от чего предостерегает. Живо и конкретно, без эзотерического тумана.]

Совет дня: [одна ёмкая фраза]

🔮 {_cta_caption()}

=== ВИЗУАЛ ===
ФРАЗА: [ударная суть карты сегодня, 2-4 слова, без точки]
ТРАКТОВКА: [суть для карточки, 2 коротких предложения, до 130 символов]
СОВЕТ: [короткий совет одной фразой, до 60 символов]

Строго два раздела."""}]
    )
    raw = resp.content[0].text.strip()
    post_text, vis_text = _split_vis(raw, "ФРАЗА")
    vis = {
        "phrase": _vis_ex(vis_text, "ФРАЗА",     "Знак дня"),
        "card":   _vis_ex(vis_text, "ТРАКТОВКА", "Карта напоминает: у каждого дня есть своя подсказка."),
        "advice": _vis_ex(vis_text, "СОВЕТ",     "Доверься процессу"),
    }
    return post_text, vis

def draw_tarot_card(name, roman, keywords, vis) -> bytes:
    W, H = CARD_W, CARD_H
    GOLD    = (231, 194, 120)
    GOLD_HI = (255, 224, 158)
    WHT     = (245, 243, 250)
    MUTED   = (172, 164, 200)
    FAINT   = (122, 116, 152)
    SEP     = (70, 58, 110)
    seed    = int(time.strftime("%Y%m%d")) + 77

    # Фон — королевский пурпур
    img = Image.new("RGB", (W, H))
    px = img.load()
    top, bot = (15, 8, 27), (36, 19, 56)
    for yy in range(H):
        t = yy / H
        row = (int(top[0]+(bot[0]-top[0])*t), int(top[1]+(bot[1]-top[1])*t), int(top[2]+(bot[2]-top[2])*t))
        for xx in range(W):
            px[xx, yy] = row
    img = img.convert("RGBA")

    glow = Image.new("RGBA", (W, H), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    for r, a in [(500, 8), (380, 12), (260, 16)]:
        gd.ellipse([W//2-r, 520-r, W//2+r, 520+r], fill=(150, 100, 220, a))
    img = Image.alpha_composite(img, glow)

    draw = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for _ in range(130):
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        s = rnd.random()
        rad = 1 if s < .7 else 2
        col = GOLD if s > .93 else (232, 228, 250)
        draw.ellipse([x-rad, y-rad, x+rad, y+rad], fill=col + (rnd.randint(30, 140),))

    # Шапка
    f_tag = _fnt(REG_PATHS, 27)
    draw.text((70, 58), "КАРТА ДНЯ", font=f_tag, fill=GOLD)
    dt = _today_ru()
    dw = draw.textbbox((0,0), dt, font=f_tag)[2]
    draw.text((W-70-dw, 58), dt, font=f_tag, fill=MUTED)
    draw.line([(70, 104), (W-70, 104)], fill=SEP, width=1)

    # ── Карта-рамка ───────────────────────────────────────────────────────────
    cx0, cy0, cx1, cy1 = W//2-280, 150, W//2+280, 880
    draw.rounded_rectangle([cx0, cy0, cx1, cy1], radius=26, fill=(22, 13, 40, 235),
                           outline=GOLD, width=3)
    draw.rounded_rectangle([cx0+14, cy0+14, cx1-14, cy1-14], radius=18,
                           outline=(GOLD[0], GOLD[1], GOLD[2], 90), width=1)
    # Уголки-звёзды
    for sx, sy in [(cx0+40, cy0+40), (cx1-40, cy0+40), (cx0+40, cy1-40), (cx1-40, cy1-40)]:
        _draw_star(draw, sx, sy, 9, GOLD)

    _ctext(draw, W//2, cy0+52, "СТАРШИЙ АРКАН", _fnt(REG_PATHS, 24), FAINT)

    # Римский номер в ореоле
    ox, oy = W//2, cy0 + 300
    for r, a in [(150, 36), (122, 66), (98, 110)]:
        draw.ellipse([ox-r, oy-r, ox+r, oy+r], outline=(GOLD[0], GOLD[1], GOLD[2], a), width=2)
    f_rom = _fnt(BOLD_PATHS, 128 if len(roman) < 3 else 96)
    rb = draw.textbbox((0,0), roman, font=f_rom)
    draw.text((ox-(rb[2]-rb[0])/2 - rb[0], oy-(rb[3]-rb[1])/2 - rb[1]), roman, font=f_rom, fill=GOLD_HI)
    for ang in (0, 90, 180, 270):
        sx = ox + int(170 * math.cos(math.radians(ang)))
        sy = oy + int(170 * math.sin(math.radians(ang)))
        _draw_star(draw, sx, sy, 6, GOLD)

    # Имя карты (автоподбор размера под рамку)
    name_up = name.upper()
    n_size = _fit_size(draw, name_up, BOLD_PATHS, 64, 460, min_size=34)
    f_name = _fnt(BOLD_PATHS, n_size)
    _ctext(draw, W//2, cy0+474, name_up, f_name, WHT, stroke=1, sfill=(0,0,0))

    # Ключевые слова внизу рамки
    f_kw = _fnt(REG_PATHS, 25)
    kw_y = cy1 - 130
    draw.line([(cx0+60, kw_y-24), (cx1-60, kw_y-24)], fill=SEP, width=1)
    for line in _wrap(draw, keywords, f_kw, 460)[:2]:
        _ctext(draw, W//2, kw_y, line, f_kw, MUTED)
        kw_y += 36

    # ── Фраза + трактовка под картой ──────────────────────────────────────────
    y = 930
    f_phrase = _fnt(BOLD_PATHS, 44)
    for line in _wrap(draw, vis["phrase"], f_phrase, W-200)[:1]:
        _ctext(draw, W//2, y, line, f_phrase, GOLD_HI)
        y += 58
    f_card = _fnt(REG_PATHS, 32)
    for line in _wrap(draw, vis["card"], f_card, W-180)[:3]:
        _ctext(draw, W//2, y, line, f_card, WHT)
        y += 44

    # Совет
    f_adv = _fnt(REG_PATHS, 27)
    _ctext(draw, W//2, 1148, "Совет: " + vis["advice"], f_adv, GOLD)

    # CTA
    cy0b, cy1b = H-120, H-52
    draw.rounded_rectangle([70, cy0b, W-70, cy1b], radius=16, fill=(26, 16, 48, 255),
                           outline=GOLD, width=2)
    _ctext(draw, W//2, (cy0b+cy1b)//2 - 20, _cta_card(), _fnt(BOLD_PATHS, 30), GOLD_HI)
    _ctext(draw, W//2, H-40, "@orakul_app", _fnt(REG_PATHS, 24), MUTED)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()

def job_card():
    log.info("Запуск «Карты дня»...")
    try:
        idx = next_arcana()
        name, roman, keywords = ARCANA[idx]
        log.info(f"Карта: {name} ({roman})")
        post_text, vis = generate_card_post(name, roman, keywords)
        image_bytes = draw_tarot_card(name, roman, keywords, vis)
        caption = _assemble_caption([post_text, "🔖 Сохрани, чтобы вернуться к карте вечером"])
        result = send_photo(caption, image_bytes, channel=TARO_CHANNEL,
                            button=("🃏 Вытянуть свою карту", taro_link("karta")))
        if result.get("ok"):
            log.info(f"«Карта дня — {name}» опубликована!")
        else:
            log.error(f"Telegram (карта): {result}")
    except Exception:
        log.exception("Ошибка в job_card()")

# ══════════════════════════════════════════════════════════════════════════════
# РУБРИКА «ВЕЧЕРНИЙ ОРАКУЛ» — по дню недели, с настоящей фазой Луны (20:30 МСК)
# ══════════════════════════════════════════════════════════════════════════════
EVENING_RUBRICS = {
    0: ("Число дня",        "нумерология сегодняшней даты: раскрой смысл числа дня {day_num} — что оно несёт и как этим воспользоваться вечером и завтра"),
    1: ("Ритуал вечера",    "короткий домашний ритуал на вечер (5-10 минут): свеча, вода, бумага, дыхание — что-то простое и красивое, шаг за шагом одним абзацем"),
    2: ("Лунный совет",     "совет вечера, опирающийся на текущую фазу Луны ({moon}): что эта фаза поддерживает, что лучше отложить"),
    3: ("Вопрос себе",      "один глубокий вопрос для вечерней рефлексии и 2-3 предложения, почему он важен именно сейчас"),
    4: ("Аффирмация",       "тёплая сильная аффирмация на завтра и выходные + короткое пояснение, как с ней работать"),
    5: ("Энергия выходных", "прогноз-настрой на выходные: какая энергия у этих дней, чему их посвятить"),
    6: ("Настрой на неделю","настрой и главный фокус на предстоящую неделю: с какой мыслью в неё войти"),
}
EVEN_HISTORY_PATH = os.path.join(_DATA_DIR, "evening_history.json")

def moon_phase():
    """Фаза Луны: доля цикла 0..1 (0 — новолуние, 0.5 — полнолуние) + имя по-русски."""
    days = (time.time() - 947182440) / 86400  # новолуние 2000-01-06 18:14 UTC
    f = (days / 29.530588853) % 1.0
    if f < .033 or f > .967: name = "новолуние"
    elif f < .22:  name = "растущий серп"
    elif f < .28:  name = "первая четверть"
    elif f < .47:  name = "растущая Луна"
    elif f < .53:  name = "полнолуние"
    elif f < .72:  name = "убывающая Луна"
    elif f < .78:  name = "последняя четверть"
    else:          name = "убывающий серп"
    return f, name

def _day_number() -> int:
    t = time.localtime(time.time() + 3 * 3600)
    s = sum(int(d) for d in f"{t.tm_mday:02d}{t.tm_mon:02d}{t.tm_year}")
    while s > 9 and s not in (11, 22):
        s = sum(int(d) for d in str(s))
    return s

def _draw_moon(draw, cx, cy, r, frac):
    """Луна с настоящей фазой: светлая часть по терминатору."""
    LC, DC = (222, 226, 240), (40, 44, 66)
    for rr, a in [(r+60, 14), (r+34, 26), (r+14, 44)]:
        draw.ellipse([cx-rr, cy-rr, cx+rr, cy+rr], outline=(210, 216, 240, a), width=2)
    bbox = [cx-r, cy-r, cx+r, cy+r]
    draw.ellipse(bbox, fill=DC)
    waxing = frac < 0.5
    ill = (1 - math.cos(2 * math.pi * frac)) / 2
    if ill > 0.02:
        # светлая половина: у растущей — правая, у убывающей — левая
        draw.pieslice(bbox, -90 if waxing else 90, 90 if waxing else 270, fill=LC)
        w = abs(math.cos(2 * math.pi * frac)) * r
        mid_fill = DC if ill < 0.5 else LC
        draw.ellipse([cx-w, cy-r, cx+w, cy+r], fill=mid_fill)
    draw.ellipse(bbox, outline=(230, 234, 250), width=2)
    # Кратеры на светлой стороне — деликатно
    crnd = random.Random(7)
    for _ in range(5):
        a_ang = crnd.uniform(0, 2*math.pi); dist = crnd.uniform(.2, .75) * r
        mx, my = cx + dist*math.cos(a_ang), cy + dist*math.sin(a_ang)
        mr = crnd.randint(6, 14)
        draw.ellipse([mx-mr, my-mr, mx+mr, my+mr], fill=(205, 209, 226, 60))

def load_evening_history():
    try:
        with open(EVEN_HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_evening_history(rubric, phrase):
    hist = load_evening_history()
    hist.append({"date": time.strftime("%Y-%m-%d %H:%M"), "rubric": rubric, "phrase": phrase})
    hist = hist[-21:]
    try:
        with open(EVEN_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning(f"evening_history не сохранилась: {e}")

def generate_evening_post(rubric, task, meta_line):
    hist = [h for h in load_evening_history() if h.get("rubric") == rubric][-3:]
    hist_block = ""
    if hist:
        prev = "\n".join(f"- {h['phrase']}" for h in hist)
        hist_block = f"Прошлые выпуски рубрики (не повторяй их мысли и формулировки):\n{prev}\n\n"
    today = _today_ru()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=TARO_SYSTEM,
        messages=[{"role": "user", "content": f"""{hist_block}Вечерняя рубрика «{rubric}». Сегодня {today}. {meta_line}.
Задача: {task}

Напиши пост и данные для карточки.

=== ТЕКСТ ПОСТА ===
🌙 {rubric}

[3-4 предложения по задаче рубрики. Тёплый вечерний тон, без тумана.]

🔮 {_cta_caption()}

=== ВИЗУАЛ ===
ФРАЗА: [суть вечера, 2-4 слова, без точки]
ТЕКСТ: [главная мысль для карточки, 2 коротких предложения, до 130 символов]

Строго два раздела."""}]
    )
    raw = resp.content[0].text.strip()
    post_text, vis_text = _split_vis(raw, "ФРАЗА")
    vis = {
        "phrase": _vis_ex(vis_text, "ФРАЗА", "Тихий вечер"),
        "card":   _vis_ex(vis_text, "ТЕКСТ", "Вечер — время вернуть себе себя."),
    }
    return post_text, vis

def draw_evening_card(rubric, vis, meta_line) -> bytes:
    W, H = CARD_W, CARD_H
    SILVER    = (198, 208, 232)
    SILVER_HI = (236, 242, 255)
    WHT       = (245, 243, 250)
    MUTED     = (160, 166, 194)
    SEP       = (56, 62, 96)
    seed      = int(time.strftime("%Y%m%d")) + 500

    img = Image.new("RGB", (W, H))
    px = img.load()
    top, bot = (7, 9, 22), (17, 23, 48)
    for yy in range(H):
        t = yy / H
        row = (int(top[0]+(bot[0]-top[0])*t), int(top[1]+(bot[1]-top[1])*t), int(top[2]+(bot[2]-top[2])*t))
        for xx in range(W):
            px[xx, yy] = row
    img = img.convert("RGBA")

    glow = Image.new("RGBA", (W, H), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    for r, a in [(420, 10), (300, 15), (190, 22)]:
        gd.ellipse([W//2-r, 330-r, W//2+r, 330+r], fill=(120, 140, 210, a))
    img = Image.alpha_composite(img, glow)

    draw = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for _ in range(160):
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        s = rnd.random()
        rad = 1 if s < .75 else 2
        draw.ellipse([x-rad, y-rad, x+rad, y+rad],
                     fill=(228, 232, 250, rnd.randint(30, 150)))

    f_tag = _fnt(REG_PATHS, 27)
    draw.text((70, 58), rubric.upper(), font=f_tag, fill=SILVER)
    dt = _today_ru()
    dw = draw.textbbox((0,0), dt, font=f_tag)[2]
    draw.text((W-70-dw, 58), dt, font=f_tag, fill=MUTED)
    draw.line([(70, 104), (W-70, 104)], fill=SEP, width=1)

    # Луна с настоящей фазой
    frac, phase_name = moon_phase()
    _draw_moon(draw, W//2, 380, 150, frac)
    _ctext(draw, W//2, 586, meta_line, _fnt(REG_PATHS, 26), SILVER)

    # Орнамент
    cx = W//2
    oy = 652
    draw.line([(cx-160, oy), (cx-24, oy)], fill=SEP, width=1)
    draw.line([(cx+24, oy), (cx+160, oy)], fill=SEP, width=1)
    _draw_star(draw, cx, oy, 7, SILVER)

    # Текстовый блок центрируем между орнаментом и CTA — без «дыры» внизу
    f_phrase = _fnt(BOLD_PATHS, 56)
    f_card   = _fnt(REG_PATHS, 37)
    ph_lines = _wrap(draw, vis["phrase"], f_phrase, W-180)[:2]
    tx_lines = _wrap(draw, vis["card"], f_card, W-190)[:4]
    block_h = len(ph_lines)*72 + 30 + len(tx_lines)*52
    y = oy + 40 + max(0, (H - 170 - (oy + 40) - block_h) // 2)
    for line in ph_lines:
        _ctext(draw, W//2, y, line, f_phrase, SILVER_HI)
        y += 72
    y += 30
    for line in tx_lines:
        _ctext(draw, W//2, y, line, f_card, WHT)
        y += 52

    cy0, cy1 = H-120, H-52
    draw.rounded_rectangle([70, cy0, W-70, cy1], radius=16, fill=(18, 22, 44, 255),
                           outline=SILVER, width=2)
    _ctext(draw, W//2, (cy0+cy1)//2 - 20, _cta_card(), _fnt(BOLD_PATHS, 30), SILVER_HI)
    _ctext(draw, W//2, H-40, "@orakul_app", _fnt(REG_PATHS, 24), MUTED)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()

def job_evening():
    log.info("Запуск «Вечернего Оракула»...")
    try:
        wday = time.localtime(time.time() + 3 * 3600).tm_wday
        rubric, task_tpl = EVENING_RUBRICS[wday]
        frac, phase_name = moon_phase()
        day_num = _day_number()
        task = task_tpl.format(day_num=day_num, moon=phase_name)
        if wday == 0:
            meta_line = f"Число дня — {day_num} · Луна: {phase_name}"
        else:
            meta_line = f"Луна: {phase_name}"
        log.info(f"Рубрика: {rubric} ({meta_line})")
        post_text, vis = generate_evening_post(rubric, task, meta_line)
        image_bytes = draw_evening_card(rubric, vis, meta_line)
        result = send_photo(post_text, image_bytes, channel=TARO_CHANNEL,
                            button=("🔮 Личный расклад — бесплатно", taro_link("vecher")))
        if result.get("ok"):
            log.info(f"«{rubric}» опубликован!")
            save_evening_history(rubric, vis["phrase"])
        else:
            log.error(f"Telegram (вечер): {result}")
    except Exception:
        log.exception("Ошибка в job_evening()")

def _posted_today(path) -> bool:
    """Защита от дублей при редеплое: был ли уже пост сегодня (по файлу истории)."""
    try:
        with open(path, encoding="utf-8") as f:
            hist = json.load(f)
        return bool(hist) and hist[-1].get("date", "").startswith(time.strftime("%Y-%m-%d"))
    except Exception:
        return False

# ══════════════════════════════════════════════════════════════════════════════
# РУБРИКА «КРИПТО-УТРО» — цифры рынка из CoinGecko (08:00 МСК)
# ══════════════════════════════════════════════════════════════════════════════
DIGEST_COINS = [("bitcoin", "BTC"), ("ethereum", "ETH"),
                ("solana", "SOL"), ("the-open-network", "TON")]

def fetch_prices() -> dict:
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ",".join(cid for cid, _ in DIGEST_COINS),
                "vs_currencies": "usd", "include_24hr_change": "true"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()

def job_digest():
    log.info("Запуск «Крипто-утра»...")
    try:
        data = fetch_prices()
        lines, changes = [], {}
        for cid, tick in DIGEST_COINS:
            d = data.get(cid)
            if not d or "usd" not in d:
                continue
            ch = d.get("usd_24h_change") or 0.0
            changes[tick] = ch
            price = d["usd"]
            price_s = f"${price:,.0f}" if price >= 100 else f"${price:,.2f}"
            lines.append(f"{'▲' if ch >= 0 else '▼'} {tick}  {price_s}  ({ch:+.1f}%)")
        if not lines:
            log.warning("CoinGecko не отдал цены")
            return
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content":
                "Утренние цифры рынка за 24 часа:\n" + "\n".join(lines) +
                "\n\nНапиши 1-2 предложения: что эти цифры значат для рынка сегодня. "
                "Уверенно, без воды, плоским текстом, до 180 символов."}]
        )
        comment = _strip_md(resp.content[0].text.strip())[:220]
        btc_ch = changes.get("BTC", 0.0)
        vis = {
            "line1": "КРИПТО-УТРО",
            "line2": "РЫНОК РАСТЁТ" if btc_ch >= 0 else "РЫНОК КРАСНЫЙ",
            "desc":  comment,
            "s1v":   f"{btc_ch:+.1f}%", "s1l": "BTC 24ч",
            "s2v":   f"{changes.get('ETH', 0.0):+.1f}%", "s2l": "ETH 24ч",
            "prompt": "sunrise over futuristic financial city, bitcoin skyline",
        }
        image_bytes = create_image(vis)
        caption = f"☕ Крипто-утро · {_today_ru()}\n\n" + "\n".join(lines) + f"\n\n{comment}"
        result = send_photo(caption[:1024], image_bytes)
        if result.get("ok"):
            log.info("«Крипто-утро» опубликовано!")
        else:
            log.error(f"Telegram (дайджест): {result}")
    except Exception:
        log.exception("Ошибка в job_digest()")

# КРИПТА (@btreygolnik): 08:00 дайджест / 09:00 / 14:00 (+опрос) / 20:00 МСК
schedule.every().day.at("05:00").do(job_digest)
schedule.every().day.at("06:00").do(job)
schedule.every().day.at("11:00").do(job, with_poll=True)
schedule.every().day.at("17:00").do(job)
# ОРАКУЛ (@orakul_app): 09:30 гороскоп / 14:30 карта дня / 20:30 вечерний оракул МСК
schedule.every().day.at("06:30").do(job_taro)
schedule.every().day.at("11:30").do(job_card)
schedule.every().day.at("17:30").do(job_evening)

if __name__ == "__main__":
    log.info("Бот запущен: крипта (дайджест + 3 поста + опрос) + Оракул (3 рубрики)")
    log.info(f"Крипта → {CHANNEL} (08:00 дайджест / 09:00 / 14:00 / 20:00 МСК)")
    log.info(f"Оракул → {TARO_CHANNEL} (09:30 гороскоп / 14:30 карта / 20:30 вечер МСК)")
    log.info(f"CTA Оракула → {taro_link('goroskop')}")
    # При старте постим только если сегодня ещё не постили (редеплой ≠ дубль)
    if not _posted_today(HISTORY_PATH):
        job()
    if not _posted_today(TARO_HISTORY_PATH):
        job_taro()
    while True:
        schedule.run_pending()
        time.sleep(60)
