import logging
import os
import time
import asyncio
import json
import re
import requests
import numpy as np
from pybit.unified_trading import HTTP
from datetime import datetime
import talib
import aiohttp
from dotenv import load_dotenv
import sys

# ✅ Исправление ошибки для aiodns на Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ======================== Загрузка конфигурации ========================
load_dotenv()  # Загружаем переменные из .env

def validate_config():
    """
    Проверяет корректность конфигурации и останавливает бота при критических ошибках
    """
    critical_errors = []
    
    # Проверяем API ключи
    if not os.getenv("BYBIT_API_KEY") or not os.getenv("BYBIT_API_SECRET"):
        critical_errors.append("Отсутствуют API ключи Bybit")
    
    # Проверяем Telegram токен
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        critical_errors.append("Отсутствуют настройки Telegram")
    
    # Проверяем символ
    symbol = os.getenv("SYMBOL", "BTCUSDT").upper()
    if not symbol.endswith("USDT"):
        critical_errors.append(f"Неподдерживаемый символ {symbol}. Бот работает только с USDT-маржинальными контрактами")
    
    # Проверяем плечо
    try:
        leverage = int(os.getenv("LEVERAGE", 3))
        min_leverage = int(os.getenv("MIN_LEVERAGE", 1))
        max_leverage = int(os.getenv("MAX_LEVERAGE", 5))
        
        if leverage < min_leverage or leverage > max_leverage:
            critical_errors.append(f"Установленное плечо {leverage} вне допустимого диапазона [{min_leverage}, {max_leverage}]")
    except ValueError:
        critical_errors.append("Некорректное значение плеча")
    
    # Проверяем параметры риск-менеджмента
    try:
        risk_percentage = float(os.getenv("RISK_PERCENTAGE", 1))
        if risk_percentage <= 0 or risk_percentage > 5:
            critical_errors.append(f"Некорректный процент риска: {risk_percentage}. Допустимый диапазон: 0-5%")
    except ValueError:
        critical_errors.append("Некорректное значение процента риска")
    
    # Добавьте после проверки процента риска
    max_daily_trades = int(os.getenv("MAX_DAILY_TRADES", 12))
    if not (1 <= max_daily_trades <= 20):
        critical_errors.append("MAX_DAILY_TRADES должно быть от 1 до 20")
    
    # Если есть критические ошибки, отправляем уведомление и останавливаем бота
    if critical_errors:
        error_message = "🚫 Критические ошибки конфигурации:\n" + "\n".join(f"- {error}" for error in critical_errors)
        print(error_message)
        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            # Используем asyncio.run для вызова асинхронной функции из синхронного кода
            asyncio.run(send_telegram_message(error_message))
        sys.exit(1)
    
    return {
        "symbol": symbol,
        "leverage": leverage,
        "min_leverage": min_leverage,
        "max_leverage": max_leverage,
        "risk_percentage": risk_percentage,
        "max_daily_trades": max_daily_trades
    }

# Валидируем конфигурацию при запуске
config = validate_config()

# Используем валидированные значения
SYMBOL = config["symbol"]
LEVERAGE = config["leverage"]
MIN_LEVERAGE = config["min_leverage"]
MAX_LEVERAGE = config["max_leverage"]
RISK_PERCENTAGE = config["risk_percentage"]
MAX_DAILY_TRADES = config["max_daily_trades"]

# Остальные параметры конфигурации
TESTNET = os.getenv("TESTNET", "True").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Параметры входа в позицию
VOLUME_THRESHOLD = float(os.getenv("VOLUME_THRESHOLD", 1.5))
ORDERBOOK_DEPTH = int(os.getenv("ORDERBOOK_DEPTH", 10))
MIN_VOLUME_RATIO = float(os.getenv("MIN_VOLUME_RATIO", 1.2))

# Параметры управления позицией
TAKE_PROFIT_1 = float(os.getenv("TAKE_PROFIT_1", 0.3))
TAKE_PROFIT_2 = float(os.getenv("TAKE_PROFIT_2", 0.6))
TAKE_PROFIT_3 = float(os.getenv("TAKE_PROFIT_3", 1.0))
TRAILING_STOP = float(os.getenv("TRAILING_STOP", 0.2))

# Параметры стоп-лосса
STOP_LOSS_PERCENTAGE = float(os.getenv("STOP_LOSS_PERCENTAGE", 0.3))
ATR_MULTIPLIER = float(os.getenv("ATR_MULTIPLIER", 2.0))

# Параметры технического анализа
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", 70))
RSI_OVERSOLD = float(os.getenv("RSI_OVERSOLD", 30))
VWAP_PERIOD = int(os.getenv("VWAP_PERIOD", 20))

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 5))

# Минимальные размеры позиций для разных пар
MIN_POSITION_SIZES = {
    "BTCUSDT": 0.001,
    "ETHUSDT": 0.01,
    "SOLUSDT": 1,
    "XRPUSDT": 10,
    "ADAUSDT": 10,
    "DOGEUSDT": 100,
    "MATICUSDT": 10,
    "LINKUSDT": 1,
    "UNIUSDT": 0.1,
    "AVAXUSDT": 0.1
}

# Минимальные расстояния для стоп-лоссов (в %)
MIN_STOP_DISTANCES = {
    "BTCUSDT": 0.1,
    "ETHUSDT": 0.1,
    "SOLUSDT": 0.2,
    "XRPUSDT": 0.2,
    "ADAUSDT": 0.2,
    "DOGEUSDT": 0.3,
    "MATICUSDT": 0.2,
    "LINKUSDT": 0.2,
    "UNIUSDT": 0.2,
    "AVAXUSDT": 0.2
}

# ======================== Настройка логирования ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)

def update_json_file(filename: str, data: dict) -> bool:
    """Обновляет JSON файл с данными"""
    try:
        # Добавляем временную метку обновления
        if isinstance(data, dict):
            data["last_updated"] = datetime.now().isoformat()
        
        # Создаем временный файл
        temp_filename = f"{filename}.tmp"
        
        # Записываем во временный файл
        with open(temp_filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # Проверяем, что файл создался и не пустой
        if os.path.exists(temp_filename) and os.path.getsize(temp_filename) > 0:
            # Если всё в порядке, переименовываем временный файл
            if os.path.exists(filename):
                os.replace(temp_filename, filename)
            else:
                os.rename(temp_filename, filename)
            
            logging.info(f"✅ Данные успешно записаны в {filename}")
            return True
        else:
            logging.error(f"❌ Временный файл {temp_filename} не создан или пуст")
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            return False
            
    except Exception as e:
        logging.error(f"❌ Ошибка записи в {filename}: {e}")
        logging.error(f"❌ Тип ошибки: {type(e).__name__}")
        import traceback
        logging.error(f"❌ Трейсбек: {traceback.format_exc()}")
        
        # Удаляем временный файл, если он существует
        if os.path.exists(temp_filename):
            os.remove(temp_filename)
        return False

def read_json_file(filename: str) -> dict:
    """Читает данные из JSON файла"""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}

def check_signals() -> bool:
    """Проверяет сигналы от Telegram бота"""
    try:
        signals = read_json_file('signals.json')
        if signals.get('refresh_data'):
            signals['refresh_data'] = False
            update_json_file('signals.json', signals)
            return True
        return False
    except Exception as e:
        logging.error(f"Ошибка при проверке сигналов: {e}")
        return False

