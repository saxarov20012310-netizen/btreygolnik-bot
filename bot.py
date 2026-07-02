#!/usr/bin/env python3
"""
Белый Треугольник | Автопилот
Telegram-бот для @btreygolnik — 3 поста в день автоматически.
Изображения: HTML-шаблон → Playwright Chromium → PNG
"""

import os, sys, io, re, time, random, logging
import schedule, feedparser, requests
from anthropic import Anthropic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("btbot")

# ── CONFIG (Railway env vars) ─────────────────────────────────────────────────
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHANNEL  = os.environ.get("CHANNEL_ID", "@btreygolnik")
ANT_KEY  = os.environ["ANTHROPIC_API_KEY"]
client   = Anthropic(api_key=ANT_KEY)

# ── RSS-ИСТОЧНИКИ ─────────────────────────────────────────────────────────────
FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://www.theblockcrypto.com/rss.xml",
    "https://cryptopotato.com/feed/",
]

def fetch_articles(n=8) -> list[dict]:
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

# ── ГЕНЕРАЦИЯ ПОСТА + ВИЗУАЛА (Claude Haiku) ──────────────────────────────────
SYSTEM_PROMPT = """Ты — редактор Telegram-канала «Белый треугольник | Братство» (@btreygolnik).
Аудитория: молодые маркетологи 18-28 лет, интересующиеся крипто-трендами,
growth hacking и product-led growth.
Стиль: уверенный, острый, без воды. Один конкретный инсайт — один пост.
Никогда не пиши «я думаю» или «возможно». Только факты и выводы."""

def generate_post(articles: list[dict]) -> tuple[str, dict]:
    """Возвращает (текст поста, словарь vis для картинки)"""
    news_block = "\n".join(
        f"[{i+1}] {a['title']}\n{a['summary']}" for i, a in enumerate(articles)
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1100,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"""Свежие новости:
{news_block}

Выбери ОДНУ самую взрывную новость и напиши пост + данные для визуала.

=== ТЕКСТ ПОСТА ===
[Эмодзи + цепляющий заголовок]

[3-4 предложения: суть + почему важно маркетологу]

Маркетологи, записывайте.

=== ВИЗУАЛ ===
СТРОКА1: [3-4 слова CAPS, главная мысль]
СТРОКА2: [2-3 слова CAPS, ударная фраза]
ОПИСАНИЕ: [одно предложение — суть новости]
ЦИФРА1: [конкретная метрика, например +900%]
МЕТКА1: [2-3 слова что означает цифра]
ЦИФРА2: [вторая метрика, например $160M]
МЕТКА2: [что означает вторая цифра]
ЦЕПОЧКА: [A → B → C → D, воронка или процесс из 4 шагов]

Строго два раздела. Без лишних объяснений."""}]
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
        "line1": ex("СТРОКА1",  "НОВЫЙ"),
        "line2": ex("СТРОКА2",  "КЕЙС"),
        "desc":  ex("ОПИСАНИЕ", "Тренд, который меняет правила игры"),
        "s1v":   ex("ЦИФРА1",   "+300%"),
        "s1l":   ex("МЕТКА1",   "рост охвата"),
        "s2v":   ex("ЦИФРА2",   "$50M"),
        "s2l":   ex("МЕТКА2",   "объём рынка"),
        "chain": ex("ЦЕПОЧКА",  "Идея → Контент → Трафик → Деньги"),
    }
    return post_text, vis

