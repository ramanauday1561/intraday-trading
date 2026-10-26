# Reliable 10-minute refresh with cron-job.org

GitHub's own `schedule:` trigger is best effort: on 26 Oct 2026 it ran the refresh only twice
instead of ~45 times. An external timer that calls GitHub's "run workflow" API fixes this.
GitHub's schedule stays on as a backup.

## 1. Create a GitHub token that can only start this repo's workflows

1. GitHub → your profile picture → **Settings** → **Developer settings** →
   **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. Name: `intraday-refresh`. Expiration: 90 days (set a reminder to renew).
3. **Repository access** → *Only select repositories* → `ramanauday1561/intraday-trading`.
4. **Permissions** → *Repository permissions* → **Actions: Read and write**.
   Leave everything else at *No access* (Metadata: read-only is added automatically).
5. **Generate token** and copy it (starts with `github_pat_`). You will not see it again.

This token can only start or cancel workflow runs in this one repository. It cannot read or change code.
If it ever leaks, delete it on the same page and create a new one.

## 2. Create the timer on cron-job.org (free)

1. Sign up at https://cron-job.org and choose **Create cronjob**.
2. **Title:** `Intraday refresh`
3. **URL:**
   `https://api.github.com/repos/ramanauday1561/intraday-trading/actions/workflows/pages.yml/dispatches`
4. **Execution schedule** → *Custom*:
   - Minutes: every 10 minutes (`*/10`)
   - Hours: `9-15`
   - Days of week: Monday–Friday
   - **Time zone: Asia/Kolkata**
5. Open the **Advanced** tab:
   - **Request method:** `POST`
   - **Headers** (add each):
     - `Accept: application/vnd.github+json`
     - `Authorization: Bearer github_pat_...` (your token)
     - `X-GitHub-Api-Version: 2022-11-28`
     - `Content-Type: application/json`
   - **Request body:** `{"ref":"main"}`
6. Save, then press **Test run**. A working setup returns **HTTP 204 No Content**.
   - `401` → token pasted wrongly or expired.
   - `403`/`404` → token lacks *Actions: Read and write* on this repo, or the URL has a typo.
   - `422` → body is not exactly `{"ref":"main"}`.

## 3. Check it worked

GitHub → repo → **Actions** → *Analyse & deploy to GitHub Pages*. During market hours you should see
a new run every ~10 minutes with the event **workflow_dispatch**. Each run takes about a minute; the app's
footer shows the "Updated" time, and a **Stale data** banner appears if the last update is over 30 minutes old.

## Cost

Public repositories get unlimited free Actions minutes. If the repo is **private**, the free plan includes
2,000 minutes/month: this refresh uses roughly 40 runs/day × ~1.2 min × 22 days ≈ 1,050 minutes, plus the
daily archive (~1 min/day) and the weekly backtest (~25 min/week), which fits within the free plan.
