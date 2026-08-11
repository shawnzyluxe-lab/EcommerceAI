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
        # SQLite tables are created fresh by db.create_all() in development.
        print("SQLite detected; skipping raw migrations.")
        return
    if raw_url.startswith("postgresql://"):
        url = "postgresql+psycopg" + raw_url[len("postgresql"):]
    elif raw_url.startswith("postgres://"):
        url = "postgresql+psycopg" + raw_url[len("postgres"):]
    else:
        url = raw_url

    engine = create_engine(url)
    with engine.begin() as conn:
        # Add sandbox lifecycle columns to merchant_profiles if missing.
        for col, typ in [
            ("sandbox_status", "VARCHAR(50)"),
            ("sandbox_started_at", "TIMESTAMP"),
            ("sandbox_expires_at", "TIMESTAMP"),
            ("live_access_enabled", "INTEGER"),
            ("approved_at", "TIMESTAMP"),
        ]:
            conn.execute(text(f"ALTER TABLE merchant_profiles ADD COLUMN IF NOT EXISTS {col} {typ}"))
            print(f"Ensured merchant_profiles.{col}")

        # Create the pending actions table if missing.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id SERIAL PRIMARY KEY,
                merchant_id VARCHAR(100) NOT NULL REFERENCES merchant_profiles(merchant_id),
                alert_id INTEGER REFERENCES alert_matrix_alerts(id),
                action_type VARCHAR(50) NOT NULL,
                title VARCHAR(255) NOT NULL,
                detail TEXT,
                payload TEXT,
                status VARCHAR(50) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW(),
                decided_at TIMESTAMP,
                decision_by VARCHAR(100),
                result_summary TEXT
            )
        """))

        # Create merchant channel and OAuth token tables if missing.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS merchant_channels (
                id SERIAL PRIMARY KEY,
                merchant_id VARCHAR(100) REFERENCES merchant_profiles(merchant_id),
                channel_id VARCHAR(100) NOT NULL,
                pending_orders INTEGER DEFAULT 0,
                conversion_rate REAL DEFAULT 0.0,
                UNIQUE (merchant_id, channel_id)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tenant_oauth_tokens (
                shop_domain VARCHAR(255) PRIMARY KEY,
                merchant_id VARCHAR(100) REFERENCES merchant_profiles(merchant_id),
                platform_id VARCHAR(50),
                access_token_encrypted TEXT,
                scope_permissions TEXT,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))

        # Create the startup pack projects table if missing.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS startup_pack_projects (
                id SERIAL PRIMARY KEY,
                merchant_id VARCHAR(100) NOT NULL UNIQUE REFERENCES merchant_profiles(merchant_id),
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
        """))
        for col in ['brief', 'curated_suppliers', 'next_steps', 'admin_notes']:
            try:
                conn.execute(text(f"ALTER TABLE startup_pack_projects ADD COLUMN IF NOT EXISTS {col} TEXT"))
            except Exception as e:
                print(f"[migrate] {col} add skipped: {e}")

        # Create the beta waitlist applications table if missing.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS beta_waitlist_applications (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) NOT NULL UNIQUE,
                business_name VARCHAR(255),
                monthly_volume VARCHAR(100),
                ad_channels VARCHAR(255),
                bottleneck TEXT,
                status VARCHAR(50) DEFAULT 'pending',
                merchant_id VARCHAR(100) REFERENCES merchant_profiles(merchant_id),
                notes TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                reviewed_at TIMESTAMP
            )
        """))
        print("Ensured beta_waitlist_applications table")


if __name__ == "__main__":
    try:
        run_migrations()
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        sys.exit(1)
