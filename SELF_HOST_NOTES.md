# Self-Hosting Notes — Vantav / Prometheus OS

This document captures the future self-hosting path for the platform. The live production app currently runs on **Render** (`vantavcommerce.com`), so these steps are not required today. They are here for a later move to a dedicated VM (e.g., AWS EC2, DigitalOcean, Hetzner).

## 1. Production architecture for self-host

```
Internet
   │
   ▼
Nginx (TLS termination, static files, reverse proxy)
   │
   ├─ /  →  static/ + index.html (public lock screen)
   ├─ /static/ → static assets
   └─ /api/v1/ → Gunicorn/Flask app on 127.0.0.1:8000
```

- Use Nginx only for TLS, static file serving, and reverse proxying API requests.
- Run the Flask app with Gunicorn behind Nginx (same command as Render: `gunicorn -w 1 -b 127.0.0.1:8000 --timeout 120 app:app`).
- Keep `gunicorn -w 1` because the app uses an in-memory `active_sessions` ring.

## 2. Hardened deployment script (cleaned version)

`deploy_frontend.sh`:

```bash
#!/bin/bash
set -e

TARGET_WWW_DIR="/var/www/vanta-veyra-saas"
NGINX_CONF_DIR="/etc/nginx/sites-available"
LOG_PREFIX="[DEPLOYS-LOG]"

# Required environment variables:
#   VANTA_DOMAIN          e.g. vanta-veyra.app
#   VANTA_API_TOKEN       secret token passed to the backend as X-Engine-Token
#   VANTA_BACKEND_PORT    default 8000
VANTA_DOMAIN="${VANTA_DOMAIN:-vanta-veyra.app}"
VANTA_API_TOKEN="${VANTA_API_TOKEN:?Must set VANTA_API_TOKEN}"
BACKEND_PORT="${VANTA_BACKEND_PORT:-8000}"

echo "$LOG_PREFIX Initializing secure production environment checkout..."

# 1. Directory setup
if [ ! -d "$TARGET_WWW_DIR" ]; then
    echo "$LOG_PREFIX Creating deployment directory..."
    sudo mkdir -p "$TARGET_WWW_DIR"
fi

# 2. Copy static frontend assets
echo "$LOG_PREFIX Syncing web files to distribution directory..."
sudo cp -r ./index.html ./static "$TARGET_WWW_DIR/" || true

# 3. Reverse-proxy Nginx config
echo "$LOG_PREFIX Injecting reverse proxy security configuration..."
sudo tee "$NGINX_CONF_DIR/vanta-veyra" > /dev/null <<EOF
server {
    listen 80;
    server_name $VANTA_DOMAIN www.$VANTA_DOMAIN;

    root $TARGET_WWW_DIR;
    index index.html;

    # Static assets and public lock screen
    location / {
        try_files \$uri \$uri/ =404;
    }

    location /static/ {
        alias $TARGET_WWW_DIR/static/;
        expires 7d;
        add_header Cache-Control "public";
    }

    # API reverse proxy
    location /api/v1/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Engine-Token "$VANTA_API_TOKEN";

        proxy_connect_timeout 5s;
        proxy_read_timeout 10s;
    }

    error_page 403 /403.html;
    location = /403.html {
        return 403 'Forbidden: Access Perimeter Denied.';
    }
}
EOF

if [ ! -L "/etc/nginx/sites-enabled/vanta-veyra" ]; then
    sudo ln -s "$NGINX_CONF_DIR/vanta-veyra" /etc/nginx/sites-enabled/
fi

# 4. Permission hardening
echo "$LOG_PREFIX Sanitizing system permissions..."
sudo chown -R www-data:www-data "$TARGET_WWW_DIR"
sudo find "$TARGET_WWW_DIR" -type d -exec chmod 755 {} \;
sudo find "$TARGET_WWW_DIR" -type f -exec chmod 644 {} \;

# 5. Nginx reload
echo "$LOG_PREFIX Running Nginx syntax verification..."
sudo nginx -t
echo "$LOG_PREFIX Reloading Nginx..."
sudo systemctl reload nginx

echo "====================================================================="
echo "DEPLOYMENT COMPLETE: https://$VANTA_DOMAIN"
echo "====================================================================="
```

### Security checklist for self-host

- Do **not** hardcode secrets in `nginx.conf`. Use `VANTA_API_TOKEN` (or remove the custom header entirely and rely on the app’s own session/auth layer).
- Run the Flask app as a non-root user (e.g., `vanta`) using `systemd` or `supervisor`.
- Use **Certbot** (`certbot --nginx`) for TLS; do not expose plain HTTP in production.
- Restrict PostgreSQL to `127.0.0.1` and use a strong `DATABASE_URL`.
- Keep the `gunicorn -w 1` setting to preserve the in-memory `active_sessions` ring.
- Set `APP_ENV=production`, `PYTHONUNBUFFERED=1`, and `SENTRY_DSN` if using Sentry.

## 3. Moving from Render to self-host

1. Provision a VM with at least 1 vCPU / 2 GB RAM.
2. Install Python 3.11+, PostgreSQL 15, Nginx, and Redis if needed.
3. Clone the repo, install dependencies (`pip install -r requirements.txt`).
4. Create the PostgreSQL database and run `python migrate.py`.
5. Set `DATABASE_URL` and `SECRET_KEY` (or `WALL_PASSWORD`) as environment variables.
6. Start Gunicorn bound to `127.0.0.1:8000`.
7. Run `./deploy_frontend.sh` to copy static files and reload Nginx.
8. Configure DNS to point the domain at the VM IP.
9. Run `certbot --nginx` for TLS.

## 4. Notes on the provided stress-test scripts

- `stress_test.py` — in-memory deterministic rules-engine throughput test.
- `optimization_tester.py` — async-wrapper benchmark for the multi-agent COO diagnostic.
- Both are safe to run locally; they do not touch the live database.
