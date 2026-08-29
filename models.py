import uuid
from datetime import datetime
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


class UserAuthentication(db.Model):
    __tablename__ = "user_authentication"
    __table_args__ = (
        db.Index("idx_user_auth_lookup", "email", "account_status"),
    )
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    clearance_level = db.Column(db.String(50), nullable=False, default="merchant")
    account_status = db.Column(db.String(50), nullable=False, default="active")
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class ActiveSessionVault(db.Model):
    __tablename__ = "active_session_vault"
    __table_args__ = (
        db.Index("idx_session_vault_expiry", "session_token", "expires_at"),
    )
    session_token = db.Column(db.String(512), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey("user_authentication.id"), nullable=False)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False)
    ip_address = db.Column(db.String(45))
    expires_at = db.Column(db.DateTime, nullable=False)
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
    role = db.Column(db.String(50), default="Merchant")
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    last_seen = db.Column(db.DateTime, server_default=db.func.now())
    # Admin impersonation: an admin can view the dashboard as another merchant.
    impersonating_merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=True)
    original_merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=True)
    original_role = db.Column(db.String(50), nullable=True)


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
    add_ons = db.Column(db.JSON, default=list)
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
    # Vetted Operator sandbox lifecycle
    sandbox_status = db.Column(db.String(50), default="pending")  # pending, sandbox, approved, rejected
    sandbox_started_at = db.Column(db.DateTime)
    sandbox_expires_at = db.Column(db.DateTime)
    live_access_enabled = db.Column(db.Integer, default=0)  # 0 = false, 1 = true
    approved_at = db.Column(db.DateTime)
    brand_color = db.Column(db.String(7))
    brand_color_secondary = db.Column(db.String(7))
    feature_flags = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class PendingAction(db.Model):
    __tablename__ = "pending_actions"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    alert_id = db.Column(db.Integer, db.ForeignKey("alert_matrix_alerts.id"), nullable=True)
    action_type = db.Column(db.String(50), nullable=False)  # reorder, refund, ad_adjust, reroute
    title = db.Column(db.String(255), nullable=False)
    detail = db.Column(db.Text)
    payload = db.Column(db.Text)  # JSON blob for action parameters
    status = db.Column(db.String(50), default="pending")  # pending, approved, denied, executed
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    decided_at = db.Column(db.DateTime)
    decision_by = db.Column(db.String(100))
    result_summary = db.Column(db.Text)


class StartupPackProject(db.Model):
    __tablename__ = "startup_pack_projects"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, unique=True, index=True)
    brand_name = db.Column(db.String(255))
    niche = db.Column(db.String(100))
    target_audience = db.Column(db.String(255))
    monthly_ad_budget = db.Column(db.REAL)
    design_vibe = db.Column(db.String(100))
    has_domain = db.Column(db.Boolean, default=False)
    sample_product = db.Column(db.String(255))
    status = db.Column(db.String(50), default="intake")  # intake, pending_brief, delivered, in_progress, launched
    brief = db.Column(db.Text)
    curated_suppliers = db.Column(db.Text)  # JSON list set by admin
    next_steps = db.Column(db.Text)
    admin_notes = db.Column(db.Text)
    checklist = db.Column(db.Text)  # JSON list
    suppliers = db.Column(db.Text)  # legacy / deprecated
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, onupdate=db.func.now())


class BetaWaitlistApplication(db.Model):
    __tablename__ = "beta_waitlist_applications"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    business_name = db.Column(db.String(255))
    monthly_volume = db.Column(db.String(100))
    monthly_ad_spend = db.Column(db.String(100))
    ad_channels = db.Column(db.String(255))  # comma-separated list (Shopify, TikTok Shop, Amazon, eBay)
    bottleneck = db.Column(db.Text)
    selected_plan = db.Column(db.String(100))  # beta_plan
    ad_plan_addon = db.Column(db.Boolean, default=False)
    add_ons = db.Column(db.JSON, default=list)
    status = db.Column(db.String(50), default="pending")  # pending, sandbox, approved, rejected
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    reviewed_at = db.Column(db.DateTime)


class TenantOAuthToken(db.Model):
    __tablename__ = "tenant_oauth_tokens"
    shop_domain = db.Column(db.String(255), primary_key=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    platform_id = db.Column(db.String(50))
    access_token_encrypted = db.Column(db.Text)
    scope_permissions = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, server_default=db.func.now())


class IntegrationLink(db.Model):
    __tablename__ = "integration_links"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    platform = db.Column(db.String(50), nullable=False)  # shopify, amazon, tiktok
    shopify_shop_domain = db.Column(db.String(255))
    amazon_seller_id = db.Column(db.String(100))
    amazon_region = db.Column(db.String(20), default="us-east-1")
    status = db.Column(db.String(50), default="active")
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


