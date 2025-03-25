Crypto Trading Bot 

This is an automated trading bot for the Bybit exchange. It supports scalping strategies with risk management, volume and order book analysis, technical indicators (ATR, RSI, VWAP), and Telegram bot integration.

Features

- 🔄 Auto-refreshing market data (price, positions, PnL)
- 📊 Risk management (daily loss limit, max trades per day)
- 📉 ATR-based stop-loss and multi-level take-profits
- 📈 VWAP, RSI, SMA, EMA indicators
- 📬 Telegram notifications & command interface
- 🧠 Volume and order book analysis for signal confirmation
- 🪙 Supports USDT-margined perpetual contracts

Tech Stack

- Python 3.10+
- Aiogram (Telegram bot)
- Bybit Unified API (pybit)
- TA-Lib (technical analysis)
- asyncio & aiohttp

!!!Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/3cryptovit/crypto_trading_bot.git
   cd crypto_trading_bot
2. Create .env file with your API keys:
BYBIT_API_KEY=your_key
BYBIT_API_SECRET=your_secret
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

3. Install dependencies:
pip install -r requirements.txt

4. Run the bot:
python main.py

python telegram.py