# ── HTML-ШАБЛОН ───────────────────────────────────────────────────────────────
def _build_html(vis: dict) -> str:
    def esc(s: str) -> str:
        return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    # Flywheel chain
    parts      = [x.strip() for x in re.split(r"→|->|—>|>", vis.get("chain",""))]
    chain_html = " <span class='arr'>→</span> ".join(
        f"<span class='ci'>{esc(p)}</span>" for p in parts if p
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset=\"UTF-8\">
<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">
<link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@300;400;700;900&display=swap\" rel=\"stylesheet\">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  width:1280px; height:720px;
  background:#05050F;
  font-family:'Inter','DejaVu Sans',system-ui,sans-serif;
  overflow:hidden; color:#EEF0F8;
  position:relative;
}}
.tri {{
  position:absolute; top:0; right:0;
  width:680px; height:720px;
  pointer-events:none; z-index:1;
}}
.content {{
  position:relative; z-index:10;
  padding:48px 64px;
  height:100%;
  display:flex; flex-direction:column;
  max-width:730px;
}}
.top-label {{
  font-size:11px; font-weight:400;
  color:#555870; letter-spacing:2.5px;
  text-transform:uppercase; margin-bottom:10px;
}}
.sep {{ height:1px; background:#12152A; margin-bottom:28px; }}
.headline {{ flex:1; }}
.h1 {{
  font-size:88px; font-weight:900;
  line-height:0.93; letter-spacing:-3px;
  color:#EEF0F8;
}}
.h2 {{
  font-size:88px; font-weight:900;
  line-height:0.93; letter-spacing:-3px;
  color:#00C3FF;
}}
.dots {{ font-size:24px; color:#00C3FF; margin-top:20px; }}
.desc {{
  font-size:18px; font-weight:400;
  color:#697082; margin-top:10px;
  line-height:1.55; max-width:580px;
}}
.stats {{
  display:flex; align-items:flex-start;
  border-top:1px solid #12152A;
  padding-top:20px; margin-top:20px; gap:0;
}}
.stat {{ padding-right:32px; }}
.stat + .stat {{ padding-left:32px; border-left:1px solid #12152A; }}
.sv {{
  font-size:54px; font-weight:900;
  line-height:1; color:#FFC400;
}}
.stat:last-child .sv {{ color:#EEF0F8; }}
.sl {{
  font-size:11px; font-weight:400;
  color:#555870; letter-spacing:1.5px;
  text-transform:uppercase; margin-top:5px;
}}
.chain {{
  font-size:13px; color:#555870;
  margin-top:14px; letter-spacing:0.4px;
}}
.ci {{ color:#AAB0C4; }}
.arr {{ color:#00C3FF; opacity:.65; }}
.footer {{
  border-top:1px solid #12152A;
  padding-top:14px; margin-top:16px;
  display:flex; justify-content:space-between; align-items:center;
}}
.handle {{ font-size:14px; font-weight:700; color:#00C3FF; letter-spacing:.5px; }}
.tagline {{ font-size:11px; color:#555870; letter-spacing:2px; text-transform:uppercase; }}
</style>
</head>
<body>
<svg class=\"tri\" viewBox=\"0 0 680 720\" xmlns=\"http://www.w3.org/2000/svg\">
  <defs>
    <radialGradient id=\"apexGlow\" cx=\"54%\" cy=\"0%\" r=\"40%\">
      <stop offset=\"0%\" stop-color=\"#00C3FF\" stop-opacity=\".45\"/>
      <stop offset=\"100%\" stop-color=\"#00C3FF\" stop-opacity=\"0\"/>
    </radialGradient>
    <filter id=\"blur\"><feGaussianBlur stdDeviation=\"22\"/></filter>
  </defs>
  <polygon points=\"370,0 0,720 680,720\" fill=\"rgba(255,255,255,0.028)\"/>
  <ellipse cx=\"370\" cy=\"40\" rx=\"140\" ry=\"90\" fill=\"url(#apexGlow)\" filter=\"url(#blur)\"/>
  <line x1=\"370\" y1=\"2\" x2=\"0\" y2=\"722\" stroke=\"rgba(220,228,255,.72)\" stroke-width=\"1.5\"/>
  <line x1=\"370\" y1=\"2\" x2=\"680\" y2=\"722\" stroke=\"rgba(220,228,255,.22)\" stroke-width=\"1\"/>
  <path d=\"M 310 0 A 60 60 0 0 1 430 0\" fill=\"none\" stroke=\"rgba(0,195,255,.55)\" stroke-width=\"1\"/>
  <path d=\"M 250 0 A 120 120 0 0 1 490 0\" fill=\"none\" stroke=\"rgba(0,195,255,.30)\" stroke-width=\"1\"/>
  <path d=\"M 190 0 A 180 180 0 0 1 550 0\" fill=\"none\" stroke=\"rgba(0,195,255,.16)\" stroke-width=\"1\"/>
  <circle cx=\"370\" cy=\"5\" r=\"5\" fill=\"#00CFFF\"/>
  <polygon points=\"572,592 540,648 604,648\" fill=\"rgba(0,195,255,.07)\" stroke=\"rgba(0,195,255,.55)\" stroke-width=\"1.2\"/>
</svg>
<div class=\"content\">
  <div class=\"top-label\">Белый Треугольник · Маркетинг</div>
  <div class=\"sep\"></div>
  <div class=\"headline\">
    <div class=\"h1\">{esc(vis['line1'])}</div>
    <div class=\"h2\">{esc(vis['line2'])}</div>
    <div class=\"dots\">...</div>
    <div class=\"desc\">{esc(vis['desc'])}</div>
  </div>
  <div class=\"stats\">
    <div class=\"stat\">
      <div class=\"sv\">{esc(vis['s1v'])}</div>
      <div class=\"sl\">{esc(vis['s1l'])}</div>
    </div>
    <div class=\"stat\">
      <div class=\"sv\">{esc(vis['s2v'])}</div>
      <div class=\"sl\">{esc(vis['s2l'])}</div>
    </div>
  </div>
  <div class=\"chain\">{chain_html}</div>
  <div class=\"footer\">
    <span class=\"handle\">@btreygolnik</span>
    <span class=\"tagline\">маркетинг без воды</span>
  </div>
</div>
</body>
</html>"""

def create_image(vis: dict) -> bytes:
    """HTML → Playwright Chromium → PNG bytes"""
    html = _build_html(vis)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            page    = browser.new_page(viewport={"width": 1280, "height": 720})
            page.set_content(html, wait_until="networkidle")
            img = page.screenshot(
                type="png",
                clip={"x": 0, "y": 0, "width": 1280, "height": 720}
            )
            browser.close()
        log.info("✓ Картинка создана через Playwright")
        return img
    except Exception as e:
        log.error(f"Playwright error: {e} — fallback PIL")
        from PIL import Image, ImageDraw
        img2 = Image.new("RGB", (1280, 720), (5, 5, 15))
        d    = ImageDraw.Draw(img2)
        d.text((64, 280), vis.get("line1", ""), fill=(238, 240, 248))
        d.text((64, 380), vis.get("line2", ""), fill=(0, 195, 255))
        d.text((64, 480), vis.get("desc",  ""), fill=(105, 110, 135))
        buf = io.BytesIO()
        img2.save(buf, "PNG")
        buf.seek(0)
        return buf.read()

def send_photo(caption: str, image_bytes: bytes) -> dict:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    r   = requests.post(
        url,
        data={"chat_id": CHANNEL, "caption": caption},
        files={"photo": ("post.png", image_bytes, "image/png")},
        timeout=30,
    )
    return r.json()

def job():
    log.info("🚀 Запуск поста...")
    try:
        articles = fetch_articles()
        if not articles:
            log.warning("Нет статей, пропускаю")
            return
        post_text, vis = generate_post(articles)
        log.info(f"✍️  Визуал: «{vis['line1']}» / «{vis['line2']}»")
        image_bytes = create_image(vis)
        result      = send_photo(post_text, image_bytes)
        if result.get("ok"):
            log.info("✅ Пост опубликован!")
        else:
            log.error(f"❌ Ошибка Telegram: {result}")
    except Exception:
        log.exception("Ошибка в job()")

schedule.every().day.at("06:00").do(job)
schedule.every().day.at("11:00").do(job)
schedule.every().day.at("17:00").do(job)

if __name__ == "__main__":
    log.info("🤖 Бот «Белый треугольник» запущен")
    log.info(f"📢 Канал: {CHANNEL}")
    log.info("🕐 Расписание: 09:00 / 14:00 / 20:00 МСК")
    job()
    while True:
        schedule.run_pending()
        time.sleep(60)
