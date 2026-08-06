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


class ActiveSession(db.Model):
    __tablename__ = "active_sessions"
    token = db.Column(db.String(255), primary_key=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class BusinessMetric(db.Model):
    __tablename__ = "business_metrics"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), default="merchant_shawn_01")
    total_unified_balance = db.Column(db.REAL)
    true_net_profit = db.Column(db.REAL)
    gross_revenue = db.Column(db.REAL)
    ai_briefing = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class CommerceChannel(db.Model):
    __tablename__ = "commerce_channels"
    channel_id = db.Column(db.String(100), primary_key=True)
    channel_name = db.Column(db.String(255), nullable=False)
    pending_orders = db.Column(db.Integer, default=0)
    conversion_rate = db.Column(db.REAL)
    performance_status = db.Column(db.String(50))


class MerchantChannel(db.Model):
    __tablename__ = "merchant_channels"
    __table_args__ = (db.UniqueConstraint("merchant_id", "channel_id"),)
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    channel_id = db.Column(db.String(100))
    pending_orders = db.Column(db.Integer, default=0)
    conversion_rate = db.Column(db.REAL, default=0.0)


class SupportMetric(db.Model):
    __tablename__ = "support_metrics"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    active_chats = db.Column(db.Integer, default=0)
    sentiment_score = db.Column(db.String(100))
    recent_resolution = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class MarketingStudio(db.Model):
    __tablename__ = "marketing_studio"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    active_campaign = db.Column(db.String(255))
    generation_status = db.Column(db.String(100))
    platform_target = db.Column(db.String(100))
    copy_preview = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class OutboundTransmission(db.Model):
    __tablename__ = "outbound_transmissions"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    transmission_type = db.Column(db.String(100))
    recipient_address = db.Column(db.String(255))
    status_chip = db.Column(db.String(100))
    payload_summary = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())


class SaaSBilling(db.Model):
    __tablename__ = "saas_billing"
    merchant_id = db.Column(db.String(100), primary_key=True)
    stripe_customer_id = db.Column(db.String(100))
    stripe_subscription_item_id = db.Column(db.String(100))
    current_plan = db.Column(db.String(100))
    metered_usage_units = db.Column(db.Integer, default=0)
    accrued_invoice_value = db.Column(db.REAL, default=0.0)
    billing_cycle_end = db.Column(db.String(20))


class LocalProductCatalog(db.Model):
    __tablename__ = "local_product_catalog"
    shopify_product_id = db.Column(db.String(100), primary_key=True)
    title = db.Column(db.String(255))
    variant_id = db.Column(db.String(100))
    price = db.Column(db.REAL)
    inventory_quantity = db.Column(db.Integer)


class MerchantProfile(db.Model):
    __tablename__ = "merchant_profiles"
    merchant_id = db.Column(db.String(100), primary_key=True)
    business_name = db.Column(db.String(255))
    admin_email = db.Column(db.String(255), unique=True)
    password_hash = db.Column(db.String(255))
    account_tier = db.Column(db.String(50), default="Basic Tier")
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class TenantOAuthToken(db.Model):
    __tablename__ = "tenant_oauth_tokens"
    shop_domain = db.Column(db.String(255), primary_key=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    platform_id = db.Column(db.String(50))
    access_token_encrypted = db.Column(db.Text)
    scope_permissions = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, server_default=db.func.now())


class MerchantMetric(db.Model):
    __tablename__ = "multi_tenant_metrics"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    total_unified_balance = db.Column(db.REAL)
    true_net_profit = db.Column(db.REAL)
    gross_revenue = db.Column(db.REAL)
    ai_briefing = db.Column(db.Text)


class SystemExceptionLog(db.Model):
    __tablename__ = "system_exception_logs"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    module_origin = db.Column(db.String(100))
    error_severity = db.Column(db.String(50))
    exception_msg = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())


class AdSpendAnalytic(db.Model):
    __tablename__ = "ad_spend_analytics"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    platform_source = db.Column(db.String(255))
    budget_allocated = db.Column(db.REAL)
    current_spend = db.Column(db.REAL)
    roas = db.Column(db.REAL)
    conversion_count = db.Column(db.Integer)


class GeneratedPurchaseOrder(db.Model):
    __tablename__ = "generated_purchase_orders"
    po_reference = db.Column(db.String(100), primary_key=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    variant_sku = db.Column(db.String(100))
    units_ordered = db.Column(db.Integer)
    tracking_number = db.Column(db.String(255))
    fulfillment_status = db.Column(db.String(50), default="PENDING")
    updated_at = db.Column(db.DateTime, server_default=db.func.now())


class AIAgent(db.Model):
    __tablename__ = "ai_agents"
    agent_id = db.Column(db.String(100), primary_key=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    agent_name = db.Column(db.String(100))
    agent_role = db.Column(db.String(100))
    status = db.Column(db.String(50), default="IDLE")
    last_action = db.Column(db.Text)
    queued_payload = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, server_default=db.func.now())


class AgentMessage(db.Model):
    __tablename__ = "agent_messages"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    sender_agent = db.Column(db.String(100))
    recipient_agent = db.Column(db.String(100))
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    payload = db.Column(db.Text)
    action_taken = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class MerchantDecisionLog(db.Model):
    __tablename__ = "merchant_decision_logs"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    action_trigger_type = db.Column(db.String(100))
    decision_type = db.Column(db.String(100))
    user_decision_vector = db.Column(db.String(50))
    chosen_variant_or_supplier = db.Column(db.String(255))
    computed_confidence_score = db.Column(db.REAL)
    decision_vector = db.Column(db.Text)
    context_snapshot = db.Column(db.Text)
    outcome = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class ProcessedWebhookEvent(db.Model):
    __tablename__ = "processed_webhook_events"
    event_id = db.Column(db.String(255), primary_key=True)
    processed_at = db.Column(db.DateTime, server_default=db.func.now())


class PredictiveLogistics(db.Model):
    __tablename__ = "predictive_logistics"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    variant_sku = db.Column(db.String(100), unique=True, nullable=False)
    days_remaining = db.Column(db.Integer)
    forecasted_demand_velocity = db.Column(db.REAL)
    optimal_restock_date = db.Column(db.String(20))
    status_flag = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, server_default=db.func.now())
