# Deploying the dashboard

Vercel, free tier. The dashboard is part of this repository (see
`dashboard/README.md` for why), so the deployment is of the repo root with
almost all of it excluded.

> **Status: DEPLOYED 2026-09-01**, all eight pages, at
> <https://algotrade-six-chi.vercel.app>. Verified live: unauthenticated
> requests get 401, an authenticated page renders in ~840 ms cold, and the
> NAV, day P&L and position count match the Telegram report exactly. **Writes
> are off** — `DASHBOARD_ALLOW_WRITES` is unset, so the recording routes exist
> and refuse.
>
> **A deploy can be checked without credentials.** A route that exists returns
> 401; one that does not returns 404. So `curl -o /dev/null -w '%{http_code}'
> <url>/history` distinguishes "not deployed" from "deployed and protected",
> which is otherwise hard to tell once the password has been rotated.
>
> The steps below are kept as the record of what was configured, and what to
> redo for a second deployment (a `prod` one, when a live portfolio exists).

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

**Check `/healthz` first.** It is unauthenticated, reads no settings and opens
no connection, so `{"status":"ok"}` means the function is alive and the
problem is configuration or Atlas. A 500 there means the function itself is
broken.

| Symptom | Cause |
|---|---|
| `Not configured: <VAR> is not set` | That environment variable is missing from the **Production** scope. Env changes need a redeploy — they do not apply to an existing deployment |
| `DASHBOARD_PASSWORD must be at least 12 characters` | The floor is enforced at startup. The URL is guessable and every page shows holdings and order ids |
| `ModuleNotFoundError: algotrade` | Root Directory is set to `dashboard/`; it must be the repo root |
| Build installs polars/pandas | Dependencies resolved from the root `requirements.txt`. It is excluded in `.vercelignore`, and `pyproject.toml` declares the four real ones |
| Every path 404s | A catch-all rewrite in `vercel.json`. Vercel routes every request to the FastAPI app already, and a rewrite now makes the app see the *rewritten* path |
| Unstyled page | `/static` is served by the app, so this means the function is erroring — check the logs, not the CSS |

### What the first deployment actually hit

Recorded because none of it was guessable from the code:

| Problem | Resolution |
|---|---|
| Build blocked: "commit author email (akash@localhost) is not valid" | The repo had a **local** `user.email` override. Global is the work identity, correct for work repos; the override is now local to this one |
| Build fell back to Python 3.12 | Local and CI are 3.13, which Vercel supports but does not default to. Pinned in `.python-version` |
| Catch-all rewrite would have 404'd everything | Removed; entrypoint declared with `tool.vercel.entrypoint` |
| `Internal Server Error` on every path, including `/healthz` | `DASHBOARD_PASSWORD` was under 12 characters — but the error handler read the settings that had failed, so it raised too and the platform's bare message won. Fixed: config errors now name the variable, and `/healthz` is no longer behind auth |

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
