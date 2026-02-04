"""
Payment Handler Module
======================
Handles Telegram Stars payments and subscription management
"""

from telegram import LabeledPrice, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
from datetime import datetime, timedelta

class PaymentHandler:
    """Handles payments via Telegram Stars"""
    
    # Pricing in Telegram Stars (1 Star ≈ $0.01-0.02 depending on region)
    # Approximate conversions: $10 ≈ 500-1000 stars, using 750 as middle ground
    PRICING = {
        'starter': {
            'price': 750,  # ~$10
            'name': '⭐ Starter Plan',
            'description': '3 routes, 100 CAs/day for 30 days',
            'duration_days': 30
        },
        'pro': {
            'price': 3000,  # ~$40
            'name': '💎 Pro Plan',
            'description': '10 routes, 500 CAs/day for 30 days',
            'duration_days': 30
        },
        'alpha': {
            'price': 10500,  # ~$140
            'name': '🔥 Alpha Plan',
            'description': 'Unlimited routes & CAs for 30 days',
            'duration_days': 30
        }
    }
    
    def __init__(self, db: Database):
        self.db = db
    
    async def show_pricing(self, update_or_query, context: ContextTypes.DEFAULT_TYPE):
        """Show pricing options to user - works with both Update and CallbackQuery"""
        
        # Determine if it's a callback or direct message
        if hasattr(update_or_query, 'from_user') and hasattr(update_or_query, 'message'):
            # It's a CallbackQuery from button click
            user_id = update_or_query.from_user.id
            chat_id = update_or_query.message.chat_id
            is_callback = True
        else:
            # It's an Update from command
            user_id = update_or_query.effective_user.id
            chat_id = update_or_query.effective_chat.id
            is_callback = False
        
        user = self.db.get_user(user_id)
        current_tier = user['subscription_tier'] if user else 'free'
        
        message = "💰 *Subscription Plans*\n\n"
        
        # Free tier
        message += "🆓 *FREE TIER*"
        if current_tier == 'free':
            message += " (Current)"
        message += "\n"
        message += "• 1 route\n"
        message += "• 3 CAs per day\n"
        message += "• Basic features\n\n"
        
        # Paid tiers
        for tier_id, tier_info in self.PRICING.items():
            emoji = "✅" if current_tier == tier_id else "⬜"
            message += f"{emoji} *{tier_info['name']}* - {tier_info['price']} ⭐\n"
            message += f"{tier_info['description']}\n\n"
        
        message += "💡 *What are Telegram Stars?*\n"
        message += "Telegram Stars is the official payment method in Telegram.\n"
        message += "You can buy stars directly in Telegram settings.\n\n"
        
        # Create subscribe buttons
        keyboard = [
            [InlineKeyboardButton("⭐ Get Starter", callback_data="subscribe_starter")],
            [InlineKeyboardButton("💎 Get Pro", callback_data="subscribe_pro")],
            [InlineKeyboardButton("🔥 Get Alpha", callback_data="subscribe_alpha")],
            [InlineKeyboardButton("« Back to Menu", callback_data="back_to_menu")]
        ]
        
        if is_callback:
            # Edit the existing message
            await update_or_query.edit_message_text(
                message, 
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            # Send a new message
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    
    async def create_invoice(self, update_or_query, context: ContextTypes.DEFAULT_TYPE, tier: str):
        """Create payment invoice for a tier - works with both Update and CallbackQuery"""
        
        if tier not in self.PRICING:
            error_msg = "❌ Invalid subscription tier!"
            if hasattr(update_or_query, 'answer'):
                # It's a CallbackQuery
                await update_or_query.answer(error_msg)
            else:
                # It's an Update
                await update_or_query.message.reply_text(error_msg)
            return
        
        # Determine if it's a callback or direct message
        if hasattr(update_or_query, 'from_user') and hasattr(update_or_query, 'message'):
            # It's a CallbackQuery from button click
            user_id = update_or_query.from_user.id
            chat_id = update_or_query.message.chat_id
            await update_or_query.answer("Creating invoice...")
        else:
            # It's an Update from command
            user_id = update_or_query.effective_user.id
            chat_id = update_or_query.effective_chat.id
        
        tier_info = self.PRICING[tier]
        
        # Create invoice
        title = tier_info['name']
        description = tier_info['description']
        payload = f"subscription_{tier}_{user_id}_{int(datetime.now().timestamp())}"
        currency = "XTR"  # Telegram Stars
        
        prices = [LabeledPrice(label=tier_info['name'], amount=tier_info['price'])]
        
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",  # Empty for Stars
            currency=currency,
            prices=prices,
            start_parameter=f"subscribe_{tier}"
        )
    
    async def handle_precheckout_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle pre-checkout query"""
        query = update.pre_checkout_query
        
        # Always approve
        await query.answer(ok=True)
    
    async def handle_successful_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle successful payment"""
        payment = update.message.successful_payment
        user_id = update.effective_user.id
        
        # Parse payload to get tier
        payload_parts = payment.invoice_payload.split('_')
        if len(payload_parts) < 2:
            await update.message.reply_text("❌ Error processing payment. Contact support.")
            return
        
        tier = payload_parts[1]
        
        if tier not in self.PRICING:
            await update.message.reply_text("❌ Invalid tier. Contact support.")
            return
        
        tier_info = self.PRICING[tier]
        
        # Create payment record
        payment_id = self.db.create_payment(
            user_id=user_id,
            amount=tier_info['price'],
            tier=tier,
            telegram_payment_id=payment.telegram_payment_charge_id
        )
        
        if not payment_id:
            await update.message.reply_text("❌ Error recording payment. Contact support.")
            return
        
        # Update subscription
        success = self.db.update_subscription(
            user_id=user_id,
            tier=tier,
            duration_days=tier_info['duration_days']
        )
        
        if not success:
            await update.message.reply_text("❌ Error activating subscription. Contact support.")
            return
        
        # Mark payment as completed
        self.db.complete_payment(payment_id)
        
        # Get updated user info
        user = self.db.get_user(user_id)
        expires = datetime.fromisoformat(user['subscription_expires_at'])
        
        # Send success message
        message = f"🎉 *Payment Successful!*\n\n"
        message += f"✅ {tier_info['name']} activated\n"
        message += f"📅 Valid until: {expires.strftime('%Y-%m-%d')}\n\n"
        message += f"🔧 You can now:\n"
        message += f"• Add up to {user['total_routes_allowed']} routes\n"
        message += f"• Forward up to {user['daily_ca_limit']} CAs per day\n\n"
        
        # Check if user needs to authenticate
        if not user['session_active']:
            message += "📱 *Next step:* Set up your monitoring\n"
            message += "Send /start and click 'Setup Authentication'"
        else:
            message += "🚀 *You're all set!*\n"
            message += "Send /start and click 'Add Route' to begin!"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    def get_tier_info(self, tier: str) -> dict:
        """Get information about a tier"""
        return self.PRICING.get(tier, {})
    
    def check_subscription_status(self, user_id: int) -> tuple[bool, str, str]:
        """
        Check subscription status
        Returns: (is_active: bool, tier: str, message: str)
        """
        user = self.db.get_user(user_id)
        if not user:
            return (False, 'free', "User not found")
        
        tier = user['subscription_tier']
        
        if tier == 'free':
            return (True, 'free', "Free tier (limited features)")
        
        if not user['subscription_expires_at']:
            return (False, tier, "Subscription expired")
        
        expires = datetime.fromisoformat(user['subscription_expires_at'])
        now = datetime.now()
        
        if expires < now:
            # Subscription expired, downgrade to free
            self.db.update_subscription(user_id, 'free', 0)
            return (False, 'free', "Subscription expired, downgraded to free tier")
        
        days_left = (expires - now).days
        
        if days_left <= 3:
            message = f"⚠️ {tier.title()} tier - {days_left} days left. Renew soon!"
        else:
            message = f"✅ {tier.title()} tier - {days_left} days remaining"
        
        return (True, tier, message)
    
    async def show_subscription_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user their subscription status"""
        user_id = update.effective_user.id
        
        is_active, tier, message = self.check_subscription_status(user_id)
        stats = self.db.get_user_stats(user_id)
        
        status_msg = f"📊 *Your Subscription*\n\n"
        status_msg += f"{message}\n\n"
        
        status_msg += f"📈 *Usage:*\n"
        status_msg += f"• CAs today: {stats['cas_today']}/{stats['daily_limit']}\n"
        status_msg += f"• Active routes: {stats['active_routes']}/{stats['max_routes']}\n"
        status_msg += f"• CAs this month: {stats['cas_this_month']}\n"
        status_msg += f"• Total CAs: {stats['total_cas_all_time']}\n\n"
        
        if tier != 'free' and is_active:
            expires = datetime.fromisoformat(stats['subscription_expires'])
            status_msg += f"⏰ Expires: {expires.strftime('%Y-%m-%d %H:%M')}\n\n"
        
        if not is_active or tier == 'free':
            status_msg += "💡 Want more? Send /pricing to upgrade!"
        
        await update.message.reply_text(status_msg, parse_mode='Markdown')
    
    async def handle_refund_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle refund request"""
        message = "🔄 *Refund Policy*\n\n"
        message += "Refunds are available within 7 days of purchase if:\n"
        message += "• You haven't used the service\n"
        message += "• There's a technical issue\n\n"
        message += "To request a refund, contact support:\n"
        message += f"@{context.bot.username} with your payment details"
        
        await update.message.reply_text(message, parse_mode='Markdown')