class SecureChannelCredential(db.Model):
    __tablename__ = "secure_channel_credentials"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    integration_link_id = db.Column(
        db.String(36),
        db.ForeignKey("integration_links.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    encrypted_access_token = db.Column(db.Text, nullable=False)
    encrypted_refresh_token = db.Column(db.Text)
    tokens_expire_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


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


class SupportMessage(db.Model):
    __tablename__ = "support_messages"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    sender = db.Column(db.String(50), nullable=False)  # 'admin' or 'merchant'
    sender_email = db.Column(db.String(255))
    message = db.Column(db.Text, nullable=False)
    read_at = db.Column(db.DateTime)
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


class MagicLoginToken(db.Model):
    __tablename__ = "magic_login_tokens"
    token = db.Column(db.String(255), primary_key=True)
    admin_email = db.Column(db.String(255))
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"))
    expires_at = db.Column(db.DateTime)
    is_used = db.Column(db.Integer, default=0)


class Alert(db.Model):
    __tablename__ = "alert_matrix_alerts"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    alert_type = db.Column(db.String(50), nullable=False, index=True)  # inventory_runout, low_inventory, fraud_risk, ad_spend
    severity = db.Column(db.String(20), default="warn")  # crit, warn, good
    title = db.Column(db.String(255), nullable=False)
    detail = db.Column(db.Text)
    source_id = db.Column(db.String(100), nullable=False)  # deterministic source for dedup
    status = db.Column(db.String(20), default="open")  # open, snoozed, resolved
    dispatched_to = db.Column(db.Text)  # JSON array of channel names
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    resolved_at = db.Column(db.DateTime)


class PredictiveLogistics(db.Model):
    __tablename__ = "predictive_logistics"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    variant_sku = db.Column(db.String(100), unique=True, nullable=False)
    days_remaining = db.Column(db.Integer)
    forecasted_demand_velocity = db.Column(db.REAL)
    optimal_restock_date = db.Column(db.String(20))
    status_flag = db.Column(db.String(50), default="NORMAL")


class TrendingProduct(db.Model):
    __tablename__ = "trending_products"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), default="merchant_shawn_01")
    source_platform = db.Column(db.String(50))  # TikTok_Shop, Amazon_Bestsellers
    external_item_id = db.Column(db.String(100))
    title = db.Column(db.String(500))
    sample_image_url = db.Column(db.String(500))
    current_velocity_score = db.Column(db.REAL)
    scraped_at = db.Column(db.DateTime, default=datetime.utcnow)
    tier = db.Column(db.String(50), default="Tier 2")  # Tier 1 (Weekly Top 50), Tier 2 (Momentum)
    alert_status = db.Column(db.String(50), default="Active")
    status_flag = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class ProductFinancialLedger(db.Model):
    __tablename__ = "product_financial_ledger"
    ledger_id = db.Column(db.String(36), primary_key=True, default=_uuid)
    tenant_id = db.Column(db.String(50), nullable=False)
    order_id = db.Column(db.String(100), unique=True, nullable=False)
    sales_channel = db.Column(db.String(50), nullable=False)  # Shopify, Amazon, TikTokShop
    gross_revenue = db.Column(db.Numeric(10, 2), nullable=False)
    marketplace_fees = db.Column(db.Numeric(10, 2), nullable=False)
    cost_of_goods_sold = db.Column(db.Numeric(10, 2), nullable=False)
    shipping_costs = db.Column(db.Numeric(10, 2), nullable=False)
    net_profit = db.Column(db.Numeric(10, 2), nullable=False)
    recorded_at = db.Column(db.DateTime, server_default=db.func.now())


class MerchantSetting(db.Model):
    __tablename__ = "merchant_settings"
    merchant_id = db.Column(db.String(100), nullable=False)
    setting_key = db.Column(db.String(100), nullable=False)
    setting_value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    __table_args__ = (db.PrimaryKeyConstraint('merchant_id', 'setting_key'),)


class ProfitFeedOrder(db.Model):
    __tablename__ = "profit_feed_orders"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    order_id = db.Column(db.String(100), nullable=False)
    channel = db.Column(db.String(50), nullable=False)  # shopify, tiktok, amazon, etsy, ebay, etc.
    items = db.Column(db.Integer, default=1)
    gross_revenue = db.Column(db.REAL, nullable=False, default=0.0)
    marketplace_fees = db.Column(db.REAL, nullable=False, default=0.0)
    cost_of_goods_sold = db.Column(db.REAL, nullable=False, default=0.0)
    shipping_costs = db.Column(db.REAL, nullable=False, default=0.0)
    ad_spend_attributed = db.Column(db.REAL, nullable=False, default=0.0)
    refund_amount = db.Column(db.REAL, nullable=False, default=0.0)
    net_profit = db.Column(db.REAL, nullable=False, default=0.0)
    state = db.Column(db.String(50), default="shipped")  # shipped, delayed, refunded, packed, cancelled
    tracking_number = db.Column(db.String(100))
    carrier = db.Column(db.String(50))
    recorded_at = db.Column(db.DateTime, server_default=db.func.now())
    __table_args__ = (db.UniqueConstraint('merchant_id', 'order_id', name='_profit_order_merchant_uc'),)


