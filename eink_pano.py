#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-Paper Rutin Panosu v2 — "Focus: sıradaki iş" düzeni
Pi Zero 2 W + Waveshare 2.13" (V3 sürücü)

Ekran: siyah üst şerit (tarih+saat) · [X DK SONRA] rozeti + sıradaki blok BÜYÜK
       · mini liste (şu anki + son biten) · ilerleme çubuğu (N/16 tamam · +K daha)
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

# ===== GÜNLÜK PLAN (gece düzeni · 02:00 kalkış; gün 02:00'de başlar) =====
SCHEDULE = [
    ("03:00", "Uyanış"),
    ("03:10", "Sabah Yürüyüşü · Zone 2"),
    ("03:40", "Kahvaltı"),
    ("04:00", "Atölye 1'e Gidiş"),
    ("04:30", "İş — Atölye 1"),
    ("09:00", "Mola · Nefes / Yürüyüş"),
    ("09:10", "İş — Atölye 1"),
    ("11:00", "Ana Yemek"),
    ("11:30", "İş — Atölye 1"),
    ("13:45", "Günü Kapat · Yarını Planla"),
    ("14:00", "Eve Dönüş"),
    ("14:30", "Son Yemek"),
    ("15:00", "Serbest / Sindirim"),
    ("15:30", "Zone 2 Kardiyo"),
    ("16:15", "Soğuk Duş / Meditasyon"),
    ("16:30", "Kitap Okuma"),
    ("17:15", "Serbest / Tampon"),
    ("18:45", "Trataka Meditasyonu · Yatış"),
    ("19:00", "UYKU"),
]
ZONE2_DAYS = (5, 6)   # Cmt, Paz → Zone 2
REST_DAYS = (6,)      # Paz → dinlenme   # Per=3, Paz=6 → antrenman "Dinlenme"
DAY_START = 3 * 60   # gün 03:00'de başlar
WATER_CUPS = 4          # 4 bardak × 1 L
WATER_FULL_AT = "16:00" # bu saatte hepsi dolu

DAYS = ["PZT", "SAL", "ÇAR", "PER", "CUM", "CMT", "PAZ"]
MONTHS = ["OCA", "ŞUB", "MAR", "NİS", "MAY", "HAZ",
          "TEM", "AĞU", "EYL", "EKİ", "KAS", "ARA"]

FD = "/usr/share/fonts/truetype/dejavu"
F_HDR = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 14)
F_BADGE = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 11)
F_TIME = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 15)
F_TITLE = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 21)
F_TITLE2 = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 17)
F_MINI = ImageFont.truetype(f"{FD}/DejaVuSans.ttf", 11)
F_FOOT = ImageFont.truetype(f"{FD}/DejaVuSans-Bold.ttf", 11)


HEALTH_LATEST = "/home/pi/health_latest.json"
HEALTH_HISTORY = "/home/pi/health_history.json"

HRV_BASE_DEFAULT = 36.0   # ms — Doruk yaz ortalaması (geçmiş birikince otomatik güncellenir)
RHR_BASE_DEFAULT = 74.0   # bpm

def stress_pct(hrv, rhr, hrv_base, rhr_base, hr=None):
    """0-100: HRV tabanın altına indikçe (45p) + RHR tabanın üstüne çıktıkça (30p)
    + anlık nabız dinlenik tabanın üstündeyse (25p; >105 = aktivite, sayılmaz)."""
    s = 0.0
    if hrv and hrv_base:
        ratio = hrv / hrv_base
        s += 45.0 * max(0.0, min(1.0, (1.0 - ratio) / 0.5))
    if rhr and rhr_base:
        s += 30.0 * max(0.0, min(1.0, (rhr - rhr_base) / 12.0))
    if hr and rhr_base and hr <= 105:
        s += 25.0 * max(0.0, min(1.0, (hr - rhr_base) / 20.0))
    return int(round(max(0.0, min(100.0, s))))

