# Deploying the dashboard

Vercel, free tier. The dashboard is part of this repository (see
`dashboard/README.md` for why), so the deployment is of the repo root with
almost all of it excluded.

> **Deploy read-only first.** Leave `DASHBOARD_ALLOW_WRITES` unset. You get the
> summary, holdings and health pages on your phone with no way to touch the
> book, and can enable recording later once you have used it. Recording
> executions is the one operation that can corrupt the portfolio; there is no
> reason for its first real use to coincide with its first day on a public URL.

---

## 1. The Atlas user — do this first

The dashboard must **not** use the trading system's credential.

**Atlas → Database Access → Add New Database User**

| Field | Value |
|---|---|
| Authentication | Password |
| Username | `algotrade_dashboard` |
| Password | Autogenerate, and copy it |
| Privileges | **Specific Privileges** |
| Role | `readWrite` · Database `algotrade_paper` |

`readWrite` rather than `read` because the dashboard records executions
directly. **Specific Privileges** rather than the built-in "Read and write to
any database": this user has no business seeing `algotrade_dev`, and none at
all seeing a future `algotrade_prod`.

Network Access already allows `0.0.0.0/0` for GitHub Actions, so there is
nothing to change there.

---

## 2. Import the repository

Vercel → **Add New… → Project** → import `akashdnb/algotrade`.

| Setting | Value |
|---|---|
| Framework Preset | **Other** |
| Root Directory | **`.`** (the repository root — *not* `dashboard/`) |
| Build Command | leave empty |
| Output Directory | leave empty |

Root Directory is the repository root because the function imports the
`algotrade` package, which lives beside `dashboard/` rather than inside it.

### Why the upload is not 2.2 GB

`.vercelignore` excludes `data/` (1.9 GB of session CSVs and the research
archive), `backups/`, `docs/`, `tests/`, `jobs/`, `scripts/` and `config/`.
What ships is `api/`, `dashboard/` and the `algotrade` package — about 2 MB.

**If a deploy is slow or fails on size, check `.vercelignore` first.** It is
the single most likely thing to be wrong.

---

## 3. Environment variables

Vercel → Project → Settings → **Environment Variables**. Add for
**Production** (and Preview, if you want preview builds to work):

| Name | Value |
|---|---|
| `MONGODB_URI` | `mongodb+srv://algotrade_dashboard:<password>@cluster0.bwhfkpo.mongodb.net/?retryWrites=true&w=majority` |
| `MONGODB_DATABASE` | `algotrade_paper` |
| `PORTFOLIO_ID` | `paper_momentum_v1_001` |
| `DASHBOARD_USER` | whatever you like |
| `DASHBOARD_PASSWORD` | **20+ random characters** — 12 is the enforced minimum, not a recommendation |

Leave `DASHBOARD_ALLOW_WRITES` **unset** for now.

`get_settings()` raises on anything missing or malformed, so a
misconfiguration fails visibly at startup rather than rendering a blank NAV as
though it were a fact. `algotrade_dev` is refused outright.

---

## 4. Deploy, then check these four things

```bash
open https://<your-project>.vercel.app
```

1. **It asks for a password.** If any page loads without one, stop and check
   the environment variables reached Production.
2. **The NAV matches the Telegram report** for the last session.
3. **Holdings shows 30 positions** and the page does not scroll sideways.
4. **`/healthz` returns `{"status":"ok"}`** — it touches no database, so it
   distinguishes "the app is broken" from "Atlas is unreachable".

### If the function fails to start

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: algotrade` | Root Directory is set to `dashboard/`; it must be the repo root |
| Build installs polars/pandas | The root `requirements.txt` was used instead of `api/requirements.txt` |
| `ConfigError: ... is not set` | An environment variable is missing from the Production scope |
| Unstyled page | `/static` is served by the app itself, so this means the function is erroring — check the logs, not the CSS |

---

## 5. Enabling execution recording, later

When you want to record fills from the dashboard, add
`DASHBOARD_ALLOW_WRITES=true` and redeploy.

Before you do, know what is protecting you:

- **CSRF tokens and an `Origin` check** on every write route. Basic Auth makes
  the browser attach credentials automatically, so without these a form on
  another site could write to your book.
- **A `batch_id` per submission**, so a double-tapped Submit or a retry on a
  flaky connection cannot record thirty fills twice. Paper fills carry no
  broker order id, so nothing else would catch it.
- **The write path runs the trading system's own services** — the execution
  and its cash ledger entry are written together, holdings are rebuilt from
  the whole history, and reconciliation runs immediately afterwards and says
  so loudly if the book no longer agrees.

Verified against a scratch database seeded from paper, never against paper
itself. See `dashboard/app/recording.py`.

---

## Cost

Vercel Hobby: 100 GB-hours of function execution a month. A dashboard opened a
few times a day by one person uses a rounding error of that. Atlas M0 and the
free tier are both unaffected by the read volume.

The one thing to watch is **cold starts**: an idle container takes a second or
two to wake and open its first Atlas connection. `maxPoolSize=1` and a
module-level client keep warm requests fast; see `dashboard/app/db.py`.
