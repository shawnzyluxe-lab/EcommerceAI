# Project Rules — Shawnzyluxe

## Locked Layouts
- **`templates/index.html` (Prometheus OS home / lock screen)** is locked. Do not change its design, colors, structure, or assets unless the user explicitly asks for a change.
- **`templates/dashboard.html` + `static/css/app.css` (dashboard layout)** is locked. Do not alter the sidebar, header, main grid, tab layout, or overall page structure unless the user explicitly asks.

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
