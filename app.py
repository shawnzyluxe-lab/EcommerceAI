import os
import hmac
import requests
from datetime import timedelta
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-this')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(days=1),
)
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('HTTPS', 'false').lower() == 'true'

# ============================================================
# SITE PASSWORD WALL (Aegis-style)
# ============================================================

SITE_WALL_PASSWORD = "IfxSVNs4iAs"


def site_wall_enabled():
    """The wall is enabled only when the env variable is set."""
    return bool(SITE_WALL_PASSWORD)


def site_wall_authenticated():
    """Check whether the browser has passed the password wall."""
    return session.get("site_wall_authenticated") is True


@app.before_request
def site_wall_protect():
    if not site_wall_enabled():
        return None
    if request.endpoint in ('home', 'site_login', 'static'):
        return None
    if site_wall_authenticated():
        return None
    return redirect(url_for('home'))


# ============================================================
# END SITE PASSWORD WALL
# ============================================================

SHOPIFY_DOMAIN = os.environ.get('SHOPIFY_DOMAIN', '').strip()
STOREFRONT_TOKEN = os.environ.get('SHOPIFY_STOREFRONT_TOKEN', '').strip()
CUSTOMER_ACCOUNT_CLIENT_ID = os.environ.get('SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_ID', '').strip()
CUSTOMER_ACCOUNT_CLIENT_SECRET = os.environ.get('SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_SECRET', '').strip()

GRAPHQL_URL = f"https://{SHOPIFY_DOMAIN}/api/2024-07/graphql.json" if SHOPIFY_DOMAIN else None
CUSTOMER_ACCOUNT_BASE = f"https://shopify.com/{SHOPIFY_DOMAIN.split('.')[0]}" if SHOPIFY_DOMAIN else None


def storefront(query, variables=None):
    if not GRAPHQL_URL or not STOREFRONT_TOKEN:
        return {}
    headers = {
        "X-Shopify-Storefront-Access-Token": STOREFRONT_TOKEN,
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers=headers,
            timeout=15,
        )
        return r.json()
    except Exception as e:
        print("Storefront API error:", e)
        return {}


@app.route('/')
def home():
    if site_wall_authenticated():
        return redirect(url_for('dashboard'))
    return render_template('gate.html', error=False, shop_name='Shawnzy Luxe')


@app.route('/dashboard')
def dashboard():
    products = []
    shop_name = 'Shawnzy Luxe'
    product_count = 0
    inventory_value = 0.0
    avg_price = 0.0
    if GRAPHQL_URL and STOREFRONT_TOKEN:
        data = storefront(
            '''{
                shop { name }
                products(first: 12) {
                    edges {
                        node {
                            title
                            priceRange {
                                minVariantPrice { amount currencyCode }
                            }
                        }
                    }
                }
            }'''
        )
        shop = data.get('data', {}).get('shop', {}) or {}
        shop_name = shop.get('name', 'Shawnzy Luxe')
        products = data.get('data', {}).get('products', {}).get('edges', [])
        product_count = len(products)
        if products:
            prices = []
            for p in products:
                try:
                    amount = float(p['node']['priceRange']['minVariantPrice']['amount'])
                    prices.append(amount)
                except (KeyError, ValueError, TypeError):
                    continue
            if prices:
                inventory_value = round(sum(prices), 2)
                avg_price = round(inventory_value / len(prices), 2)
    return render_template(
        'dashboard.html',
        shop_name=shop_name,
        products=products,
        product_count=product_count,
        inventory_value=inventory_value,
        avg_price=avg_price,
    )


@app.route('/site-login', methods=['GET', 'POST'])
def site_login():
    if not site_wall_enabled():
        return redirect(url_for('home'))
    if site_wall_authenticated():
        return redirect(url_for('home'))

    error = False
    if request.method == 'POST':
        submitted = request.form.get('password', '')
        if hmac.compare_digest(submitted, SITE_WALL_PASSWORD):
            session.permanent = True
            session['site_wall_authenticated'] = True
            return redirect(url_for('home'))
        error = True
    return render_template('gate.html', error=error, shop_name='Shawnzy Luxe')


@app.route('/site-logout')
def site_logout():
    session.pop('site_wall_authenticated', None)
    return redirect(url_for('site_login'))


@app.route('/login')
def login():
    return render_template('login.html', domain=SHOPIFY_DOMAIN)


@app.route('/account')
def account():
    if 'customer_access_token' not in session:
        return redirect(url_for('login'))
    return render_template('account.html', customer=session.get('customer'))


@app.route('/logout')
def logout():
    session.pop('customer_access_token', None)
    session.pop('customer', None)
    return redirect(url_for('home'))


if __name__ == '__main__':
    app.run(debug=True, port=3000)
