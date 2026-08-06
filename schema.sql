-- Shawnzyluxe — 3-Tier Multi-Tenant Account Architecture

-- 1. TENANT ACCOUNTS (E-commerce Stores/Brands)
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name VARCHAR(255) NOT NULL,            -- e.g., 'GymStore LLC'
    tier_level VARCHAR(50) DEFAULT 'Starter',      -- 'Starter', 'Pro', 'Enterprise'
    monthly_order_limit INT DEFAULT 500,           -- Cap enforcement column
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. USERS (Staff, Admins, and Engineers)
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),         -- Foreign key to partition store data
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL,                     -- 'Admin', 'Engineer', 'Merchant_Staff'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. CHANNELS (Connected Storefront Endpoints)
CREATE TABLE connected_channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    channel_type VARCHAR(100) NOT NULL,            -- 'Shopify', 'Amazon', 'TikTokShop'
    store_name VARCHAR(255) NOT NULL,              -- e.g., '://myshopify.com'
    api_access_token TEXT NOT NULL,                -- Encrypted store credential
    sync_status VARCHAR(50) DEFAULT 'Active',
    last_successful_sync TIMESTAMP
);