# ======================== Функции для работы с Telegram ========================
async def send_telegram_message(message):
    """
    Отправляет сообщение в Telegram чат
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Не заданы переменные для Telegram бота")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as response:
            if response.status != 200:
                logging.error(f"Ошибка отправки Telegram-сообщения: {await response.text()}")

class BybitAPI:
    """
    Класс для работы с API Bybit с контролем частоты запросов
    """
    def __init__(self, session, min_request_interval=1.0):
        self.session = session
        self.min_request_interval = min_request_interval
        self.last_request_time = 0
        self.rate_limit_retries = 3
        self.rate_limit_delay = 60  # секунды

    async def _wait_for_rate_limit(self):
        """Ожидание между запросами"""
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        if time_since_last_request < self.min_request_interval:
            await asyncio.sleep(self.min_request_interval - time_since_last_request)
        self.last_request_time = time.time()

    async def _handle_api_error(self, response, retry_count=0):
        """Обработка ошибок API"""
        if response.get("retCode") == 0:
            return response

        error_msg = response.get("retMsg", "Неизвестная ошибка")
        error_code = response.get("retCode")

        # Обработка rate limit
        if error_code == 10006 and retry_count < self.rate_limit_retries:
            logging.warning(f"Превышен лимит запросов. Ожидание {self.rate_limit_delay} секунд...")
            await asyncio.sleep(self.rate_limit_delay)
            return None

        # Обработка других ошибок
        error_message = f"Ошибка API (код {error_code}): {error_msg}"
        logging.error(error_message)
        await send_telegram_message(f"⚠️ {error_message}")
        return None

    async def get_positions(self, category="linear", symbol=SYMBOL):
        """Получение позиций с обработкой ошибок"""
        await self._wait_for_rate_limit()
        try:
            response = self.session.get_positions(category=category, symbol=symbol)
            return await self._handle_api_error(response)
        except Exception as e:
            logging.error(f"Ошибка при получении позиций: {e}")
            return None

    async def get_kline(self, category="linear", symbol=SYMBOL, interval="5", limit=50):
        """Получение свечей с обработкой ошибок"""
        await self._wait_for_rate_limit()
        try:
            response = self.session.get_kline(category=category, symbol=symbol, interval=interval, limit=limit)
            return await self._handle_api_error(response)
        except Exception as e:
            logging.error(f"Ошибка при получении свечей: {e}")
            return None

    async def get_orderbook(self, category="linear", symbol=SYMBOL, limit=50):
        """Получение стакана с обработкой ошибок"""
        await self._wait_for_rate_limit()
        try:
            response = self.session.get_orderbook(category=category, symbol=symbol, limit=limit)
            return await self._handle_api_error(response)
        except Exception as e:
            logging.error(f"Ошибка при получении стакана: {e}")
            return None

    async def get_executions(self, category="linear", symbol=SYMBOL, limit=50):
        """Получение исполненных ордеров с обработкой ошибок"""
        await self._wait_for_rate_limit()
        try:
            response = self.session.get_executions(category=category, symbol=symbol, limit=limit)
            return await self._handle_api_error(response)
        except Exception as e:
            logging.error(f"Ошибка при получении исполненных ордеров: {e}")
            return None

    async def get_wallet_balance(self, accountType="UNIFIED"):
        """Получение баланса с обработкой ошибок"""
        await self._wait_for_rate_limit()
        try:
            response = self.session.get_wallet_balance(accountType=accountType)
            return await self._handle_api_error(response)
        except Exception as e:
            logging.error(f"Ошибка при получении баланса: {e}")
            return None

    async def set_leverage(self, symbol=SYMBOL, leverage=5):
        """Устанавливает плечо раздельно для лонга и шорта"""
        try:
            await self._wait_for_rate_limit()
            response = self.session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage)
            )
            return await self._handle_api_error(response)
        except Exception as e:
            logging.error(f"Ошибка при установке плеча: {e}")
            return None

    async def place_order(self, side, qty, stop_loss=None, take_profit_1=None, take_profit_2=None, take_profit_3=None):
        """
        Размещает лимитный мейкер-ордер для USDT-M Perpetual Futures с поддержкой частичного закрытия
        """
        try:
            # Получаем текущую цену
            current_price = await self.get_latest_price()
            if not current_price:
                return False

            # Размещаем основной ордер
            await self._wait_for_rate_limit()
            try:
                order = self.session.place_order(
                    category="linear",
                    symbol=SYMBOL,
                    side=side,
                    orderType="Limit",
                    qty=str(qty),
                    price=str(current_price),
                    timeInForce="PostOnly"  # Мейкерский ордер
                )
            except Exception as e:
                logging.error(f"Ошибка при размещении ордера: {e}")
                return False

            if not order or "result" not in order:
                logging.error(f"Ошибка размещения ордера: {order}")
                return False

            order_id = order["result"]["orderId"]
            logging.info(f"Размещен ордер {order_id}")

            # Устанавливаем стоп-лосс и первый тейк-профит
            if stop_loss and take_profit_1:
                await self._wait_for_rate_limit()
                try:
                    self.session.set_trading_stop(
                        category="linear",
                        symbol=SYMBOL,
                        side=side,
                        stopLoss=str(stop_loss),
                        takeProfit=str(take_profit_1)
                    )
                    logging.info(f"Установлены SL: {stop_loss} и TP1: {take_profit_1}")
                except Exception as e:
                    logging.error(f"Ошибка при установке SL/TP: {e}")
                    # Продолжаем выполнение, так как ордер уже размещен

            # Размещаем дополнительные тейк-профиты как лимитные ордера
            if take_profit_2 and take_profit_3:
                # Рассчитываем размеры для частичного закрытия
                tp2_qty = qty * 0.3  # 30% позиции
                tp3_qty = qty * 0.4  # 40% позиции

                # Проверяем минимальные размеры
                if tp2_qty >= MIN_POSITION_SIZES.get(SYMBOL, 0.002):
                    await self._wait_for_rate_limit()
                    try:
                        tp2_order = self.session.place_order(
                            category="linear",
                            symbol=SYMBOL,
                            side="Sell" if side == "Buy" else "Buy",
                            orderType="Limit",
                            qty=str(tp2_qty),
                            price=str(take_profit_2),
                            timeInForce="PostOnly",
                            reduceOnly=True
                        )
                        if tp2_order and "result" in tp2_order:
                            logging.info(f"Размещен TP2 ордер: {tp2_order['result']['orderId']}")
                    except Exception as e:
                        logging.error(f"Ошибка при размещении TP2 ордера: {e}")

                if tp3_qty >= MIN_POSITION_SIZES.get(SYMBOL, 0.003):
                    await self._wait_for_rate_limit()
                    try:
                        tp3_order = self.session.place_order(
                            category="linear",
                            symbol=SYMBOL,
                            side="Sell" if side == "Buy" else "Buy",
                            orderType="Limit",
                            qty=str(tp3_qty),
                            price=str(take_profit_3),
                            timeInForce="PostOnly",
                            reduceOnly=True
                        )
                        if tp3_order and "result" in tp3_order:
                            logging.info(f"Размещен TP3 ордер: {tp3_order['result']['orderId']}")
                    except Exception as e:
                        logging.error(f"Ошибка при размещении TP3 ордера: {e}")

            return True
        except Exception as e:
            error_msg = f"Ошибка при размещении ордера: {e}"
            logging.error(error_msg)
            await send_telegram_message(f"⚠ {error_msg}")
            return False

    async def set_trading_stop(self, category="linear", symbol=SYMBOL, side="Buy", stopLoss=None, takeProfit=None):
        """Установка стоп-лосса и тейк-профита с обработкой ошибок"""
        await self._wait_for_rate_limit()
        params = {
            "category": category,
            "symbol": symbol,
            "side": side
        }
        if stopLoss:
            params["stopLoss"] = str(stopLoss)
        if takeProfit:
            params["takeProfit"] = str(takeProfit)
        
        try:
            response = self.session.set_trading_stop(**params)
            return await self._handle_api_error(response)
        except Exception as e:
            logging.error(f"Ошибка при установке стоп-лосса: {e}")
            return None

    async def get_closed_pnl(self, category="linear", symbol=SYMBOL, startTime=None, endTime=None, limit=50):
        """Получение закрытых PNL с обработкой ошибок"""
        await self._wait_for_rate_limit()
        params = {
            "category": category,
            "symbol": symbol,
            "limit": limit
        }
        if startTime:
            params["startTime"] = startTime
        if endTime:
            params["endTime"] = endTime
        
        try:    
            response = self.session.get_closed_pnl(**params)
            return await self._handle_api_error(response)
        except Exception as e:
            logging.error(f"Ошибка при получении закрытых PNL: {e}")
            return None

    async def get_order_list(self, category="linear", symbol=SYMBOL, orderId=None):
        """Получение информации о конкретном ордере"""
        await self._wait_for_rate_limit()
        params = {
            "category": category,
            "symbol": symbol,
            "orderId": orderId
        }
        try:
            response = self.session.get_open_orders(**params)
            return await self._handle_api_error(response)
        except Exception as e:
            logging.error(f"Ошибка при получении списка ордеров: {e}")
            return None

    async def get_tickers(self, category="linear", symbol=SYMBOL):
        """Получение текущей цены тикера"""
        await self._wait_for_rate_limit()
        try:
            response = self.session.get_tickers(category=category, symbol=symbol)
            return await self._handle_api_error(response)
        except Exception as e:
            logging.error(f"Ошибка при получении тикеров: {e}")
            return None

    async def get_latest_price(self):
        """Получает последнюю цену"""
        try:
            tickers = await self.api.get_tickers(category="linear", symbol=SYMBOL)
            if tickers and "result" in tickers and "list" in tickers["result"]:
                price = float(tickers["result"]["list"][0]["lastPrice"])
                # Сохраняем цену в price.json
                price_data = {"price": price, "last_updated": datetime.now().isoformat()}
                if update_json_file("price.json", price_data):
                    logging.info(f"✅ Цена успешно обновлена: {price}")
                else:
                    logging.error("❌ Ошибка при сохранении цены в JSON")
                return price
            return None
        except Exception as e:
            logging.error(f"Ошибка при получении цены: {e}")
            return None

# ======================== Класс торгового бота ========================
class TradingBot:
    """
    Торговый бот для работы с Bybit API
    """
    def __init__(self):
        """
        Инициализация бота
        """
        try:
            # Инициализация базовых атрибутов
            self.leverage_set = False
            self.stop_monitor = False
            self.active_position = False
            self.current_position = None
            self.last_order_time = 0
            self.last_trade_time = time.time()  # Добавляем инициализацию
            self.daily_trade_count = 0
            self.max_daily_trades = MAX_DAILY_TRADES
            self.min_order_interval = 300
            self.last_checked_price = None
            self.consecutive_losses = 0
            self.processed_orders = set()
            self.last_daily_reset = datetime.now().date()
            self.daily_pnl = 0
            
            # Инициализация сессии Bybit API
            self.session = HTTP(
                testnet=TESTNET,
                api_key=os.getenv("BYBIT_API_KEY"),
                api_secret=os.getenv("BYBIT_API_SECRET")
            )
            
            # Создаем API клиент с контролем частоты запросов
            self.api = BybitAPI(self.session)
            
            self.last_positions_update = 0
            self.last_pnl_update = 0
            self.last_signal_time = None
            self.is_running = False
            self.monitor_task = None
            self.bot_task = None
            self.positions = {}
            self.pnl_data = {"daily_pnl": 0, "trades": []}
            self.signals = {"refresh_data": False, "last_signal": None, "last_updated": None}
            
            logging.info(f"✅ Бот успешно инициализирован. leverage_set = {self.leverage_set}")
        except Exception as e:
            logging.error(f"❌ Ошибка при инициализации бота: {e}")
            raise

    async def initialize_leverage(self):
        """Инициализация плеча"""
        try:
            if not hasattr(self, 'leverage_set'):
                self.leverage_set = False
                logging.info("✅ Атрибут leverage_set создан")
            
            # Сначала проверяем текущее плечо
            positions = await self.api.get_positions(symbol=SYMBOL)
            if positions and positions.get("retCode") == 0 and "result" in positions and "list" in positions["result"]:
                position_list = positions["result"]["list"]
                
                if position_list:
                    current_leverage = None
                    # Проверяем каждую позицию
                    for position in position_list:
                        if position.get("symbol") == SYMBOL:
                            current_leverage = position.get("leverage")
                            if current_leverage:
                                current_leverage = float(current_leverage)
                                break
                    
                    # Если плечо уже установлено правильно, не меняем его
                    if current_leverage == LEVERAGE:
                        self.leverage_set = True
                        logging.info(f"✅ Плечо уже установлено: {LEVERAGE}x")
                        return True
            
            # Если плечо не установлено или отличается от требуемого, устанавливаем новое
            retries = 3
            while retries > 0:
                result = await self.api.set_leverage(symbol=SYMBOL, leverage=LEVERAGE)
                if result and result.get("retCode") == 0:
                    self.leverage_set = True
                    logging.info(f"✅ Плечо успешно установлено: {LEVERAGE}x")
                    return True
                elif result and result.get("retCode") == 110043:
                    # Ошибка 'leverage not modified' - плечо уже установлено
                    self.leverage_set = True
                    logging.info(f"✅ Плечо уже установлено ранее: {LEVERAGE}x")
                    return True
                retries -= 1
                await asyncio.sleep(1)
            
            error_msg = "❌ Не удалось установить плечо после нескольких попыток"
            logging.error(error_msg)
            await send_telegram_message(error_msg)
            return False
            
        except Exception as e:
            error_msg = f"❌ Ошибка при установке плеча: {e}"
            logging.error(error_msg)
            await send_telegram_message(error_msg)
            return False

    async def process_signal(self, signal_data):
        """Обрабатывает входной сигнал из signals.json"""
        try:
            side = signal_data.get("side")
            price = float(signal_data.get("price"))
            stop_loss = float(signal_data.get("stop_loss"))
            take_profit_1 = float(signal_data.get("take_profit_1"))
            take_profit_2 = float(signal_data.get("take_profit_2"))
            take_profit_3 = float(signal_data.get("take_profit_3"))
            qty = float(signal_data.get("qty"))

            if not side or not price or not qty:
                logging.error("❌ Ошибка: некорректные данные сигнала")
                return

            # Размещаем ордер
            if await self.api.place_order(side, qty, stop_loss, take_profit_1, take_profit_2, take_profit_3):
                logging.info(f"✅ Успешно открыт ордер {side} {qty} @ {price}")
                self.active_position = True
                self.current_position = {
                    "side": side,
                    "entry_price": price,
                    "stop_loss": stop_loss,
                    "take_profit_1": take_profit_1,
                    "take_profit_2": take_profit_2,
                    "take_profit_3": take_profit_3,
                    "size": qty
                }

                await send_telegram_message(f"✅ Открыта позиция: {side} {qty} @ {price:.2f}")

                # Очистка сигнала
                signal_data["force_trade"] = False
                update_json_file("signals.json", signal_data)
        except Exception as e:
            logging.error(f"❌ Ошибка при обработке сигнала: {e}")

    async def run(self):
        """Запуск бота"""
        global bot_running
        bot_running = True

        try:
            # Проверяем и инициализируем leverage_set если нужно
            if not hasattr(self, 'leverage_set'):
                self.leverage_set = False
                logging.info("✅ Атрибут leverage_set создан в run()")

            # Инициализация плеча
            if not self.leverage_set:
                success = await self.initialize_leverage()
                if not success:
                    logging.error("❌ Ошибка установки плеча, бот не может продолжить работу")
                    return False

            # Запуск мониторинга позиций
            self.position_watcher_task = asyncio.create_task(self.position_monitor())

            # Отправка приветственного сообщения
            await send_telegram_message(
                f"🤖 Торговый бот запущен\n"
                f"📊 Торговая пара: {SYMBOL}\n"
                f"📈 Плечо: {LEVERAGE}x\n"
                f"💰 Риск на сделку: {RISK_PERCENTAGE}%"
            )

            logging.info("Торговый бот запущен и ожидает сигналы")
            
            # Основной цикл бота
            while bot_running:
                if check_signals():
                    logging.info("Получен сигнал на обновление данных")
                    await self.update_trading_data()

                # Проверяем сигналы на вход в сделку
                signal_data = read_json_file("signals.json")
                if signal_data.get("force_trade", False):
                    await self.process_signal(signal_data)

                # Обновляем трейлинг-стоп для активной позиции
                if self.active_position:
                    current_price = await self.api.get_latest_price()
                    if current_price:
                        await self.update_trailing_stop(self.current_position, current_price)
                
                await self.check_pnl()
                await self.check_positions()
                await asyncio.sleep(CHECK_INTERVAL)
                
        except asyncio.CancelledError:
            logging.info("Задача бота отменена")
            bot_running = False
        except Exception as e:
            logging.error(f"Ошибка в основном цикле бота: {e}")
            await send_telegram_message(f"⚠️ Ошибка в работе бота: {e}")
            bot_running = False
            raise  # Пробрасываем ошибку дальше для отладки
        finally:
            if hasattr(self, 'position_watcher_task') and self.position_watcher_task:
                self.position_watcher_task.cancel()
            
            logging.info("Бот остановлен")
            await send_telegram_message("🛑 Бот остановлен")
            return True

    async def update_trading_data(self):
        """
        Обновляет данные о балансе, позициях и PnL
        """
        try:
            current_time = time.time()
            
            # Обновляем баланс каждые 5 минут
            if current_time - self.last_positions_update >= 300:
                logging.info("🔄 Начинаем обновление баланса...")
                balance_info = await self.api.get_wallet_balance(accountType="UNIFIED")
                logging.info(f"📊 Ответ API get_wallet_balance: {balance_info}")

                if balance_info and "result" in balance_info and "list" in balance_info["result"]:
                    wallet = balance_info["result"]["list"][0]
                    logging.info(f"📊 Данные кошелька: {wallet}")
                    
                    # Собираем все балансы
                    all_balances = {}
                    for coin in wallet.get("coin", []):
                        try:
                            coin_name = coin.get("coin", "")
                            if coin_name in ["USDT", "USD"]:
                                balance = float(coin.get("walletBalance", 0))
                                equity = float(coin.get("equity", 0))
                                unrealized_pnl = float(coin.get("unrealizedPnl", 0))
                                all_balances[coin_name] = {
                                    "balance": balance,
                                    "equity": equity,
                                    "unrealized_pnl": unrealized_pnl
                                }
                                logging.info(f"📊 Баланс {coin_name}: {balance}, Equity: {equity}, Unrealized PnL: {unrealized_pnl}")
                        except (ValueError, TypeError) as e:
                            logging.error(f"❌ Ошибка при обработке баланса {coin_name}: {e}")
                            continue

                    # Суммируем все USD и USDT балансы
                    total_balance = sum(b["balance"] for b in all_balances.values())
                    total_equity = sum(b["equity"] for b in all_balances.values())
                    total_unrealized_pnl = sum(b["unrealized_pnl"] for b in all_balances.values())
                    
                    logging.info(f"📊 Общий баланс: {total_balance:.2f}")
                    logging.info(f"📊 Общий equity: {total_equity:.2f}")
                    logging.info(f"📊 Общий unrealized PnL: {total_unrealized_pnl:.2f}")
                    
                    if total_balance > 0:
                        try:
                            balance_data = {
                                "balance": total_balance,
                                "equity": total_equity,
                                "unrealized_pnl": total_unrealized_pnl,
                                "used_margin": float(wallet.get("lockedBalance", 0)),
                                "free_margin": float(wallet.get("availableBalance", 0)),
                                "last_updated": datetime.now().isoformat(),
                                "details": {
                                    "USDT": all_balances.get("USDT", 0),
                                    "USD": all_balances.get("USD", 0)
                                }
                            }
                            
                            if update_json_file("balance.json", balance_data):
                                logging.info(f"✅ balance.json обновлен: {balance_data}")
                            else:
                                logging.error("❌ Ошибка при записи balance.json")
                        except (ValueError, TypeError) as e:
                            logging.error(f"❌ Ошибка при преобразовании данных баланса: {e}")
                    else:
                        logging.warning("⚠️ API вернул нулевой баланс, обновление отменено")
                else:
                    logging.error("❌ Некорректный формат ответа API")
            
            # Обновляем позиции каждые 60 секунд
            if current_time - self.last_positions_update >= 60:
                logging.info("🔄 Начинаем обновление позиций...")
                positions = await self.api.get_positions(category="linear", symbol=SYMBOL)
                if positions and positions.get("result", {}).get("list"):
                    self.positions = positions["result"]["list"][0]
                    self.last_positions_update = current_time
                    logging.info(f"📊 Получены позиции: {self.positions}")
                    
                    # Сохраняем в JSON
                    if update_json_file("positions.json", {"positions": self.positions}):
                        logging.info("✅ positions.json обновлен")
                    else:
                        logging.error("❌ Ошибка при сохранении positions.json")
            
            # Обновляем PnL каждые 300 секунд
            if current_time - self.last_pnl_update >= 300:
                logging.info("🔄 Начинаем обновление PnL...")
                pnl = await self.api.get_closed_pnl(category="linear", symbol=SYMBOL)
                if pnl and pnl.get("result", {}).get("list"):
                    self.pnl_data["trades"] = pnl["result"]["list"]
                    self.pnl_data["daily_pnl"] = sum(float(trade["closedPnl"]) for trade in pnl["result"]["list"])
                    self.last_pnl_update = current_time
                    logging.info(f"📊 Обновлен PnL: {self.pnl_data}")
                    
                    # Сохраняем в JSON
                    if update_json_file("pnl.json", self.pnl_data):
                        logging.info("✅ pnl.json обновлен")
                    else:
                        logging.error("❌ Ошибка при сохранении pnl.json")
            
            # Обновляем цену каждые 5 секунд
            logging.info("🔄 Начинаем обновление цены...")
            price = await self.api.get_latest_price()
            if price:
                price_data = {"price": price, "last_updated": datetime.now().isoformat()}
                if update_json_file("price.json", price_data):
                    logging.info(f"✅ price.json обновлен: {price}")
                else:
                    logging.error("❌ Ошибка при сохранении price.json")
            
            # Обновляем сигналы
            if self.signals["refresh_data"]:
                logging.info("🔄 Начинаем обновление сигналов...")
                self.signals["refresh_data"] = False
                self.signals["last_updated"] = datetime.now().isoformat()
                if update_json_file("signals.json", self.signals):
                    logging.info("✅ signals.json обновлен")
                else:
                    logging.error("❌ Ошибка при обновлении signals.json")
                    
        except Exception as e:
            logging.error(f"❌ Ошибка при обновлении данных: {str(e)}")
            logging.error(f"❌ Тип ошибки: {type(e).__name__}")
            import traceback
            logging.error(f"❌ Трейсбек: {traceback.format_exc()}")

    async def get_atr(self, period=14):
        """Рассчитывает ATR (Average True Range)"""
        try:
            candles = await self.api.get_kline(category="linear", symbol=SYMBOL, interval=5, limit=period)
            
            if "result" not in candles or "list" not in candles["result"]:
                logging.error("Некорректный формат данных свечей")
                return None

            highs = np.array([float(candle[2]) for candle in candles["result"]["list"]])
            lows = np.array([float(candle[3]) for candle in candles["result"]["list"]])
            closes = np.array([float(candle[4]) for candle in candles["result"]["list"]])

            if len(closes) < period:
                logging.error(f"Недостаточно данных для ATR. Получено {len(closes)} свечей, требуется {period}")
                return None

            atr = talib.ATR(highs, lows, closes, timeperiod=period)[-1]
            return atr
        except Exception as e:
            logging.error(f"Ошибка при расчёте ATR: {e}")
            return None

    async def reset_daily_stats(self):
        today = datetime.now().date()
        if self.last_daily_reset != today:
            self.daily_pnl = 0
            self.daily_trade_count = 0
            self.last_daily_reset = today
            await send_telegram_message("📊 Дневная статистика сброшена.")

    async def check_pnl(self):
        """
        Проверяет PNL по закрытым позициям
        """
        try:
            # Сбрасываем статистику если начался новый день
            await self.reset_daily_stats()
            
            # Получаем время последней проверки
            current_time = time.time()
            if self.last_trade_time is None:
                self.last_trade_time = current_time - 300  # Начинаем с последних 5 минут
                return
            
            # Получаем закрытые ордера за период с последней проверки
            closed_orders = await self.api.get_closed_pnl(
                category="linear",
                symbol=SYMBOL,
                startTime=int(self.last_trade_time * 1000),
                endTime=int(current_time * 1000),
                limit=50
            )

            if not closed_orders or "result" not in closed_orders or "list" not in closed_orders["result"]:
                logging.info("❌ Нет закрытых позиций, PnL отсутствует.")
                return True

            trades = closed_orders["result"]["list"]
            if not trades:
                logging.info("📉 Нет закрытых сделок, PnL пока нулевой.")
                return True

            # Обрабатываем каждый закрытый ордер
            for order in trades:
                order_id = order.get("orderId")
                if not order_id or order_id in self.processed_orders:
                    continue

                order_details = await self.api.get_order_list(
                    category="linear",
                    symbol=SYMBOL,
                    orderId=order_id
                )

                if not order_details or "result" not in order_details or "list" not in order_details["result"]:
                    logging.info(f"⚠️ Нет деталей для ордера {order_id}")
                    continue

                order_info = order_details["result"]["list"][0]
                side = order_info.get("side")
                qty = float(order_info.get("qty", 0))
                entry_price = float(order_info.get("price", 0))
                exit_price = float(order_info.get("closePrice", 0))
                pnl = float(order.get("closedPnl", 0))

                if side and qty > 0 and entry_price > 0 and exit_price > 0:
                    # Обновляем статистику
                    self.daily_pnl += pnl
                    if pnl < 0:
                        self.consecutive_losses += 1
                    else:
                        self.consecutive_losses = 0

                    # Отправляем уведомление
                    message = f"""
                    🎯 Закрыта сделка:
                    Сторона: {side}
                    Размер: {qty} {SYMBOL}
                    Вход: {entry_price:.2f}
                    Выход: {exit_price:.2f}
                    PNL: {pnl:.2f} USDT
                    📊 Дневной PNL: {self.daily_pnl:.2f} USDT
                    """
                    await send_telegram_message(message)

                    # Проверяем лимиты
                    if self.daily_pnl <= -100:  # Максимальный дневной убыток 100 USDT
                        error_msg = "⚠️ Достигнут лимит дневного убытка. Торговля остановлена."
                        logging.warning(error_msg)
                        await send_telegram_message(error_msg)
                        return False

                    if self.consecutive_losses >= 3:  # Максимум 3 последовательных убытка
                        error_msg = "⚠️ Достигнут лимит последовательных убытков. Торговля остановлена."
                        logging.warning(error_msg)
                        await send_telegram_message(error_msg)
                        return False

                    # Добавляем ордер в обработанные
                    self.processed_orders.add(order_id)
                    if len(self.processed_orders) > 50:  # Ограничиваем размер множества
                        self.processed_orders = set(list(self.processed_orders)[-50:])

            # Обновляем время последней проверки
            self.last_trade_time = current_time
            return True

        except Exception as e:
            error_msg = f"Ошибка при проверке PNL: {e}"
            logging.error(error_msg)
            await send_telegram_message(f"⚠️ {error_msg}")
            return False

    async def scalping_strategy(self):
        """
        Улучшенная стратегия скальпинга с учетом стакана, объемов и технических индикаторов
        """
        try:
            # Проверяем лимит сделок на день
            if self.daily_trade_count >= self.max_daily_trades:
                logging.info("Достигнут дневной лимит сделок")
                return

            # Проверяем, не слишком ли рано для нового ордера
            current_time = time.time()
            if current_time - self.last_order_time < self.min_order_interval:
                logging.info("Ждём следующего интервала для входа.")
                return

            # Получаем текущую цену
            price = await self.get_latest_price()
            if price is None:
                return

            # Если это первый запуск, сохраняем цену и выходим
            if self.last_checked_price is None:
                self.last_checked_price = price
                return

            # Проверяем наличие активной позиции
            if self.active_position:
                # Обновляем трейлинг-стоп для открытой позиции
                await self.update_trailing_stop(self.current_position, price)
                logging.info("Уже есть активная позиция, пропускаем вход")
                return

            # Проверяем волатильность рынка
            atr = await self.get_atr()
            if atr:
                # Если волатильность слишком высокая, увеличиваем интервал между ордерами
                if atr > price * 0.01:  # Если ATR > 1% от цены
                    self.min_order_interval = 600  # Увеличиваем до 10 минут
                    logging.info(f"Высокая волатильность (ATR: {atr:.2f}), увеличиваем интервал между ордерами")
            else:
                self.min_order_interval = 300  # Возвращаем к 5 минутам

            # Анализируем объемы и стакан
            volume_direction = await self.analyze_volume()
            if volume_direction is None:
                logging.info("Нет четкого направления по объемам")
                return

            # Получаем уровни поддержки и сопротивления
            long_term_levels = await self.analyze_long_term_levels()
            if not long_term_levels or "4H" not in long_term_levels:
                logging.warning("Не удалось получить долгосрочные уровни")
                return

            # Проверяем тренд и условия входа
            trend_confirmed = await self.check_trend(volume_direction)
            if trend_confirmed:
                # Проверяем долгосрочные уровни
                if price < long_term_levels["4H"]["support"]:
                    logging.info("Цена у глобальной поддержки. Вход отменён.")
                    return

                # Рассчитываем стоп-лосс
                stop_loss = await self.calculate_stop_loss(volume_direction, price, atr)
                if not stop_loss:
                    return
                
                # Проверяем минимальное расстояние стоп-лосса
                if not await self.check_stop_loss_distance(price, stop_loss):
                    return

                # Рассчитываем тейк-профиты
                if volume_direction == "Buy":
                    take_profit_1 = price * (1 + TAKE_PROFIT_1 / 100)
                    take_profit_2 = price * (1 + TAKE_PROFIT_2 / 100)
                    take_profit_3 = price * (1 + TAKE_PROFIT_3 / 100)
                else:
                    take_profit_1 = price * (1 - TAKE_PROFIT_1 / 100)
                    take_profit_2 = price * (1 - TAKE_PROFIT_2 / 100)
                    take_profit_3 = price * (1 - TAKE_PROFIT_3 / 100)

                # Проверяем достаточность маржи
                try:
                    account_info = await self.api.get_wallet_balance(accountType="UNIFIED")
                    if account_info and "result" in account_info and "list" in account_info["result"]:
                        balance_info = account_info["result"]["list"][0]
                        if "coin" in balance_info:
                            for item in balance_info["coin"]:
                                if item["coin"].upper() == "USDT":
                                    available_balance = float(item["walletBalance"])
                                    # Рассчитываем размер позиции с учетом плеча
                                    qty = await self.calculate_position_size(stop_loss, price)
                                    if qty is None:
                                        return
                                        
                                    required_margin = (price * qty) / LEVERAGE
                                    if available_balance < required_margin:
                                        logging.warning(f"Недостаточно маржи: доступно {available_balance:.2f} USDT, требуется {required_margin:.2f} USDT")
                                        return
                                    break
                except Exception as e:
                    logging.error(f"Ошибка при проверке маржи: {e}")
                    return

                # Проверяем ликвидность
                if not await self.check_liquidity(volume_direction):
                    logging.info("Недостаточная ликвидность для входа")
                    return

                logging.info(f"Планируем {volume_direction} ордер: qty={qty}, entry={price}, SL={stop_loss}")
                logging.info(f"Цели: TP1={take_profit_1}, TP2={take_profit_2}, TP3={take_profit_3}")

                # Размещаем ордер
                if await self.place_order(volume_direction, qty, stop_loss, take_profit_1, take_profit_2, take_profit_3):
                    self.active_position = True
                    self.last_order_time = current_time
                    self.daily_trade_count += 1  # Увеличиваем счетчик сделок
                    self.current_position = {
                        "side": volume_direction,
                        "entry_price": price,
                        "stop_loss": stop_loss,
                        "take_profit_1": take_profit_1,
                        "take_profit_2": take_profit_2,
                        "take_profit_3": take_profit_3,
                        "size": qty
                    }
                    await send_telegram_message(f"""
                    🎯 Открыта позиция:
                    Сторона: {volume_direction}
                    Размер: {qty} {SYMBOL}
                    Вход: {price:.2f}
                    SL: {stop_loss:.2f}
                    TP1: {take_profit_1:.2f}
                    TP2: {take_profit_2:.2f}
                    TP3: {take_profit_3:.2f}
                    📊 ATR: {atr:.2f if atr else 'N/A'}
                    📈 Сделка {self.daily_trade_count} из {self.max_daily_trades}
                    """)
            else:
                logging.info("Нет подтверждения тренда для входа")

            # Обновляем предыдущую цену
            self.last_checked_price = price
        except Exception as e:
            error_msg = f"Ошибка в scalping_strategy: {e}"
            logging.error(error_msg)
            await send_telegram_message(f"⚠ {error_msg}")

    async def update_trailing_stop(self, position, current_price):
        """
        Обновляет трейлинг-стоп для открытой позиции с учетом минимального шага
        """
        try:
            side = position["side"]
            current_stop = position["stop_loss"]

            if side == "Buy":
                new_stop = current_price * (1 - TRAILING_STOP / 100)
                if new_stop <= current_stop or ((new_stop - current_stop) / current_stop * 100) < 0.1:
                    return False
                await self.api.set_trading_stop(category="linear", symbol=SYMBOL, side="Buy", stopLoss=str(new_stop))
                position["stop_loss"] = new_stop
                return True

            else:
                new_stop = current_price * (1 + TRAILING_STOP / 100)
                if new_stop >= current_stop or ((current_stop - new_stop) / current_stop * 100) < 0.1:
                    return False
                await self.api.set_trading_stop(category="linear", symbol=SYMBOL, side="Sell", stopLoss=str(new_stop))
                position["stop_loss"] = new_stop
                return True

        except Exception as e:
            logging.error(f"Ошибка при обновлении трейлинг-стопа: {e}")
            return False

    async def check_trend(self, side):
        """
        Улучшенная проверка тренда с использованием нескольких индикаторов
        """
        try:
            # Получаем свечи
            candles = await self.api.get_kline(category="linear", symbol=SYMBOL, interval="5", limit=50)
            if not candles or "result" not in candles or "list" not in candles["result"]:
                logging.error("Неверный формат данных свечей")
                return False

            # Получаем цены закрытия - Bybit API возвращает свечи в формате списка, где индекс 4 - это цена закрытия
            closes = [float(candle[4]) for candle in candles["result"]["list"]]
            if len(closes) < 50:
                logging.warning("Недостаточно данных для анализа")
                return False

            # Рассчитываем индикаторы
            sma50 = talib.SMA(np.array(closes), timeperiod=50)[-1]
            rsi = talib.RSI(np.array(closes), timeperiod=RSI_PERIOD)[-1]
            vwap = await self.calculate_vwap()

            if not vwap:
                return False

            current_price = closes[-1]
            
            # Получаем сигналы один раз и сохраняем их
            orderbook_signal = await self.analyze_orderbook(side)
            volume_signal = await self.analyze_volume()

            # Проверяем условия для входа
            if side == "Buy":
                # Условия для покупки
                buy_condition = (
                    current_price > sma50 and
                    current_price > vwap and
                    rsi < RSI_OVERSOLD and
                    orderbook_signal and
                    volume_signal == "Buy"
                )
                return buy_condition
            else:
                # Условия для продажи
                sell_condition = (
                    current_price < sma50 and
                    current_price < vwap and
                    rsi > RSI_OVERBOUGHT and
                    orderbook_signal and
                    volume_signal == "Sell"
                )
                return sell_condition
        except Exception as e:
            logging.error(f"Ошибка в check_trend: {e}")
            return False

    async def check_liquidity(self, side):
        """
        Проверяет ликвидность в стакане для заданной стороны
        """
        try:
            # Получаем стакан
            orderbook = await self.api.get_orderbook(category="linear", symbol=SYMBOL)
            if not orderbook or "result" not in orderbook:
                logging.error("Неверный формат данных стакана")
                return False

            # Суммируем объемы в первых 5 уровнях
            bids = sum([float(order["size"]) for order in orderbook["result"]["b"][:5]])
            asks = sum([float(order["size"]) for order in orderbook["result"]["a"][:5]])

            # Проверяем соотношение объемов
            if side == "Buy":
                # Для покупки: объемы на покупку должны быть в 2 раза больше
                return bids > asks * MIN_VOLUME_RATIO
            else:
                # Для продажи: объемы на продажу должны быть в 2 раза больше
                return asks > bids * MIN_VOLUME_RATIO
        except Exception as e:
            logging.error(f"Ошибка при проверке ликвидности: {e}")
            return False

    async def calculate_trade_size(self, stop_loss_price, entry_price):
        try:
            # 🔥 Получаем баланс USDT
            account_info = await self.api.get_wallet_balance(accountType="UNIFIED")
            logging.info(f"API ответ get_wallet_balance: {account_info}")

            # Проверяем баланс USDT
            if account_info and "result" in account_info and "list" in account_info["result"]:
                balance_info = account_info["result"]["list"][0]  # Берём первый объект в списке
                if "coin" in balance_info:
                    for item in balance_info["coin"]:
                        if item["coin"].upper() == "USDT":
                            balance = float(item["walletBalance"])
                            logging.info(f"Обнаружен баланс USDT: {balance} USDT")
                            break
                    else:
                        logging.error("USDT не найден в списке активов.")
                        return None
                else:
                    logging.error("Ошибка: ключ 'coin' отсутствует в ответе API.")
                    return None
            else:
                logging.error("Ошибка: некорректный ответ API при получении баланса.")
                return None

            # 🔢 Рассчёт риска и размера позиции в USDT
            risk_amount = balance * (RISK_PERCENTAGE / 100)  # Какой % от баланса используем в сделке
            stop_loss_distance = abs(entry_price - stop_loss_price)  # Дистанция стоп-лосса

            if stop_loss_distance == 0:
                logging.error("Ошибка: стоп-лосс равен нулю.")
                return None

            trade_size = risk_amount / stop_loss_distance  # Расчёт базового объёма сделки
            trade_size = round(trade_size, 3)  # ✅ Округление до 0.001 BTC

            return trade_size if trade_size >= 0.001 else None  # Проверка на минимальный размер
        except Exception as e:
            logging.error(f"Ошибка при расчёте размера сделки: {e}")
            return None

    async def check_stop_loss_distance(self, entry_price, stop_loss_price):
        """
        Проверяет, соответствует ли расстояние до стоп-лосса минимальным требованиям биржи
        """
        try:
            # Получаем минимальное расстояние для текущей пары
            min_distance = MIN_STOP_DISTANCES.get(SYMBOL, 0.1)
            
            # Рассчитываем фактическое расстояние в процентах
            distance_percent = abs(entry_price - stop_loss_price) / entry_price * 100
            
            if distance_percent < min_distance:
                error_msg = f"Расстояние до стоп-лосса ({distance_percent:.2f}%) меньше минимально допустимого ({min_distance}%)"
                logging.warning(error_msg)
                await send_telegram_message(f"⚠️ {error_msg}")
                return False
                
            return True
            
        except Exception as e:
            logging.error(f"Ошибка при проверке расстояния стоп-лосса: {e}")
            return False

    async def calculate_stop_loss(self, side, entry_price, atr=None):
        """
        Рассчитывает уровень стоп-лосса с учетом минимального расстояния
        """
        try:
            # Получаем минимальное расстояние для текущей пары
            min_distance = MIN_STOP_DISTANCES.get(SYMBOL, 0.1)
            
            # Рассчитываем стоп-лосс на основе ATR или процента
            if atr:
                distance = atr * ATR_MULTIPLIER
                # Проверяем, что расстояние не меньше минимального
                min_distance_price = entry_price * (min_distance / 100)
                distance = max(distance, min_distance_price)
            else:
                # Используем процентный стоп-лосс
                distance = entry_price * (max(STOP_LOSS_PERCENTAGE, min_distance) / 100)
            
            # Рассчитываем стоп-лосс в зависимости от стороны
            stop_loss = entry_price - distance if side == "Buy" else entry_price + distance
            
            logging.info(f"Рассчитан стоп-лосс: {stop_loss:.2f} (расстояние: {(distance/entry_price*100):.2f}%)")
            return stop_loss
        except Exception as e:
            logging.error(f"Ошибка при расчете стоп-лосса: {e}")
            return None

    async def analyze_orderbook(self, side):
        """
        Анализирует стакан заявок для определения дисбаланса
        """
        try:
            orderbook = await self.api.get_orderbook(category="linear", symbol=SYMBOL, limit=ORDERBOOK_DEPTH)
            
            if "result" in orderbook:
                bids = orderbook["result"]["b"]
                asks = orderbook["result"]["a"]
                
                # Считаем общий объем на покупку и продажу
                total_bids = sum(float(order["size"]) for order in bids)
                total_asks = sum(float(order["size"]) for order in asks)
                
                # Проверяем наличие крупных стен
                large_bids = sum(float(order["size"]) for order in bids if float(order["size"]) > VOLUME_THRESHOLD)
                large_asks = sum(float(order["size"]) for order in asks if float(order["size"]) > VOLUME_THRESHOLD)
                
                if side == "Buy":
                    return (total_bids > total_asks * MIN_VOLUME_RATIO and 
                           large_bids > large_asks * 1.5)
                else:
                    return (total_asks > total_bids * MIN_VOLUME_RATIO and 
                           large_asks > large_bids * 1.5)
            
            return False
        except Exception as e:
            logging.error(f"Ошибка при анализе стакана: {e}")
            return False

    async def analyze_volume(self):
        """Анализирует объемы торгов для определения импульса"""
        try:
            trades = await self.api.get_executions(category="linear", symbol=SYMBOL, limit=50)

            if "result" not in trades or "list" not in trades["result"]:
                logging.error("Ошибка: API Bybit вернул некорректные данные о сделках")
                return None

            buy_volume = 0
            sell_volume = 0

            for trade in trades["result"]["list"]:
                try:
                    size = float(trade["execQty"])  # Размер сделки
                    side = trade["side"]  # Buy / Sell

                    if side == "Buy":
                        buy_volume += size
                    elif side == "Sell":
                        sell_volume += size
                except KeyError as e:
                    logging.error(f"Ошибка при обработке сделки: {e}")
                    continue

            if buy_volume > sell_volume * MIN_VOLUME_RATIO:
                return "Buy"
            elif sell_volume > buy_volume * MIN_VOLUME_RATIO:
                return "Sell"

            return None
        except Exception as e:
            logging.error(f"Ошибка при анализе объемов: {e}")
            return None

    async def calculate_vwap(self):
        """
        Рассчитывает VWAP (Volume Weighted Average Price)
        """
        try:
            candles = await self.api.get_kline(category="linear", symbol=SYMBOL, interval=1, limit=VWAP_PERIOD)
            
            if "result" in candles and "list" in candles["result"]:
                total_volume = 0
                total_price_volume = 0
                
                for candle in candles["result"]["list"]:
                    volume = float(candle["volume"])
                    price = float(candle["close"])
                    total_volume += volume
                    total_price_volume += price * volume
                
                return total_price_volume / total_volume if total_volume > 0 else None
            return None
        except Exception as e:
            logging.error(f"Ошибка при расчете VWAP: {e}")
            return None

    async def calculate_position_size(self, account_balance, current_price, atr):
        """
        Рассчитывает размер позиции на основе риска 1% от баланса
        """
        try:
            if not account_balance or not current_price or not atr:
                return None

            # Рассчитываем максимальный риск в долларах (1% от баланса)
            risk_amount = account_balance * 0.01
            
            # Рассчитываем размер позиции на основе ATR
            # Используем 0.5 ATR как стоп-лосс
            stop_distance = atr * 0.5
            
            # Рассчитываем размер позиции
            position_size = risk_amount / stop_distance
            
            # Округляем размер до 2 знаков после запятой
            position_size = round(position_size, 2)
            
            # Проверяем минимальный размер позиции
            min_size = 0.01
            if position_size < min_size:
                position_size = min_size
                
            return position_size

        except Exception as e:
            logging.error(f"Ошибка при расчете размера позиции: {e}")
            return None

    TIMEFRAME_MAPPING = {
        "1D": "D",
        "1W": "W",
        "1M": "M"
    }

    async def fetch_historical_data(self, timeframe="1D", limit=200):
        """
        Запрашивает исторические свечи по заданному таймфрейму.
        Возвращает список цен закрытия (close).
        """
        try:
            interval = self.TIMEFRAME_MAPPING.get(timeframe, "D")  # По умолчанию "D" (день)
            response = await self.api.get_kline(category="linear", symbol=SYMBOL, interval=interval, limit=limit)
            
            if "result" in response and "list" in response["result"]:
                closes = [float(candle[4]) for candle in response["result"]["list"]]
                if not closes:
                    logging.warning(f"⚠️ API не вернуло свечи для {timeframe}")
                return closes

            logging.warning(f"⚠️ Нет данных для {timeframe}")
            return []
        except Exception as e:
            logging.error(f"❌ Ошибка получения данных {timeframe}: {e}")
            return []

    async def get_support_resistance(self, closes):
        """
        Определяет ключевые уровни поддержки и сопротивления с фильтрацией выбросов.
        """
        if not closes or len(closes) < 10:
            return None, None

        # Фильтрация выбросов: убираем 5% самых низких и 5% самых высоких цен
        lower_bound = np.percentile(closes, 5)
        upper_bound = np.percentile(closes, 95)

        # Получаем поддержку и сопротивление внутри этого диапазона
        support = min([price for price in closes if price >= lower_bound])
        resistance = max([price for price in closes if price <= upper_bound])

        return support, resistance

    async def market_structure_analysis(self, closes):
        """
        Анализирует структуру рынка (Higher Highs, Lower Lows).
        """
        if len(closes) < 10:
            return None

        highs = [closes[i] for i in range(1, len(closes) - 1) if closes[i] > closes[i - 1] and closes[i] > closes[i + 1]]
        lows = [closes[i] for i in range(1, len(closes) - 1) if closes[i] < closes[i - 1] and closes[i] < closes[i + 1]]

        last_high = highs[-1] if highs else None
        last_low = lows[-1] if lows else None

        # Определяем тренд на основе последних 10 свечей
        trend = "Боковик"
        if last_high and last_low:
            if last_high > last_low * 1.02:
                trend = "Бычий тренд 🟢"
            elif last_low < last_high * 0.98:
                trend = "Медвежий тренд 🔴"

        logging.info(f"📊 Структура рынка: {trend}, High: {last_high}, Low: {last_low}")
        return trend, last_high, last_low

    async def perform_long_term_analysis(self):
        """
        Выполняет долгосрочный анализ рынка.
        """
        # 📊 Получаем исторические данные
        closes_1D = await self.fetch_historical_data("1D", 200)
        closes_1W = await self.fetch_historical_data("1W", 100)
        closes_1M = await self.fetch_historical_data("1M", 50)

        # 🏆 Определяем уровни поддержки и сопротивления
        support_1D, resistance_1D = await self.get_support_resistance(closes_1D)
        support_1W, resistance_1W = await self.get_support_resistance(closes_1W)
        support_1M, resistance_1M = await self.get_support_resistance(closes_1M)

        # 🔥 Анализируем тренды
        trend_1D = await self.analyze_trend(closes_1D)
        trend_1W = await self.analyze_trend(closes_1W)
        trend_1M = await self.analyze_trend(closes_1M)

        # 📈 Анализируем объемы
        volume_trend = await self.analyze_volume()

        # 📉 Определяем рыночную структуру
        market_trend_1D, last_high_1D, last_low_1D = await self.market_structure_analysis(closes_1D)

        # ✅ Логируем результаты
        logging.info(f"""
        📊 Долгосрочный анализ:
        - 1D: Поддержка {support_1D}, Сопротивление {resistance_1D}, Тренд: {trend_1D}, Структура: {market_trend_1D}
        - 1W: Поддержка {support_1W}, Сопротивление {resistance_1W}, Тренд: {trend_1W}
        - 1M: Поддержка {support_1M}, Сопротивление {resistance_1M}, Тренд: {trend_1M}
        - 🔥 Объёмный тренд: {volume_trend}
        """)

        return {
            "support_resistance": {
                "1D": (support_1D, resistance_1D),
                "1W": (support_1W, resistance_1W),
                "1M": (support_1M, resistance_1M)
            },
            "trends": {
                "1D": trend_1D,
                "1W": trend_1W,
                "1M": trend_1M
            },
            "volume_trend": volume_trend,
            "market_structure": {
                "1D": (last_high_1D, last_low_1D, market_trend_1D)
            }
        }

    async def analyze_trend(self, closes):
        """
        Анализирует тренд с использованием SMA, EMA и RSI.
        """
        if len(closes) < 50:
            logging.warning("⚠️ Недостаточно данных для анализа тренда.")
            return None

        sma_50 = talib.SMA(np.array(closes), timeperiod=50)[-1]
        sma_200 = talib.SMA(np.array(closes), timeperiod=200)[-1]
        ema_21 = talib.EMA(np.array(closes), timeperiod=21)[-1]
        rsi = talib.RSI(np.array(closes), timeperiod=14)[-1]
        
        trend = "Нейтральный"
        if sma_50 > sma_200 and ema_21 > sma_50:
            trend = "Бычий 🟢"
        elif sma_50 < sma_200 and ema_21 < sma_50:
            trend = "Медвежий 🔴"
        
        logging.info(f"📈 Тренд: {trend} (SMA50: {sma_50}, SMA200: {sma_200}, EMA21: {ema_21}, RSI: {rsi})")
        return trend

    async def analyze_long_term_levels(self):
        """
        Анализирует долгосрочные уровни поддержки и сопротивления
        """
        try:
            # Запрашиваем свечи с разными интервалами
            candles_1H = await self.api.get_kline(category="linear", symbol=SYMBOL, interval="60", limit=100)
            candles_4H = await self.api.get_kline(category="linear", symbol=SYMBOL, interval="240", limit=100)
            candles_1D = await self.api.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=100)

            # Функция для извлечения цен закрытия из ответа API
            def extract_closes(candles):
                if candles and "result" in candles and "list" in candles["result"] and candles["result"]["list"]:
                    return [float(candle[4]) for candle in reversed(candles["result"]["list"])]
                else:
                    return []

            # Извлекаем данные
            closes_1H = extract_closes(candles_1H)
            closes_4H = extract_closes(candles_4H)
            closes_1D = extract_closes(candles_1D)

            # Логируем последние свечи для отладки
            if closes_1H:
                logging.info(f"🧐 Данные свечей 1H: {closes_1H[-5:]}")
            if closes_4H:
                logging.info(f"🧐 Данные свечей 4H: {closes_4H[-5:]}")
            if closes_1D:
                logging.info(f"🧐 Данные свечей 1D: {closes_1D[-5:]}")

            # Проверяем наличие данных
            if not closes_1H:
                logging.warning("⚠️ Недостаточно данных для анализа уровней на 1H!")
            if not closes_4H:
                logging.warning("⚠️ Недостаточно данных для анализа уровней на 4H!")
            if not closes_1D:
                logging.warning("⚠️ Недостаточно данных для анализа уровней на 1D!")

            # Определяем уровни поддержки и сопротивления для каждого таймфрейма
            levels = {}

            if closes_1H:
                support_1H, resistance_1H = await self.detect_support_resistance(closes_1H)
                if support_1H and resistance_1H:
                    levels["1H"] = {"support": support_1H, "resistance": resistance_1H}
                    logging.info(f"🔵 1H: Support: {support_1H:.2f}, Resistance: {resistance_1H:.2f}")

            if closes_4H:
                support_4H, resistance_4H = await self.detect_support_resistance(closes_4H)
                if support_4H and resistance_4H:
                    levels["4H"] = {"support": support_4H, "resistance": resistance_4H}
                    logging.info(f"🟢 4H: Support: {support_4H:.2f}, Resistance: {resistance_4H:.2f}")

            if closes_1D:
                support_1D, resistance_1D = await self.detect_support_resistance(closes_1D)
                if support_1D and resistance_1D:
                    levels["1D"] = {"support": support_1D, "resistance": resistance_1D}
                    logging.info(f"🔴 1D: Support: {support_1D:.2f}, Resistance: {resistance_1D:.2f}")

            return levels if levels else None

        except Exception as e:
            logging.error(f"❌ Ошибка при анализе долгосрочных уровней: {e}")
            return None

    async def detect_support_resistance(self, closes):
        """
        Находит ближайшие уровни поддержки и сопротивления.
        """
        try:
            if not closes or len(closes) < 10:
                return None, None
            
            high = max(closes)
            low = min(closes)
            return low, high
        except Exception as e:
            logging.error(f"Ошибка при определении уровней поддержки/сопротивления: {e}")
            return None, None

    async def check_positions(self):
        """
        Проверяет текущие позиции и обновляет флаг active_position
        """
        try:
            positions = await self.api.get_positions()
            
            # Проверяем наличие ошибок в ответе API
            if not positions or "result" not in positions or "list" not in positions["result"]:
                logging.info("📉 Нет открытых позиций.")
                self.active_position = False
                self.current_position = None
                return True

            position_list = positions["result"]["list"]
            if not position_list:
                logging.info("📉 Нет активных позиций.")
                self.active_position = False
                self.current_position = None
                return True

            # Проверяем каждую позицию
            for position in position_list:
                size = float(position.get("size", 0))
                side = position.get("side", "")
                
                if size == 0:
                    continue

                # Проверяем, что это наша позиция
                if (self.current_position and 
                    self.current_position.get("side") == side and
                    abs(size - self.current_position.get("size", 0)) < 0.001):
                    
                    # Обновляем информацию о позиции
                    self.current_position.update({
                        "size": size,
                        "leverage": float(position.get("leverage", 0)),
                        "unrealized_pnl": float(position.get("unrealizedPnl", 0)),
                        "mark_price": float(position.get("markPrice", 0))
                    })
                    
                    self.active_position = True
                    logging.info(f"🔵 Активная позиция обновлена: {self.current_position}")
                    return True

            # Если мы дошли до этой точки, значит активной позиции нет
            self.active_position = False
            self.current_position = None
            logging.info("📉 Нет активных позиций.")
            return True

        except Exception as e:
            error_msg = f"Ошибка при проверке позиций: {e}"
            logging.error(error_msg)
            await send_telegram_message(f"⚠ {error_msg}")
            self.active_position = False
            self.current_position = None
            return False

    async def position_monitor(self):
        """
        Мониторит открытые позиции и их статус
        """
        logging.info("Запущен мониторинг позиций")
        
        try:
            while not self.stop_monitor:
                # Получаем текущие позиции
                positions = await self.api.get_positions(category="linear", symbol=SYMBOL)
                
                if positions and "result" in positions and "list" in positions["result"]:
                    position_list = positions["result"]["list"]
                    
                    if position_list and position_list[0]["size"] != "0":
                        position = position_list[0]
                        side = position["side"]
                        size = float(position["size"])
                        entry_price = float(position["entryPrice"])
                        position_value = float(position["positionValue"])
                        leverage = float(position["leverage"])
                        unrealized_pnl = float(position["unrealisedPnl"])
                        
                        # Получаем текущую цену
                        current_price_data = await self.api.get_latest_price()
                        if current_price_data:
                            current_price = float(current_price_data)
                            
                            # Рассчитываем процент изменения
                            if side == "Buy":
                                pnl_percent = (current_price - entry_price) / entry_price * 100 * leverage
                            else:
                                pnl_percent = (entry_price - current_price) / entry_price * 100 * leverage
                            
                            # Логируем информацию о позиции
                            logging.info(
                                f"Открыта позиция {SYMBOL}: "
                                f"Сторона: {side}, "
                                f"Размер: {size}, "
                                f"Цена входа: {entry_price}, "
                                f"Текущая цена: {current_price}, "
                                f"P&L: {unrealized_pnl:.2f} USD ({pnl_percent:.2f}%)"
                            )
            
                # Ждем перед следующей проверкой
                await asyncio.sleep(60)  # Проверяем каждую минуту
                
        except asyncio.CancelledError:
            logging.info("Мониторинг позиций остановлен")
        except Exception as e:
            logging.error(f"Ошибка в мониторинге позиций: {e}")
            await send_telegram_message(f"⚠️ Ошибка в мониторинге позиций: {e}")

    async def monitor_positions(self):
        """
        Мониторит открытые позиции и управляет трейлинг-стопом и частичным закрытием
        """
        try:
            # Получаем текущие индикаторы
            indicators = await self.calculate_indicators()
            if not indicators:
                return

            current_price = float(indicators['last_close'])
            atr = float(indicators['ATR'][-1])

            # Получаем открытые позиции
            positions = read_json_file('positions.json')
            if not positions:
                return

            # Если positions это словарь, преобразуем в список
            if isinstance(positions, dict):
                positions = [positions]

            for position in positions:
                try:
                    # Проверяем частичное закрытие
                    close_size = await self.partial_close_position(position, current_price, atr)
                    if close_size:
                        # Закрываем часть позиции
                        order = await self.api.place_order(
                            category="linear",
                            symbol=SYMBOL,
                            side="Sell" if position['side'] == "Buy" else "Buy",
                            orderType="Market",
                            qty=str(close_size)
                        )

                        if "result" in order:
                            # Обновляем размер позиции
                            position['size'] = float(position['size']) - close_size
                            if position['size'] <= 0:
                                positions.remove(position)
                            else:
                                # Обновляем тейк-профиты для оставшейся части
                                sl_tp = await self.calculate_sl_tp(position['side'], current_price, atr)
                                if sl_tp:
                                    position['stopLoss'] = sl_tp['stop_loss']
                                    position['takeProfit1'] = sl_tp['take_profit_1']
                                    position['takeProfit2'] = sl_tp['take_profit_2']
                                    position['takeProfit3'] = sl_tp['take_profit_3']

                                # Обновляем positions.json
                                update_json_file('positions.json', positions)

                                # Отправляем уведомление
                                message = f"🔄 Частично закрыта {position['side']} позиция:\n"
                                message += f"Закрыто: {close_size}\n"
                                message += f"Осталось: {position['size']}\n"
                                message += f"Цена: {current_price}"
                                await send_telegram_message(message)

                    # Проверяем трейлинг-стоп
                    new_stop = await self.update_trailing_stop(position, current_price, atr)
                    if new_stop and new_stop != position['stopLoss']:
                        # Обновляем стоп-лосс
                        await self.api.set_stop_loss(
                            category="linear",
                            symbol=SYMBOL,
                            stopLoss=str(new_stop)
                        )
                        position['stopLoss'] = new_stop
                        update_json_file('positions.json', positions)

                        # Отправляем уведомление
                        message = f"📈 Обновлен трейлинг-стоп для {position['side']} позиции:\n"
                        message += f"Новый стоп: {new_stop}\n"
                        message += f"Текущая цена: {current_price}"
                        await send_telegram_message(message)

                except Exception as e:
                    logging.error(f"Ошибка при мониторинге позиции: {e}")
                    continue

        except Exception as e:
            logging.error(f"Ошибка при мониторинге позиций: {e}")

    async def calculate_indicators(self):
        """
        Рассчитывает все необходимые индикаторы для стратегии
        """
        try:
            # Получаем свечи
            candles = await self.api.get_kline(category="linear", symbol=SYMBOL, interval="5", limit=100)
            if not candles or "result" not in candles or "list" not in candles["result"]:
                logging.error("Неверный формат данных свечей")
                return None

            # Преобразуем данные в numpy массивы
            closes = np.array([float(candle[4]) for candle in candles["result"]["list"]])
            highs = np.array([float(candle[2]) for candle in candles["result"]["list"]])
            lows = np.array([float(candle[3]) for candle in candles["result"]["list"]])
            volumes = np.array([float(candle[5]) for candle in candles["result"]["list"]])

            if len(closes) < 50:
                logging.warning("Недостаточно данных для анализа")
                return None

            # Рассчитываем индикаторы
            rsi = talib.RSI(closes, timeperiod=14)[-1]
            atr = talib.ATR(highs, lows, closes, timeperiod=14)[-1]
            
            # Рассчитываем VWAP
            typical_price = (highs + lows + closes) / 3
            cumulative_vp = np.cumsum(typical_price * volumes)
            cumulative_volume = np.cumsum(volumes)
            vwap = cumulative_vp[-1] / cumulative_volume[-1]

            # Рассчитываем SMA для определения тренда
            sma20 = talib.SMA(closes, timeperiod=20)[-1]
            sma50 = talib.SMA(closes, timeperiod=50)[-1]

            # Рассчитываем уровни поддержки и сопротивления
            support_resistance = await self.get_support_resistance(closes)

            return {
                'RSI': rsi,
                'ATR': atr,
                'VWAP': vwap,
                'SMA20': sma20,
                'SMA50': sma50,
                'last_close': closes[-1],
                'last_high': highs[-1],
                'last_low': lows[-1],
                'last_volume': volumes[-1],
                'support_resistance': support_resistance
            }

        except Exception as e:
            logging.error(f"Ошибка при расчете индикаторов: {e}")
            return None

    async def check_entry_conditions(self, indicators):
        """
        Проверяет условия для входа в позицию
        """
        try:
            if not indicators:
                return None

            price = indicators['last_close']
            vwap = indicators['VWAP']
            rsi = indicators['RSI']
            atr = indicators['ATR']
            sma20 = indicators['SMA20']
            sma50 = indicators['SMA50']
            volume = await self.analyze_volume()

            if not volume:
                return None

            # Проверяем условия для покупки
            if (price > vwap and 
                rsi < 65 and 
                price > sma20 and 
                sma20 > sma50 and 
                volume == 'Buy'):
                
                # Проверяем, не слишком ли близко к уровню сопротивления
                if indicators['support_resistance']:
                    resistance = indicators['support_resistance'].get('resistance', float('inf'))
                    if price < resistance - atr:
                        return 'Buy'

            # Проверяем условия для продажи
            if (price < vwap and 
                rsi > 35 and 
                price < sma20 and 
                sma20 < sma50 and 
                volume == 'Sell'):
                
                # Проверяем, не слишком ли близко к уровню поддержки
                if indicators['support_resistance']:
                    support = indicators['support_resistance'].get('support', 0)
                    if price > support + atr:
                        return 'Sell'

            return None

        except Exception as e:
            logging.error(f"Ошибка при проверке условий входа: {e}")
            return None

    async def calculate_sl_tp(self, side, price, atr):
        """
        Рассчитывает стоп-лосс и тейк-профиты на основе ATR
        """
        try:
            if side == 'Buy':
                # Стоп-лосс: минимум прошлой свечи или 0.5 ATR
                stop_loss = price - atr * 0.5
                
                # Тейк-профиты: 1 ATR, 2 ATR и 3 ATR
                take_profit_1 = price + atr
                take_profit_2 = price + atr * 2
                take_profit_3 = price + atr * 3
                
                # Проверяем минимальное расстояние для стоп-лосса
                min_distance = MIN_STOP_DISTANCES.get(SYMBOL, 0.1)
                min_stop_distance = price * (min_distance / 100)
                if (price - stop_loss) < min_stop_distance:
                    stop_loss = price - min_stop_distance
                    
            else:  # Sell
                # Стоп-лосс: максимум прошлой свечи или 0.5 ATR
                stop_loss = price + atr * 0.5
                
                # Тейк-профиты: 1 ATR, 2 ATR и 3 ATR
                take_profit_1 = price - atr
                take_profit_2 = price - atr * 2
                take_profit_3 = price - atr * 3
                
                # Проверяем минимальное расстояние для стоп-лосса
                min_distance = MIN_STOP_DISTANCES.get(SYMBOL, 0.1)
                min_stop_distance = price * (min_distance / 100)
                if (stop_loss - price) < min_stop_distance:
                    stop_loss = price + min_stop_distance

            return {
                'stop_loss': stop_loss,
                'take_profit_1': take_profit_1,
                'take_profit_2': take_profit_2,
                'take_profit_3': take_profit_3
            }

        except Exception as e:
            logging.error(f"Ошибка при расчете SL/TP: {e}")
            return None

    async def update_trailing_stop(self, position, current_price, atr):
        """
        Обновляет трейлинг-стоп на основе текущей цены и ATR
        """
        try:
            if not position or not current_price or not atr:
                return None

            # Получаем текущий стоп-лосс
            current_stop = float(position.get('stopLoss', 0))
            entry_price = float(position.get('entryPrice', 0))
            unrealized_pnl = float(position.get('unrealisedPnl', 0))
            
            # Рассчитываем расстояние до стопа в ATR
            if position['side'] == 'Buy':
                distance_to_stop = (current_price - current_stop) / atr
                # Если прибыль больше 0.75 ATR, двигаем стоп в безубыток
                if distance_to_stop > 0.75:
                    new_stop = entry_price
                    if new_stop > current_stop:
                        return new_stop
            else:  # Sell
                distance_to_stop = (current_stop - current_price) / atr
                # Если прибыль больше 0.75 ATR, двигаем стоп в безубыток
                if distance_to_stop > 0.75:
                    new_stop = entry_price
                    if new_stop < current_stop:
                        return new_stop

            return current_stop

        except Exception as e:
            logging.error(f"Ошибка при обновлении трейлинг-стопа: {e}")
            return None

    async def partial_close_position(self, position, current_price, atr):
        """
        Частично закрывает позицию при достижении тейк-профитов
        """
        try:
            if not position or not current_price or not atr:
                return None

            side = position['side']
            size = float(position['size'])
            entry_price = float(position['entryPrice'])
            
            # Рассчитываем расстояние от входа в ATR
            if side == 'Buy':
                distance = (current_price - entry_price) / atr
                # Закрываем 50% при достижении 1 ATR
                if distance >= 1.0 and size > 0.5:
                    close_size = size * 0.5
                    return close_size
                # Закрываем еще 25% при достижении 2 ATR
                elif distance >= 2.0 and size > 0.25:
                    close_size = size * 0.25
                    return close_size
                # Закрываем оставшиеся 25% при достижении 3 ATR
                elif distance >= 3.0 and size > 0:
                    close_size = size
                    return close_size
            else:  # Sell
                distance = (entry_price - current_price) / atr
                # Закрываем 50% при достижении 1 ATR
                if distance >= 1.0 and size > 0.5:
                    close_size = size * 0.5
                    return close_size
                # Закрываем еще 25% при достижении 2 ATR
                elif distance >= 2.0 and size > 0.25:
                    close_size = size * 0.25
                    return close_size
                # Закрываем оставшиеся 25% при достижении 3 ATR
                elif distance >= 3.0 and size > 0:
                    close_size = size
                    return close_size

            return None

        except Exception as e:
            logging.error(f"Ошибка при частичном закрытии позиции: {e}")
            return None

    async def execute_trade(self, side):
        """
        Выполняет торговую операцию с учетом новой стратегии
        """
        try:
            # Получаем текущие индикаторы
            indicators = await self.calculate_indicators()
            if not indicators:
                logging.error("Не удалось получить индикаторы")
                return False

            # Получаем текущую цену и ATR
            current_price = float(indicators['last_close'])
            atr = float(indicators['ATR'][-1])

            # Проверяем условия входа
            entry_conditions = await self.check_entry_conditions(indicators)
            if not entry_conditions or entry_conditions != side:
                logging.info(f"Условия для входа в {side} не выполнены")
                return False

            # Получаем баланс аккаунта
            account_info = await self.api.get_wallet_balance(accountType="UNIFIED")
            if "result" not in account_info or "list" not in account_info["result"]:
                logging.error("Не удалось получить информацию о балансе")
                return False

            # Получаем доступный баланс USDT
            available_balance = None
            for coin in account_info["result"]["list"][0].get("coin", []):
                if coin["coin"].upper() == "USDT":
                    available_balance = float(coin["availableBalance"])
                    break

            if available_balance is None:
                logging.error("Не удалось получить баланс USDT")
                return False

            # Рассчитываем размер позиции
            position_size = await self.calculate_position_size(available_balance, current_price, atr)
            if not position_size:
                logging.error("Не удалось рассчитать размер позиции")
                return False

            # Рассчитываем стоп-лосс и тейк-профиты
            sl_tp = await self.calculate_sl_tp(side, current_price, atr)
            if not sl_tp:
                logging.error("Не удалось рассчитать SL/TP")
                return False

            # Открываем позицию
            order = await self.api.place_order(
                category="linear",
                symbol=SYMBOL,
                side=side,
                orderType="Market",
                qty=str(position_size),
                stopLoss=str(sl_tp['stop_loss']),
                takeProfit=str(sl_tp['take_profit_1'])
            )

            if "result" not in order:
                logging.error(f"Ошибка при открытии позиции: {order}")
                return False

            # Сохраняем информацию о позиции
            position_info = {
                'symbol': SYMBOL,
                'side': side,
                'size': position_size,
                'entryPrice': current_price,
                'stopLoss': sl_tp['stop_loss'],
                'takeProfit1': sl_tp['take_profit_1'],
                'takeProfit2': sl_tp['take_profit_2'],
                'takeProfit3': sl_tp['take_profit_3'],
                'timestamp': int(time.time() * 1000)
            }

            # Обновляем positions.json
            positions = read_json_file('positions.json')
            if isinstance(positions, dict):
                positions = [positions]
            positions.append(position_info)
            update_json_file('positions.json', positions)

            # Отправляем уведомление
            message = f"✅ Открыта {side} позиция:\n"
            message += f"Цена входа: {current_price}\n"
            message += f"Размер: {position_size}\n"
            message += f"Стоп-лосс: {sl_tp['stop_loss']}\n"
            message += f"Тейк-профиты: {sl_tp['take_profit_1']}, {sl_tp['take_profit_2']}, {sl_tp['take_profit_3']}"
            await send_telegram_message(message)

            return True

        except Exception as e:
            logging.error(f"Ошибка при выполнении торговой операции: {e}")
            return False

def initialize_json_files():
    """Инициализирует JSON файлы с дефолтными значениями"""
    try:
        # Инициализация balance.json
        if not os.path.exists('balance.json'):
            balance_data = {
                "balance": 0,
                "equity": 0,
                "unrealized_pnl": 0,
                "used_margin": 0,
                "free_margin": 0,
                "last_updated": datetime.now().isoformat(),
                "details": {
                    "USDT": 0,
                    "USD": 0
                }
            }
            update_json_file('balance.json', balance_data)
            logging.info("✅ Создан файл balance.json")

        # Инициализация indicators.json
        if not os.path.exists('indicators.json'):
            indicators_data = {
                "vwap": 0,
                "rsi": 0,
                "atr": 0,
                "sma": 0,
                "support": 0,
                "resistance": 0,
                "last_updated": datetime.now().isoformat()
            }
            update_json_file('indicators.json', indicators_data)
            logging.info("✅ Создан файл indicators.json")

        # Инициализация positions.json
        if not os.path.exists('positions.json'):
            positions_data = {
                "positions": [],
                "last_updated": datetime.now().isoformat()
            }
            update_json_file('positions.json', positions_data)
            logging.info("✅ Создан файл positions.json")

        # Инициализация pnl.json
        if not os.path.exists('pnl.json'):
            pnl_data = {
                "daily": 0,
                "trades": [],
                "last_updated": datetime.now().isoformat()
            }
            update_json_file('pnl.json', pnl_data)
            logging.info("✅ Создан файл pnl.json")

        # Инициализация price.json
        if not os.path.exists('price.json'):
            price_data = {
                "price": 0,
                "last_updated": datetime.now().isoformat()
            }
            update_json_file('price.json', price_data)
            logging.info("✅ Создан файл price.json")

        # Инициализация signals.json
        if not os.path.exists('signals.json'):
            signals_data = {
                "refresh_data": False,
                "last_updated": datetime.now().isoformat()
            }
            update_json_file('signals.json', signals_data)
            logging.info("✅ Создан файл signals.json")

        logging.info("✅ Все JSON файлы успешно инициализированы")
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка при инициализации JSON файлов: {e}")
        return False

async def main():
    """
    Основная функция для запуска бота
    """
    global bot_running
    
    # Настройка логгера
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler('trading_bot.log'),
            logging.StreamHandler()
        ]
    )
    
    logging.info("Запуск торгового бота...")
    
    # Инициализируем JSON файлы
    if not initialize_json_files():
        logging.error("❌ Не удалось инициализировать JSON файлы")
        return
    
    # Создаем и запускаем бота внутри event loop
    bot = TradingBot()
    bot_running = True
    
    try:
        await bot.run()
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем")
        bot_running = False
    except Exception as e:
        logging.error(f"Критическая ошибка в работе бота: {e}")
        bot_running = False
    finally:
        bot_running = False
        logging.info("Завершение работы бота")

if __name__ == "__main__":
    asyncio.run(main())