def get_health():
    """(hrv, rhr, hr, stres%) — veri yoksa/eskiyse None."""
    try:
        import json as _j
        from datetime import datetime as _dt
        if not os.path.exists(HEALTH_LATEST):
            return None
        L = _j.load(open(HEALTH_LATEST))
        age_h = (_dt.now() - _dt.fromisoformat(L["ts"])).total_seconds() / 3600
        if age_h > 3:
            return None
        hrv, rhr, hr = L.get("hrv"), L.get("rhr"), L.get("hr")
        hrv_base, rhr_base = HRV_BASE_DEFAULT, RHR_BASE_DEFAULT
        if os.path.exists(HEALTH_HISTORY):
            H = _j.load(open(HEALTH_HISTORY))
            hv = [v["hrv"] for v in H.values() if "hrv" in v][-7:]
            rv = [v["rhr"] for v in H.values() if "rhr" in v][-7:]
            if len(hv) >= 3: hrv_base = sum(hv) / len(hv)
            if len(rv) >= 3: rhr_base = sum(rv) / len(rv)
        return (hrv, rhr, hr, stress_pct(hrv, rhr, hrv_base, rhr_base, hr))
    except Exception:
        return None


def to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def norm(m):
    """Dakikayı gün-başlangıcı (02:00) eksenine çevir."""
    return (m - DAY_START) % (24 * 60)


def day_name_fix(name, weekday):
    if name.startswith("Zone 2"):
        return "Zone 2 Kardiyo 40dk" if weekday in ZONE2_DAYS else "Serbest / Dinlenme"
    return name


def state(now):
    """Aktif idx, sıradaki blok bilgisi, biten sayısı, kalan dk."""
    cur = norm(now.hour * 60 + now.minute)
    n = len(SCHEDULE)
    starts = [norm(to_min(s)) for s, _ in SCHEDULE]   # artan sıralı (0'dan)
    idx = n - 1
    for i in range(n):
        end = starts[i + 1] if i + 1 < n else 24 * 60
        if starts[i] <= cur < end:
            idx = i
            break
    nxt = (idx + 1) % n
    nxt2 = (idx + 2) % n
    nxt_start_norm = starts[nxt] if nxt != 0 else 24 * 60
    wait = nxt_start_norm - cur     # şu anki bloğun bitişine kalan
    duration = nxt_start_norm - starts[idx]
    pct = max(0.0, min(1.0, (cur - starts[idx]) / duration)) if duration > 0 else 0.0
    done = idx                      # bugün biten blok sayısı
    wd = now.weekday()
    full_at = norm(to_min(WATER_FULL_AT))
    water = max(0.0, min(float(WATER_CUPS), cur / full_at * WATER_CUPS)) if full_at > 0 else 0.0
    return {
        "water": water,
        "cur_name": day_name_fix(SCHEDULE[idx][1], wd),
        "cur_start": SCHEDULE[idx][0],
        "nxt_name": day_name_fix(SCHEDULE[nxt][1], wd),
        "nxt_start": SCHEDULE[nxt][0],
        "nxt2_name": day_name_fix(SCHEDULE[nxt2][1], wd),
        "nxt2_start": SCHEDULE[nxt2][0],
        "wait": wait,
        "done": done, "total": n, "pct": pct,
        "sleeping": SCHEDULE[idx][1] == "UYKU",
    }


def wrap2(d, text, font, maxw):
    if d.textlength(text, font=font) <= maxw:
        return [text]
    words = text.split()
    l1 = ""
    for i, w in enumerate(words):
        t = (l1 + " " + w).strip()
        if d.textlength(t, font=font) <= maxw:
            l1 = t
        else:
            return [l1, " ".join(words[i:])]
    return [l1]


TIPS_HIGH = ["10 dk meditasyon yap", "Nefes egzersizi yap", "2 kısa nefes, uzun ver",
             "10 dk yürü, telefonsuz", "Kahveyi kes, su iç", "Yükü hafiflet",
             "Omuzları indir", "19:00'da yat", "Sadece hafif yürü"]
