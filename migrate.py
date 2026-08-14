"""One-off database migration runner for Render pre/post deploy."""
import os
import sys
from sqlalchemy import create_engine, text


def run_migrations():
    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        print("DATABASE_URL not set; skipping migrations.")
        return
    if raw_url.startswith("sqlite"):
        print("SQLite detected; skipping raw migrations.")
        return
    if raw_url.startswith("postgresql://"):
        url = "postgresql+psycopg" + raw_url[len("postgresql"):]
    elif raw_url.startswith("postgres://"):
        url = "postgresql+psycopg" + raw_url[len("postgres"):]
    else:
        url = raw_url

    engine = create_engine(url)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        def _run(label, stmt):
            try:
                conn.execute(text(stmt))
                print(f"[migrate] {label}: ok")
            except Exception as e:
                print(f"[migrate] {label}: {e}")

        for col, typ in [
            ("sandbox_status", "VARCHAR(50)"),
            ("sandbox_started_at", "TIMESTAMP"),
            ("sandbox_expires_at", "TIMESTAMP"),
            ("live_access_enabled", "INTEGER"),
            ("approved_at", "TIMESTAMP"),
            ("brand_color", "VARCHAR(7)"),
            ("brand_color_secondary", "VARCHAR(7)"),
        ]:
            _run(f"merchant_profiles.{col}", f"ALTER TABLE merchant_profiles ADD COLUMN IF NOT EXISTS {col} {typ}")

        _run(
            "merchant_channels table",
            """
            CREATE TABLE IF NOT EXISTS merchant_channels (
                id SERIAL PRIMARY KEY,
                merchant_id VARCHAR(100),
                channel_id VARCHAR(100) NOT NULL,
                pending_orders INTEGER DEFAULT 0,
                conversion_rate REAL DEFAULT 0.0,
                UNIQUE (merchant_id, channel_id)
            )
            """,
        )

        _run(
            "tenant_oauth_tokens table",
            """
            CREATE TABLE IF NOT EXISTS tenant_oauth_tokens (
                shop_domain VARCHAR(255) PRIMARY KEY,
                merchant_id VARCHAR(100),
                platform_id VARCHAR(50),
                access_token_encrypted TEXT,
                scope_permissions TEXT,
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """,
        )

        _run(
            "startup_pack_projects table",
            """
            CREATE TABLE IF NOT EXISTS startup_pack_projects (
                id SERIAL PRIMARY KEY,
                merchant_id VARCHAR(100) NOT NULL UNIQUE,
                brand_name VARCHAR(255),
                niche VARCHAR(100),
                target_audience VARCHAR(255),
                monthly_ad_budget REAL,
                design_vibe VARCHAR(100),
                has_domain BOOLEAN DEFAULT FALSE,
                sample_product VARCHAR(255),
                status VARCHAR(50) DEFAULT 'intake',
                brief TEXT,
                curated_suppliers TEXT,
                next_steps TEXT,
                admin_notes TEXT,
                checklist TEXT,
                suppliers TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """,
        )

        for col in ['brief', 'curated_suppliers', 'next_steps', 'admin_notes']:
            _run(f"startup_pack_projects.{col}", f"ALTER TABLE startup_pack_projects ADD COLUMN IF NOT EXISTS {col} TEXT")

        for wl_col in ['selected_plan', 'monthly_ad_spend']:
            _run(f"beta_waitlist_applications.{wl_col}", f"ALTER TABLE beta_waitlist_applications ADD COLUMN IF NOT EXISTS {wl_col} VARCHAR(100)")

        _run("beta_waitlist_applications.ad_plan_addon", "ALTER TABLE beta_waitlist_applications ADD COLUMN IF NOT EXISTS ad_plan_addon BOOLEAN DEFAULT FALSE")
        _run("beta_waitlist_applications.add_ons", "ALTER TABLE beta_waitlist_applications ADD COLUMN IF NOT EXISTS add_ons JSONB DEFAULT '[]'")

        _run(
            "active_sessions table",
            """
            CREATE TABLE IF NOT EXISTS active_sessions (
                token VARCHAR(255) PRIMARY KEY,
                merchant_id VARCHAR(100),
                role VARCHAR(50) DEFAULT 'Merchant',
                created_at TIMESTAMP DEFAULT NOW(),
                last_seen TIMESTAMP DEFAULT NOW()
            )
            """,
        )
        _run("active_sessions.last_seen", "ALTER TABLE active_sessions ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP DEFAULT NOW()")

        for col, typ in [
            ("tracking_number", "VARCHAR(100)"),
            ("carrier", "VARCHAR(50)"),
        ]:
            _run(f"profit_feed_orders.{col}", f"ALTER TABLE profit_feed_orders ADD COLUMN IF NOT EXISTS {col} {typ}")

        _run(
            "beta_waitlist_applications table",
            """
            CREATE TABLE IF NOT EXISTS beta_waitlist_applications (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) NOT NULL UNIQUE,
                business_name VARCHAR(255),
                monthly_volume VARCHAR(100),
                monthly_ad_spend VARCHAR(100),
                ad_channels VARCHAR(255),
                bottleneck TEXT,
                selected_plan VARCHAR(100),
                ad_plan_addon BOOLEAN DEFAULT FALSE,
                add_ons JSONB DEFAULT '[]',
                status VARCHAR(50) DEFAULT 'pending',
                merchant_id VARCHAR(100),
                notes TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                reviewed_at TIMESTAMP
            )
            """,
        )

        _run("saas_billing.add_ons", "ALTER TABLE saas_billing ADD COLUMN IF NOT EXISTS add_ons JSONB DEFAULT '[]'")

        for old_name in ['profit_feed_orders_order_id_key', 'profit_feed_orders_order_id_uq']:
            _run(f"drop constraint {old_name}", f"ALTER TABLE profit_feed_orders DROP CONSTRAINT IF EXISTS {old_name}")
            _run(f"drop index {old_name}", f"DROP INDEX IF EXISTS {old_name}")

        _run(
            "profit_feed_orders unique (merchant_id, order_id)",
            "ALTER TABLE profit_feed_orders ADD CONSTRAINT _profit_order_merchant_uc UNIQUE (merchant_id, order_id)",
        )

        for col, typ in [
            ("verified_at", "TIMESTAMP"),
            ("verification_report", "TEXT"),
        ]:
            _run(f"action_evidence.{col}", f"ALTER TABLE action_evidence ADD COLUMN IF NOT EXISTS {col} {typ}")

        _run("suppliers.email", "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS email VARCHAR(255)")

        _run(
            "marketing_campaigns table",
            """
            CREATE TABLE IF NOT EXISTS marketing_campaigns (
                id VARCHAR(36) PRIMARY KEY,
                merchant_id VARCHAR(100) NOT NULL,
                channel VARCHAR(50) NOT NULL,
                external_campaign_id VARCHAR(255) NOT NULL UNIQUE,
                campaign_name VARCHAR(255) NOT NULL,
                campaign_type VARCHAR(50) NOT NULL DEFAULT 'SPARK_ADS',
                sku_target VARCHAR(100),
                daily_budget NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
                current_spend_24h NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
                attributed_revenue_24h NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
                platform_coupons_cost NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
                affiliate_commissions_cost NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
                active_roas NUMERIC(6, 2) NOT NULL DEFAULT 0.00,
                status VARCHAR(50) NOT NULL DEFAULT 'active',
                updated_at TIMESTAMP DEFAULT NOW(),
                created_at TIMESTAMP DEFAULT NOW()
            )
            """,
        )
        _run(
            "marketing_campaigns.index",
            "CREATE INDEX IF NOT EXISTS idx_marketing_attribution ON marketing_campaigns(merchant_id, channel, status)",
        )

        _run(
            "shop_affiliates table",
            """
            CREATE TABLE IF NOT EXISTS shop_affiliates (
                id VARCHAR(36) PRIMARY KEY,
                merchant_id VARCHAR(100) NOT NULL,
                creator_handle VARCHAR(150) NOT NULL,
                creator_uid VARCHAR(255) NOT NULL,
                commission_rate_percentage INTEGER NOT NULL DEFAULT 10,
                gmv_generated_30d NUMERIC(12, 4) NOT NULL DEFAULT 0.0000,
                sample_fulfillment_status VARCHAR(50) NOT NULL DEFAULT 'none',
                reliability_rating INTEGER NOT NULL DEFAULT 100,
                last_active_at TIMESTAMP DEFAULT NOW(),
                created_at TIMESTAMP DEFAULT NOW()
            )
            """,
        )
        _run(
            "shop_affiliates.index",
            "CREATE INDEX IF NOT EXISTS idx_affiliate_creators ON shop_affiliates(merchant_id, creator_uid)",
        )


if __name__ == "__main__":
    run_migrations()
    print("Migration pass complete.")
