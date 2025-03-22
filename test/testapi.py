import os
from pybit.unified_trading import HTTP

session = HTTP(
    api_key=os.getenv("BYBIT_API_KEY"),
    api_secret=os.getenv("BYBIT_API_SECRET"),
    testnet=True  # или False, если торгуешь в реале
)

response = session.get_wallet_balance(accountType="UNIFIED")
print(response)
