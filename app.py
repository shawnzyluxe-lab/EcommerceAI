import os
import requests
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-this')

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
    products = []
    shop_name = 'Shawnzy Luxe'
    if GRAPHQL_URL and STOREFRONT_TOKEN:
        data = storefront(
            '''{
                shop { name }
                products(first: 12) {
                    edges {
                        node {
                            id
                            title
                            handle
                            description
                            priceRange {
                                minVariantPrice { amount currencyCode }
                            }
                            images(first: 1) {
                                edges { node { url } }
                            }
                        }
                    }
                }
            }'''
        )
        shop = data.get('data', {}).get('shop', {}) or {}
        shop_name = shop.get('name', 'Shawnzy Luxe')
        products = shop.get('products', {}).get('edges', [])
    return render_template(
        'index.html',
        shop_name=shop_name,
        products=products,
        domain=SHOPIFY_DOMAIN,
    )


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
    session.clear()
    return redirect(url_for('home'))


if __name__ == '__main__':
    app.run(debug=True, port=3000)