class AdSpendFeed(db.Model):
    __tablename__ = "ad_spend_feed"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    platform_source = db.Column(db.String(100), nullable=False)  # meta, tiktok, google, shopify, amazon
    amount = db.Column(db.REAL, nullable=False, default=0.0)
    conversion_count = db.Column(db.Integer, default=0)
    recorded_at = db.Column(db.DateTime, server_default=db.func.now())


class BusinessMemory(db.Model):
    __tablename__ = "business_memory"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, unique=True)

    max_cac_threshold = db.Column(db.Numeric(12, 4), nullable=False, default=18.00)
    floor_margin_percentage = db.Column(db.Integer, nullable=False, default=20)
    max_daily_ad_spend = db.Column(db.Numeric(12, 4), nullable=False, default=500.00)

    autopilot_enabled = db.Column(db.Boolean, nullable=False, default=False)
    autopilot_max_order_value = db.Column(db.Numeric(12, 4), nullable=False, default=500.00)
    autopilot_max_action_cost = db.Column(db.Numeric(12, 4), nullable=False, default=100.00)
    auto_approve_action_types = db.Column(db.JSON, default=lambda: ["reorder"])
    required_approval_action_types = db.Column(db.JSON, default=lambda: ["refund", "ad_adjust"])
    learned_preferences = db.Column(db.JSON, default=dict)

    forbidden_discount_skus = db.Column(db.JSON, default=list)
    preferred_supplier_ids = db.Column(db.JSON, default=dict)
    auto_escalation_rules = db.Column(db.JSON, default=lambda: {
        "refund_rate_ceiling": 0.05,
        "out_of_stock_buffer_days": 5,
    })

    # Seat allocation and Stripe billing indices
    max_authorized_seats = db.Column(db.Integer, nullable=False, default=1)
    current_active_seats = db.Column(db.Integer, nullable=False, default=1)
    stripe_customer_id = db.Column(db.String(255), nullable=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True)

    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class WorkspaceSeat(db.Model):
    __tablename__ = "merchant_workspace_seats"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id", ondelete="CASCADE"), nullable=False, index=True)
    user_email = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False, default="merchant")  # admin, engineer, merchant
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    __table_args__ = (db.UniqueConstraint("merchant_id", "user_email", name="unique_merchant_email"),)


class ActionEvidence(db.Model):
    __tablename__ = "action_evidence"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    action_id = db.Column(db.Integer, db.ForeignKey("pending_actions.id"), nullable=False, unique=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False)

    confidence_score = db.Column(db.Integer, nullable=False, default=82)
    expected_weekly_impact_min = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    expected_weekly_impact_max = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)

    telemetry_evidence_log = db.Column(db.JSON, default=lambda: {
        "conversion_rate_delta": 0.0,
        "competitor_median_price": 0.0,
        "sales_velocity_delta": 0.0,
        "historical_trend_days": 14,
    })
    reasoning_summary = db.Column(db.Text, nullable=False, default="AI evaluated this as a high-impact opportunity")

    before_metrics = db.Column(db.JSON, default=dict)
    after_metrics = db.Column(db.JSON, default=dict)
    execution_report = db.Column(db.Text)
    verified_at = db.Column(db.DateTime)
    verification_report = db.Column(db.Text)

    created_at = db.Column(db.DateTime, server_default=db.func.now())


class Supplier(db.Model):
    __tablename__ = "suppliers"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    lead_days = db.Column(db.Integer, nullable=False, default=14)
    defect_rate = db.Column(db.Numeric(5, 4), nullable=False, default=0.0000)
    refund_rate = db.Column(db.Numeric(5, 4), nullable=False, default=0.0000)
    reliability_score = db.Column(db.Integer, nullable=False, default=100)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class Product(db.Model):
    __tablename__ = "products"
    sku = db.Column(db.String(100), primary_key=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    channel_ids = db.Column(db.JSON, nullable=False, default=dict)
    on_hand = db.Column(db.Integer, nullable=False, default=0)
    inbound = db.Column(db.Integer, nullable=False, default=0)
    reorder_point = db.Column(db.Integer, nullable=False, default=10)
    unit_cost = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    supplier_id = db.Column(db.String(36), db.ForeignKey("suppliers.id", ondelete="SET NULL"))
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


class UnifiedOrder(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.String(150), primary_key=True)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    channel = db.Column(db.String(50), nullable=False)
    revenue = db.Column(db.Numeric(12, 4), nullable=False)
    shipping_charged = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    tax = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    status = db.Column(db.String(50), nullable=False, default="pending")
    fraud_score = db.Column(db.Integer, nullable=False, default=0)
    customer_id = db.Column(db.String(255))
    ship_to = db.Column(db.JSON, nullable=False, default=dict)
    promised_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())


