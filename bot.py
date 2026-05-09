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
import hashlib
from datetime import datetime
from typing import Dict, Set, Optional
from dotenv import load_dotenv
from io import BytesIO

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
from logger import logger
from validators import validate_api_id, validate_api_hash, validate_phone, validate_chat_id

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

# Minimum length for verification codes
MIN_VERIFICATION_CODE_LENGTH = 5

# User states for conversation flow
USER_STATES = {
    'AWAITING_API_ID': 'awaiting_api_id',
    'AWAITING_API_HASH': 'awaiting_api_hash',
    'AWAITING_PHONE': 'awaiting_phone',
    'AWAITING_CODE': 'awaiting_code',
    'AWAITING_SOURCE_CHAT': 'awaiting_source_chat',
    'AWAITING_TARGET_CHAT': 'awaiting_target_chat',
}

# Help message constant
HELP_MESSAGE = """📚 *VultMirror Help*

🔮 *What is VultMirror?*
A bot that monitors Telegram channels for Solana contract addresses (CAs) and forwards them to your private chat instantly.

📋 *Commands:*
• `/start` - Main menu
• `/help` - This help message
• `/routes` - View your routes
• `/stats` - Your statistics
• `/pricing` - Subscription plans
• `/search` - Search CA history
• `/export` - Export CA history

🚀 *Quick Start:*
1️⃣ Click 'Setup Authentication'
2️⃣ Enter your Telegram API credentials
3️⃣ Add a route (source → target)
4️⃣ CAs will auto-forward! 🎉

💡 *Tips:*
• Use @userinfobot to get chat IDs
• You must be a member of source channels
• Target can be any chat you can message

❓ Need more help? Contact the admin!"""

