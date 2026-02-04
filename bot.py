"""
Multi-User CA Mirror Bot
========================
Main orchestrator for subscription-based Telegram CA monitoring service

Author: Built for profit 💰
Version: 1.0
"""

import re
import os
import asyncio
from datetime import datetime
from typing import Dict, Set, Optional
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    PreCheckoutQueryHandler,
    filters, 
    ContextTypes
)
from telethon import events
from telethon.tl.types import User, Chat, Channel

from database import Database
from session_manager import SessionManager
from payment_handler import PaymentHandler

load_dotenv()

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

# Solana CA Detection (from your original bot)
SOLANA_CA_PATTERN = r'[1-9A-HJ-NP-Za-km-z]{32,44}'
IGNORE_ADDRESSES = {
    '11111111111111111111111111111111',
    'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
    'So11111111111111111111111111111111111111112',
}
IGNORE_PREFIXES = ['bafk', 'Qm']

# User states for conversation flow
USER_STATES = {
    'AWAITING_API_ID': 'awaiting_api_id',
    'AWAITING_API_HASH': 'awaiting_api_hash',
    'AWAITING_PHONE': 'awaiting_phone',
    'AWAITING_CODE': 'awaiting_code',
    'AWAITING_SOURCE_CHAT': 'awaiting_source_chat',
    'AWAITING_TARGET_CHAT': 'awaiting_target_chat',
}