TIPS_MID  = ["5 dk nefes molası", "Kısa meditasyon", "Su iç, ayağa kalk", "Tempoyu düşür",
             "Ekranı azalt", "Kısa yürüyüş", "Yavaş ye"]
F_TINY = ImageFont.truetype(f"{FD}/DejaVuSans.ttf", 9)

def draw_face(d, x, y, r, mood):
    """r yarıçaplı yüz: happy / sad / stressed — kalın çizgili."""
    cx, cy = x + r, y + r
    d.ellipse((x, y, x + 2*r, y + 2*r), outline=0, width=2)
    ey = cy - r*0.28
    for ex in (cx - r*0.38, cx + r*0.38):
        d.ellipse((ex-2, ey-2, ex+2, ey+2), fill=0)
    if mood == "happy":
        d.arc((cx - r*0.58, cy - r*0.15, cx + r*0.58, cy + r*0.7), 15, 165, fill=0, width=2)
    elif mood == "sad":
        d.arc((cx - r*0.6, cy + r*0.3, cx + r*0.6, cy + r*1.4), 195, 345, fill=0, width=2)
    else:  # stressed: çatık kaşlar + dalgalı ağız
        d.line((cx - r*0.62, ey - r*0.5, cx - r*0.18, ey - r*0.28), fill=0, width=2)
        d.line((cx + r*0.62, ey - r*0.5, cx + r*0.18, ey - r*0.28), fill=0, width=2)
        my = cy + r*0.48
        pts = []
        for i in range(7):
            px = cx - r*0.58 + i * (r*1.16/6)
            pts.append((px, my + (r*0.16 if i % 2 else -r*0.16)))
        d.line(pts, fill=0, width=2)


