# Project Rules — Shawnzyluxe

## Locked Layouts
- **`templates/index.html` (Prometheus OS home / lock screen)** is locked. Do not change its design, colors, structure, or assets unless the user explicitly asks for a change.
- **`templates/dashboard.html` + `static/css/app.css` (dashboard layout)** is locked. Do not alter the sidebar, header, main grid, tab layout, or overall page structure unless the user explicitly asks.
- **Exception (active):** The user requested the dashboard sidebar be made tabbed and collapsible. This is now the approved structure.

If a user sends code that would modify either of these, flag it and wait for explicit approval before applying.

## Security
- Aegis-style password gate at `/`:
  - `SITE_WALL_PASSWORD` is hardcoded in `app.py` as `IfxSVNs4iAs`.
  - Tokens are stored in an in-memory `active_sessions` set.
  - Cookie: `HttpOnly`, `Secure`, `SameSite=Lax`, 5-minute `max_age`.
  - `hmac.compare_digest` is used for timing-safe password checks.
- The app runs 1 gunicorn worker (`gunicorn -w 1 -b 0.0.0.0:$PORT app:app`) so the in-memory session ring stays consistent.
- The public `/` home is a functional lock screen. The `/dashboard` and all API routes are protected by the wall.

## Build & Deploy
- `python3 -m py_compile app.py dashboard_context.py` to verify syntax.
- Push to `main`, then trigger a Render deploy via the API.

## Feature Integration Pattern
For any new dashboard feature going forward:
1. Create a focused backend module (e.g., `smart_router.py`) with a clear function.
2. Add a protected API endpoint in `app.py` tied to a specific role/tier.
3. Add a `<section class="section" id="feature-name">` card in `templates/dashboard.html`.
4. Add the label + `#feature-name` anchor to `NAV` in `dashboard_context.py`.
5. The section must be hidden/shown by `switchView()` like all other tabbed sections.
6. New features must not appear globally on the dashboard; they are "locked" to their menu tab and only visible when that tab is selected.
7. The sidebar is now collapsible. Add new nav links to the appropriate group (Workspace, Intelligence, Operations, Store).
