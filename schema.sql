-- Multi-User CA Mirror Bot Database Schema
-- ==========================================

-- Users table - stores user info and subscription details
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    
    -- Telegram API credentials (encrypted)
    api_id_encrypted TEXT,
    api_hash_encrypted TEXT,
    phone_encrypted TEXT,
    
    -- Subscription info
    subscription_tier TEXT DEFAULT 'free',  -- free, starter, pro, alpha
    subscription_expires_at TIMESTAMP,
    payment_method TEXT DEFAULT 'telegram_stars',
    
    -- Usage tracking
    daily_ca_limit INTEGER DEFAULT 3,
    daily_ca_count INTEGER DEFAULT 0,
    last_reset_date DATE,
    total_routes_allowed INTEGER DEFAULT 1,
    
    -- Session status
    session_active BOOLEAN DEFAULT 0,
    session_path TEXT,
    
    -- Analytics
    total_cas_forwarded INTEGER DEFAULT 0,
    total_messages_forwarded INTEGER DEFAULT 0,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Referral system (for future)
    referred_by INTEGER,
    referral_code TEXT UNIQUE
);

-- Routes table - each user's monitoring routes
CREATE TABLE IF NOT EXISTS routes (
    route_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    
    -- Route configuration
    source_chat_id INTEGER NOT NULL,
    target_chat_id INTEGER NOT NULL,
    source_name TEXT,
    target_name TEXT,
    
    -- Filter settings
    filter_type TEXT DEFAULT 'ca_only',
    filter_config TEXT,  -- JSON string for complex filters
    
    -- Status
    is_active BOOLEAN DEFAULT 1,
    
    -- Stats
    total_forwarded INTEGER DEFAULT 0,
    last_forwarded_at TIMESTAMP,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Forwarded CAs tracking - for analytics and deduplication
CREATE TABLE IF NOT EXISTS forwarded_cas (
    ca_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    route_id INTEGER NOT NULL,
    
    -- CA details
    ca_address TEXT NOT NULL,
    source_chat_id INTEGER,
    source_message_id INTEGER,
    
    -- Message content
    original_message TEXT,
    sender_name TEXT,
    
    -- Performance tracking (for analytics)
    initial_market_cap REAL,
    current_market_cap REAL,
    price_change_24h REAL,
    last_price_check TIMESTAMP,
    
    -- Timestamps
    forwarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (route_id) REFERENCES routes(route_id) ON DELETE CASCADE
);

-- Payments table - track all payments
CREATE TABLE IF NOT EXISTS payments (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    
    -- Payment details
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'XTR',  -- Telegram Stars
    tier TEXT NOT NULL,
    
    -- Telegram Stars specific
    telegram_payment_id TEXT,
    invoice_payload TEXT,
    
    -- Status
    status TEXT DEFAULT 'pending',  -- pending, completed, failed, refunded
    
    -- Period
    period_start TIMESTAMP,
    period_end TIMESTAMP,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- User sessions log - track login attempts
CREATE TABLE IF NOT EXISTS session_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    
    -- Session details
    action TEXT,  -- created, login_attempt, success, failed
    ip_address TEXT,
    error_message TEXT,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Admin actions log
CREATE TABLE IF NOT EXISTS admin_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_user_id INTEGER NOT NULL,
    
    -- Action details
    action_type TEXT,  -- grant_subscription, revoke, modify_limits, etc
    target_user_id INTEGER,
    details TEXT,  -- JSON string
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Forwarded URLs tracking - for analytics and deduplication
CREATE TABLE IF NOT EXISTS forwarded_urls (
    url_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    route_id INTEGER NOT NULL,
    
    -- URL details
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    source_chat_id INTEGER NOT NULL,
    source_message_id INTEGER,
    
    -- Message content
    sender_name TEXT,
    
    -- Timestamps
    forwarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (route_id) REFERENCES routes(route_id) ON DELETE CASCADE
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_routes_user_id ON routes(user_id);
CREATE INDEX IF NOT EXISTS idx_routes_source ON routes(source_chat_id);
CREATE INDEX IF NOT EXISTS idx_forwarded_cas_user ON forwarded_cas(user_id);
CREATE INDEX IF NOT EXISTS idx_forwarded_cas_route ON forwarded_cas(route_id);
CREATE INDEX IF NOT EXISTS idx_forwarded_cas_address ON forwarded_cas(ca_address);
CREATE INDEX IF NOT EXISTS idx_forwarded_urls_user ON forwarded_urls(user_id);
CREATE INDEX IF NOT EXISTS idx_forwarded_urls_hash ON forwarded_urls(url_hash);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_users_tier ON users(subscription_tier);
CREATE INDEX IF NOT EXISTS idx_users_expires ON users(subscription_expires_at);

