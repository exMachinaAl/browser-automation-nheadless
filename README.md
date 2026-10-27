# Browser Automation Gateway v0.6.1 — Navigation Hotfix (Python)

**This is an urgent bugfix for v0.6.0, not a new feature milestone.** Python + Playwright + FastAPI + SQLite; Windows and Debian. Existing mapping, site profiles, tab management and response recorder remain available. **No CAPTCHA or Cloudflare bypass** is included.

## Why v0.6.0 browser did not open URLs

The Playwright network routing hook blocked **every document navigation except a fixed allowlist**. Its default `allowed_origins` contained only `http://127.0.0.1:8787`. Even the URL bar and manual web search were blocked. Also, demo port 8787 was a separate process, and generic pages incorrectly inherited the chatbot demo readiness selector.

**v0.6.1 separates two operations:**

1. **Manual browsing:** use Chrome address bar or the dashboard's **Buka Situs / Cari** entry to browse normal `http`/`https` websites and localhost ports. This is normal human browsing, not a challenge bypass. If you type search terms instead of a URL, the dashboard opens a search page.
2. **Browser bot actions:** before DOM mapping, text extraction or clicking on another site, explicitly click **Izinkan Otomasi di Situs Terbuka**. The dashboard requests confirmation for the exact origin and saves it locally to `data/trusted-origins.json`. Only explicitly approved origins are available to automation. Make sure the site's terms and your permission allow automated access.

A manual page open **does not automatically authorize bot automation**. Origin grants are exact protocol + hostname + port (no wildcards). If you allow `http://localhost:8080`, that does not imply `http://localhost:8787` or `https://localhost:8080`.

## Full install on Windows

Unzip, open PowerShell in folder, then:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m gateway doctor
.venv\Scripts\python.exe -m gateway serve
```

Or `START_WINDOWS.bat`. Open the **dashboard in your usual browser** at http://127.0.0.1:8788 and load the token from `data/gateway-token.txt` (keep it private). The controlled Chromium opens separately. With default config, the chatbot demo is **automatically** served at http://127.0.0.1:8787 (no second terminal needed). If port 8787 is occupied, BAG does not override it and logs a notice.

## Full install on Debian 13

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m gateway doctor
.venv/bin/python -m gateway serve
```

For an installed compatible Chromium, `BAG_CHROMIUM=/usr/bin/chromium .venv/bin/python -m gateway serve` is an optional override. Do not run gateway as root. If you have no display, use headless only after running visible-mode checks on your desktop.

## Safe in-place upgrade v0.6.0 -> v0.6.1

1. **Stop** v0.6.0 gateway and its managed Chromium process. If possible, separately back up the existing `data/` and `config.json` once.
2. Unzip v0.6.1 beside your existing v0.6.0 project. Do **not** copy-and-replace the old folder.
3. Dry-run and then apply. The upgrader backs up modified source to `_backups/` inside the *new* package and preserves existing `config.json`, `data/`, browser profile, cookies, SQLite and token.

Windows (from the newly unzipped v0.6.1 folder):

```powershell
py -3 tools/upgrade.py --target "C:\path\to\BAG-v0.6.0"
py -3 tools/upgrade.py --target "C:\path\to\BAG-v0.6.0" --apply
```

Or launch `UPGRADE_WINDOWS.bat`.

Debian:

```bash
python3 tools/upgrade.py --target /path/to/BAG-v0.6.0
python3 tools/upgrade.py --target /path/to/BAG-v0.6.0 --apply
```

Or `bash UPGRADE_DEBIAN.sh /path/to/BAG-v0.6.0`. The original v0.6.0 folder becomes v0.6.1 *in place*; the extracted v0.6.1 directory acts as update source. The updater only accepts targets reporting `__version__ == '0.6.0'`.

## Practical first-run check

1. Run `python -m gateway network-check`. It creates a temporary loopback web server, checks it using Python and then **real Chromium**, closes everything, and prints diagnostic JSON. No external website is needed.
2. Run `python -m gateway serve` and check `http://127.0.0.1:8788/health` in your ordinary browser. A healthy server responds with JSON `ok: true` and `version: 0.6.1`. Open `http://127.0.0.1:8788` for dashboard.
3. From the controlled Chromium open `http://127.0.0.1:8787`. With the default config, the built-in chatbot demo should load. Try a different localhost port of a service you control, and an external site that permits automation.
4. In the dashboard paste your token, press **Hubungkan**, open a site via **Buka Situs / Cari** or use Chromium directly. For DOM actions, confirm **Izinkan Otomasi di Situs Terbuka** after verifying the current tab. If you opened or changed tabs directly in Chromium, use **Status & Tab → Aktifkan Tab** to select the correct tab before mapping.
5. Use screenshot → inspect → save map → preview. If a page reports `untrusted_origin`, browser browsing is permitted, but automation is intentionally disabled until authorized.

## Troubleshooting decision tree

- `http://127.0.0.1:8788/health` fails in *ordinary browser*: server failed to start, port 8788 is in use, wrong host/port, or local firewall interference; inspect terminal first.
- 8788 works but 8787 fails: check auto-demo startup notice, and whether port 8787 is already occupied by another service.
- `network-check` shows `python_http: true` and `chromium_http: false`: look at `browser_error` — a Chromium/system/proxy restriction may be involved. `ERR_BLOCKED_BY_ADMINISTRATOR` is an environment policy **not corrected by changing BAG's allowlist**.
- `ERR_CONNECTION_REFUSED`: service at the target port isn't listening. Verify service/port using Python or system tools.
- `untrusted_origin`: click the dashboard's explicit approval button only for sites you are authorized to automate.
- `not_ready` on chatbot: configure a matching input / send / response selector or site profile. Generic sites use `document.readyState`, not the demo `#prompt` selector.
- Cloudflare/CAPTCHA: use an authorized manual completion path. Browser access to challenge pages does not imply bypass or permission to automate them.

## Verification commands

```bash
python -m gateway doctor
python -m gateway network-check
python -m gateway test
```

Optional Chromium fixture tests on Debian:

```bash
BAG_BROWSER_SMOKE=1 BAG_SMOKE_CHROMIUM=/usr/bin/chromium python -m gateway test
```

v0.6.1 does not change the SQLite schema (still v3). Browser login/profile data are not bundled in the ZIP. On this development environment Playwright Chromium networking reports `ERR_BLOCKED_BY_ADMINISTRATOR` even against localhost, so real HTTP page navigation from Chromium could not be validated here. This is **not** a claim of cross-site compatibility or guaranteed headless operation. The network diagnostic is intended to verify such limitations on your machine.
# browser-automation-nheadless