# Subscription tier configuration
TIER_EMOJI = {'free': '🆓', 'starter': '⭐', 'pro': '💎', 'alpha': '🔥'}
VALID_TIERS = ['starter', 'pro', 'alpha']


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
        """
        Extract and validate Solana CA from text (from your original bot).
        
        Returns None if no standalone addresses found (e.g., only in URLs or no addresses at all).
        """
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
            
            # Skip if in URL - excludes CAs found in trading platform URLs
            url_pattern = rf'(https?://[^\s]*{re.escape(addr)}|solscan\.io[^\s]*{re.escape(addr)}|dexscreener[^\s]*{re.escape(addr)}|pump\.fun[^\s]*{re.escape(addr)}|birdeye\.so[^\s]*{re.escape(addr)})'
            if re.search(url_pattern, text):
                continue
            
            filtered_addresses.append(addr)
        
        # Take only first standalone address and validate
        if filtered_addresses:
            ca = filtered_addresses[0]
            if self.is_valid_solana_address(ca):
                return ca
        
        return None
    
    def extract_trading_links(self, text: str) -> Optional[str]:
        """Extract DexScreener, Pump.fun, BirdEye, DEXTools links"""
        if not text:
            return None
        
        # Regex patterns for trading platforms
        link_patterns = [
            r'https?://(?:www\.)?dexscreener\.com/solana/[A-Za-z0-9]+',
            r'https?://(?:www\.)?pump\.fun/(?:coin/)?[A-Za-z0-9]+',
            r'https?://(?:www\.)?birdeye\.so/token/[A-Za-z0-9]+(?:\?[^\s]*)?',
            r'https?://(?:www\.)?dextools\.io/app/[^\s]+',
        ]
        
        for pattern in link_patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        
        return None
    
    # ==================== MESSAGE HANDLING ====================
    
    async def handle_monitored_message(self, user_id: int, event):
        """Handle messages from monitored channels (called by SessionManager)"""
        try:
            # Get user's routes
            routes = self.db.get_user_routes(user_id, active_only=True)
            event_chat_id = event.chat_id
            matching_routes = [r for r in routes if r['source_chat_id'] == event_chat_id]

            if not matching_routes:
                return

            # Extract message text
            message_text = event.message.message or ""

            # Get sender info
            sender = await event.get_sender()
            sender_name = self._get_entity_name(sender)

            client = self.session_manager.get_user_client(user_id)

            if not client:
                return

            # Get user's format preference
            ca_format = self.db.get_user_ca_format(user_id)

            # Count forwards for this message across all routes
            forward_count = 0

            # Try to extract CA first
            ca = self.extract_solana_cas(message_text)
            # Try to extract trading link
            trading_link = self.extract_trading_links(message_text)
            url_hash = hashlib.sha256(trading_link.encode()).hexdigest() if trading_link else None

            # Check duplicates once, before looping over routes
            ca_is_duplicate = bool(ca and self.db.ca_already_forwarded(user_id, ca, hours=24))
            url_is_duplicate = bool(trading_link and self.db.url_already_forwarded(user_id, url_hash, hours=24))

            # Forward to every matching destination
            for matching_route in matching_routes:
                target_chat_id = matching_route['target_chat_id']

                if ca:
                    if ca_is_duplicate:
                        print(f"🔄 Duplicate CA for user {user_id}: {ca}")
                    elif not self.db.can_forward_ca(user_id):
                        print(f"⚠️ User {user_id} hit daily CA limit")
                    else:
                        if ca_format == 'minimal':
                            await client.send_message(target_chat_id, ca)
                        else:
                            ca_message = f"💎 *New CA Detected!*\n\n"
                            ca_message += f"`{ca}`\n\n"
                            ca_message += f"📥 From: {matching_route['source_name']}\n"
                            ca_message += f"👤 Posted by: {sender_name}\n"
                            ca_message += f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                            await client.send_message(target_chat_id, ca_message, parse_mode='md')

                        self.db.log_forwarded_ca(
                            user_id=user_id,
                            route_id=matching_route['route_id'],
                            ca_address=ca,
                            source_chat_id=event_chat_id,
                            source_message_id=event.message.id,
                            original_message=message_text[:500],
                            sender_name=sender_name
                        )
                        self.db.increment_daily_ca_count(user_id)
                        forward_count += 1
                        logger.info(f"CA forwarded for user {user_id}: {ca[:20]}... to {matching_route['target_name']}")

                if trading_link:
                    if url_is_duplicate:
                        print(f"🔄 Duplicate URL for user {user_id}: {trading_link}")
                    elif not self.db.can_forward_ca(user_id):
                        print(f"⚠️ User {user_id} hit daily limit")
                    else:
                        if ca_format == 'minimal':
                            await client.send_message(target_chat_id, trading_link)
                        else:
                            url_message = f"🔗 *New Trading Link Detected!*\n\n"
                            url_message += f"{trading_link}\n\n"
                            url_message += f"📥 From: {matching_route['source_name']}\n"
                            url_message += f"👤 Posted by: {sender_name}\n"
                            url_message += f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                            await client.send_message(target_chat_id, url_message, parse_mode='md')

                        self.db.log_forwarded_url(
                            user_id=user_id,
                            route_id=matching_route['route_id'],
                            url=trading_link,
                            url_hash=url_hash,
                            source_chat_id=event_chat_id,
                            source_message_id=event.message.id,
                            sender_name=sender_name
                        )
                        self.db.increment_daily_ca_count(user_id)
                        forward_count += 1
                        logger.info(f"URL forwarded for user {user_id}: {trading_link} to {matching_route['target_name']}")
        
        except Exception as e:
            logger.error(f"Error handling message for user {user_id}: {e}", exc_info=True)
    
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
        message = f"👋 *Welcome to VultMirror*\n\n"
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
        
        keyboard.append([
            InlineKeyboardButton("💰 Pricing", callback_data="pricing"),
            InlineKeyboardButton("📊 Stats", callback_data="my_stats")
        ])
        keyboard.append([InlineKeyboardButton("❓ Help", callback_data="show_help")])
        
        if user_id == ADMIN_USER_ID:
            keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
        
        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        user_id = update.effective_user.id
        
        help_text = HELP_MESSAGE
        
        # Add admin commands if user is admin
        if user_id == ADMIN_USER_ID:
            help_text += "\n\n👑 *Admin Commands:*\n"
            help_text += "• `/grant <user_id> <tier> [days]` - Grant subscription\n"
            help_text += "• `/revoke <user_id>` - Revoke subscription\n"
            help_text += "• `/userinfo <user_id>` - View user details\n"
        
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")]]
        
        await update.message.reply_text(
            help_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def routes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /routes command"""
        user_id = update.effective_user.id
        routes = self.db.get_user_routes(user_id)  # all routes, including paused

        if not routes:
            message = "📋 *Your Routes*\n\n"
            message += "You have no routes.\n"
            message += "Use /start to add your first route!"
        else:
            active = sum(1 for r in routes if r['is_active'])
            message = f"📋 *Your Routes* ({active} active, {len(routes)} total)\n\n"
            for i, route in enumerate(routes, 1):
                status = "✅" if route['is_active'] else "⏸️"
                message += f"{i}. {status} {route['source_name']}\n"
                message += f"   └ To: {route['target_name']}\n\n"
        
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")]]
        
        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        user_id = update.effective_user.id
        stats = self.db.get_user_stats(user_id)
        
        message = "📊 *Your Statistics*\n\n"
        message += f"💎 CAs Today: {stats['cas_today']}/{stats['daily_limit']}\n"
        message += f"🔗 URLs Today: {stats['urls_today']}\n"
        message += f"📈 Combined Total: {stats['total_today']}/{stats['daily_limit']}\n\n"
        message += f"📅 This Month:\n"
        message += f"  • CAs: {stats['cas_this_month']}\n"
        message += f"  • URLs: {stats['urls_this_month']}\n\n"
        message += f"🎯 All Time:\n"
        message += f"  • CAs: {stats['total_cas_all_time']}\n"
        message += f"  • URLs: {stats['total_urls_all_time']}\n\n"
        message += f"📋 Active Routes: {stats['active_routes']}\n"
        
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")]]
        
        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def pricing_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pricing command"""
        await self.show_pricing(update.message, context)
    
    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Search CA and URL history"""
        user_id = update.effective_user.id
        
        if not context.args:
            await update.message.reply_text(
                "🔍 *Search History*\n\n"
                "Usage: `/search <query>`\n\n"
                "Examples:\n"
                "• `/search pump` - Find CAs/URLs containing 'pump'\n"
                "• `/search 7xK` - Find CAs starting with '7xK'\n"
                "• `/search dexscreener` - Find DexScreener links\n",
                parse_mode='Markdown'
            )
            return
        
        query = ' '.join(context.args)
        
        # Search both CAs and URLs
        ca_results = self.db.search_cas(user_id, query, limit=10)
        url_results = self.db.search_urls(user_id, query, limit=10)
        
        if not ca_results and not url_results:
            await update.message.reply_text(f"No results found matching '{query}'")
            return
        
        message = f"🔍 *Search Results for '{query}'*\n\n"
        
        if ca_results:
            message += "💎 *Contract Addresses:*\n"
            for i, ca in enumerate(ca_results, 1):
                message += f"{i}. `{ca['ca_address']}`\n"
                message += f"   📅 {ca['forwarded_at'][:16]}\n"
                if ca.get('source_name'):
                    message += f"   📥 {ca['source_name']}\n"
                message += "\n"
        
        if url_results:
            message += "🔗 *Trading Links:*\n"
            for i, url in enumerate(url_results, 1):
                message += f"{i}. {url['url']}\n"
                message += f"   📅 {url['forwarded_at'][:16]}\n"
                if url.get('source_name'):
                    message += f"   📥 {url['source_name']}\n"
                message += "\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def export_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Export CA and URL history as text file"""
        user_id = update.effective_user.id
        
        # Get all CAs and URLs
        cas = self.db.get_user_cas(user_id, limit=1000)
        urls = self.db.get_user_urls(user_id, limit=1000)
        
        if not cas and not urls:
            await update.message.reply_text("No history to export.")
            return
        
        # Create export text
        export_text = "VultMirror Export\n"
        export_text += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        export_text += "=" * 50 + "\n\n"
        
        if cas:
            export_text += "CONTRACT ADDRESSES\n"
            export_text += "=" * 50 + "\n"
            for ca in cas:
                export_text += f"CA: {ca['ca_address']}\n"
                export_text += f"Date: {ca['forwarded_at']}\n"
                export_text += f"Sender: {ca.get('sender_name', 'Unknown')}\n"
                if ca.get('source_name'):
                    export_text += f"Source: {ca['source_name']}\n"
                export_text += "-" * 30 + "\n"
            export_text += "\n"
        
        if urls:
            export_text += "TRADING LINKS\n"
            export_text += "=" * 50 + "\n"
            for url in urls:
                export_text += f"URL: {url['url']}\n"
                export_text += f"Date: {url['forwarded_at']}\n"
                export_text += f"Sender: {url.get('sender_name', 'Unknown')}\n"
                if url.get('source_name'):
                    export_text += f"Source: {url['source_name']}\n"
                export_text += "-" * 30 + "\n"
        
        # Send as file
        file = BytesIO(export_text.encode())
        file.name = f"vultmirror_export_{datetime.now().strftime('%Y%m%d')}.txt"
        
        await update.message.reply_document(
            document=file,
            caption="📁 Your history export"
        )
    
    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /settings command"""
        await self.show_settings(update.message, update.effective_user.id)
    
    async def show_settings(self, message_or_query, user_id: int, edit: bool = False):
        """Show settings menu"""
        current_format = self.db.get_user_ca_format(user_id)
        
        rich_check = "✓" if current_format == 'rich' else ""
        minimal_check = "✓" if current_format == 'minimal' else ""
        
        msg = "⚙️ *Settings*\n\n"
        msg += "📋 *CA Message Format:*\n"
        if current_format == 'rich':
            msg += "Current: 💎 Rich (with source info & links)\n\n"
        else:
            msg += "Current: 📋 Minimal (just the CA)\n\n"
        
        msg += "*Preview:*\n"
        if current_format == 'rich':
            msg += "```\n💎 New CA Detected!\n\n7xKXtg2CW87d97TXJ...\n\n📥 From: Alpha Calls\n👤 Posted by: @trader123\n⏰ 15:42:33\n```\n"
        else:
            msg += "```\n7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU\n```\n"
        
        keyboard = [
            [
                InlineKeyboardButton(f"💎 Rich {rich_check}", callback_data="set_format_rich"),
                InlineKeyboardButton(f"📋 Minimal {minimal_check}", callback_data="set_format_minimal")
            ],
            [InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")]
        ]
        
        if edit and hasattr(message_or_query, 'edit_message_text'):
            await message_or_query.edit_message_text(
                msg,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            target = message_or_query if hasattr(message_or_query, 'reply_text') else message_or_query.message
            await target.reply_text(
                msg,
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
        
        keyboard.append([
            InlineKeyboardButton("💰 Pricing", callback_data="pricing"),
            InlineKeyboardButton("📊 Stats", callback_data="my_stats")
        ])
        keyboard.append([
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
            InlineKeyboardButton("❓ Help", callback_data="show_help")
        ])
        
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
        elif data == "show_help":
            await self.show_help_callback(query, context)
        elif data == "settings":
            await self.show_settings(query, user_id, edit=True)
        elif data == "set_format_rich":
            self.db.set_user_ca_format(user_id, 'rich')
            await query.answer("✅ Format set to Rich!")
            await self.show_settings(query, user_id, edit=True)
        elif data == "set_format_minimal":
            self.db.set_user_ca_format(user_id, 'minimal')
            await query.answer("✅ Format set to Minimal!")
            await self.show_settings(query, user_id, edit=True)
        elif data.startswith("toggle_route_"):
            route_id = int(data.split("_")[2])
            await self.toggle_route(query, context, route_id)
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
        
        # Check route limits (only count active routes against the cap)
        user = self.db.get_user(user_id)
        routes = self.db.get_user_routes(user_id, active_only=True)

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
            status = "✅" if route['is_active'] else "⏸️"
            message += f"{status} *{i}. {route['source_name']}* → {route['target_name']}\n"
            message += f"   📊 Forwarded: {route['total_forwarded']} CAs\n"
            message += f"   🆔 Route ID: {route['route_id']}\n"
            
            # Add pause/resume and delete buttons
            keyboard.append([
                InlineKeyboardButton(
                    "⏸️ Pause" if route['is_active'] else "▶️ Resume",
                    callback_data=f"toggle_route_{route['route_id']}"
                ),
                InlineKeyboardButton(
                    f"🗑️ Delete",
                    callback_data=f"delete_route_{route['route_id']}"
                )
            ])
            message += "\n"
        
        keyboard.append([InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")])

        try:
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        except Exception as e:
            if "message is not modified" in str(e).lower():
                pass  # content already matches — no action needed
            else:
                raise
    
    async def toggle_route(self, query, context, route_id: int):
        """Toggle route active/paused status"""
        user_id = query.from_user.id
        
        success, new_status = self.db.toggle_route_status(user_id, route_id)
        
        if success:
            status_text = "▶️ Resumed" if new_status else "⏸️ Paused"
            await query.answer(f"{status_text} route!")
            await self.view_routes(query, context)
        else:
            await query.answer("❌ Route not found or failed to update")
    
    async def show_help_callback(self, query, context):
        """Handle help callback"""
        user_id = query.from_user.id
        
        help_text = HELP_MESSAGE
        
        # Add admin commands if user is admin
        if user_id == ADMIN_USER_ID:
            help_text += "\n\n👑 *Admin Commands:*\n"
            help_text += "• `/grant <user_id> <tier> [days]` - Grant subscription\n"
            help_text += "• `/revoke <user_id>` - Revoke subscription\n"
            help_text += "• `/userinfo <user_id>` - View user details\n"
        
        keyboard = [[InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")]]
        
        await query.edit_message_text(
            help_text,
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
        message += f"• URLs today: {stats['urls_today']}\n"
        message += f"• Combined total: {stats['total_today']}/{stats['daily_limit']}\n"
        message += f"• Active routes: {stats['active_routes']}/{stats['max_routes']}\n\n"
        message += f"📅 *This Month:*\n"
        message += f"• CAs: {stats['cas_this_month']}\n"
        message += f"• URLs: {stats['urls_this_month']}\n\n"
        message += f"🎯 *All Time:*\n"
        message += f"• Total CAs: {stats['total_cas_all_time']}\n"
        message += f"• Total URLs: {stats['total_urls_all_time']}\n\n"
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
                    valid_id, id_msg = validate_api_id(api_id)
                    if not valid_id:
                        await update.message.reply_text(f"❌ Invalid API ID: {id_msg}\n\nTry again:")
                        return
                    valid_hash, hash_msg = validate_api_hash(api_hash)
                    if not valid_hash:
                        await update.message.reply_text(f"❌ Invalid API Hash: {hash_msg}\n\nTry again:")
                        return
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
            valid_phone, phone_msg = validate_phone(phone)
            if not valid_phone:
                await update.message.reply_text(f"❌ {phone_msg}")
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
                    "📲 Check Telegram for your verification code!\n\n"
                    "⚠️ *IMPORTANT:* To avoid Telegram blocking the login:\n"
                    "Enter the code with *spaces between each digit*\n\n"
                    "Example: If your code is `12345`, send:\n"
                    "`1 2 3 4 5`\n\n"
                    "This prevents Telegram from detecting it as code sharing.",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(f"❌ {message}\n\nTry again from /start")
                del self.user_states[user_id]
        
        elif state == USER_STATES['AWAITING_CODE']:
            code = text.strip()
            
            # Validate format - should contain enough digits
            # Use the same cleaning logic as session_manager to stay consistent
            digits_only = ''.join(c for c in code if c.isdigit())
            if len(digits_only) < MIN_VERIFICATION_CODE_LENGTH:
                await update.message.reply_text(
                    "❌ Code seems too short.\n\n"
                    "Remember to enter with spaces: `1 2 3 4 5`",
                    parse_mode='Markdown'
                )
                return
            
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
    
    async def grant_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command to grant subscription to a user"""
        user_id = update.effective_user.id
        
        # Check if admin
        if user_id != ADMIN_USER_ID:
            await update.message.reply_text("❌ Admin access required.")
            return
        
        # Parse arguments
        if len(context.args) < 2:
            await update.message.reply_text(
                "📝 *Usage:* `/grant <user_id> <tier> [days]`\n\n"
                "**Tiers:** `starter`, `pro`, `alpha`\n"
                "**Days:** Default 30\n\n"
                "**Example:**\n"
                "`/grant 123456789 pro 30`",
                parse_mode='Markdown'
            )
            return
        
        try:
            target_user_id = int(context.args[0])
            tier = context.args[1].lower()
            days = int(context.args[2]) if len(context.args) > 2 else 30
            
            if tier not in VALID_TIERS:
                await update.message.reply_text("❌ Invalid tier. Use: `starter`, `pro`, or `alpha`", parse_mode='Markdown')
                return
            
            # Check if user exists
            target_user = self.db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text(f"❌ User {target_user_id} not found in database.")
                return
            
            # Grant subscription
            success = self.db.update_subscription(target_user_id, tier, days)
            
            if success:
                # Notify admin
                await update.message.reply_text(
                    f"✅ *Subscription Granted!*\n\n"
                    f"👤 User: `{target_user_id}`\n"
                    f"🎫 Tier: {TIER_EMOJI.get(tier, '')} {tier.title()}\n"
                    f"📅 Duration: {days} days",
                    parse_mode='Markdown'
                )
                
                # Try to notify the user
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=f"🎉 *Congratulations!*\n\n"
                             f"You've been granted {TIER_EMOJI.get(tier, '')} *{tier.title()}* access for {days} days!\n\n"
                             f"Send /start to see your new features.",
                        parse_mode='Markdown'
                    )
                except Exception:
                    pass  # User may have blocked the bot
            else:
                await update.message.reply_text("❌ Failed to grant subscription.")
        
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID or days. Must be numbers.")
    
    async def revoke_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command to revoke subscription (downgrade to free)"""
        user_id = update.effective_user.id
        
        if user_id != ADMIN_USER_ID:
            await update.message.reply_text("❌ Admin access required.")
            return
        
        if not context.args:
            await update.message.reply_text(
                "📝 *Usage:* `/revoke <user_id>`\n\n"
                "**Example:**\n"
                "`/revoke 123456789`",
                parse_mode='Markdown'
            )
            return
        
        try:
            target_user_id = int(context.args[0])
            
            target_user = self.db.get_user(target_user_id)
            if not target_user:
                await update.message.reply_text(f"❌ User {target_user_id} not found.")
                return
            
            old_tier = target_user['subscription_tier']
            success = self.db.update_subscription(target_user_id, 'free', 0)
            
            if success:
                await update.message.reply_text(
                    f"✅ *Subscription Revoked*\n\n"
                    f"👤 User: `{target_user_id}`\n"
                    f"📉 {old_tier.title()} → Free",
                    parse_mode='Markdown'
                )
                
                # Notify user
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text="⚠️ Your subscription has been changed to the free tier.",
                    )
                except Exception:
                    pass
            else:
                await update.message.reply_text("❌ Failed to revoke subscription.")
        
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
    
    async def userinfo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command to view user info"""
        user_id = update.effective_user.id
        
        if user_id != ADMIN_USER_ID:
            await update.message.reply_text("❌ Admin access required.")
            return
        
        if not context.args:
            await update.message.reply_text(
                "📝 *Usage:* `/userinfo <user_id>`",
                parse_mode='Markdown'
            )
            return
        
        try:
            target_user_id = int(context.args[0])
            target_user = self.db.get_user(target_user_id)
            
            if not target_user:
                await update.message.reply_text(f"❌ User {target_user_id} not found.")
                return
            
            stats = self.db.get_user_stats(target_user_id)
            routes = self.db.get_user_routes(target_user_id)
            
            message = f"👤 *User Info*\n\n"
            message += f"🆔 ID: `{target_user_id}`\n"
            message += f"👤 Username: @{target_user.get('username', 'N/A')}\n"
            message += f"📛 Name: {target_user.get('first_name', 'N/A')}\n\n"
            
            message += f"🎫 *Subscription:*\n"
            message += f"├ Tier: {TIER_EMOJI.get(stats['subscription_tier'], '')} {stats['subscription_tier'].title()}\n"
            if stats.get('subscription_expires'):
                message += f"└ Expires: {stats['subscription_expires'][:10]}\n\n"
            else:
                message += f"└ Expires: Never (free)\n\n"
            
            message += f"📊 *Stats:*\n"
            message += f"├ Routes: {len(routes)}/{stats['max_routes']}\n"
            message += f"├ CAs Today: {stats['cas_today']}/{stats['daily_limit']}\n"
            message += f"├ CAs This Month: {stats['cas_this_month']}\n"
            message += f"└ Total CAs: {stats['total_cas_all_time']}\n\n"
            
            message += f"📅 Member since: {stats['member_since'][:10]}\n"
            message += f"🔐 Session: {'✅ Active' if target_user.get('session_active') else '❌ Not set up'}"
            
            await update.message.reply_text(message, parse_mode='Markdown')
        
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
    
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
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors globally"""
        logger.error(f"Exception while handling update: {context.error}", exc_info=context.error)
        
        # Try to notify user
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ An error occurred. Please try again.\n\n"
                    "If this persists, contact support."
                )
        except Exception:
            pass
    
    # ==================== MAIN RUN LOOP ====================
    
    async def run(self):
        """Run the bot"""
        logger.info("🚀 Starting Multi-User CA Mirror Bot...")
        logger.info(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Load existing user sessions
        await self.session_manager.load_all_sessions()
        
        # Setup Telegram bot
        self.bot_app = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        self.bot_app.add_handler(CommandHandler("start", self.start_command))
        self.bot_app.add_handler(CommandHandler("help", self.help_command))
        self.bot_app.add_handler(CommandHandler("routes", self.routes_command))
        self.bot_app.add_handler(CommandHandler("stats", self.stats_command))
        self.bot_app.add_handler(CommandHandler("pricing", self.pricing_command))
        self.bot_app.add_handler(CommandHandler("search", self.search_command))
        self.bot_app.add_handler(CommandHandler("export", self.export_command))
        self.bot_app.add_handler(CommandHandler("settings", self.settings_command))
        self.bot_app.add_handler(CommandHandler("subscribe_starter", self.subscribe_starter))
        self.bot_app.add_handler(CommandHandler("subscribe_pro", self.subscribe_pro))
        self.bot_app.add_handler(CommandHandler("subscribe_alpha", self.subscribe_alpha))
        
        # Admin commands
        self.bot_app.add_handler(CommandHandler("grant", self.grant_command))
        self.bot_app.add_handler(CommandHandler("revoke", self.revoke_command))
        self.bot_app.add_handler(CommandHandler("userinfo", self.userinfo_command))
        
        self.bot_app.add_handler(CallbackQueryHandler(self.button_callback))
        self.bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_handler))
        self.bot_app.add_handler(PreCheckoutQueryHandler(self.handle_precheckout))
        self.bot_app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, self.handle_successful_payment))
        
        # Add error handler
        self.bot_app.add_error_handler(self.error_handler)
        
        logger.info("✅ Telegram bot interface ready!")
        bot_me = await self.bot_app.bot.get_me()
        logger.info(f"🤖 Bot username: @{bot_me.username}")
        logger.info(f"\n💬 Send /start to @{bot_me.username} to begin!\n")
        
        # Initialize and run
        await self.bot_app.initialize()
        await self.bot_app.start()
        await self.bot_app.updater.start_polling()
        
        # Keep running
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("\n\n👋 Shutting down...")
            await self.session_manager.disconnect_all()
            await self.bot_app.stop()


async def main():
    """Main entry point"""
    # Validate configuration
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not found in .env!")
        logger.info("\n📝 Steps to create a bot:")
        logger.info("1. Message @BotFather on Telegram")
        logger.info("2. Send /newbot")
        logger.info("3. Follow instructions")
        logger.info("4. Add BOT_TOKEN=your_token to .env")
        return
    
    if not ADMIN_USER_ID:
        logger.error("❌ ADMIN_USER_ID not found in .env!")
        logger.info("Add your Telegram user ID to .env")
        return
    
    # Create and run bot
    bot = MultiUserCABot()
    
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("\n\n👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())

