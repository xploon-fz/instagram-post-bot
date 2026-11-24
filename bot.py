"""
Daily Instagram bot for Xploon.

Each run:
  1. Takes the next project from your Google Sheet. When the sheet is used up,
     it picks a project straight from xploon.com (so it never runs out).
  2. Never repeats a project, and never uses the same developer twice within 7 posts.
  3. Builds the post image (and picks the most eye-catching photo for Xploon projects).
  4. Posts it to the Instagram feed AND to the story.
  5. Remembers what was posted in posted.txt.

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

from make_post import make_post, make_story, money
import xploon

HERE = Path(__file__).parent
POSTED_FILE = HERE / "posted.txt"
POSTS_DIR = HERE / "posts"
PREVIEW_DIR = HERE / "previews"
CAPTION_FILE = HERE / "caption_template.txt"
TMP = HERE / "tmp"

SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "").strip()
IG_USER_ID = os.environ.get("IG_USER_ID", "").strip()
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
GRAPH_VERSION = os.environ.get("GRAPH_VERSION", "v26.0").strip() or "v26.0"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
PREVIEW_ALL = os.environ.get("PREVIEW_ALL", "false").lower() == "true"
POST_STORY = os.environ.get("POST_STORY", "true").lower() != "false"
REPO = os.environ.get("GITHUB_REPOSITORY", "")  # filled in by GitHub automatically

NO_REPEAT_WINDOW = 7      # a developer can't appear again within this many posts
PREVIEW_COUNT = 10        # how many upcoming posts "preview all" makes

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
        print("No Google Sheet set — using Xploon projects only.")
        return []
    try:
        r = requests.get(SHEET_CSV_URL, timeout=60)
    except Exception as e:
        print(f"Couldn't read the Google Sheet ({e}) — using Xploon projects only.")
        return []
    if r.status_code != 200 or "<html" in r.text[:200].lower():
        print("Couldn't read the Google Sheet — using Xploon projects only.")
        return []
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    return [{clean_key(k): (v or "").strip() for k, v in row.items() if k} for row in rows]


def row_key(row):
    if row.get("id"):
        return row["id"]
    return f"{row.get('project','')}|{row.get('price','')}|{row.get('beds','')}|{row.get('size_sqft','')}"


# ---------- What has been posted ----------

def read_posted():
    """posted.txt lines:  key <TAB> developer <TAB> project"""
    keys, devs, projects = set(), [], set()
    if POSTED_FILE.exists():
        for line in POSTED_FILE.read_text().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            keys.add(parts[0])
            devs.append(parts[1].strip().lower() if len(parts) > 1 else "")
            if len(parts) > 2:
                projects.add(parts[2].strip().lower())
    return keys, devs, projects


def dev_of(row):
    return row.get("developer", "").strip().lower()


def is_skipped(row):
    return row.get("skip", "").lower() in ("yes", "y", "true", "1")


# ---------- Choosing the next project(s) ----------

def upcoming(sheet_rows, how_many):
    """
    Returns the next `how_many` projects in posting order, each already rendered-ready
    (Xploon rows get their best photo chosen and saved). Sheet rows come first.
    """
    done, devs, projects = read_posted()
    recent = [d for d in devs[-NO_REPEAT_WINDOW:]]
    picked = []

    def ok(row):
        if row_key(row) in done or row["project"].strip().lower() in projects:
            return False
        if dev_of(row) and dev_of(row) in recent[-NO_REPEAT_WINDOW:]:
            return False
        return True

    def take(row):
        picked.append(row)
        recent.append(dev_of(row))
        projects.add(row["project"].strip().lower())

    TMP.mkdir(exist_ok=True)

    # 1. Your sheet first, in order
    for row in sheet_rows:
        if len(picked) >= how_many:
            return picked
        if not row.get("project") or not row.get("photo_url") or not row.get("logo_url") or is_skipped(row):
            continue
        if ok(row):
            take(row)

    # 2. Then projects from xploon.com
    for row in xploon.listings():
        if len(picked) >= how_many:
            break
        if not ok(row):
            continue
        photo_file = TMP / f"photo-{len(picked)}"
        url = xploon.best_photo(row["photo_candidates"], photo_file)
        if not url:
            continue  # no good-looking photo for this project, try the next one
        row["photo_url"] = url
        row["photo_file"] = str(photo_file)
        take(row)
    return picked


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
    try:
        r = requests.get(direct_link(url), timeout=120, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            path.write_bytes(r.content)
            Image.open(path).verify()
            return path
    except Exception:
        pass
    return None


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
            continue  # drop lines whose details are empty
        lines.append(line.format(**values))
    caption = "\n".join(lines).strip()
    if row.get("extra_caption"):
        caption = row["extra_caption"] + "\n\n" + caption
    return caption[:2200]  # Instagram limit


# ---------- Building the images ----------

def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render(row, post_out, story_out):
    """Builds the feed image and the story image for one project. Returns False if it can't."""
    TMP.mkdir(exist_ok=True)
    photo = Path(row["photo_file"]) if row.get("photo_file") else download(row["photo_url"], TMP / "photo")
    logo = download(row["logo_url"], TMP / "logo")
    if not photo or not logo:
        print(f"Skipping '{row['project']}': couldn't download its photo or logo.")
        return False
    make_post(row, photo, logo, post_out)
    make_story(post_out, photo, story_out)
    return True


