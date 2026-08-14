"""PostgreSQL row-level security helpers for Vantav.

RLS policies are applied to the restricted application role (`vanta_saas_app_user`).
When the application connects as that role it must call `set_tenant_scope()` before
querying tenant tables; otherwise the policies return no rows.
"""

import os
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import text

from models import db


RLS_APP_ROLE = "vanta_saas_app_user"


def set_tenant_scope(merchant_id: Optional[str]) -> None:
    """Set the PostgreSQL runtime variable used by RLS policies for this transaction."""
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url or raw_url.startswith("sqlite"):
        return
    value = merchant_id or ""
    db.session.execute(
        text("SELECT set_config('app.current_merchant_id', :mid, true)"),
        {"mid": value},
    )


def reset_tenant_scope() -> None:
    """Clear the tenant scope variable for the current transaction."""
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url or raw_url.startswith("sqlite"):
        return
    db.session.execute(
        text("SELECT set_config('app.current_merchant_id', '', true)")
    )


@contextmanager
def scoped_session(merchant_id: Optional[str]):
    """Context manager that sets the tenant scope around a block of database work."""
    set_tenant_scope(merchant_id)
    try:
        yield
    finally:
        reset_tenant_scope()
