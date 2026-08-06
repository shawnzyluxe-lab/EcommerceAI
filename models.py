import uuid
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _uuid():
    return str(uuid.uuid4())


class Tenant(db.Model):
    __tablename__ = "tenants"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    company_name = db.Column(db.String(255), nullable=False)
    tier_level = db.Column(db.String(50), default="Starter")
    monthly_order_limit = db.Column(db.Integer, default=500)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    users = db.relationship("User", backref="tenant", lazy=True)
    channels = db.relationship("ConnectedChannel", backref="tenant", lazy=True)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class ConnectedChannel(db.Model):
    __tablename__ = "connected_channels"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    channel_type = db.Column(db.String(100), nullable=False)
    store_name = db.Column(db.String(255), nullable=False)
    api_access_token = db.Column(db.Text, nullable=False)
    sync_status = db.Column(db.String(50), default="Active")
    last_successful_sync = db.Column(db.DateTime, nullable=True)
