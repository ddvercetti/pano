#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-Paper Beslenme Panosu v3 — sadece takviye · öğün · su
Pi Zero 2 W + Waveshare 2.13" (V3 sürücü)

Ekran: üst şerit (tarih·pil·saat) · ŞİMDİ/SIRADAKİ + öğün ya da takviye BÜYÜK
       · içerik satırı · sonraki madde · 8 bardak su · günün ilerlemesi
"""

import os
import sys
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

LIB = os.path.expanduser("~/e-Paper/RaspberryPi_JetsonNano/python/lib")
if os.path.exists(LIB):
    sys.path.insert(0, LIB)

# ================= AYAR =================
EPD_VERSION = "V3"   # senin kartında test: V3
INA219_ADDR = 0x43   # Waveshare UPS HAT (C) I2C adresi
ROTATE_180 = True    # ekran ters takılıysa True
# ========================================

# ---- Waveshare UPS HAT (C): INA219 pil okuma ----
try:
    import smbus
    _bus = smbus.SMBus(1)
except Exception:
    _bus = None

def _ina_word(reg):
    d = _bus.read_i2c_block_data(INA219_ADDR, reg, 2)
    return (d[0] << 8) | d[1]

_ina_ready = False
def _ina_init():
    global _ina_ready
    if _bus is None:
        return
    try:
        # kalibrasyon: 0.1Ω shunt, ~3.2A aralık (Waveshare örneğiyle uyumlu)
        cal = 4096
        _bus.write_i2c_block_data(INA219_ADDR, 0x05, [(cal >> 8) & 0xFF, cal & 0xFF])
        cfg = 0x19FF   # 32V, 12bit, sürekli ölçüm
        _bus.write_i2c_block_data(INA219_ADDR, 0x00, [(cfg >> 8) & 0xFF, cfg & 0xFF])
        _ina_ready = True
    except Exception:
        _ina_ready = False

def get_battery():
    """(yüzde, şarj_mı) döndürür; HAT yoksa None."""
    global _ina_ready
    if _bus is None:
        return None
    if not _ina_ready:
        _ina_init()
        if not _ina_ready:
            return None
    try:
        raw = _ina_word(0x02)
        volts = (raw >> 3) * 0.004          # bus voltajı
        pct = (volts - 3.0) / 1.2 * 100.0    # 3.0V boş – 4.2V dolu (LiPo)
        pct = max(0, min(100, round(pct)))
        cur_raw = _ina_word(0x04)
        if cur_raw > 32767:
            cur_raw -= 65536
        charging = cur_raw > 100             # + akım = şarj oluyor
        return (pct, charging)
    except Exception:
        _ina_ready = False
        return None

if EPD_VERSION == "V4":
    from waveshare_epd import epd2in13_V4 as epd_mod
elif EPD_VERSION == "V3":
    from waveshare_epd import epd2in13_V3 as epd_mod
else:
    from waveshare_epd import epd2in13_V2 as epd_mod

# ===== BESLENME PLANI (diyetisyen) =====
DAY_START = 5 * 60 + 15        # gün 05:15'te başlar (uyanış 05:30)
WAKE, SLEEP = "05:30", "20:15"
OFF_DAYS = (5, 6)              # Cmt, Paz → karbonhidrat yarı
WATER_TIMES = ["05:30", "06:00", "06:30", "08:30", "09:30", "12:00", "14:30", "17:00"]  # 8 × 0,5 L = 4 L

# (saat, tür, başlık, içerik)  — içerik None ise gün tipine göre hesaplanır
ITEMS = [
    ("05:30", "T", "Psyllium",           "Aç karnına, bol suyla"),
    ("05:35", "T", "L-Karnitin",         "Antrenmandan önce"),
    ("05:40", "T", "EAA + elektrolit",   "EAA 10 g · antrenman boyunca"),
    ("06:00", "T", "Berberine + krom",   "Berberine 500 mg · Öğün 1'den 30 dk önce"),
    ("06:30", "Y", "Öğün 1",             None),
    ("06:50", "T", "Öğün 1 sonrası",     "D3K2 · Kreatin 5 g · Prebiyotik"),
    ("09:30", "Y", "Öğün 2",             None),
    ("09:50", "T", "Omega-3",            "1000 mg · Öğün 2 sonrası"),
    ("12:00", "Y", "Öğün 3",             None),
    ("16:30", "Y", "Öğün 4",             None),
    ("20:00", "T", "Magnezyum + çinko",  "Yatmadan önce"),
]
PRE, POST_T, POST_Y = 10, 20, 30     # "ŞİMDİ" penceresi (dk): takviye −10/+20, öğün −10/+30

def meal_text(name, wd):
    off = wd in OFF_DAYS
    if name == "Öğün 1": return f"Whey 30g · 1 yumurta · yulaf {40 if off else 80}g · çilek"
    if name == "Öğün 2": return f"Tavuk/hindi 150g · pirinç {40 if off else 80}g · salata"
    if name == "Öğün 3": return f"Tavuk/kıyma 150g · pirinç {40 if off else 80}g"
    if name == "Öğün 4": return f"Mezgit/tavuk 150g · patates {120 if off else 250}g · sebze"
    return ""

DAYS = ["PZT", "SAL", "ÇAR", "PER", "CUM", "CMT", "PAZ"]
MONTHS = ["OCA", "ŞUB", "MAR", "NİS", "MAY", "HAZ",
          "TEM", "AĞU", "EYL", "EKİ", "KAS", "ARA"]

FD = "/usr/share/fonts/truetype/dejavu"
F_HDR = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 14)
F_BADGE = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 11)
F_TIME = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 15)
F_TITLE = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 21)
F_MINI = ImageFont.truetype(f"{FD}/DejaVuSans.ttf", 11)
F_TINY = ImageFont.truetype(f"{FD}/DejaVuSans.ttf", 9)
F_FOOT = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 11)


def to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def norm(m):
    """Dakikayı gün-başlangıcı (05:15) eksenine çevir."""
    return (m - DAY_START) % (24 * 60)


def state(now):
    cur = norm(now.hour * 60 + now.minute)
    wd = now.weekday()
    items = []
    for t, kind, title, body in ITEMS:
        items.append({"t": t, "m": norm(to_min(t)), "kind": kind, "title": title,
                      "body": body if body is not None else meal_text(title, wd)})
    def post(it): return POST_Y if it["kind"] == "Y" else POST_T
    # ŞİMDİ: penceresi içindeki madde; öğün ile takviye çakışırsa öğün öne çıkar
    live = [i for i, it in enumerate(items) if it["m"] - PRE <= cur <= it["m"] + post(it)]
    meals = [i for i in live if items[i]["kind"] == "Y"]
    active = (meals or live or [None])[-1]
    upcoming = [i for i, it in enumerate(items) if it["m"] > cur]
    focus = active if active is not None else (upcoming[0] if upcoming else None)
    fm = items[focus]["m"] if focus is not None else cur
    rest = [i for i, it in enumerate(items) if i != focus and it["m"] + post(it) >= cur and it["m"] >= fm]
    nxt = rest[0] if rest else None
    done = sum(1 for it in items if it["m"] + post(it) < cur)
    cups = sum(1 for t in WATER_TIMES if norm(to_min(t)) <= cur)
    next_cup = next((t for t in WATER_TIMES if norm(to_min(t)) > cur), None)
    sleeping = focus is None or cur >= norm(to_min(SLEEP))
    return {"cur": cur, "items": items, "active": active, "focus": focus, "nxt": nxt,
            "done": done, "total": len(items), "cups": cups, "next_cup": next_cup,
            "sleeping": sleeping}


def render(now, st, W, H):
    img = Image.new("1", (W, H), 255)
    d = ImageDraw.Draw(img)

    # ---- Üst siyah şerit: tarih · pil · saat ----
    d.rectangle((0, 0, W, 19), fill=0)
    hdr = f"{now.day} {MONTHS[now.month-1]} {DAYS[now.weekday()]}"
    if now.weekday() in OFF_DAYS:
        hdr += " · OFF"
    d.text((4, 2), hdr, font=F_HDR, fill=255)
    clock = now.strftime("%H:%M")
    cw = d.textlength(clock, font=F_HDR)
    d.text((W - cw - 4, 2), clock, font=F_HDR, fill=255)
    bat = get_battery()
    if bat is not None:
        pct, charging = bat
        x1 = W - cw - 14
        bw2, bh = 22, 11
        x0, y0 = x1 - bw2, 4
        d.rectangle((x0, y0, x1, y0 + bh), outline=255, width=1)
        d.rectangle((x1 + 1, y0 + 3, x1 + 3, y0 + bh - 3), fill=255)
        fw2 = int((bw2 - 4) * pct / 100)
        if fw2 > 0:
            d.rectangle((x0 + 2, y0 + 2, x0 + 2 + fw2, y0 + bh - 2), fill=255)
        label = ("⚡" if charging else "") + f"%{pct}"
        lw = d.textlength(label, font=F_BADGE)
        d.text((x0 - lw - 5, 3), label, font=F_BADGE, fill=255)

    def fit(txt, x, y, font, small, maxw):
        f = font
        if d.textlength(txt, font=f) > maxw:
            f = small
        while d.textlength(txt, font=f) > maxw and len(txt) > 4:
            txt = txt[:-2].rstrip() + "…"
        d.text((x, y + (0 if f is font else 1)), txt, font=f, fill=0)

    items = st["items"]
    # ---- Rozet + başlık ----
    if st["sleeping"]:
        badge, sub = "UYKU", f"Uyanış {WAKE} · Psyllium"
        title, body = "İyi uykular", "Su ve takviyeler bugün bitti"
    else:
        it = items[st["focus"]]
        tag = "TAKVİYE" if it["kind"] == "T" else "ÖĞÜN"
        if st["active"] is not None:
            badge, sub = "ŞİMDİ", f"{it['t']} · {tag}"
        else:
            wait = it["m"] - st["cur"]
            h, m = divmod(wait, 60)
            badge = "SIRADAKİ"
            sub = f"{it['t']} · {h} sa {m} dk sonra" if h else f"{it['t']} · {m} dk sonra"
        title, body = it["title"], it["body"]
    bw = d.textlength(badge, font=F_BADGE)
    d.rounded_rectangle((4, 24, 4 + bw + 10, 40), radius=4, fill=0)
    d.text((9, 26), badge, font=F_BADGE, fill=255)
    fit(sub, 4 + bw + 16, 24, F_TIME, F_MINI, W - bw - 24)

    fit(title, 4, 43, F_TITLE, F_TIME, W - 8)
    fit(body, 4, 67, F_MINI, F_TINY, W - 8)

    # ---- Sonraki madde ----
    if st["nxt"] is not None:
        n = items[st["nxt"]]
        fit(f"→ {n['t']}  {n['title']}", 4, 81, F_MINI, F_TINY, W - 8)

    # ---- Su: 8 bardak × 0,5 L ----
    y = 95
    d.text((4, y), "Su", font=F_MINI, fill=0)
    x = 24
    for i in range(len(WATER_TIMES)):
        d.rectangle((x, y, x + 10, y + 11), outline=0, width=1)
        if i < st["cups"]:
            d.rectangle((x + 1, y + 1, x + 9, y + 10), fill=0)
        x += 14
    litre = st["cups"] * 0.5
    ltxt = f"{litre:.1f}/4 L".replace(".", ",")
    if st["next_cup"]:
        ltxt += f" · {st['next_cup']}"
    fit(ltxt, x + 3, y, F_MINI, F_TINY, W - x - 7)

    # ---- Alt: günün beslenme ilerlemesi ----
    d.line((0, H - 12, W, H - 12), fill=0, width=1)
    prog = f"{st['done']}/{st['total']}"
    pw = d.textlength(prog, font=F_FOOT)
    d.text((W - pw - 4, H - 12), prog, font=F_FOOT, fill=0)
    bar_w = W - int(pw) - 18
    d.rectangle((4, H - 9, 4 + bar_w, H - 3), outline=0, width=1)
    fill_w = int(bar_w * st["done"] / st["total"])
    if fill_w > 1:
        d.rectangle((4, H - 9, 4 + fill_w, H - 3), fill=0)
    return img


def main():
    epd = epd_mod.EPD()
    W, H = epd.height, epd.width   # yatay: 250×122

    last_key = None
    base_set = False

    while True:
        now = datetime.now()
        st = state(now)
        # Uykuda 30 dk'da bir; diğer zamanlarda her dakika
        minute_key = now.minute if not st["sleeping"] else (now.minute // 30)
        key = ((st["focus"], st["active"] is not None, st["cups"]), now.hour, minute_key)

        if key != last_key:
            img = render(now, st, W, H)
            if ROTATE_180:
                img = img.rotate(180)
            buf = epd.getbuffer(img)
            block_changed = (last_key is None or key[0] != last_key[0])

            if block_changed or not base_set:
                try:
                    epd.init(epd.FULL_UPDATE)      # V2 API
                except (AttributeError, TypeError):
                    epd.init()                      # V3/V4 API
                if hasattr(epd, "displayPartBaseImage"):
                    epd.displayPartBaseImage(buf)
                else:
                    epd.display(buf)
                try:
                    epd.init(epd.PART_UPDATE)
                except (AttributeError, TypeError):
                    pass
                base_set = True
            else:
                epd.displayPartial(buf)
            last_key = key

        time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        epd_mod.epdconfig.module_exit()
        sys.exit()
