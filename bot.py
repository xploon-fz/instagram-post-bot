"""
Daily Instagram bot.

Each run:
  1. Reads your Google Sheet (published as CSV)
  2. Picks the first project that hasn't been posted yet
  3. Builds the post image in the fixed layout
  4. Posts it to Instagram with the caption from caption_template.txt
  5. Remembers the project in posted.txt so it never posts twice

Settings come from GitHub Secrets (see SETUP.md). You don't need to edit this file.
"""
import csv
import io
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from PIL import Image

from make_post import make_post, money

HERE = Path(__file__).parent
POSTED_FILE = HERE / "posted.txt"
POSTS_DIR = HERE / "posts"
CAPTION_FILE = HERE / "caption_template.txt"

SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "").strip()
IG_USER_ID = os.environ.get("IG_USER_ID", "").strip()
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
GRAPH_VERSION = os.environ.get("GRAPH_VERSION", "v26.0").strip() or "v26.0"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
REPO = os.environ.get("GITHUB_REPOSITORY", "")  # filled in by GitHub automatically

# Tokens from "Instagram Login" start with IG and use a different address
GRAPH_HOST = "https://graph.instagram.com" if IG_ACCESS_TOKEN.startswith("IG") else "https://graph.facebook.com"

DUBAI = timezone(timedelta(hours=4))


def stop(msg):
    print(f"\n❌ {msg}\n")
    sys.exit(1)


# ---------- Google Sheet ----------

def clean_key(k):
    return re.sub(r"[^a-z0-9]+", "_", k.strip().lower()).strip("_")


def read_sheet():
    if not SHEET_CSV_URL:
        stop("SHEET_CSV_URL secret is missing. See SETUP.md step 2.")
    r = requests.get(SHEET_CSV_URL, timeout=60)
    if r.status_code != 200 or "<html" in r.text[:200].lower():
        stop("Couldn't read the Google Sheet. Make sure it's published to the web as CSV (SETUP.md step 2).")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    return [{clean_key(k): (v or "").strip() for k, v in row.items() if k} for row in rows]


def row_key(row):
    if row.get("id"):
        return row["id"]
    return f"{row.get('project','')}|{row.get('price','')}|{row.get('beds','')}|{row.get('size_sqft','')}"


NO_REPEAT_WINDOW = 7   # a developer can't appear again within this many posts


def read_posted():
    """posted.txt lines look like:  project key <TAB> developer"""
    keys, devs = set(), []
    if POSTED_FILE.exists():
        for line in POSTED_FILE.read_text().splitlines():
            if not line.strip():
                continue
            key, _, dev = line.partition("\t")
            keys.add(key)
            devs.append(dev.strip().lower())
    return keys, devs


def dev_of(row):
    return row.get("developer", "").strip().lower()


def eligible(rows, done):
    return [r for r in rows if r.get("project") and r.get("photo_url")
            and r.get("skip", "").lower() not in ("yes", "y", "true", "1")
            and row_key(r) not in done]


def pick_next(rows):
    done, devs = read_posted()
    recent = set(d for d in devs[-NO_REPEAT_WINDOW:] if d)
    todo = eligible(rows, done)
    for row in todo:
        if not dev_of(row) or dev_of(row) not in recent:
            return row
    if todo:
        stop("Every project left in the sheet is from a developer posted in the last "
             f"{NO_REPEAT_WINDOW} posts. Add projects from other developers.")
    return None


# ---------- Downloads ----------

