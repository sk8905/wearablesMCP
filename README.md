# wearablesMCP

A **private, local, read-only** [MCP](https://modelcontextprotocol.io) server that
gives Claude access to your **Whoop** and **Garmin** health data. It runs as a
local subprocess over **stdio** — nothing is hosted, no public endpoint exists,
and no data leaves your machine except direct HTTPS calls to Whoop's and
Garmin's own APIs.

- **Read-only.** No tool can write back to Whoop or Garmin.
- **No hardcoded secrets.** Credentials come from a git-ignored `.env`.
- **Encrypted token at rest.** The Whoop OAuth token is stored in a single
  Fernet-encrypted file; the encryption key lives in your OS keyring, not on disk.
- **Local cache.** Responses are cached in a small SQLite file with a
  refresh-if-older-than-N-hours policy so you don't trip API rate limits.

## Tools exposed

All take an optional `start_date` and `end_date` (`YYYY-MM-DD`). Omit them and
you get the last 7 days.

| Tool | Source | Data |
|------|--------|------|
| `get_whoop_recovery` | Whoop | Recovery score %, HRV (RMSSD ms), resting heart rate |
| `get_whoop_sleep` | Whoop | Sleep stages, durations, performance/efficiency/consistency |
| `get_whoop_strain` | Whoop | Daily strain (cycles) + individual workouts |
| `get_garmin_sleep` | Garmin | Sleep stages, duration, sleep scores per day |
| `get_garmin_daily_summary` | Garmin | Daily summary, Body Battery, HRV, resting HR, stress |
| `get_garmin_activities` | Garmin | Activities: type, duration, distance, HR, calories |

---

## 1. Create your Whoop developer app

Whoop's API is OAuth2-only, so you register a personal developer app to get a
client ID/secret. A freshly created app can access **your own** account's data
immediately — no app review needed for personal use.

1. Sign in at **https://developer.whoop.com** with your normal Whoop account
   (you need an active membership).
2. In the Developer Dashboard, click **Create New App** (you may first need to
   create a Team — name it anything, e.g. "Personal").
3. Fill in:
   - **Name:** e.g. `personal-health-mcp`
   - **Contact email:** your email
   - **Redirect URIs:** exactly `http://localhost:8080/callback`
     (must match `WHOOP_REDIRECT_URI` in your `.env`)
   - **Scopes:** enable the read-only scopes and **`offline`**:
     `read:recovery`, `read:sleep`, `read:cycles`, `read:workout`,
     `read:profile`, `read:body_measurement`, **`offline`**
     (`offline` is required — it's what returns a refresh token so the server
     keeps working without you re-logging in.)
4. Save. Copy the generated **Client ID** and **Client Secret** — the secret is
   shown once (regenerate later if needed).

You do **not** create a refresh token by hand; the one-time authorization step
below does that.

---

## 2. Install

Requires Python 3.10+.

```bash
git clone <this repo> wearablesMCP
cd wearablesMCP

python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> **Linux headless note:** `keyring` needs a backend. On macOS (Keychain) and
> Windows (Credential Locker) it works out of the box. On a headless Linux box
> install `keyrings.alt` (`pip install keyrings.alt`) or run a Secret Service
> daemon; otherwise the encrypted token can't be keyed.

---

## 3. Add your credentials

Copy the example and fill it in. **`.env` is git-ignored — never commit it.**

```bash
cp .env.example .env
```

```dotenv
WHOOP_CLIENT_ID=...            # from step 1
WHOOP_CLIENT_SECRET=...        # from step 1
WHOOP_REDIRECT_URI=http://localhost:8080/callback

GARMIN_EMAIL=you@example.com   # your normal Garmin login
GARMIN_PASSWORD=...

# optional:
# DATA_DIR=/home/you/.wearables-mcp
# CACHE_TTL_HOURS=6
```

Local state (encrypted Whoop token, SQLite cache, Garmin session tokens) is
written under `DATA_DIR` (default `~/.wearables-mcp`), **outside** the repo.

---

## 4. Authorize Whoop (one time)

This opens your browser, you approve access, and the server stores an encrypted
refresh token. You only repeat this if you revoke access or delete the token.

```bash
python -m wearables_mcp.authorize_whoop
```

A browser window opens to Whoop's consent screen → approve → the tab confirms
success → the terminal prints where the encrypted token was saved. Done.

Garmin needs no separate step — it logs in with your email/password on first use
and caches the session.

---

## 5. Register the server with Claude Code (local scope)

Local scope keeps this server **private to you and to this project only** — it's
not shared and not written into any checked-in config.

From the project directory, using the venv's Python (absolute paths are safest):

```bash
claude mcp add wearables --scope local -- /ABS/PATH/wearablesMCP/.venv/bin/python -m wearables_mcp
```

Replace `/ABS/PATH/` with your real path (`pwd` prints it). On Windows the
command is `...\.venv\Scripts\python.exe`.

Verify:

```bash
claude mcp list          # should show "wearables"
```

Then in Claude Code you can ask things like *"What was my Whoop recovery this
week?"* or *"Compare my Garmin sleep and Body Battery for the last 5 days."*

---

## How it works

```
Claude Code ──stdio──> wearables_mcp.server
                          ├── WhoopClient   ──HTTPS──> api.prod.whoop.com (v2)
                          ├── GarminClient  ──HTTPS──> Garmin Connect (garminconnect)
                          ├── Cache         ──> SQLite (DATA_DIR/cache.sqlite)
                          └── TokenStore    ──> Fernet-encrypted token
                                                (key in OS keyring)
```

- **Whoop auth:** `authorize_whoop.py` does the one-time OAuth2 code exchange.
  The server refreshes the short-lived access token automatically using the
  stored refresh token + client credentials.
- **Caching:** every API response is cached by source/endpoint/date under
  `CACHE_TTL_HOURS` (default 6). Ask the same thing twice within the window and
  it's served from SQLite — no API call.
- **Read-only:** only `GET`-style calls are wrapped; there are no write tools.

## Security notes

- `.env`, `.data/`, `*.enc`, and `*.sqlite` are git-ignored.
- The Whoop token file is Fernet-encrypted (0600); its key is in the OS keyring.
- Garmin credentials are only sent to Garmin's own login endpoint via the
  `garminconnect` library; the resulting session is cached under `DATA_DIR`.
- Everything runs locally; the server has no network listener.

## Troubleshooting

- **"No Whoop token found"** — run `python -m wearables_mcp.authorize_whoop`.
- **"token refresh failed"** — refresh token expired/revoked; re-run authorization.
- **"no refresh_token returned"** — the `offline` scope wasn't enabled/granted on
  the Whoop app; add it in the dashboard and re-authorize.
- **Garmin login fails** — check credentials; if you have MFA on Garmin you may
  be prompted, and repeated logins can be rate-limited (the cached session
  minimizes this).
- **keyring errors on Linux** — see the headless note in step 2.

## Project layout

```
wearables_mcp/
  server.py           # FastMCP stdio server + the 6 tools
  whoop_client.py     # Whoop API v2 (OAuth2 refresh, pagination)
  garmin_client.py    # garminconnect wrapper (session caching)
  authorize_whoop.py  # one-time OAuth2 login flow
  token_store.py      # Fernet encryption + OS keyring
  cache.py            # SQLite TTL cache
  config.py           # .env loading + state paths
```
