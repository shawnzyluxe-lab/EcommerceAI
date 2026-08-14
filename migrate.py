"""One-off database migration runner for Render pre/post deploy."""
import os
import sys
from sqlalchemy import create_engine, text


def run_migrations():
    # Use an admin/owner connection for DDL when available so the app can run
    # with a separate, RLS-restricted application role.
    raw_url = os.environ.get("DATABASE_URL_ADMIN") or os.environ.get("DATABASE_URL")
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
        def _run(label, stmt, params=None):
            try:
                conn.execute(text(stmt), params or {})
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

        _run(
            "generated_marketing_assets table",
            """
            CREATE TABLE IF NOT EXISTS generated_marketing_assets (
                id VARCHAR(36) PRIMARY KEY,
                merchant_id VARCHAR(100) NOT NULL,
                sku VARCHAR(100) NOT NULL,
                asset_id VARCHAR(50) NOT NULL UNIQUE,
                kind VARCHAR(50) NOT NULL,
                copy_payload JSONB DEFAULT '{}',
                state VARCHAR(50) NOT NULL DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """,
        )
        _run(
            "generated_marketing_assets.index",
            "CREATE INDEX IF NOT EXISTS idx_marketing_assets_merchant ON generated_marketing_assets(merchant_id, state)",
        )

        for col, typ in [
            ("max_authorized_seats", "INTEGER NOT NULL DEFAULT 1"),
            ("current_active_seats", "INTEGER NOT NULL DEFAULT 1"),
            ("stripe_customer_id", "VARCHAR(255)"),
            ("stripe_subscription_id", "VARCHAR(255)"),
        ]:
            _run(f"business_memory.{col}", f"ALTER TABLE business_memory ADD COLUMN IF NOT EXISTS {col} {typ}")

        _run(
            "merchant_workspace_seats table",
            """
            CREATE TABLE IF NOT EXISTS merchant_workspace_seats (
                id VARCHAR(36) PRIMARY KEY,
                merchant_id VARCHAR(100) NOT NULL,
                user_email VARCHAR(255) NOT NULL,
                role VARCHAR(50) NOT NULL DEFAULT 'merchant',
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE (merchant_id, user_email)
            )
            """,
        )
        _run(
            "merchant_workspace_seats.index",
            "CREATE INDEX IF NOT EXISTS idx_workspace_seats ON merchant_workspace_seats(merchant_id, user_email)",
        )

        # Dual-auth credential schema: integration links and secure token store
        _run(
            "integration_links table",
            """
            CREATE TABLE IF NOT EXISTS integration_links (
                id VARCHAR(36) PRIMARY KEY,
                merchant_id VARCHAR(100) NOT NULL,
                platform VARCHAR(50) NOT NULL,
                shopify_shop_domain VARCHAR(255),
                amazon_seller_id VARCHAR(100),
                amazon_region VARCHAR(20) DEFAULT 'us-east-1',
                status VARCHAR(50) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """,
        )
        for col, typ in [
            ("shopify_shop_domain", "VARCHAR(255)"),
            ("amazon_seller_id", "VARCHAR(100)"),
            ("amazon_region", "VARCHAR(20) DEFAULT 'us-east-1'"),
        ]:
            _run(f"integration_links.{col}", f"ALTER TABLE integration_links ADD COLUMN IF NOT EXISTS {col} {typ}")
        _run(
            "integration_links.index",
            "CREATE INDEX IF NOT EXISTS idx_integration_links_merchant ON integration_links(merchant_id, platform)",
        )

        _run(
            "secure_channel_credentials table",
            """
            CREATE TABLE IF NOT EXISTS secure_channel_credentials (
                id VARCHAR(36) PRIMARY KEY,
                integration_link_id VARCHAR(36) NOT NULL UNIQUE,
                encrypted_access_token TEXT NOT NULL,
                encrypted_refresh_token TEXT,
                tokens_expire_at TIMESTAMP WITH TIME ZONE,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (integration_link_id) REFERENCES integration_links(id) ON DELETE CASCADE
            )
            """,
        )
        _run(
            "secure_channel_credentials.index",
            "CREATE INDEX IF NOT EXISTS idx_secure_credentials ON secure_channel_credentials(integration_link_id)",
        )

        # Row-level security (RLS) for multi-tenant isolation
        _run(
            "rls.app_role",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vanta_saas_app_user') THEN
                    CREATE ROLE vanta_saas_app_user NOINHERIT LOGIN;
                END IF;
            END
            $$
            """,
        )

        # If a password is configured for the restricted app role, ensure it is set.
        rls_password = os.environ.get("RLS_APP_USER_PASSWORD")
        if rls_password:
            # PostgreSQL ALTER ROLE does not accept parameter placeholders for the
            # password, so we escape any single quotes and build the literal safely.
            safe_pw = rls_password.replace("'", "''")
            _run("rls.set_password", f"ALTER ROLE vanta_saas_app_user WITH PASSWORD '{safe_pw}'")

        # Grant the restricted app role the privileges it needs for runtime queries.
        # Existing per-table grants are broadened to all tables/sequences and defaults.
        _run("rls.grant.connect", "GRANT CONNECT ON DATABASE current_database() TO vanta_saas_app_user")
        _run("rls.grant.usage_schema", "GRANT USAGE ON SCHEMA public TO vanta_saas_app_user")
        _run(
            "rls.grant.all_tables",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vanta_saas_app_user",
        )
        _run(
            "rls.grant.all_sequences",
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vanta_saas_app_user",
        )
        _run(
            "rls.default_privs.tables",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vanta_saas_app_user",
        )
        _run(
            "rls.default_privs.sequences",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO vanta_saas_app_user",
        )

        for rls_table in ["merchant_profiles", "business_memory", "products", "orders"]:
            _run(
                f"rls.enable.{rls_table}",
                f"ALTER TABLE {rls_table} ENABLE ROW LEVEL SECURITY",
            )
            _run(
                f"rls.grant.{rls_table}",
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON {rls_table} TO vanta_saas_app_user",
            )

        for table_name in ["merchant_profiles", "business_memory", "products", "orders"]:
            policy_name = f"{table_name}_isolation_policy"
            _run(
                f"rls.policy.{table_name}",
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies
                        WHERE schemaname = 'public' AND tablename = '{table_name}' AND policyname = '{policy_name}'
                    ) THEN
                        CREATE POLICY {policy_name} ON {table_name}
                            FOR ALL
                            TO vanta_saas_app_user
                            USING (merchant_id = NULLIF(current_setting('app.current_merchant_id', true), ''));
                    END IF;
                END
                $$
                """,
            )

        # High-performance indexes for the True Profit Engine and AI COO Regression Engine
        _run(
            "idx.daily_costs.sku_date",
            "CREATE INDEX IF NOT EXISTS idx_daily_costs_sku_date ON daily_costs (sku, log_date DESC)",
        )
        _run(
            "idx.daily_costs.covering_metrics",
            "CREATE INDEX IF NOT EXISTS idx_daily_costs_covering_metrics ON daily_costs (sku, log_date) INCLUDE (ad_spend, ship_cost, fee, refund)",
        )
        _run(
            "idx.order_items.composite_lookup",
            "CREATE INDEX IF NOT EXISTS idx_order_items_composite_lookup ON order_items (order_id, sku) INCLUDE (qty, unit_cost)",
        )
        _run(
            "idx.orders.partial_fraud_exceptions",
            "CREATE INDEX IF NOT EXISTS idx_orders_partial_fraud_exceptions ON orders (fraud_score DESC, created_at DESC) WHERE status = 'pending' AND fraud_score >= 75",
        )

        # Refresh PostgreSQL planner statistics after schema/index changes
        _run("analyze.daily_costs", "ANALYZE daily_costs")
        _run("analyze.order_items", "ANALYZE order_items")
        _run("analyze.orders", "ANALYZE orders")


if __name__ == "__main__":
    run_migrations()
    print("Migration pass complete.")