class MultiUserCABot:
    """Main bot orchestrator for multi-user CA monitoring"""
    
    def __init__(self):
        self.db = Database()
        self.session_manager = SessionManager(self.db, self.handle_monitored_message)
        self.payment_handler = PaymentHandler(self.db)
        self.bot_app = None
        
        # Track user conversation states
        self.user_states: Dict[int, Dict] = {}
    
    # ==================== SOLANA CA DETECTION ====================
    
    def is_valid_solana_address(self, address: str) -> bool:
        """Validate Solana address (from your original bot)"""
        if len(address) < 32 or len(address) > 44:
            return False
        if address in IGNORE_ADDRESSES:
            return False
        for prefix in IGNORE_PREFIXES:
            if address.startswith(prefix):
                return False
        if address.islower():
            return False
        valid_chars = set('123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz')
        if not all(c in valid_chars for c in address):
            return False
        return True
    
    def extract_solana_cas(self, text: str) -> Optional[str]:
        """Extract and validate Solana CA from text (from your original bot)"""
        if not text:
            return None
        
        potential_addresses = re.findall(SOLANA_CA_PATTERN, text)
        if not potential_addresses:
            return None
        
        # Filter addresses
        seen = set()
        filtered_addresses = []
        for addr in potential_addresses:
            if addr.lower() in seen:
                continue
            seen.add(addr.lower())
            
            # Skip if in URL
            url_pattern = rf'(https?://[^\s]*{re.escape(addr)}|solscan\.io[^\s]*{re.escape(addr)}|dexscreener[^\s]*{re.escape(addr)})'
            if re.search(url_pattern, text):
                continue
            
            filtered_addresses.append(addr)
        
        # Fallback to first unique address if all filtered out
        if not filtered_addresses and potential_addresses:
            unique_addrs = []
            seen = set()
            for addr in potential_addresses:
                if addr.lower() not in seen:
                    unique_addrs.append(addr)
                    seen.add(addr.lower())
            filtered_addresses = unique_addrs[:1]
        
        # Take only first address and validate
        if filtered_addresses:
            ca = filtered_addresses[0]
            if self.is_valid_solana_address(ca):
                return ca
        
        return None
    
    # ==================== MESSAGE HANDLING ====================
    
    async def handle_monitored_message(self, user_id: int, event):
        """Handle messages from monitored channels (called by SessionManager)"""
        try:
            # Get user's routes
            routes = self.db.get_user_routes(user_id)
            if not routes:
                return
            
            # Find matching route
            event_chat_id = event.chat_id
            matching_route = None
            for route in routes:
                if route['source_chat_id'] == event_chat_id:
                    matching_route = route
                    break
            
            if not matching_route:
                return
            
            # Check if user can forward more CAs today
            if not self.db.can_forward_ca(user_id):
                print(f"⚠️ User {user_id} hit daily CA limit")
                return
            
            # Extract CA from message
            message_text = event.message.message or ""
            ca = self.extract_solana_cas(message_text)
            
            if not ca:
                return
            
            # Check for duplicates (per user, last 24 hours)
            if self.db.ca_already_forwarded(user_id, ca, hours=24):
                print(f"🔄 Duplicate CA for user {user_id}: {ca}")
                return
            
            # Get sender info
            sender = await event.get_sender()
            sender_name = self._get_entity_name(sender)
            
            # Forward CA to user's target
            target_chat_id = matching_route['target_chat_id']
            client = self.session_manager.get_user_client(user_id)
            
            if client:
                # Send just the CA
                await client.send_message(target_chat_id, ca)
                
                # Log the forwarded CA
                self.db.log_forwarded_ca(
                    user_id=user_id,
                    route_id=matching_route['route_id'],
                    ca_address=ca,
                    source_chat_id=event_chat_id,
                    source_message_id=event.message.id,
                    original_message=message_text[:500],  # First 500 chars
                    sender_name=sender_name
                )
                
                # Increment daily count
                self.db.increment_daily_ca_count(user_id)
                
                print(f"\n✅ CA FORWARDED!")
                print(f"   👤 User: {user_id}")
                print(f"   📥 Source: {matching_route['source_name']}")
                print(f"   💎 CA: {ca}")
                print(f"   📤 Target: {matching_route['target_name']}")
                print(f"   👤 Sender: {sender_name}")
                print("=" * 60)
        
        except Exception as e:
            print(f"❌ Error handling message for user {user_id}: {e}")
            import traceback
            traceback.print_exc()
    
    def _get_entity_name(self, entity) -> str:
        """Get readable name from Telegram entity"""
        if isinstance(entity, User):
            return f"@{entity.username}" if entity.username else \
                   f"{entity.first_name or ''} {entity.last_name or ''}".strip()
        elif isinstance(entity, (Chat, Channel)):
            return entity.title or "Unknown Chat"
        return "Unknown"
    
    # ==================== BOT COMMANDS ====================
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        user_id = user.id
        
        # Create user in database if new
        self.db.create_user(user_id, user.username, user.first_name)
        
        # Check if user has active session
        db_user = self.db.get_user(user_id)
        has_session = db_user and db_user['session_active']
        
        # Build welcome message
        message = f"👋 *Welcome to CA Mirror Bot!*\n\n"
        message += "💎 Monitor Solana calls from ANY channel\n"
        message += "🚀 Forward CAs instantly to your group\n"
        message += "🔒 100% private - channels won't know\n\n"
        
        # Show subscription status
        is_active, tier, status_msg = self.payment_handler.check_subscription_status(user_id)
        message += f"📊 *Your Status:* {status_msg}\n\n"
        
        # Main menu buttons
        keyboard = []
        
        if not has_session:
            keyboard.append([InlineKeyboardButton("🔐 Setup Authentication", callback_data="setup_auth")])
        else:
            keyboard.append([InlineKeyboardButton("➕ Add Route", callback_data="add_route")])
            keyboard.append([InlineKeyboardButton("📋 My Routes", callback_data="view_routes")])
        
        keyboard.append([InlineKeyboardButton("💰 Pricing & Subscribe", callback_data="pricing")])
        keyboard.append([InlineKeyboardButton("📊 My Stats", callback_data="my_stats")])
        
        if user_id == ADMIN_USER_ID:
            keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
        
        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def show_main_menu(self, query, context):
        """Show main menu in response to callback"""
        user_id = query.from_user.id
        
        # Get user info
        db_user = self.db.get_user(user_id)
        has_session = db_user and db_user['session_active']
        
        # Build welcome message
        message = f"👋 *Welcome to CA Mirror Bot!*\n\n"
        message += "💎 Monitor Solana calls from ANY channel\n"
        message += "🚀 Forward CAs instantly to your group\n"
        message += "🔒 100% private - channels won't know\n\n"
        
        # Show subscription status
        is_active, tier, status_msg = self.payment_handler.check_subscription_status(user_id)
        message += f"📊 *Your Status:* {status_msg}\n\n"
        
        # Main menu buttons
        keyboard = []
        
        if not has_session:
            keyboard.append([InlineKeyboardButton("🔐 Setup Authentication", callback_data="setup_auth")])
        else:
            keyboard.append([InlineKeyboardButton("➕ Add Route", callback_data="add_route")])
            keyboard.append([InlineKeyboardButton("📋 My Routes", callback_data="view_routes")])
        
        keyboard.append([InlineKeyboardButton("💰 Pricing & Subscribe", callback_data="pricing")])
        keyboard.append([InlineKeyboardButton("📊 My Stats", callback_data="my_stats")])
        
        if user_id == ADMIN_USER_ID:
            keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        if data == "setup_auth":
            await self.start_authentication(query, context)
        elif data == "add_route":
            await self.start_add_route(query, context)
        elif data == "view_routes":
            await self.view_routes(query, context)
        elif data == "pricing":
            await self.payment_handler.show_pricing(query, context)
        elif data == "my_stats":
            await self.show_user_stats(query, context)
        elif data == "admin_panel":
            if user_id == ADMIN_USER_ID:
                await self.show_admin_panel(query, context)
            else:
                await query.edit_message_text("❌ Admin access only!")
        elif data == "back_to_menu":
            await self.show_main_menu(query, context)
        elif data.startswith("delete_route_"):
            route_id = int(data.split("_")[2])
            await self.delete_route(query, context, route_id)
        elif data.startswith("subscribe_"):
            tier = data.split("_")[1]
            await self.payment_handler.create_invoice(query, context, tier)
        else:
            await query.edit_message_text(f"❌ Unknown button: {data}")
    
    # ==================== AUTHENTICATION FLOW ====================
    
    async def start_authentication(self, query, context):
        """Start authentication flow"""
        user_id = query.from_user.id
        
        message = "🔐 *Setup Authentication*\n\n"
        message += "To monitor channels, I need your Telegram API credentials.\n\n"
        message += "📱 *Step 1:* Get API Credentials\n"
        message += "1. Go to: https://my.telegram.org/auth\n"
        message += "2. Login with your phone number\n"
        message += "3. Click 'API Development Tools'\n"
        message += "4. Create an app (any name)\n\n"
        message += "Send me your credentials in this format:\n"
        message += "`API_ID:12345678`\n"
        message += "`API_HASH:abcdef123456789`\n\n"
        message += "⚠️ Send both on separate lines or together"
        
        self.user_states[user_id] = {
            'state': USER_STATES['AWAITING_API_ID'],
            'data': {}
        }
        
        await query.edit_message_text(message, parse_mode='Markdown')
    
    async def start_add_route(self, query, context):
        """Start add route flow"""
        user_id = query.from_user.id
        
        # Check if user has authenticated
        if not self.session_manager.is_user_active(user_id):
            keyboard = [
                [InlineKeyboardButton("🔐 Setup Authentication", callback_data="setup_auth")],
                [InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")]
            ]
            await query.edit_message_text(
                "❌ Please authenticate first!\n\n"
                "Click 'Setup Authentication' below to get started.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        # Check route limits
        user = self.db.get_user(user_id)
        routes = self.db.get_user_routes(user_id)
        
        if len(routes) >= user['total_routes_allowed']:
            message = f"❌ Route limit reached!\n\n"
            message += f"Your plan: {user['subscription_tier']}\n"
            message += f"Routes: {len(routes)}/{user['total_routes_allowed']}\n\n"
            message += "Upgrade to add more routes!"
            
            keyboard = [
                [InlineKeyboardButton("💰 Upgrade Plan", callback_data="pricing")],
                [InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")]
            ]
            
            await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        message = "➕ *Add New Route*\n\n"
        message += "📥 *Step 1:* What's the SOURCE chat ID?\n"
        message += "(The channel you want to monitor)\n\n"
        message += "💡 Tip: Forward a message from that channel to @userinfobot to get the ID"
        
        self.user_states[user_id] = {
            'state': USER_STATES['AWAITING_SOURCE_CHAT'],
            'data': {}
        }
        
        await query.edit_message_text(message, parse_mode='Markdown')
    
    async def view_routes(self, query, context):
        """View user's routes"""
        user_id = query.from_user.id
        routes = self.db.get_user_routes(user_id)
        
        if not routes:
            keyboard = [
                [InlineKeyboardButton("➕ Add Route", callback_data="add_route")],
                [InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")]
            ]
            await query.edit_message_text(
                "📭 You don't have any routes yet!\n\n"
                "Click 'Add Route' below to create your first route.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        message = "📋 *Your Routes*\n\n"
        
        keyboard = []
        for i, route in enumerate(routes, 1):
            message += f"*{i}. {route['source_name']}* → {route['target_name']}\n"
            message += f"   📊 Forwarded: {route['total_forwarded']} CAs\n"
            message += f"   🆔 Route ID: {route['route_id']}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑️ Delete Route {i}",
                    callback_data=f"delete_route_{route['route_id']}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def delete_route(self, query, context, route_id: int):
        """Delete a route"""
        user_id = query.from_user.id
        
        success = self.db.delete_route(user_id, route_id)
        
        if success:
            await query.answer("✅ Route deleted!")
            await self.view_routes(query, context)
        else:
            await query.answer("❌ Error deleting route")
    
    async def show_user_stats(self, query, context):
        """Show user statistics"""
        user_id = query.from_user.id
        stats = self.db.get_user_stats(user_id)
        
        message = f"📊 *Your Statistics*\n\n"
        message += f"🎫 *Subscription:* {stats['subscription_tier'].title()}\n"
        
        if stats['subscription_tier'] != 'free' and stats.get('subscription_expires'):
            expires = datetime.fromisoformat(stats['subscription_expires'])
            days_left = (expires - datetime.now()).days
            message += f"⏰ Expires: {expires.strftime('%Y-%m-%d')} ({days_left} days)\n"
        
        message += f"\n📈 *Usage:*\n"
        message += f"• CAs today: {stats['cas_today']}/{stats['daily_limit']}\n"
        message += f"• Active routes: {stats['active_routes']}/{stats['max_routes']}\n"
        message += f"• CAs this month: {stats['cas_this_month']}\n"
        message += f"• Total CAs: {stats['total_cas_all_time']}\n\n"
        message += f"📅 Member since: {stats['member_since'][:10]}"
        
        keyboard = [[InlineKeyboardButton("« Back", callback_data="back_to_menu")]]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    # ==================== MESSAGE HANDLER ====================
    
    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages based on user state"""
        user_id = update.effective_user.id
        text = update.message.text
        
        if user_id not in self.user_states:
            await update.message.reply_text(
                "Please use /start to begin, or click a button from the menu."
            )
            return
        
        state = self.user_states[user_id]['state']
        data = self.user_states[user_id]['data']
        
        # Authentication flow
        if state == USER_STATES['AWAITING_API_ID']:
            # Parse API_ID and API_HASH
            if 'API_ID:' in text and 'API_HASH:' in text:
                parts = text.split('\n')
                api_id = None
                api_hash = None
                for part in parts:
                    if 'API_ID:' in part:
                        api_id = part.split('API_ID:')[1].strip()
                    if 'API_HASH:' in part:
                        api_hash = part.split('API_HASH:')[1].strip()
                
                if api_id and api_hash:
                    data['api_id'] = api_id
                    data['api_hash'] = api_hash
                    self.user_states[user_id]['state'] = USER_STATES['AWAITING_PHONE']
                    await update.message.reply_text(
                        "✅ Got your credentials!\n\n"
                        "📱 *Step 2:* Send your phone number\n"
                        "Format: +1234567890",
                        parse_mode='Markdown'
                    )
                else:
                    await update.message.reply_text("❌ Invalid format. Try again with:\nAPI_ID:12345\nAPI_HASH:abc123")
            else:
                await update.message.reply_text("❌ Please send both API_ID and API_HASH")
        
        elif state == USER_STATES['AWAITING_PHONE']:
            phone = text.strip()
            if not phone.startswith('+'):
                await update.message.reply_text("❌ Phone must start with + and country code")
                return
            
            data['phone'] = phone
            
            # Create session
            success, message = await self.session_manager.create_user_session(
                user_id, data['api_id'], data['api_hash'], phone
            )
            
            if success:
                self.user_states[user_id]['state'] = USER_STATES['AWAITING_CODE']
                await update.message.reply_text(
                    f"✅ {message}\n\n"
                    "📲 Check Telegram! Enter the verification code:"
                )
            else:
                await update.message.reply_text(f"❌ {message}\n\nTry again from /start")
                del self.user_states[user_id]
        
        elif state == USER_STATES['AWAITING_CODE']:
            code = text.strip()
            
            success, message = await self.session_manager.verify_code(
                user_id, data['api_id'], data['api_hash'], data['phone'], code
            )
            
            if success:
                await update.message.reply_text(
                    f"{message}\n\n"
                    "🎉 Setup complete! Send /start to add routes."
                )
                del self.user_states[user_id]
            else:
                await update.message.reply_text(f"❌ {message}")
        
        # Add route flow
        elif state == USER_STATES['AWAITING_SOURCE_CHAT']:
            try:
                source_chat_id = int(text.strip())
                
                # Test access
                success, result = await self.session_manager.test_chat_access(user_id, source_chat_id)
                
                if success:
                    chat_info = await self.session_manager.get_chat_info(user_id, source_chat_id)
                    data['source_chat_id'] = source_chat_id
                    data['source_name'] = chat_info['name'] if chat_info else "Unknown"
                    
                    self.user_states[user_id]['state'] = USER_STATES['AWAITING_TARGET_CHAT']
                    
                    await update.message.reply_text(
                        f"✅ Verified: {data['source_name']}\n\n"
                        "📤 *Step 2:* What's the TARGET chat ID?\n"
                        "(Where to send the CAs)",
                        parse_mode='Markdown'
                    )
                else:
                    await update.message.reply_text(f"{result}\n\nTry another chat ID:")
            
            except ValueError:
                await update.message.reply_text("❌ Invalid chat ID. Must be a number (e.g., -1001234567890)")
        
        elif state == USER_STATES['AWAITING_TARGET_CHAT']:
            try:
                target_chat_id = int(text.strip())
                
                # Test access
                success, result = await self.session_manager.test_chat_access(user_id, target_chat_id)
                
                if success:
                    chat_info = await self.session_manager.get_chat_info(user_id, target_chat_id)
                    data['target_chat_id'] = target_chat_id
                    data['target_name'] = chat_info['name'] if chat_info else "Unknown"
                    
                    # Add route to database
                    route_id = self.db.add_route(
                        user_id=user_id,
                        source_chat_id=data['source_chat_id'],
                        target_chat_id=data['target_chat_id'],
                        source_name=data['source_name'],
                        target_name=data['target_name'],
                        filter_type='ca_only'
                    )
                    
                    if route_id:
                        await update.message.reply_text(
                            f"✅ *Route Added!*\n\n"
                            f"📊 Monitoring: {data['source_name']}\n"
                            f"📤 Forwarding to: {data['target_name']}\n\n"
                            f"🎯 Bot is now watching for Solana CAs!",
                            parse_mode='Markdown'
                        )
                        del self.user_states[user_id]
                    else:
                        await update.message.reply_text("❌ Error adding route. You may have reached your limit.")
                        del self.user_states[user_id]
                else:
                    await update.message.reply_text(f"{result}\n\nTry another chat ID:")
            
            except ValueError:
                await update.message.reply_text("❌ Invalid chat ID. Must be a number")
    
    # ==================== ADMIN COMMANDS ====================
    
    async def show_admin_panel(self, query, context):
        """Show admin panel"""
        stats = self.db.get_admin_stats()
        
        message = "👑 *ADMIN DASHBOARD*\n\n"
        message += "📊 *System Stats:*\n"
        message += "━━━━━━━━━━━━━━━━━━━━━━\n"
        message += f"Users: {stats['total_users']}\n"
        
        for tier, count in stats['users_by_tier'].items():
            message += f"├─ {tier.title()}: {count}\n"
        
        message += f"\n💰 *Revenue:*\n"
        message += f"├─ Total: ${stats['total_revenue']:.2f}\n"
        message += f"\n📈 *Activity:*\n"
        message += f"├─ Active routes: {stats['active_routes']}\n"
        message += f"├─ CAs today: {stats['cas_today']}\n"
        message += f"└─ CAs this month: {stats['cas_this_month']}\n"
        
        keyboard = [[InlineKeyboardButton("« Back", callback_data="back_to_menu")]]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    # ==================== PAYMENT HANDLERS ====================
    
    async def handle_precheckout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle pre-checkout query"""
        await self.payment_handler.handle_precheckout_query(update, context)
    
    async def handle_successful_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle successful payment"""
        await self.payment_handler.handle_successful_payment(update, context)
    
    # ==================== SUBSCRIPTION COMMANDS ====================
    
    async def subscribe_starter(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Subscribe to Starter tier"""
        await self.payment_handler.create_invoice(update, context, 'starter')
    
    async def subscribe_pro(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Subscribe to Pro tier"""
        await self.payment_handler.create_invoice(update, context, 'pro')
    
    async def subscribe_alpha(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Subscribe to Alpha tier"""
        await self.payment_handler.create_invoice(update, context, 'alpha')
    
    # ==================== MAIN RUN LOOP ====================
    
    async def run(self):
        """Run the bot"""
        print("🚀 Starting Multi-User CA Mirror Bot...")
        print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Load existing user sessions
        await self.session_manager.load_all_sessions()
        
        # Setup Telegram bot
        self.bot_app = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        self.bot_app.add_handler(CommandHandler("start", self.start_command))
        self.bot_app.add_handler(CommandHandler("subscribe_starter", self.subscribe_starter))
        self.bot_app.add_handler(CommandHandler("subscribe_pro", self.subscribe_pro))
        self.bot_app.add_handler(CommandHandler("subscribe_alpha", self.subscribe_alpha))
        self.bot_app.add_handler(CallbackQueryHandler(self.button_callback))
        self.bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_handler))
        self.bot_app.add_handler(PreCheckoutQueryHandler(self.handle_precheckout))
        self.bot_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, self.handle_successful_payment))
        
        print("✅ Telegram bot interface ready!")
        bot_me = await self.bot_app.bot.get_me()
        print(f"🤖 Bot username: @{bot_me.username}")
        print(f"\n💬 Send /start to @{bot_me.username} to begin!\n")
        print("=" * 60)
        
        # Initialize and run
        await self.bot_app.initialize()
        await self.bot_app.start()
        await self.bot_app.updater.start_polling()
        
        # Keep running
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            print("\n\n👋 Shutting down...")
            await self.session_manager.disconnect_all()
            await self.bot_app.stop()


async def main():
    """Main entry point"""
    # Validate configuration
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN not found in .env!")
        print("\n📝 Steps to create a bot:")
        print("1. Message @BotFather on Telegram")
        print("2. Send /newbot")
        print("3. Follow instructions")
        print("4. Add BOT_TOKEN=your_token to .env")
        return
    
    if not ADMIN_USER_ID:
        print("❌ ADMIN_USER_ID not found in .env!")
        print("Add your Telegram user ID to .env")
        return
    
    # Create and run bot
    bot = MultiUserCABot()
    
    try:
        await bot.run()
    except KeyboardInterrupt:
        print("\n\n👋 Bot stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())

