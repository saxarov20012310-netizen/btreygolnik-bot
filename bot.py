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

client = Anthropic(api_key=ANT_KEY)

FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://www.theblockcrypto.com/rss.xml",
    "https://cryptopotato.com/feed/",
]

def _font(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()

def load_fonts():
    lato_black = ["/usr/share/fonts/truetype/lato/Lato-Black.ttf","/usr/share/fonts/truetype/lato/Lato-Bold.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    lato_light = ["/usr/share/fonts/truetype/lato/Lato-Light.ttf","/usr/share/fonts/truetype/lato/Lato-Regular.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    mono = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf","/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"]
    mono_reg = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf","/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"]
    return {"head": _font(lato_black,72), "sub": _font(lato_light,20), "tag": _font(mono_reg,12), "hand": _font(mono,14)}

FONTS = load_fonts()

def fetch_articles(n=8):
    articles = []
    random.shuffle(FEEDS)
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:3]:
                title = e.get("title","").strip()
                summary = re.sub(r"<[^>]+>","",e.get("summary",e.get("description",""))).strip()[:350]
                if title:
                    articles.append({"title":title,"summary":summary})
        except Exception as ex:
            log.warning(f"Feed error {url}: {ex}")
        if len(articles) >= n:
            break
    return articles[:n]

SYSTEM_PROMPT = """Ты редактор Telegram-канала Белый треугольник | Братство (@btreygolnik).
Аудитория: молодые маркетологи 18-28 лет, крипто-тренды, viral-кейсы, growth hacking.
Стиль: уверенный, острый, без воды. Один конкретный инсайт в каждом посте. Пиши как эксперт."""

def generate_post(articles):
    news_block = "\n".join(f"[{i+1}] {a['title']}\n{a['summary']}" for i,a in enumerate(articles))
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=900,
        system=SYSTEM_PROMPT,
        messages=[{"role":"user","content":f"Свежие новости:\n{news_block}\n\nНапиши пост: строка 1 эмодзи+заголовок, пустая, 3-4 предложения сути, пустая, вывод, Маркетологи записывайте. Без markdown. 150-220 слов."}]
    )
    full_text = resp.content[0].text.strip()
    lines = [l for l in full_text.split("\n") if l.strip()]
    raw_head = lines[0] if lines else "МАРКЕТИНГОВЫЙ КЕЙС"
    clean_head = re.sub(r"[^\w\s.,!?-]","",raw_head).strip()
    words = clean_head.split()
    if len(words) > 7:
        clean_head = " ".join(words[:7])
    body_lines = [l for l in lines[1:] if l.strip()]
    sub = (body_lines[0][:75]+"…") if body_lines and len(body_lines[0])>75 else (body_lines[0] if body_lines else "")
    return full_text, clean_head.upper(), sub

def wrap_text(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textbbox((0,0),test,font=font)[2] <= max_w:
            cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines

def create_image(headline, subtext=""):
    W,H = 1280,720
    BG=(5,5,9); ELEC=(0,195,255); DIM=(50,53,70); DTXT=(105,110,135); WHT=(238,240,248)
    img = Image.new("RGBA",(W,H),BG+(255,))
    gr = Image.new("RGBA",(W,H),(0,0,0,0))
    gd = ImageDraw.Draw(gr)
    for x in range(0,W+64,64): gd.line([(x,0),(x,H)],fill=(255,255,255,7))
    for y in range(0,H+64,64): gd.line([(0,y),(W,y)],fill=(255,255,255,7))
    img = Image.alpha_composite(img,gr)
    bm = Image.new("RGBA",(W,H),(0,0,0,0))
    bd = ImageDraw.Draw(bm)
    for i in range(90):
        a=max(0,20-i//4); o=i*3
        bd.line([(0,H-o),(W,H-W-o)],fill=(0,195,255,a))
    img = Image.alpha_composite(img,bm)
    apex,bl,br=(1068,50),(648,700),(1380,700)
    tr = Image.new("RGBA",(W,H),(0,0,0,0))
    td = ImageDraw.Draw(tr)
    td.polygon([apex,bl,br],fill=(255,255,255,16))
    td.line([apex,bl],fill=(225,230,255,200),width=2)
    td.line([apex,br],fill=(225,230,255,100),width=2)
    img = Image.alpha_composite(img,tr)
    sa = Image.new("RGBA",(W,H),(0,0,0,0))
    sd = ImageDraw.Draw(sa)
    pts=[(64,102),(28,170),(100,170)]
    sd.polygon(pts,fill=(0,195,255,20))
    sd.line(pts+[pts[0]],fill=(0,195,255,170),width=1)
    img = Image.alpha_composite(img,sa)
    ar = Image.new("RGBA",(W,H),(0,0,0,0))
    ad = ImageDraw.Draw(ar)
    cx,cy=apex
    for r,a in [(75,55),(145,36),(225,22),(315,12)]:
        ad.arc([cx-r,cy-r,cx+r,cy+r],125,218,fill=(0,195,255,a))
    img = Image.alpha_composite(img,ar)
    gl = Image.new("RGBA",(W,H),(0,0,0,0))
    gd2 = ImageDraw.Draw(gl)
    for r,a in [(24,10),(15,28),(8,65),(4,175)]:
        gd2.ellipse([cx-r,cy-r,cx+r,cy+r],fill=(0,215,255,a))
    img = Image.alpha_composite(img,gl)
    draw = ImageDraw.Draw(img)
    LM=62; MAX_W=W//2-LM-10
    draw.text((LM,50),"БЕЛЫЙ ТРЕУГОЛЬНИК  /  МАРКЕТИНГ",font=FONTS["tag"],fill=DIM)
    draw.line([(LM,74),(555,74)],fill=DIM)
    hl_lines=wrap_text(draw,headline,FONTS["head"],MAX_W)
    y=94
    for i,line in enumerate(hl_lines[:3]):
        color=ELEC if i==len(hl_lines)-1 else WHT
        draw.text((LM,y),line,font=FONTS["head"],fill=color)
        y+=84
    if subtext:
        y+=10
        sub_lines=wrap_text(draw,subtext,FONTS["sub"],MAX_W)
        for line in sub_lines[:2]:
            draw.text((LM,y),line,font=FONTS["sub"],fill=DTXT)
            y+=28
    draw.line([(LM,636),(W-LM,636)],fill=DIM)
    draw.text((LM,650),"@btreygolnik",font=FONTS["hand"],fill=ELEC)
    tag="маркетинг без воды"
    bbox=draw.textbbox((0,0),tag,font=FONTS["tag"])
    draw.text((W-LM-(bbox[2]-bbox[0]),652),tag,font=FONTS["tag"],fill=DTXT)
    buf=io.BytesIO()
    img.convert("RGB").save(buf,"PNG")
    buf.seek(0)
    return buf.read()

def send_photo(caption, image_bytes):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    r=requests.post(url,data={"chat_id":CHANNEL,"caption":caption},files={"photo":("post.png",image_bytes,"image/png")},timeout=30)
    return r.json()

def job():
    log.info("Запуск поста...")
    try:
        articles=fetch_articles()
        if not articles: log.warning("Нет статей"); return
        post_text,headline,subtext=generate_post(articles)
        log.info(f"Заголовок: {headline}")
        image_bytes=create_image(headline,subtext)
        result=send_photo(post_text,image_bytes)
        if result.get("ok"): log.info("Пост опубликован!")
        else: log.error(f"Ошибка Telegram: {result}")
    except Exception: log.exception("Ошибка в job()")

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
