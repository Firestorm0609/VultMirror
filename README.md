# 🔮 VultMirror

> **The Ultimate Solana CA Mirror Bot for Telegram**

Monitor Solana contract addresses from ANY Telegram channel and instantly forward them to your private group. Stay ahead of the market with real-time CA alerts.

## ✨ Features

- 🔍 **Smart CA Detection** - Automatically extracts valid Solana addresses
- 🚀 **Instant Forwarding** - Zero-delay CA mirroring to your target chat
- 👥 **Multi-User Support** - Each user has their own private monitoring
- 🔒 **100% Private** - Source channels will never know you're monitoring
- 💰 **Subscription Tiers** - Free, Starter, Pro, and Alpha plans
- 📊 **Analytics Dashboard** - Track your CA forwarding stats
- 🛡️ **Encrypted Credentials** - Your API keys are safely stored

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Your Telegram User ID

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/Firestorm0609/VultMirror.git
   cd VultMirror
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

4. **Initialize database**
   ```bash
   python -c "from database import Database; Database()"
   ```

5. **Run the bot**
   ```bash
   python bot.py
   ```

## 📱 User Guide

### Getting Started
1. Start the bot: `/start`
2. Set up authentication with your Telegram API credentials
3. Add your first route (source channel → target chat)
4. Watch CAs flow in! 🎉

### Commands
| Command | Description |
|---------|-------------|
| `/start` | Main menu & bot status |
| `/help` | Show help and commands |
| `/routes` | View your active routes |
| `/stats` | Your forwarding statistics |
| `/pricing` | View subscription plans |

### Subscription Tiers

| Tier | Routes | CAs/Day | Price |
|------|--------|---------|-------|
| 🆓 Free | 1 | 3 | Free |
| ⭐ Starter | 3 | 100 | 750 Stars |
| 💎 Pro | 10 | 500 | 3,000 Stars |
| 🔥 Alpha | Unlimited | Unlimited | 10,500 Stars |

## 🔧 Configuration

### Environment Variables
| Variable | Description | Required |
|----------|-------------|----------|
| `BOT_TOKEN` | Telegram Bot API token | ✅ |
| `ADMIN_USER_ID` | Your Telegram user ID | ✅ |
| `DB_PATH` | Custom database path | ❌ |

## 🛡️ Security

- All user credentials are encrypted using Fernet symmetric encryption
- Session files are stored locally and never transmitted
- Database contains no plaintext sensitive data

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🤝 Support

Having issues? Open an issue or contact the admin through the bot.
