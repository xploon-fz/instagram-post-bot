"""
Builds one post image in the fixed Xploon layout.
You don't need to edit this file unless the design changes.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).parent
ASSETS = HERE
FONTS = HERE

BLUE = (35, 37, 211)
TEXT = (34, 34, 34)
VALUE = (0, 0, 0)
GREY = (73, 73, 73)

PHOTO_BOX = (0, 0, 1080, 727)      # left, top, right, bottom
LOGO_BOX = (854, 749, 1017, 912)
TEXT_MAX_W = 780                   # space left of the logo


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def fit_font(draw, text, name, size, max_w, min_size=20):
    """Shrink the font until the text fits the width."""
    while size > min_size:
        f = font(name, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return font(name, min_size)


TRACK = 0.8   # letter spacing for the location line


def tracked_len(draw, text, f):
    return draw.textlength(text, font=f) + TRACK * max(len(text) - 1, 0)


def draw_tracked(draw, xy, text, f, fill):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill, anchor="ls")
        x += draw.textlength(ch, font=f) + TRACK


def wrap(draw, text, f, max_w):
    words, lines, line = text.split(), [], ""
    for w in words:
        test = (line + " " + w).strip()
        if tracked_len(draw, test, f) <= max_w:
            line = test
        else:
            lines.append(line)
            line = w
    lines.append(line)
    return lines


def money(value):
    """'1798225' or '1,798,225' -> '1,798,225'. Text like 'On request' stays as is."""
    s = str(value).replace(",", "").replace("AED", "").strip()
    try:
        return f"{round(float(s)):,}"
    except ValueError:
        return str(value).strip()


def paste_cover(base, img, box):
    w, h = box[2] - box[0], box[3] - box[1]
    img = ImageOps.fit(img.convert("RGB"), (w, h), Image.LANCZOS, centering=(0.5, 0.5))
    base.paste(img, box[:2])


def make_post(data, photo_path, logo_path, out_path):
    """
    data keys: price, project, area, city, till_handover, mortgage_monthly,
               property_type, beds, size_sqft
    """
    base = Image.open(ASSETS / "template.png").convert("RGB")
    d = ImageDraw.Draw(base)

    # 1. Photo + heart
    paste_cover(base, Image.open(photo_path), PHOTO_BOX)
    heart = Image.open(ASSETS / "heart.png")
    base.paste(heart, (955, 625), heart)

    # 2. Developer logo
    if logo_path:
        paste_cover(base, Image.open(logo_path), LOGO_BOX)

    # 3. Price
    price = f"AED  {money(data['price'])}"
    f = fit_font(d, price, "LiberationSans-Bold.ttf", 68, TEXT_MAX_W)
    d.text((49, 814), price, font=f, fill=BLUE, anchor="ls")

    # 4. Location (up to 2 lines)
    location = ", ".join(x.strip() for x in [data["project"], data["area"], data["city"]] if x.strip())
    size = 37
    while True:
        f = font("HankenGrotesk.ttf", size)
        f.set_variation_by_axes([500])
        lines = wrap(d, location, f, 600)
        if len(lines) <= 2 or size <= 24:
            break
        size -= 2
    for i, line in enumerate(lines[:2]):
        draw_tracked(d, (51, 863 + i * round(size * 1.38)), line, f, TEXT)

    # 5. Handover + mortgage boxes
    f = font("Roboto.ttf", 39)
    f.set_variation_by_axes([400, 100])
    d.text((294, 1069), f"AED {money(data['till_handover'])}", font=f, fill=VALUE, anchor="ms")
    d.text((797, 1069), f"AED  {money(data['mortgage_monthly'])} / Month", font=f, fill=VALUE, anchor="ms")

    # 6. Type | Beds | Size
    beds = str(data["beds"]).strip()
    if beds.replace(".", "").isdigit():
        beds = f"{beds} Bed" if beds == "1" else f"{beds} Beds"
    sqft = f"{money(data['size_sqft'])} sqft"
    for x, text, max_w in [(118, data["property_type"], 188), (412, beds, 112), (639, sqft, 390)]:
        size = 37
        while True:
            f = font("Roboto.ttf", size)
            f.set_variation_by_axes([400, 100])
            if d.textlength(text, font=f) <= max_w or size <= 22:
                break
            size -= 1
        d.text((x, 1175), text, font=f, fill=GREY, anchor="ls")

    base.save(out_path, "JPEG", quality=92)   # Instagram needs JPEG
    return out_path


if __name__ == "__main__":
    # Quick test: python make_post.py photo.jpg logo.png
    import sys
    sample = dict(price="1798225", project="Flamingo Lux", area="Nad Al Sheba 1", city="Dubai",
                  till_handover="0", mortgage_monthly="9492", property_type="Apartment",
                  beds="1", size_sqft="794")
    make_post(sample, sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None, "test_post.jpg")
    print("Saved test_post.jpg")
