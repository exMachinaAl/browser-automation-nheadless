# CHANGELOG — BAG v0.6.1 (Navigation / Browser Usability Hotfix)

## Root cause fixed
- v0.6.0 installed a Playwright context.route() rule that aborted all top-level navigation except `allowed_origins` (only http://127.0.0.1:8787 by default). The Chrome address bar, web search and arbitrary localhost ports therefore appeared broken.
- Demo server on port 8787 had to be started in a separate terminal; failure was easily confused with a browser failure.
- Probe required demo CSS selector `#prompt` even after navigating to generic authorized sites.

## Fixed
- Unrestricted *human visual browsing* in owned Chromium; no all-page route blocker. Automated read/write/click/chat remain **allowlisted**.
- Dashboard browser search/URL entry, including local HTTP origins; explicit confirm-to-trust for current origin; file `data/trusted-origins.json`, preserving config and browser sessions.
- Automatic loopback demo port 8787 when default demo start_url is configured and port available.
- Improved generic page readiness probe (without demo selector requirement) and stale navigation error reset on main-frame navigation.
- Added `python -m gateway network-check` to distinguish Python loopback connectivity from Chromium network restrictions.
- New regression tests on browsing restrictions, opt-in origins, persistence, demo autostart.
- Source-only safe in-place updater v0.6.0 -> v0.6.1; no changes to SQLite schema.

## Limits
- Headless and external site navigation are not confirmed in this environment; Chromium HTTP navigation here reports `ERR_BLOCKED_BY_ADMINISTRATOR` even without BAG. CAPTCHA/Cloudflare challenges are not bypassed. Don't enable automation on sites without permission.
