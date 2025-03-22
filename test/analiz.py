from pybit.unified_trading import HTTP
import pandas as pd
import datetime

# Подключение к API Bybit (используй свои ключи, если нужно)
session = HTTP(testnet=True)  # Для демо-версии

# Запрос исторических данных с 15 марта 00:00
symbol = "BTCUSDT"
interval = "15"  # 15-минутные свечи
start_time = int(datetime.datetime(2024, 3, 15, 0, 0).timestamp() * 1000)  # В миллисекундах
limit = 500  # Максимальное число свечей

response = session.get_kline(
    category="linear",
    symbol=symbol,
    interval=interval,
    start=start_time,
    limit=limit
)

# Обрабатываем данные
if response["retCode"] == 0:
    data = response["result"]["list"]
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")  # Преобразуем время
    df.to_csv("bybit_btcusdt_15m.csv", index=False)
    print("✅ Данные сохранены в bybit_btcusdt_15m.csv")
else:
    print("❌ Ошибка при получении данных:", response)
