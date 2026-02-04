"""
Database Management Module
===========================
Handles all database operations for multi-user CA mirror bot
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from cryptography.fernet import Fernet
import os

class Database:
    def __init__(self, db_path: str = "bot_data_multiuser.db"):
        self.db_path = db_path
        self.encryption_key = self._get_or_create_encryption_key()
        self.cipher = Fernet(self.encryption_key)
        self._init_database()
    
    def _get_or_create_encryption_key(self) -> bytes:
        """Get or create encryption key for sensitive data"""
        key_file = ".encryption_key"
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with open(key_file, 'wb') as f:
                f.write(key)
            return key
    
    def _init_database(self):
        """Initialize database with schema"""
        with open('schema.sql', 'r') as f:
            schema = f.read()
        
        conn = sqlite3.connect(self.db_path)
        conn.executescript(schema)
        conn.close()
        print("✅ Database initialized")
    
    def _encrypt(self, data: str) -> str:
        """Encrypt sensitive data"""
        return self.cipher.encrypt(data.encode()).decode()
    
    def _decrypt(self, encrypted_data: str) -> str:
        """Decrypt sensitive data"""
        return self.cipher.decrypt(encrypted_data.encode()).decode()
    
    # ==================== USER MANAGEMENT ====================
    
    def create_user(self, user_id: int, username: str = None, first_name: str = None) -> bool:
        """Create new user"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR IGNORE INTO users (user_id, username, first_name)
                VALUES (?, ?, ?)
            """, (user_id, username, first_name))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ Error creating user: {e}")
            return False
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user details"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        return None
    
    def update_user_credentials(self, user_id: int, api_id: str, api_hash: str, phone: str) -> bool:
        """Store encrypted user credentials"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE users 
                SET api_id_encrypted = ?,
                    api_hash_encrypted = ?,
                    phone_encrypted = ?,
                    session_active = 1,
                    session_path = ?
                WHERE user_id = ?
            """, (
                self._encrypt(api_id),
                self._encrypt(api_hash),
                self._encrypt(phone),
                f"sessions/user_{user_id}.session",
                user_id
            ))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ Error updating credentials: {e}")
            return False
    
    def get_user_credentials(self, user_id: int) -> Optional[Tuple[str, str, str]]:
        """Get decrypted user credentials"""
        user = self.get_user(user_id)
        if not user or not user['api_id_encrypted']:
            return None
        
        try:
            api_id = self._decrypt(user['api_id_encrypted'])
            api_hash = self._decrypt(user['api_hash_encrypted'])
            phone = self._decrypt(user['phone_encrypted'])
            return (api_id, api_hash, phone)
        except Exception as e:
            print(f"❌ Error decrypting credentials: {e}")
            return None
    
    def update_subscription(self, user_id: int, tier: str, duration_days: int = 30) -> bool:
        """Update user subscription"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Determine limits based on tier
            tier_limits = {
                'free': {'routes': 1, 'cas': 3},
                'starter': {'routes': 3, 'cas': 100},
                'pro': {'routes': 10, 'cas': 500},
                'alpha': {'routes': 999, 'cas': 999999}
            }
            
            limits = tier_limits.get(tier, tier_limits['free'])
            expires_at = datetime.now() + timedelta(days=duration_days)
            
            cursor.execute("""
                UPDATE users 
                SET subscription_tier = ?,
                    subscription_expires_at = ?,
                    total_routes_allowed = ?,
                    daily_ca_limit = ?
                WHERE user_id = ?
            """, (tier, expires_at, limits['routes'], limits['cas'], user_id))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ Error updating subscription: {e}")
            return False
    
    def check_subscription_active(self, user_id: int) -> bool:
        """Check if user's subscription is active"""
        user = self.get_user(user_id)
        if not user:
            return False
        
        if user['subscription_tier'] == 'free':
            return True
        
        if not user['subscription_expires_at']:
            return False
        
        expires = datetime.fromisoformat(user['subscription_expires_at'])
        return datetime.now() < expires
    
    def increment_daily_ca_count(self, user_id: int) -> bool:
        """Increment daily CA count and reset if needed"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        user = self.get_user(user_id)
        today = datetime.now().date()
        
        if user['last_reset_date']:
            last_reset = datetime.fromisoformat(user['last_reset_date']).date()
            if last_reset < today:
                # Reset count for new day
                cursor.execute("""
                    UPDATE users 
                    SET daily_ca_count = 1,
                        last_reset_date = ?
                    WHERE user_id = ?
                """, (today, user_id))
            else:
                # Increment count
                cursor.execute("""
                    UPDATE users 
                    SET daily_ca_count = daily_ca_count + 1
                    WHERE user_id = ?
                """, (user_id,))
        else:
            # First time
            cursor.execute("""
                UPDATE users 
                SET daily_ca_count = 1,
                    last_reset_date = ?
                WHERE user_id = ?
            """, (today, user_id))
        
        conn.commit()
        conn.close()
        return True
    
    def can_forward_ca(self, user_id: int) -> bool:
        """Check if user can forward another CA today"""
        user = self.get_user(user_id)
        if not user:
            return False
        
        # Reset count if needed
        today = datetime.now().date()
        if user['last_reset_date']:
            last_reset = datetime.fromisoformat(user['last_reset_date']).date()
            if last_reset < today:
                self.increment_daily_ca_count(user_id)
                return True
        
        return user['daily_ca_count'] < user['daily_ca_limit']
    
    # ==================== ROUTE MANAGEMENT ====================
    
    def add_route(self, user_id: int, source_chat_id: int, target_chat_id: int,
                  source_name: str = "Unknown", target_name: str = "Unknown",
                  filter_type: str = "ca_only") -> Optional[int]:
        """Add new route for user"""
        try:
            # Check if user can add more routes
            user = self.get_user(user_id)
            if not user:
                return None
            
            # Count existing routes
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM routes WHERE user_id = ? AND is_active = 1", (user_id,))
            route_count = cursor.fetchone()[0]
            
            if route_count >= user['total_routes_allowed']:
                conn.close()
                return None
            
            # Add route
            cursor.execute("""
                INSERT INTO routes (user_id, source_chat_id, target_chat_id, 
                                   source_name, target_name, filter_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, source_chat_id, target_chat_id, source_name, target_name, filter_type))
            
            route_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return route_id
        except Exception as e:
            print(f"❌ Error adding route: {e}")
            return None
    
    def get_user_routes(self, user_id: int) -> List[Dict]:
        """Get all routes for a user"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM routes 
            WHERE user_id = ? AND is_active = 1
            ORDER BY created_at DESC
        """, (user_id,))
        
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def delete_route(self, user_id: int, route_id: int) -> bool:
        """Delete a route"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE routes 
                SET is_active = 0
                WHERE route_id = ? AND user_id = ?
            """, (route_id, user_id))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ Error deleting route: {e}")
            return False
    
    def get_all_active_routes(self) -> List[Dict]:
        """Get all active routes from all users"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT r.*, u.subscription_tier, u.daily_ca_count, u.daily_ca_limit
            FROM routes r
            JOIN users u ON r.user_id = u.user_id
            WHERE r.is_active = 1 AND u.session_active = 1
        """, ())
        
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    # ==================== CA TRACKING ====================
    
    def log_forwarded_ca(self, user_id: int, route_id: int, ca_address: str,
                        source_chat_id: int, source_message_id: int = None,
                        original_message: str = None, sender_name: str = None) -> bool:
        """Log a forwarded CA"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO forwarded_cas 
                (user_id, route_id, ca_address, source_chat_id, source_message_id,
                 original_message, sender_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, route_id, ca_address, source_chat_id, source_message_id,
                  original_message, sender_name))
            
            # Update route stats
            cursor.execute("""
                UPDATE routes 
                SET total_forwarded = total_forwarded + 1,
                    last_forwarded_at = ?
                WHERE route_id = ?
            """, (datetime.now(), route_id))
            
            # Update user stats
            cursor.execute("""
                UPDATE users 
                SET total_cas_forwarded = total_cas_forwarded + 1,
                    last_active_at = ?
                WHERE user_id = ?
            """, (datetime.now(), user_id))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ Error logging CA: {e}")
            return False
    
    def get_user_cas(self, user_id: int, limit: int = 50) -> List[Dict]:
        """Get recent CAs forwarded by user"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM forwarded_cas 
            WHERE user_id = ?
            ORDER BY forwarded_at DESC
            LIMIT ?
        """, (user_id, limit))
        
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def ca_already_forwarded(self, user_id: int, ca_address: str, hours: int = 24) -> bool:
        """Check if CA was already forwarded recently"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        time_threshold = datetime.now() - timedelta(hours=hours)
        
        cursor.execute("""
            SELECT COUNT(*) FROM forwarded_cas 
            WHERE user_id = ? AND ca_address = ? AND forwarded_at > ?
        """, (user_id, ca_address, time_threshold))
        
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    
    # ==================== PAYMENT TRACKING ====================
    
    def create_payment(self, user_id: int, amount: float, tier: str,
                      telegram_payment_id: str = None) -> Optional[int]:
        """Create payment record"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            period_start = datetime.now()
            period_end = period_start + timedelta(days=30)
            
            cursor.execute("""
                INSERT INTO payments 
                (user_id, amount, tier, telegram_payment_id, period_start, period_end)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, amount, tier, telegram_payment_id, period_start, period_end))
            
            payment_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return payment_id
        except Exception as e:
            print(f"❌ Error creating payment: {e}")
            return None
    
    def complete_payment(self, payment_id: int) -> bool:
        """Mark payment as completed"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE payments 
                SET status = 'completed',
                    completed_at = ?
                WHERE payment_id = ?
            """, (datetime.now(), payment_id))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ Error completing payment: {e}")
            return False
    
    # ==================== ANALYTICS ====================
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Get comprehensive user statistics"""
        user = self.get_user(user_id)
        if not user:
            return {}
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Count routes
        cursor.execute("SELECT COUNT(*) FROM routes WHERE user_id = ? AND is_active = 1", (user_id,))
        active_routes = cursor.fetchone()[0]
        
        # Count CAs today
        today = datetime.now().date()
        cursor.execute("""
            SELECT COUNT(*) FROM forwarded_cas 
            WHERE user_id = ? AND DATE(forwarded_at) = ?
        """, (user_id, today))
        cas_today = cursor.fetchone()[0]
        
        # Count CAs this month
        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0)
        cursor.execute("""
            SELECT COUNT(*) FROM forwarded_cas 
            WHERE user_id = ? AND forwarded_at >= ?
        """, (user_id, month_start))
        cas_this_month = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'subscription_tier': user['subscription_tier'],
            'subscription_expires': user['subscription_expires_at'],
            'active_routes': active_routes,
            'max_routes': user['total_routes_allowed'],
            'cas_today': cas_today,
            'daily_limit': user['daily_ca_limit'],
            'cas_this_month': cas_this_month,
            'total_cas_all_time': user['total_cas_forwarded'],
            'member_since': user['created_at']
        }
    
    def get_admin_stats(self) -> Dict:
        """Get overall system statistics for admin"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Total users
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        # Users by tier
        cursor.execute("""
            SELECT subscription_tier, COUNT(*) 
            FROM users 
            GROUP BY subscription_tier
        """)
        users_by_tier = dict(cursor.fetchall())
        
        # Total revenue (completed payments only)
        cursor.execute("""
            SELECT SUM(amount) FROM payments 
            WHERE status = 'completed'
        """)
        total_revenue = cursor.fetchone()[0] or 0
        
        # Active routes
        cursor.execute("SELECT COUNT(*) FROM routes WHERE is_active = 1")
        active_routes = cursor.fetchone()[0]
        
        # CAs forwarded today
        today = datetime.now().date()
        cursor.execute("""
            SELECT COUNT(*) FROM forwarded_cas 
            WHERE DATE(forwarded_at) = ?
        """, (today,))
        cas_today = cursor.fetchone()[0]
        
        # CAs forwarded this month
        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0)
        cursor.execute("""
            SELECT COUNT(*) FROM forwarded_cas 
            WHERE forwarded_at >= ?
        """, (month_start,))
        cas_this_month = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_users': total_users,
            'users_by_tier': users_by_tier,
            'total_revenue': total_revenue,
            'active_routes': active_routes,
            'cas_today': cas_today,
            'cas_this_month': cas_this_month
        }
    
    def get_all_users(self, tier: str = None) -> List[Dict]:
        """Get all users, optionally filtered by tier"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if tier:
            cursor.execute("""
                SELECT * FROM users 
                WHERE subscription_tier = ?
                ORDER BY created_at DESC
            """, (tier,))
        else:
            cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
        
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    # ==================== NEW FEATURE METHODS ====================
    
    def search_cas(self, user_id: int, query: str, limit: int = 10) -> List[Dict]:
        """Search CAs by address or source"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT fc.*, r.source_name 
            FROM forwarded_cas fc
            LEFT JOIN routes r ON fc.route_id = r.route_id
            WHERE fc.user_id = ? AND (
                fc.ca_address LIKE ? OR
                fc.sender_name LIKE ? OR
                fc.original_message LIKE ?
            )
            ORDER BY fc.forwarded_at DESC
            LIMIT ?
        """, (user_id, f"%{query}%", f"%{query}%", f"%{query}%", limit))
        
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def toggle_route_status(self, user_id: int, route_id: int) -> Tuple[bool, bool]:
        """Toggle route is_active status. Returns (success, new_status)"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get current status
            cursor.execute(
                "SELECT is_active FROM routes WHERE route_id = ? AND user_id = ?",
                (route_id, user_id)
            )
            row = cursor.fetchone()
            if not row:
                conn.close()
                return False, False
            
            new_status = not row[0]
            
            cursor.execute(
                "UPDATE routes SET is_active = ? WHERE route_id = ? AND user_id = ?",
                (new_status, route_id, user_id)
            )
            
            conn.commit()
            conn.close()
            return True, new_status
        except Exception as e:
            print(f"Error toggling route: {e}")
            return False, False
    
    def get_user_cas(self, user_id: int, limit: int = 1000) -> List[Dict]:
        """Get all CAs for a user"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT fc.*, r.source_name 
            FROM forwarded_cas fc
            LEFT JOIN routes r ON fc.route_id = r.route_id
            WHERE fc.user_id = ?
            ORDER BY fc.forwarded_at DESC
            LIMIT ?
        """, (user_id, limit))
        
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]