def render(now, st, W, H):
    img = Image.new("1", (W, H), 255)
    d = ImageDraw.Draw(img)

    # ---- Üst siyah şerit ----
    d.rectangle((0, 0, W, 19), fill=0)
    hdr = f"{now.day} {MONTHS[now.month-1]} {DAYS[now.weekday()]}"
    d.text((4, 2), hdr, font=F_HDR, fill=255)
    clock = now.strftime("%H:%M")
    cw = d.textlength(clock, font=F_HDR)
    d.text((W - cw - 4, 2), clock, font=F_HDR, fill=255)

    # ---- Pil (UPS HAT) — saatin solunda ----
    bat = get_battery()
    if bat is not None:
        pct, charging = bat
        x1 = W - cw - 14           # pil gövdesi sağ kenarı
        bw2, bh = 22, 11
        x0, y0 = x1 - bw2, 4
        d.rectangle((x0, y0, x1, y0 + bh), outline=255, width=1)
        d.rectangle((x1 + 1, y0 + 3, x1 + 3, y0 + bh - 3), fill=255)   # uç
        fw2 = int((bw2 - 4) * pct / 100)
        if fw2 > 0:
            d.rectangle((x0 + 2, y0 + 2, x0 + 2 + fw2, y0 + bh - 2), fill=255)
        label = ("⚡" if charging else "") + f"%{pct}"
        lw = d.textlength(label, font=F_BADGE)
        d.text((x0 - lw - 5, 3), label, font=F_BADGE, fill=255)

    # ---- Rozet: ŞİMDİ + kalan süre ----
    badge = "ŞİMDİ"
    bw = d.textlength(badge, font=F_BADGE)
    d.rounded_rectangle((4, 24, 4 + bw + 10, 40), radius=4, fill=0)
    d.text((9, 26), badge, font=F_BADGE, fill=255)
    h, m = divmod(st["wait"], 60)
    kalan = f"Kalan: {h} sa {m} dk" if h else f"Kalan: {m} dk"
    d.text((4 + bw + 16, 24), kalan, font=F_TIME, fill=0)

    # ---- Sağ üst: stres yüzü + yüzde ----
    hz = get_health()
    face_w = 0
    if hz:
        _, _, _, spct = hz
        mood = "happy" if spct < 35 else ("sad" if spct < 65 else "stressed")
        fr = 18                       # yüz yarıçapı (36 px)
        fx, fy = W - 2*fr - 2, 21     # sağ üst köşe
        draw_face(d, fx, fy, fr, mood)
        ptxt = f"%{spct}"
        pw2 = d.textlength(ptxt, font=F_BADGE)
        d.text((fx + fr - pw2/2, fy + 2*fr + 1), ptxt, font=F_BADGE, fill=0)
        face_w = 2*fr + 12

    # ---- Şu anki iş (büyük; 1-2 satır) ----
    lines = wrap2(d, st["cur_name"], F_TITLE, W - 18 - face_w)
    tf, step = F_TITLE, 22
    if len(lines) > 1:
        lines = wrap2(d, st["cur_name"], F_TITLE2, W - 18 - face_w)
        tf, step = F_TITLE2, 19
    y = 44
    for i, ln in enumerate(lines):
        if i == 0:
            d.text((4, y + 4), "▶", font=F_MINI, fill=0)
        d.text((16, y - 1), ln, font=tf, fill=0)
        y += step

    def current_tip():
        if not hz:
            return None
        spct = hz[3]
        if spct >= 65:
            return TIPS_HIGH[now.hour % len(TIPS_HIGH)]
        if spct >= 35:
            return TIPS_MID[now.hour % len(TIPS_MID)]
        return None
    tip = current_tip()
    def draw_water(y):
        d.text((4, y + 1), "Su", font=F_MINI, fill=0)
        x = 24
        litre = st["water"]
        for i in range(WATER_CUPS):
            frac = max(0.0, min(1.0, litre - i))
            # bardak gövdesi (12×13)
            d.rectangle((x, y, x + 11, y + 12), outline=0, width=1)
            fh = int(round(11 * frac))
            if fh > 0:
                d.rectangle((x + 1, y + 12 - fh, x + 10, y + 11), fill=0)
            x += 15
        ltxt = f"{litre:.1f} L".replace(".", ",")
        d.text((x + 2, y + 1), ltxt, font=F_MINI, fill=0)
        # tavsiye (stres ≥ %35): sağa yaslı, sığmazsa küçük font
        if tip:
            avail = W - 4 - (x + 2 + d.textlength(ltxt, font=F_MINI) + 8)
            f = F_MINI if d.textlength(tip, font=F_MINI) <= avail else F_TINY
            tw = d.textlength(tip, font=f)
            d.text((W - tw - 4, y + (1 if f is F_MINI else 3)), tip, font=f, fill=0)
    if len(lines) == 1:
        d.text((4, 68), f"→ {st['nxt_start']}  {st['nxt_name']}", font=F_MINI, fill=0)
        d.text((4, 79), f"→ {st['nxt2_start']}  {st['nxt2_name']}", font=F_MINI, fill=0)
        draw_water(91)
    else:
        d.text((4, 81), f"→ {st['nxt_start']}  {st['nxt_name']}", font=F_MINI, fill=0)
        draw_water(93)

    # ---- Alt: solda şu anki işin ilerleme barı, sağda genel x/x ----
    d.line((0, H - 14, W, H - 14), fill=0, width=1)
    prog = f"{st['done']}/{st['total']}"
    pw = d.textlength(prog, font=F_FOOT)
    d.text((W - pw - 4, H - 13), prog, font=F_FOOT, fill=0)
    bar_w = W - int(pw) - 18        # bar, x/x yazısına kadar uzanır
    d.rectangle((4, H - 11, 4 + bar_w, H - 4), outline=0, width=1)
    fill_w = int(bar_w * st["pct"])
    if fill_w > 1:
        d.rectangle((4, H - 11, 4 + fill_w, H - 4), fill=0)
    return img


def main():
    epd = epd_mod.EPD()
    W, H = epd.height, epd.width   # yatay: 250×122

    last_key = None
    base_set = False

    while True:
        now = datetime.now()
        st = state(now)
        # Uykuda 30 dk'da bir; diğer bloklarda her dakika
        minute_key = now.minute if not st["sleeping"] else (now.minute // 30)
        key = (st["cur_name"], now.hour, minute_key)

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