def direct_link(url):
    """Turns Google Drive / Dropbox share links into direct download links."""
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?.*id=)([\w-]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    if "dropbox.com" in url:
        return re.sub(r"[?&]dl=0", "", url) + ("&" if "?" in url else "?") + "dl=1"
    return url


def download(url, path):
    r = requests.get(direct_link(url), timeout=120, headers={"User-Agent": "Mozilla/5.0"})
    if r.status_code == 200:
        path.write_bytes(r.content)
        try:
            Image.open(path).verify()
            return path
        except Exception:
            pass
    stop(f"Couldn't download the image: {url}\n   Check the link is public (anyone with the link can view).")


# ---------- Caption ----------

def build_caption(row):
    beds = row.get("beds", "")
    values = {
        "project": row.get("project", ""),
        "area": row.get("area", ""),
        "city": row.get("city", "") or "Dubai",
        "price": money(row.get("price", "")),
        "till_handover": money(row.get("till_handover", "")),
        "mortgage_monthly": money(row.get("mortgage_monthly", "")),
        "property_type": row.get("property_type", ""),
        "beds": (f"{beds} Bed" if beds == "1" else f"{beds} Beds") if beds.isdigit() else beds,
        "size_sqft": money(row.get("size_sqft", "")),
        "developer": row.get("developer", ""),
        "listing_url": row.get("listing_url", ""),
    }
    lines = []
    for line in CAPTION_FILE.read_text(encoding="utf-8").splitlines():
        used = re.findall(r"{(\w+)}", line)
        if any(not values.get(u) for u in used):
            continue  # drop lines whose details are empty in the sheet
        lines.append(line.format(**values))
    caption = "\n".join(lines).strip()
    if row.get("extra_caption"):
        caption = row["extra_caption"] + "\n\n" + caption
    return caption[:2200]  # Instagram limit


# ---------- GitHub (hosts the image so Instagram can fetch it) ----------

def git(*args):
    subprocess.run(["git", *args], check=True, cwd=HERE)


def push_files(paths, message):
    git("config", "user.name", "post-bot")
    git("config", "user.email", "post-bot@users.noreply.github.com")
    git("add", *[str(p) for p in paths])
    git("commit", "-m", message)
    git("push")
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()


# ---------- Instagram ----------

def ig(method, path, **params):
    params["access_token"] = IG_ACCESS_TOKEN
    url = f"{GRAPH_HOST}/{GRAPH_VERSION}/{path}"
    r = requests.request(method, url, data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=120)
    data = r.json()
    if "error" in data:
        stop(f"Instagram said: {data['error'].get('message')}")
    return data


def post_to_instagram(image_url, caption):
    # Give GitHub a moment so the image link is live
    for _ in range(10):
        if requests.head(image_url, timeout=30).status_code == 200:
            break
        time.sleep(6)
    container = ig("POST", f"{IG_USER_ID}/media", image_url=image_url, caption=caption)["id"]
    for _ in range(30):
        status = ig("GET", container, fields="status_code").get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            stop("Instagram couldn't process the image.")
        time.sleep(5)
    return ig("POST", f"{IG_USER_ID}/media_publish", creation_id=container)["id"]


# ---------- Main ----------

PREVIEW_ALL = os.environ.get("PREVIEW_ALL", "false").lower() == "true"


def render(row, out):
    """Download the photo + logo for one row and build its image."""
    if not row.get("logo_url"):
        stop(f"'{row['project']}' has no logo link in the sheet. Add the logo_url and run again.")
    tmp = HERE / "tmp"
    tmp.mkdir(exist_ok=True)
    photo = download(row["photo_url"], tmp / "photo")
    logo = download(row["logo_url"], tmp / "logo")
    make_post(row, photo, logo, out)
    return out


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def preview_all(rows):
    """Test mode: build images for every project still to post. Nothing is posted."""
    done, devs = read_posted()
    recent = list(devs[-NO_REPEAT_WINDOW:])
    todo = []
    for r in eligible(rows, done):  # same order the daily run would use
        if dev_of(r) and dev_of(r) in recent[-NO_REPEAT_WINDOW:]:
            print(f"Note: '{r['project']}' skipped for now (developer posted recently).")
            continue
        todo.append(r)
        recent.append(dev_of(r))
    if not todo:
        stop("No projects to preview in the sheet.")
    folder = HERE / "previews"
    folder.mkdir(exist_ok=True)
    for old in folder.glob("*.jpg"):
        old.unlink()
    files = []
    for i, row in enumerate(todo, 1):
        out = render(row, folder / f"{i:02d}-{slugify(row['project'])}.jpg")
        files.append(out)
        print(f"\n=== {i}. {row['project']} ===\n{build_caption(row)}")
    if REPO:
        git("add", "-A", "previews")
        push_files(files, "Update previews (nothing posted)")
    print(f"\nMade {len(files)} previews in the 'previews' folder. Nothing was posted.")


def main():
    rows = read_sheet()
    if DRY_RUN and PREVIEW_ALL:
        return preview_all(rows)

    row = pick_next(rows)
    if not row:
        stop("No new projects left in the sheet. Add more rows and the bot will continue tomorrow.")

    print(f"Today's project: {row['project']} — AED {money(row.get('price', ''))}")

    POSTS_DIR.mkdir(exist_ok=True)
    out = render(row, POSTS_DIR / f"{datetime.now(DUBAI):%Y-%m-%d}-{slugify(row['project'])}.jpg")
    caption = build_caption(row)
    print(f"Image saved: {out.name}\n\n--- Caption ---\n{caption}\n---------------")

    if DRY_RUN:
        print("\nTest run: nothing was posted. Download the image from the 'post-preview' artifact.")
        return

    if not (IG_USER_ID and IG_ACCESS_TOKEN):
        stop("IG_USER_ID or IG_ACCESS_TOKEN secret is missing. See SETUP.md step 3.")
    if not REPO:
        stop("This must run on GitHub Actions (needs GITHUB_REPOSITORY).")

    sha = push_files([out], f"Post image: {row['project']}")
    image_url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{out.relative_to(HERE).as_posix()}"
    media_id = post_to_instagram(image_url, caption)
    print(f"\n✅ Posted to Instagram (media id {media_id})")

    with POSTED_FILE.open("a") as f:
        f.write(f"{row_key(row)}\t{row.get('developer', '')}\n")
    push_files([POSTED_FILE], f"Mark posted: {row['project']}")


if __name__ == "__main__":
    main()
