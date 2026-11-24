"""
Gets projects straight from xploon.com so the bot never runs out.
It also picks the most eye-catching photo for each project.
You don't need to edit this file.
"""
import io
import re
import time

import requests
from PIL import Image, ImageFilter, ImageStat

API = "https://xploon.com/api/local/search-properties"
HEADERS = {"User-Agent": "Mozilla/5.0 (post-bot)", "Accept": "application/json"}

# Xploon's list is read in these orders, one after another, so the bot reaches far more projects
ORDERS = [
    {"selectedFeaturedProp": "1"},   # featured
    {"selectedOrder": "ec"},         # earliest handover
    {"selectedOrder": "hp"},         # highest price
    {"selectedOrder": "lp"},         # lowest price
    {},                              # Xploon's default order
]
PAGES_PER_ORDER = 60                 # 20 listings per page
MAX_REQUESTS_PER_RUN = 40            # stay well under Xploon's limit
PAUSE_BETWEEN_PAGES = 1.5            # seconds — read slowly so Xploon doesn't block us


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
    tidy = lambda t: t.title() if t.isupper() and len(t) > 4 else t
    parts = [tidy(p) for p in parts]
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
        "photo_candidates": [_clean(u if isinstance(u, str) else (u.get("path") or u.get("url")))
                             for u in item["images"]][:10],
        "logo_url": _clean(item.get("developer_image_path")),
        "source": "xploon",
    }


def listings(start=(0, 1)):
    """
    Yields usable rows from Xploon, one per project. Starts at `start` = (order number, page)
    where the last run stopped, and wraps around. Each row carries its position in row["pos"].
    """
    seen = set()
    order_i, page = start
    if order_i >= len(ORDERS) or page < 1:
        order_i, page = 0, 1
    requests_made, steps = 0, 0
    total_steps = len(ORDERS) * PAGES_PER_ORDER
    while requests_made < MAX_REQUESTS_PER_RUN and steps < total_steps:
        q = {"country_code": "ae", "user_id": "", "page": page, **ORDERS[order_i]}
        items, last = [], page
        try:
            if requests_made:
                time.sleep(PAUSE_BETWEEN_PAGES)
            r = requests.get(API, params=q, headers=HEADERS, timeout=40)
            requests_made += 1
            data = r.json()
            items = data.get("data") or []
            last = (data.get("meta") or {}).get("last_page") or page
        except Exception as e:
            print(f"Couldn't read Xploon (order {order_i}, page {page}): {e}", flush=True)
            requests_made += 1
        for item in items:
            row = to_row(item)
            if row and row["id"] not in seen:
                seen.add(row["id"])
                row["pos"] = (order_i, page)
                yield row
        steps += 1
        page += 1
        if not items or page > min(last, PAGES_PER_ORDER):
            order_i, page = (order_i + 1) % len(ORDERS), 1


# ---------- Picking an attractive photo ----------

def _score(img):
    """
    Higher = more eye-catching for a property post. Works on the small thumbnail.
    Likes: bright colours, blue sky / pool / sea, sharpness.
    Rejects: black bars, very dark or washed-out shots.
    """
    small = img.convert("RGB")
    small.thumbnail((240, 240))
    gray = small.convert("L")

    # black bars on the sides or top/bottom (photos pasted into a frame)
    sw, sh = small.size
    edges = [gray.crop((0, 0, max(sw // 12, 1), sh)), gray.crop((sw - max(sw // 12, 1), 0, sw, sh)),
             gray.crop((0, 0, sw, max(sh // 12, 1))), gray.crop((0, sh - max(sh // 12, 1), sw, sh))]
    if any(ImageStat.Stat(e).mean[0] < 18 and ImageStat.Stat(e).stddev[0] < 8 for e in edges):
        return None

    brightness = ImageStat.Stat(gray).mean[0]
    if brightness < 45 or brightness > 225:
        return None

    # colourfulness (Hasler & Susstrunk)
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
    return colourful + blue * 120 + min(sharp, 40) * 0.8


def _get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            img = Image.open(io.BytesIO(r.content))
            img.load()
            return r.content, img
    except Exception:
        pass
    return None, None


def best_photo(urls, dest):
    """
    Scores the small thumbnails, then downloads the best ones at full size until one
    is big enough and landscape. Saves it to dest and returns its URL (or None).
    """
    scored = []
    for url in urls:
        _, thumb = _get(url)  # thumbnails are small and quick
        if thumb is None:
            continue
        s = _score(thumb)
        if s is not None:
            scored.append((s, url))
    for s, url in sorted(scored, reverse=True)[:4]:
        full_url = _full_size(url)
        data, img = _get(full_url)
        if img is None:
            continue
        w, h = img.size
        if w >= 1000 and 1.25 <= w / h <= 2.4:
            dest.write_bytes(data)
            return full_url
    return None
