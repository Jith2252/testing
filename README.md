# Stream Player

A browser video player with a Python HTTP server, subtitle conversion, playlists,
byte-range playback, and an authenticated admin panel for shareable stream links.
Uses SQLite locally and PostgreSQL on Heroku.

## Prerequisites and local setup

Use Python 3.13 (declared in `.python-version`) and Git. PowerShell commands:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python make_admin_hash.py
```

Enter a password at the prompt, then set the printed hash and a random secret:

```powershell
$env:ADMIN_USER = 'admin'
$env:ADMIN_PASSWORD_HASH = 'paste-the-complete-scrypt-hash-here'
$env:SECRET_KEY = (.\.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))")
$env:PRODUCTION = '0'
.\.venv\Scripts\python server.py
```

Open `http://127.0.0.1:8000/` and `/admin/login`. The server creates `streams.db`
automatically. On macOS/Linux use `.venv/bin/python` and `export NAME='value'`.
There is no frontend build step or Node dependency installation.

`.env.example` lists placeholders only. The app does **not** automatically load
`.env` files: set variables in the shell or in Heroku Config Vars. Local mode
serves the selected directory; use it with trusted local files. Production
restricts static access to the application's public assets.

## Environment variables

| Variable | Purpose / default |
| --- | --- |
| `ADMIN_USER` | Admin name, explicitly required in production |
| `ADMIN_PASSWORD_HASH` | Complete output of `python make_admin_hash.py` |
| `SECRET_KEY` | Random session secret, at least 32 characters in production |
| `PRODUCTION` | `1` on Heroku enables startup validation and Secure cookies; local default `0` |
| `DATABASE_URL` | PostgreSQL connection URL; supplied by Heroku Postgres |
| `PORT` | HTTP port, default 8000; assigned by Heroku |
| `HOST` | Defaults to 0.0.0.0 when PORT is set, otherwise 127.0.0.1 |
| `ENABLE_PROXY` | Optional remote media proxy, default off; enable with `1` |
| `PROXY_MAX_BYTES` | Proxy response limit, default 67108864 bytes |
| `PROXY_TIMEOUT_SECONDS` | Proxy timeout, default 15 seconds |
| `ALLOW_EMBED` | Player embedding, default off; enable with `1` |

Remote media must support browser CORS and HTTPS when the player uses HTTPS.
Only use sources you are authorized to access. The repository includes the
licensed browser builds `vendor/hls.min.js` (hls.js v1.7.3) and
`vendor/dash.all.min.js` (dash.js v5.2.1), with their license notices in
`vendor/HLS_JS_LICENSE.txt` and `vendor/DASH_JS_LICENSE.md`.

### Embedded subtitles and audio tracks

The player exposes a **Subtitles** selector with `Off`, uploaded/URL subtitles,
and embedded tracks. It also exposes the browser/native audio track list and the
audio tracks reported by hls.js or dash.js. Selected audio and subtitle language
preferences are saved in local storage and matched by exact language, base
language (for example `en-US` to `en`), browser language, then the media
default.

The player deliberately does **not** set `crossorigin="anonymous"` on every
`<video>` source. Adding that attribute can make a source that plays normally
without CORS headers fail to load. Same-origin media and browser-native local
files can expose embedded tracks without it; cross-origin media must provide
the CORS headers required by the browser/player before its embedded text or
audio tracks can be inspected. HLS and DASH manifests, playlists, segments,
and caption resources also need suitable CORS headers when loaded directly.

Embedded subtitle cues are rendered through the player overlay so the existing
font, color, background, and position controls apply consistently. Browser
support is not uniform: `HTMLMediaElement.audioTracks` is not implemented in
every browser, and MKV/AVI/MOV containers may not be playable by the browser at
all even when they contain multiple tracks. Native HLS support varies by
browser; hls.js is used where native HLS is unavailable, while dash.js handles
`.mpd` sources. DRM-protected tracks remain unsupported.

## Checks

```powershell
.\.venv\Scripts\python -m pytest -q
```

Production dependencies are in `requirements.txt`; pytest is in
`requirements-dev.txt`. No compiled build artifacts are needed.

## Deploy to Heroku

Run these steps yourself after publishing to GitHub. Use a Heroku account and
CLI. Replace `your-stream-player` with your unique app name. These commands use
PowerShell from the checkout containing the deployment commit.

1. Create the app and database:

   ```powershell
   heroku login
   heroku create your-stream-player
   heroku buildpacks:set heroku/python -a your-stream-player
   heroku addons:create heroku-postgresql:essential-0 -a your-stream-player
   ```

   The add-on and web dyno incur charges. PostgreSQL is necessary for persistent
   records because Heroku's local filesystem is ephemeral. Local SQLite records
   are not transferred automatically.

2. Generate a password hash using the interactive helper, then configure the app:

   ```powershell
   heroku config:set ADMIN_USER=admin PRODUCTION=1 ENABLE_PROXY=0 ALLOW_EMBED=0 -a your-stream-player
   heroku config:set 'ADMIN_PASSWORD_HASH=paste-the-complete-scrypt-hash-here' -a your-stream-player
   $sessionSecret = (python -c "import secrets; print(secrets.token_urlsafe(32))")
   heroku config:set "SECRET_KEY=$sessionSecret" -a your-stream-player
   ```

   Single quotes preserve the hash's `$` characters. Keep real values out of Git.
   Do not set PORT; Heroku sets it. The add-on sets DATABASE_URL.

3. Deploy and start one web dyno:

   ```powershell
   heroku git:remote -a your-stream-player
   git push heroku HEAD:main
   heroku ps:scale web=1 -a your-stream-player
   ```

   Alternatively connect the GitHub repository and intended branch in Heroku's
   dashboard Deploy tab and deploy that branch. The Procfile runs
   `python server.py`, which binds to 0.0.0.0 and the assigned PORT.

4. Verify:

   ```powershell
   heroku open /health -a your-stream-player
   heroku logs --tail -a your-stream-player
   ```

   Expect `status: ok`. Sign in at `/admin/login`, add a stream, and open its
   `/p/<slug>` link. Enabled, unexpired sources are accessible to viewers via
   `/api/stream/<slug>`; share links do not keep playable source URLs secret.

Tables and indexes are created on startup; there is no separate migration command.
Use one web dyno because login rate limits and logout invalidation are in process
memory. The health endpoint checks HTTP availability, not database connectivity.

Platform references: [Python runtimes](https://devcenter.heroku.com/articles/python-runtimes)
and [Postgres provisioning](https://devcenter.heroku.com/articles/provisioning-heroku-postgres).

## Files to commit

Include source, browser assets, tests, requirements, Procfile, `.python-version`,
`.env.example`, ignore files, and documentation. Exclude secrets, databases, stream
exports, virtual environments, caches, logs, editor files, and local agent metadata.
