"""
Gets projects straight from xploon.com so the bot never runs out.
It also picks the most eye-catching photo for each project.
You don't need to edit this file.
"""
import io
import re

import requests
from PIL import Image, ImageFilter, ImageStat

API = "https://xploon.com/api/local/search-properties"
HEADERS = {"User-Agent": "Mozilla/5.0 (post-bot)", "Accept": "application/json"}

# Which Xploon lists to look through, in order
SOURCES = [
    {"selectedFeaturedProp": "1"},   # featured projects first
    {},                              # then everything else
]
MAX_PAGES_PER_SOURCE = 40            # 20 listings per page


def _num(text):
    """'AED 1,234,567' -> '1234567'. Returns '' if there is no number."""
    m = re.search(r"[\d,.]+", str(text or ""))
    return m.group(0).replace(",", "") if m else ""


def _clean(url):
    return str(url or "").split("?")[0]


def _full_size(url):
    return _clean(url).replace("/363x240/", "/")


def _beds(text):
    text = str(text or "").strip()
    if text.lower().startswith("studio"):
        return "Studio"
    m = re.search(r"\d+", text)
    return m.group(0) if m else text


def to_row(item):
    """Turns one Xploon listing into a sheet-style row. Returns None if it's not usable."""
    if str(item.get("price_on_application")) in ("1", "True", "true"):
        return None
    price = _num(item.get("currency_price_formatted"))
    if not price or not item.get("developer_image_path") or not item.get("images"):
        return None
    parts = [p.strip() for p in str(item.get("location", "")).split(",") if p.strip()]
    if len(parts) < 2:
        return None
    project = parts[0]
    city = parts[-1] if len(parts) > 2 else "Dubai"
    area = ", ".join(parts[1:-1]) if len(parts) > 2 else parts[1]
    return {
        "id": f"xploon-project-{item.get('project_id') or item.get('id')}",
        "project": project,
        "developer": str(item.get("display_name", "")).strip(),
        "area": area,
        "city": city,
        "price": price,
        "till_handover": _num(item.get("pay_till_handover")) or "0",
        "mortgage_monthly": _num(item.get("pay_per_month")) or "0",
        "property_type": str(item.get("project_type") or "Apartment").strip(),
        "beds": _beds(item.get("beds")),
        "size_sqft": _num(item.get("area_formatted")),
        "photo_candidates": [_full_size(u if isinstance(u, str) else (u.get("path") or u.get("url")))
                             for u in item["images"]][:10],
        "logo_url": _clean(item.get("developer_image_path")),
        "source": "xploon",
    }


def listings():
    """Yields usable rows from Xploon, one per project, featured first."""
    seen = set()
    for params in SOURCES:
        for page in range(1, MAX_PAGES_PER_SOURCE + 1):
            q = {"country_code": "ae", "user_id": "", "page": page, **params}
            try:
                r = requests.get(API, params=q, headers=HEADERS, timeout=60)
                data = r.json()
            except Exception as e:
                print(f"Couldn't read Xploon listings (page {page}): {e}")
                break
            items = data.get("data") or []
            if not items:
                break
            for item in items:
                row = to_row(item)
                if row and row["id"] not in seen:
                    seen.add(row["id"])
                    yield row
            last = (data.get("meta") or {}).get("last_page") or page
            if page >= last:
                break


# ---------- Picking an attractive photo ----------

def _score(img):
    """
    Higher = more eye-catching for a property post.
    Likes: bright colours, blue sky / pool / sea, good sharpness, landscape shape.
    Rejects: portrait posters, small images, black bars, very dark or washed-out shots.
    """
    w, h = img.size
    ratio = w / h
    if w < 1000 or ratio < 1.25 or ratio > 2.4:
        return None
    small = img.convert("RGB").resize((240, int(240 / ratio)))
    gray = small.convert("L")

    # black bars on the sides or top/bottom (photos pasted into a frame)
    sw, sh = small.size
    edges = [gray.crop((0, 0, sw // 12, sh)), gray.crop((sw - sw // 12, 0, sw, sh)),
             gray.crop((0, 0, sw, sh // 12)), gray.crop((0, sh - sh // 12, sw, sh))]
    if any(ImageStat.Stat(e).mean[0] < 18 and ImageStat.Stat(e).stddev[0] < 8 for e in edges):
        return None

    brightness = ImageStat.Stat(gray).mean[0]
    if brightness < 45 or brightness > 225:
        return None

    # colourfulness (Hasler & Süsstrunk)
    px = list(small.getdata())
    rg = [r - g for r, g, b in px]
    yb = [0.5 * (r + g) - b for r, g, b in px]
    mean = lambda a: sum(a) / len(a)
    std = lambda a: (sum((x - mean(a)) ** 2 for x in a) / len(a)) ** 0.5
    colourful = (std(rg) ** 2 + std(yb) ** 2) ** 0.5 + 0.3 * (mean(rg) ** 2 + mean(yb) ** 2) ** 0.5

    # share of sky / pool / sea blues and turquoise
    hsv = small.convert("HSV")
    blue = sum(1 for hh, ss, vv in hsv.getdata() if 120 <= hh <= 175 and ss > 60 and vv > 90) / len(px)

    sharp = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]

    return colourful * 1.0 + blue * 120 + min(sharp, 40) * 0.8 + (10 if w >= 1500 else 0)


def best_photo(urls, dest):
    """Downloads candidate photos, saves the best one to dest. Returns its URL or None."""
    best = (None, None)
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            if r.status_code != 200:
                continue
            img = Image.open(io.BytesIO(r.content))
            img.load()
        except Exception:
            continue
        s = _score(img)
        if s is not None and (best[0] is None or s > best[0]):
            best = (s, url)
            dest.write_bytes(r.content)
    return best[1]