# ---------- GitHub (hosts the images so Instagram can fetch them) ----------

def git(*args):
    subprocess.run(["git", *args], check=True, cwd=HERE)


def push_files(paths, message):
    git("config", "user.name", "post-bot")
    git("config", "user.email", "post-bot@users.noreply.github.com")
    git("add", "-A", *[str(p) for p in paths])
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=HERE).returncode != 0:
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


def wait_for_link(url):
    for _ in range(10):
        if requests.head(url, timeout=30).status_code == 200:
            return
        time.sleep(6)


def publish(image_url, caption=None, story=False):
    wait_for_link(image_url)
    params = {"image_url": image_url}
    if story:
        params["media_type"] = "STORIES"
    else:
        params["caption"] = caption
    container = ig("POST", f"{IG_USER_ID}/media", **params)["id"]
    for _ in range(30):
        status = ig("GET", container, fields="status_code").get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            stop("Instagram couldn't process the image.")
        time.sleep(5)
    return ig("POST", f"{IG_USER_ID}/media_publish", creation_id=container)["id"]


# ---------- Main ----------

def preview_all(sheet_rows):
    """Test mode: builds the next posts + stories. Nothing is posted."""
    PREVIEW_DIR.mkdir(exist_ok=True)
    for old in PREVIEW_DIR.glob("*.jpg"):
        old.unlink()
    rows = upcoming(sheet_rows, PREVIEW_COUNT + 5)
    made = 0
    for row in rows:
        if made >= PREVIEW_COUNT:
            break
        n = made + 1
        name = f"{n:02d}-{slugify(row['project'])}"
        if not render(row, PREVIEW_DIR / f"{name}.jpg", PREVIEW_DIR / f"{name}-story.jpg"):
            continue
        made += 1
        src = "your sheet" if row.get("source") != "xploon" else "xploon.com"
        print(f"\n=== {n}. {row['project']} — {row.get('developer','')} (from {src}) ===\n{build_caption(row)}")
    if not made:
        stop("Couldn't make any previews.")
    if REPO:
        push_files([PREVIEW_DIR], "Update previews (nothing posted)")
    print(f"\nMade {made} previews (post + story) in the 'previews' folder. Nothing was posted.")


def main():
    sheet_rows = read_sheet()
    if DRY_RUN and PREVIEW_ALL:
        return preview_all(sheet_rows)

    POSTS_DIR.mkdir(exist_ok=True)
    stamp = f"{datetime.now(DUBAI):%Y-%m-%d}"
    row = post_img = story_img = None
    for candidate in upcoming(sheet_rows, 5):
        base = POSTS_DIR / f"{stamp}-{slugify(candidate['project'])}"
        post_img, story_img = Path(f"{base}.jpg"), Path(f"{base}-story.jpg")
        if render(candidate, post_img, story_img):
            row = candidate
            break
    if not row:
        stop("No project could be prepared today (sheet used up and Xploon gave nothing usable).")

    caption = build_caption(row)
    print(f"Today's project: {row['project']} — {row.get('developer','')}\n\n--- Caption ---\n{caption}\n---------------")

    if DRY_RUN:
        print("\nTest run: nothing was posted. Download the images from the 'post-preview' artifact.")
        return

    if not (IG_USER_ID and IG_ACCESS_TOKEN):
        stop("IG_USER_ID or IG_ACCESS_TOKEN secret is missing. See SETUP.md step 3.")
    if not REPO:
        stop("This must run on GitHub Actions (needs GITHUB_REPOSITORY).")

    sha = push_files([post_img, story_img], f"Images: {row['project']}")
    raw = f"https://raw.githubusercontent.com/{REPO}/{sha}"
    media_id = publish(f"{raw}/{post_img.relative_to(HERE).as_posix()}", caption=caption)
    print(f"\n✅ Posted to the feed (media id {media_id})")

    # Remember it straight away so a story problem can never cause a double post
    with POSTED_FILE.open("a") as f:
        f.write(f"{row_key(row)}\t{row.get('developer', '')}\t{row['project']}\n")
    push_files([POSTED_FILE], f"Mark posted: {row['project']}")

    if POST_STORY:
        story_id = publish(f"{raw}/{story_img.relative_to(HERE).as_posix()}", story=True)
        print(f"✅ Added to the story (media id {story_id})")


if __name__ == "__main__":
    main()
