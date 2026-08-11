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
