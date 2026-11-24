# Daily Instagram Post Bot — Setup

Every day at 10:17 AM Dubai time, the bot:
1. takes the next project from your Google Sheet,
2. builds the post image in your fixed layout,
3. posts it to Instagram with the caption,
4. marks the project as done so it never posts it twice.

One-time setup takes about 30–45 minutes. After that you only add rows to the sheet.

---

## Step 1 — Put the files on GitHub

1. Create a free account at github.com (skip if you have one).
2. Click **New repository**. Name it `instagram-post-bot`. Choose **Public**.
   *(Public is needed so Instagram can fetch the image from GitHub. Your passwords and tokens stay hidden in Secrets, so nobody sees them.)*
3. On the new repo page click **uploading an existing file**.
4. Unzip `instagram-post-bot.zip` on your computer, open the folder, select **everything inside it** and drag it into the upload box.
   - The `.github` folder is hidden on Mac. Press **Cmd + Shift + .** in Finder to show it. It must be uploaded, or the bot won't run on its own.
5. Click **Commit changes**.

## Step 2 — Make the Google Sheet

1. Create a new Google Sheet. Go to **File → Import → Upload** and upload `projects_template.csv`. Choose **Replace current sheet**.
2. Keep the column names in row 1 exactly as they are. One row = one post. The bot posts rows from top to bottom.

| Column | What to put | Example |
|---|---|---|
| project | Project name | Flamingo Lux |
| area | Area | Nad Al Sheba 1 |
| city | City | Dubai |
| price | Price in AED (with or without commas) | 1798225 |
| till_handover | Amount till handover | 0 |
| mortgage_monthly | Monthly mortgage | 9492 |
| property_type | Apartment, Villa, Townhouse… | Apartment |
| beds | Number of beds, or "Studio" | 1 |
| size_sqft | Size | 794 |
| photo_url | Link to the main photo | Google Drive link |
| logo_url | Link to the developer logo (square works best) | Google Drive link |
| skip | Type `yes` to skip a row | |

   **Photo and logo links:** upload the image to Google Drive → right click → **Share** → General access: **Anyone with the link** → **Copy link**. Paste that link. Landscape photos work best.

3. Publish the sheet so the bot can read it: **File → Share → Publish to web** → choose your sheet tab → choose **Comma-separated values (.csv)** → **Publish**. Copy the link it gives you.

## Step 3 — Get your Instagram access

Your Instagram must be a **Business** (or Creator) account linked to a Facebook Page.

**3a. Create the app**
1. Go to developers.facebook.com → **My Apps → Create App**.
2. Pick the use case for **managing messaging & content on Instagram**, and connect it to your Business portfolio (Xploon's Business Manager).

**3b. Get a token that doesn't expire (System User)**
1. Go to business.facebook.com → **Settings → Users → System users → Add**. Name: `post-bot`, role: Admin.
2. Click **Assign assets**: give it full control of your **app**, your **Facebook Page** and your **Instagram account**.
3. Click **Generate new token** → choose your app → tick these permissions:
   `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `business_management`
4. Set expiry to **Never**. Copy the token and keep it safe. This is your `IG_ACCESS_TOKEN`.

**3c. Find your Instagram account ID**
1. Open developers.facebook.com/tools/explorer
2. Paste your token in the **Access Token** box.
3. In the query box, type: `me/accounts?fields=name,instagram_business_account` and click **Submit**.
4. Find your Page. The number under `instagram_business_account → id` is your `IG_USER_ID` (it usually starts with 1784…).

## Step 4 — Add the 3 secrets to GitHub

In your repo: **Settings → Secrets and variables → Actions → New repository secret**. Add these three:

| Name | Value |
|---|---|
| `SHEET_CSV_URL` | The publish link from Step 2 |
| `IG_ACCESS_TOKEN` | The token from Step 3b |
| `IG_USER_ID` | The number from Step 3c |

## Step 5 — Test it

1. In your repo click **Actions**. If asked, click **I understand… enable them**.
2. Click **Daily Instagram post → Run workflow**. Leave "Test only" **ticked** → **Run workflow**.
3. When it finishes (green tick), click the run and download **post-preview** at the bottom. Check the image. The caption shows in the log under "Make and post today's image".
4. Happy with it? Run it again with "Test only" **unticked**. That one posts for real.

From now on it posts on its own every day.

---

## Everyday use

- **Add projects:** add rows to the sheet. That's it.
- **Skip a project:** type `yes` in the skip column.
- **Ran out of projects:** the run fails and GitHub emails you "No new projects left". Add rows and it continues next day.
- **Something failed:** GitHub emails you. Open **Actions** → the red run → the error message says what went wrong in plain words.
- **Change the caption:** edit `caption_template.txt` on GitHub (pencil icon). Words in `{curly brackets}` are filled from the sheet. A line is left out automatically if its detail is empty.
- **Change the time:** edit `.github/workflows/daily-post.yml`, the `cron` line. It uses UTC (Dubai minus 4 hours). `"17 6 * * *"` = 10:17 AM Dubai. GitHub can run a few minutes late.
- **Post the same project again:** delete its line from `posted.txt`.
- Instagram links in captions aren't clickable, so the caption points to the link in bio.
