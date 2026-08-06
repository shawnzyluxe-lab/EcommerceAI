# shawnzyluxe.com

Headless Shopify storefront for shawnzyluxe.com.

## Stack
- Python / Flask
- Shopify Storefront API (products)
- Shopify Customer Account API (login)

## Setup

1. Copy `.env.example` to `.env` and fill in your Shopify values.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the local server:
   ```bash
   python app.py
   ```
4. Open `http://localhost:3000`.

## Next Steps
- Add Shopify credentials to `.env`.
- Finish Customer Account API OAuth callback in `app.py`.
- Connect AI features (recommendations, profit dashboard, alerts).