class OrderItem(db.Model):
    __tablename__ = "order_items"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    order_id = db.Column(db.String(150), db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    sku = db.Column(db.String(100), db.ForeignKey("products.sku", ondelete="RESTRICT"), nullable=False, index=True)
    qty = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 4), nullable=False)
    unit_cost = db.Column(db.Numeric(12, 4), nullable=False)


class DailyCost(db.Model):
    __tablename__ = "daily_costs"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    sku = db.Column(db.String(100), db.ForeignKey("products.sku", ondelete="CASCADE"), nullable=False, index=True)
    log_date = db.Column(db.Date, nullable=False)
    ad_spend = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    ship_cost = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    fee = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    refund = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    tax = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    __table_args__ = (db.UniqueConstraint("sku", "log_date", name="unique_sku_date"),)


class MarketingCampaign(db.Model):
    __tablename__ = "marketing_campaigns"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    channel = db.Column(db.String(50), nullable=False)  # tiktok_ads, meta_ads
    external_campaign_id = db.Column(db.String(255), nullable=False, unique=True)
    campaign_name = db.Column(db.String(255), nullable=False)
    campaign_type = db.Column(db.String(50), nullable=False, default="SPARK_ADS")  # GMV_MAX_PRO, SPARK_ADS
    sku_target = db.Column(db.String(100), nullable=True)
    daily_budget = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    current_spend_24h = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    attributed_revenue_24h = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    platform_coupons_cost = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    affiliate_commissions_cost = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    active_roas = db.Column(db.Numeric(6, 2), nullable=False, default=0.00)
    status = db.Column(db.String(50), nullable=False, default="active")
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    __table_args__ = (db.Index("idx_marketing_attribution", "merchant_id", "channel", "status"),)


class ShopAffiliate(db.Model):
    __tablename__ = "shop_affiliates"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    creator_handle = db.Column(db.String(150), nullable=False)
    creator_uid = db.Column(db.String(255), nullable=False)
    commission_rate_percentage = db.Column(db.Integer, nullable=False, default=10)
    gmv_generated_30d = db.Column(db.Numeric(12, 4), nullable=False, default=0.0000)
    sample_fulfillment_status = db.Column(db.String(50), nullable=False, default="none")
    reliability_rating = db.Column(db.Integer, nullable=False, default=100)
    last_active_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    __table_args__ = (db.Index("idx_affiliate_creators", "merchant_id", "creator_uid"),)


class GeneratedMarketingAsset(db.Model):
    __tablename__ = "generated_marketing_assets"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    merchant_id = db.Column(db.String(100), db.ForeignKey("merchant_profiles.merchant_id"), nullable=False, index=True)
    sku = db.Column(db.String(100), nullable=False, index=True)
    asset_id = db.Column(db.String(50), nullable=False, unique=True)
    kind = db.Column(db.String(50), nullable=False)  # tiktok_description, sms_blast, email_sequence
    copy_payload = db.Column(db.JSON, nullable=False, default=dict)
    state = db.Column(db.String(50), nullable=False, default="draft")  # draft, approved, rejected, sent
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    __table_args__ = (db.Index("idx_marketing_assets_merchant", "merchant_id", "state"),)


class AdminPlatformControl(db.Model):
    """Global platform switches controlled from the admin panel."""
    __tablename__ = "admin_platform_controls"
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.JSON, default=dict)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    @classmethod
    def get(cls, key, default=None):
        row = cls.query.get(key)
        if not row:
            return default
        return row.value if row.value is not None else default

    @classmethod
    def set(cls, key, value):
        row = cls.query.get(key)
        if not row:
            row = cls(key=key)
            db.session.add(row)
        row.value = value
        db.session.commit()

    @classmethod
    def get_bool(cls, key, default=False):
        val = cls.get(key, default)
        if isinstance(val, bool):
            return val
        if isinstance(val, dict):
            return bool(val.get("enabled", default))
        return bool(val)

    @classmethod
    def set_bool(cls, key, enabled):
        cls.set(key, bool(enabled))


class AdminAuditLog(db.Model):
    """Immutable admin action log for the control panel."""
    __tablename__ = "admin_audit_logs"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    admin_email = db.Column(db.String(255))
    action = db.Column(db.String(100), nullable=False, index=True)
    target_merchant_id = db.Column(db.String(100), index=True)
    details = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), index=True)
