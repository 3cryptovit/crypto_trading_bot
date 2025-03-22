import os
import time
import requests
import logging
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
import talib
import sys
from datetime import datetime
import numpy as np
from colorama import Fore, Style
import aiohttp
import asyncio

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
            send_telegram_message(error_message)
        sys.exit(1)
    
    return {
        "symbol": symbol,
        "leverage": leverage,
        "min_leverage": min_leverage,
        "max_leverage": max_leverage,
        "risk_percentage": risk_percentage
    }

# Валидируем конфигурацию при запуске
config = validate_config()

# Используем валидированные значения
SYMBOL = config["symbol"]
LEVERAGE = config["leverage"]
MIN_LEVERAGE = config["min_leverage"]
MAX_LEVERAGE = config["max_leverage"]
RISK_PERCENTAGE = config["risk_percentage"]

# Остальные параметры конфигурации
TESTNET = os.getenv("TESTNET", "True").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Параметры входа в позицию
VOLUME_THRESHOLD = float(os.getenv("VOLUME_THRESHOLD", 1.5))
ORDERBOOK_DEPTH = int(os.getenv("ORDERBOOK_DEPTH", 10))
MIN_VOLUME_RATIO = float(os.getenv("MIN_VOLUME_RATIO", 1.05))

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

# Добавляем флаг демо-режима
DEMO_MODE = True  # Меняем на False для реальной торговли

# ======================== Настройка логирования ========================
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s')

class BybitAPI:
    """
    Класс для работы с API Bybit с контролем частоты запросов
    """
    def __init__(self):
        self.session = aiohttp.ClientSession()

    async def get_positions(self, category, symbol):
        url = f"https://api-testnet.bybit.com/v5/position/list?category={category}&symbol={symbol}"
        async with self.session.get(url) as resp:
            return await resp.json()

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

    async def get_kline(self, category="linear", symbol=SYMBOL, interval=5, limit=50):
        """Получение свечей с обработкой ошибок"""
        await self._wait_for_rate_limit()
        response = await self.session.get_kline(category=category, symbol=symbol, interval=interval, limit=limit)
        if not response or "result" not in response or "list" not in response["result"]:
            logging.warning("⚠ Bybit API не вернул данные, пропускаем шаг.")
            return None
        return await self._handle_api_error(response)

    async def get_orderbook(self, category="linear", symbol=SYMBOL, limit=50):
        """Получение стакана с обработкой ошибок"""
        await self._wait_for_rate_limit()
        response = await self.session.get_orderbook(category=category, symbol=symbol, limit=limit)
        return await self._handle_api_error(response)

    async def get_executions(self, category="linear", symbol=SYMBOL, limit=50):
        """Получение исполненных ордеров с обработкой ошибок"""
        await self._wait_for_rate_limit()
        response = await self.session.get_executions(category=category, symbol=symbol, limit=limit)
        return await self._handle_api_error(response)

    async def get_wallet_balance(self, accountType="UNIFIED"):
        """Получение баланса с обработкой ошибок"""
        await self._wait_for_rate_limit()
        response = await self.session.get_wallet_balance(accountType=accountType)
        return await self._handle_api_error(response)

    async def set_leverage(self, symbol=SYMBOL, leverage=5):
        """ Устанавливает плечо, только если оно отличается от текущего """
        try:
            current_positions = await self.get_positions(symbol=symbol)
            if current_positions and "result" in current_positions and "list" in current_positions["result"]:
                current_leverage = float(current_positions["result"]["list"][0].get("leverage", 1))
                if current_leverage == leverage:
                    logging.info(f"🔹 Плечо {leverage}x уже установлено, изменение не требуется.")
                    return True  # Ничего не меняем

            # Если плечо отличается – изменяем
            await self._wait_for_rate_limit()
            response = await self.session.set_leverage(
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
            order = await self.session.place_order(
                category="linear",
                symbol=SYMBOL,
                side=side,
                orderType="Limit",
                qty=str(qty),
                price=str(current_price),
                timeInForce="PostOnly"  # Мейкерский ордер
            )

            if not order or "result" not in order:
                logging.error(f"Ошибка размещения ордера: {order}")
                return False

            order_id = order["result"]["orderId"]
            logging.info(f"Размещен ордер {order_id}")

            # Устанавливаем стоп-лосс и первый тейк-профит
            if stop_loss and take_profit_1:
                await self.session.set_trading_stop(
                    category="linear",
                    symbol=SYMBOL,
                    side=side,
                    stopLoss=str(stop_loss),
                    takeProfit=str(take_profit_1)
                )
                logging.info(f"Установлены SL: {stop_loss} и TP1: {take_profit_1}")

            # Размещаем дополнительные тейк-профиты как лимитные ордера
            if take_profit_2 and take_profit_3:
                # Рассчитываем размеры для частичного закрытия
                tp2_qty = qty * 0.3  # 30% позиции
                tp3_qty = qty * 0.4  # 40% позиции

                # Проверяем минимальные размеры
                if tp2_qty >= MIN_POSITION_SIZES.get(SYMBOL, 0.002):
                    tp2_order = await self.session.place_order(
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

                if tp3_qty >= MIN_POSITION_SIZES.get(SYMBOL, 0.003):
                    tp3_order = await self.session.place_order(
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
        
        response = await self.session.set_trading_stop(**params)
        return await self._handle_api_error(response)

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
            
        response = await self.session.get_closed_pnl(**params)
        return await self._handle_api_error(response)

    async def get_order_list(self, category="linear", symbol=SYMBOL, orderId=None):
        """Получение информации о конкретном ордере"""
        await self._wait_for_rate_limit()
        params = {
            "category": category,
            "symbol": symbol,
            "orderId": orderId
        }
        response = await self.session.get_order_list(**params)
        return await self._handle_api_error(response)

    async def get_tickers(self, category="linear", symbol=SYMBOL):
        """Получение текущей цены тикера"""
        await self._wait_for_rate_limit()
        response = await self.session.get_tickers(category=category, symbol=symbol)
        return await self._handle_api_error(response)

# ======================== Функция для уведомлений в Telegram ========================
async def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as response:
            if response.status != 200:
                logging.error(f"Ошибка отправки Telegram-сообщения: {await response.text()}")

# ======================== Класс торгового бота ========================
class TradingBot:
    def __init__(self):
        """
        Инициализация бота
        """
        self.session = HTTP(
            testnet=TESTNET,
            api_key=os.getenv("BYBIT_API_KEY"),
            api_secret=os.getenv("BYBIT_API_SECRET")
        )
        self.api = BybitAPI()  # Используем новый класс для работы с API
        self.last_checked_price = None
        self.active_position = False
        self.current_position = None
        self.last_order_time = 0
        self.min_order_interval = 300  # 5 минут
        self.processed_orders = set()
        self.last_trade_time = None
        self.daily_pnl = 0
        self.consecutive_losses = 0
        self.last_daily_reset = None
        self.last_position_check = None
        self.position_check_interval = 60  # Проверка позиций каждую минуту
        self.lock = asyncio.Lock()  # Добавляем блокировку

        # Ограничение на количество сделок в день
        self.max_daily_trades = 12
        self.daily_trade_count = 0

        # Инициализация плеча при запуске
        asyncio.create_task(self.initialize_leverage())

    async def initialize_leverage(self):
        try:
            # Сначала получаем текущие настройки позиции
            position_info = await self.api.get_positions(category="linear", symbol=SYMBOL)
            if position_info and position_info.get("result", {}).get("list"):
                current_leverage = float(position_info["result"]["list"][0].get("leverage", 1))

                # Устанавливаем новое плечо только если оно отличается от текущего
                if current_leverage != LEVERAGE:
                    await self.api.set_leverage(leverage=LEVERAGE)
                    logging.info(f"Установлено плечо {LEVERAGE}x для {SYMBOL}")
        except Exception as e:
            logging.error(f"Ошибка при установке плеча: {e}")
            await send_telegram_message(f"⚠ Ошибка при установке плеча: {e}")

    def get_atr(self, period=14):
        """Рассчитывает ATR (Average True Range)"""
        try:
            candles = self.api.get_kline(category="linear", symbol=SYMBOL, interval=5, limit=period)
            
            if not candles or "result" not in candles or "list" not in candles["result"]:
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

    def reset_daily_stats(self):
        today = datetime.now().date()
        if self.last_daily_reset != today:
            self.daily_pnl = 0
            self.daily_trade_count = 0
            self.last_daily_reset = today
            send_telegram_message("📊 Дневная статистика сброшена.")

    def check_pnl(self):
        """
        Проверяет PNL по закрытым позициям
        """
        try:
            # Сбрасываем статистику если начался новый день
            self.reset_daily_stats()
            
            # Получаем время последней проверки
            current_time = time.time()
            if self.last_trade_time is None:
                self.last_trade_time = current_time - 300  # Начинаем с последних 5 минут
                return
            
            # Получаем закрытые ордера за период с последней проверки
            try:
                closed_orders = self.api.get_closed_pnl(
                    category="linear",
                    symbol=SYMBOL,
                    startTime=int(self.last_trade_time * 1000),
                    endTime=int(current_time * 1000),
                    limit=50
                )
            except Exception as e:
                error_msg = f"Ошибка при получении закрытых ордеров: {e}"
                logging.error(error_msg)
                send_telegram_message(f"⚠️ {error_msg}")
                return

            if "result" not in closed_orders or "list" not in closed_orders["result"]:
                logging.error("Неверный формат ответа API при получении закрытых ордеров")
                return

            # Обрабатываем каждый закрытый ордер
            for order in closed_orders["result"]["list"]:
                order_id = order.get("orderId")
                if not order_id or order_id in self.processed_orders:
                    continue

                try:
                    # Получаем детали ордера
                    order_details = self.api.get_order_list(
                        category="linear",
                        symbol=SYMBOL,
                        orderId=order_id
                    )

                    if "result" not in order_details or "list" not in order_details["result"]:
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
                        send_telegram_message(message)

                        # Проверяем лимиты
                        if self.daily_pnl <= -100:  # Максимальный дневной убыток 100 USDT
                            error_msg = "⚠️ Достигнут лимит дневного убытка. Торговля остановлена."
                            logging.warning(error_msg)
                            send_telegram_message(error_msg)
                            return False

                        if self.consecutive_losses >= 3:  # Максимум 3 последовательных убытка
                            error_msg = "⚠️ Достигнут лимит последовательных убытков. Торговля остановлена."
                            logging.warning(error_msg)
                            send_telegram_message(error_msg)
                            return False

                        # Добавляем ордер в обработанные
                        self.processed_orders.add(order_id)
                        if len(self.processed_orders) > 50:  # Ограничиваем размер множества
                            self.processed_orders = set(list(self.processed_orders)[-50:])

                except Exception as e:
                    logging.error(f"Ошибка при обработке ордера {order_id}: {e}")
                    continue

            # Обновляем время последней проверки
            self.last_trade_time = current_time
            return True

        except Exception as e:
            error_msg = f"Ошибка при проверке PNL: {e}"
            logging.error(error_msg)
            send_telegram_message(f"⚠️ {error_msg}")
            return False

    def get_latest_price(self):
        try:
            result = self.api.get_tickers(category="linear", symbol=SYMBOL)
            if "result" in result and "list" in result["result"] and result["result"]["list"]:
                price = float(result["result"]["list"][0]["lastPrice"])
                return price
            else:
                logging.error("Не удалось получить последнюю цену.")
                return None
        except Exception as e:
            logging.error(f"Ошибка при получении цены: {e}")
            return None

    def calculate_trade_size(self, stop_loss_price, entry_price):
      try:
          # 🔥 Получаем баланс USDT
          account_info = self.api.get_wallet_balance(accountType="UNIFIED")
          logging.info(f"API ответ get_wallet_balance: {account_info}")

          # Проверяем баланс USDT
          if "result" in account_info and "list" in account_info["result"]:
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

    def check_stop_loss_distance(self, entry_price, stop_loss_price):
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
                send_telegram_message(f"⚠️ {error_msg}")
                return False
                
            return True
            
        except Exception as e:
            logging.error(f"Ошибка при проверке расстояния стоп-лосса: {e}")
            return False

    def calculate_stop_loss(self, side, entry_price, atr=None):
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

    def analyze_long_term_levels(self):
        try:
            # ✅ Запрашиваем свечи (уменьшаем limit до 100 для большей стабильности)
            candles_1H = self.api.get_kline(category="linear", symbol=SYMBOL, interval=60, limit=100)
            candles_4H = self.api.get_kline(category="linear", symbol=SYMBOL, interval=240, limit=100)
            candles_1D = self.api.get_kline(category="linear", symbol=SYMBOL, interval="D", limit=100)

            # ✅ Функция для извлечения закрытий (разворачиваем список)
            def extract_closes(candles):
                if "result" in candles and "list" in candles["result"] and candles["result"]["list"]:
                    return [float(candle[4]) for candle in reversed(candles["result"]["list"])]
                else:
                    return []

            # ✅ Извлекаем данные
            closes_1H = extract_closes(candles_1H)
            closes_4H = extract_closes(candles_4H)
            closes_1D = extract_closes(candles_1D)

            # ✅ Логируем данные свечей для отладки (берём последние 5 свечей)
            logging.info(f"🧐 Данные свечей 1H: {closes_1H[-5:]}")
            logging.info(f"🧐 Данные свечей 4H: {closes_4H[-5:]}")
            logging.info(f"🧐 Данные свечей 1D: {closes_1D[-5:]}")

            # ✅ Проверяем, есть ли данные
            if not closes_1H:
                logging.warning("⚠️ Недостаточно данных для анализа уровней на 1H!")
            if not closes_4H:
                logging.warning("⚠️ Недостаточно данных для анализа уровней на 4H!")
            if not closes_1D:
                logging.warning("⚠️ Недостаточно данных для анализа уровней на 1D!")

            # ✅ Определяем уровни поддержки и сопротивления, если данные есть
            levels = {}

            if closes_1H:
                support_1H, resistance_1H = self.detect_support_resistance(closes_1H) if closes_1H else (None, None)
                levels["1H"] = {"support": support_1H, "resistance": resistance_1H}
                logging.info(f"🔵 1H: Support: {support_1H:.2f}, Resistance: {resistance_1H:.2f}")

            if closes_4H:
                support_4H, resistance_4H = self.detect_support_resistance(closes_4H) if closes_4H else (None, None)
                levels["4H"] = {"support": support_4H, "resistance": resistance_4H}
                logging.info(f"🟢 4H: Support: {support_4H:.2f}, Resistance: {resistance_4H:.2f}")

            if closes_1D:
                support_1D, resistance_1D = self.detect_support_resistance(closes_1D) if closes_1D else (None, None)
                levels["1D"] = {"support": support_1D, "resistance": resistance_1D}
                logging.info(f"🔴 1D: Support: {support_1D:.2f}, Resistance: {resistance_1D:.2f}")

            return levels if levels else None

        except Exception as e:
            logging.error(f"❌ Ошибка при анализе долгосрочных уровней: {e}")
            return None


    def detect_support_resistance(self, closes):
        """
        Находит ближайшие уровни поддержки и сопротивления.
        """
        if not closes:
            return None, None
        high = max(closes)
        low = min(closes)
        return low, high

    def scalping_strategy(self):
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
            price = self.get_latest_price()
            if price is None:
                return

            # Если это первый запуск, сохраняем цену и выходим
            if self.last_checked_price is None:
                self.last_checked_price = price
                return

            # Проверяем наличие активной позиции
            if self.active_position:
                # Обновляем трейлинг-стоп для открытой позиции
                self.update_trailing_stop(self.current_position, price)
                logging.info("Уже есть активная позиция, пропускаем вход")
                return

            # Проверяем волатильность рынка
            atr = self.get_atr()
            if atr:
                # Если волатильность слишком высокая, увеличиваем интервал между ордерами
                if atr > price * 0.01:  # Если ATR > 1% от цены
                    self.min_order_interval = 600  # Увеличиваем до 10 минут
                    logging.info(f"Высокая волатильность (ATR: {atr:.2f}), увеличиваем интервал между ордерами")
                else:
                    self.min_order_interval = 300  # Возвращаем к 5 минутам

            # Анализируем объемы и стакан
            volume_direction = self.analyze_volume()
            if volume_direction is None:
                logging.info("Нет четкого направления по объемам")
                return

            # Проверяем тренд и условия входа
            trend = self.check_trend(volume_direction)
            if trend == "Нейтральный":
                logging.info("❌ Тренд слишком слабый, пропускаем вход в сделку.")
                return

            if self.check_trend(volume_direction):
                # Проверяем долгосрочные уровни
                if price < self.long_term_levels["4H"]["support"]:
                    logging.info("Цена у глобальной поддержки. Вход отменён.")
                    return

                # Рассчитываем стоп-лосс
                stop_loss = self.calculate_stop_loss(volume_direction, price, atr)
                if not stop_loss:
                    return
                
                # Проверяем минимальное расстояние стоп-лосса
                if not self.check_stop_loss_distance(price, stop_loss):
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
                    account_info = self.api.get_wallet_balance(accountType="UNIFIED")
                    if "result" in account_info and "list" in account_info["result"]:
                        balance_info = account_info["result"]["list"][0]
                        if "coin" in balance_info:
                            for item in balance_info["coin"]:
                                if item["coin"].upper() == "USDT":
                                    available_balance = float(item["availableBalance"])
                                    # Рассчитываем размер позиции с учетом плеча
                                    qty = self.calculate_position_size(stop_loss, price)
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
                if not self.check_liquidity(volume_direction):
                    logging.info("Недостаточная ликвидность для входа")
                    return

                logging.info(f"Планируем {volume_direction} ордер: qty={qty}, entry={price}, SL={stop_loss}")
                logging.info(f"Цели: TP1={take_profit_1}, TP2={take_profit_2}, TP3={take_profit_3}")

                # Размещаем ордер
                if self.place_order(volume_direction, qty, stop_loss, take_profit_1, take_profit_2, take_profit_3):
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
                    send_telegram_message(f"""
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
            send_telegram_message(f"⚠ {error_msg}")

    def update_trailing_stop(self, position, current_price):
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
                self.api.set_trading_stop(category="linear", symbol=SYMBOL, side="Buy", stopLoss=str(new_stop))
                position["stop_loss"] = new_stop
                return True

            else:
                new_stop = current_price * (1 + TRAILING_STOP / 100)
                if new_stop >= current_stop or ((current_stop - new_stop) / current_stop * 100) < 0.1:
                    return False
                self.api.set_trading_stop(category="linear", symbol=SYMBOL, side="Sell", stopLoss=str(new_stop))
                position["stop_loss"] = new_stop
                return True

        except Exception as e:
            logging.error(f"Ошибка при обновлении трейлинг-стопа: {e}")
            return False

    def check_trend(self, side):
        """Улучшенная проверка тренда с использованием нескольких индикаторов"""
        try:
            # Получаем свечи
            candles = self.get_kline_data(category="linear", symbol=SYMBOL, interval=5, limit=50)
            if not candles:
                logging.error("Неверный формат данных свечей")
                return False

            # Получаем цены закрытия
            closes = [float(candle[4]) for candle in candles["list"]]
            if len(closes) < 50:
                logging.warning("Недостаточно данных для анализа")
                return False

            # Преобразуем список в numpy.ndarray
            closes_np = np.array(closes, dtype=np.float64)

            # Рассчитываем индикаторы
            sma_50 = talib.SMA(closes_np, timeperiod=50)[-1]
            sma_200 = None  # Инициализируем пустым значением
            if len(closes) >= 200:
                sma_200 = talib.SMA(closes_np, timeperiod=200)[-1]
            else:
                logging.warning("⚠ Недостаточно данных для SMA200. Используем только SMA50 и EMA21.")
                sma_200 = sma_50

            ema_21 = talib.EMA(closes_np, timeperiod=21)[-1]
            rsi = talib.RSI(closes_np, timeperiod=14)[-1]

            trend = "Нейтральный"
            if sma_50 > sma_200 and ema_21 > sma_50:
                trend = "Бычий 🟢"
            elif sma_50 < sma_200 and ema_21 < sma_50:
                trend = "Медвежий 🔴"
            elif abs(sma_50 - sma_200) < 0.5 * sma_50 and 45 <= rsi <= 55:
                trend = "Нейтральный"

            logging.info(f"📈 Тренд: {trend} (SMA50: {sma_50}, SMA200: {sma_200}, EMA21: {ema_21}, RSI: {rsi})")
            return trend

        except Exception as e:
            logging.error(f"Ошибка в check_trend: {e}")
            return False

    def check_liquidity(self, side):
        """
        Проверяет ликвидность в стакане для заданной стороны
        """
        try:
            # Получаем стакан
            orderbook = self.api.get_orderbook(category="linear", symbol=SYMBOL)
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

    def check_positions(self):
        """
        Проверяет текущие позиции и обновляет флаг active_position
        """
        try:
            positions = self.api.get_positions(category="linear", symbol=SYMBOL)
            
            # Проверяем наличие ошибок в ответе API
            if positions.get("retCode") != 0:
                error_msg = f"Ошибка при получении позиций: {positions.get('retMsg', 'Неизвестная ошибка')}"
                logging.error(error_msg)
                send_telegram_message(f"⚠ {error_msg}")
                return False

            # Проверяем структуру ответа
            if not positions.get("result", {}).get("list"):
                logging.warning("Нет данных о позициях")
                self.active_position = False
                self.current_position = None
                return False

            # Проверяем каждую позицию
            for position in positions["result"]["list"]:
                size = float(position.get("size", 0))
                side = position.get("side", "")
                
                # Проверяем, что это наша позиция
                if (size != 0 and 
                    self.current_position and 
                    self.current_position.get("side") == side and
                    abs(float(position.get("size", 0)) - self.current_position.get("qty", 0)) < 0.001):
                    
                    # Обновляем информацию о позиции
                    self.current_position.update({
                        "size": size,
                        "leverage": float(position.get("leverage", 0)),
                        "unrealized_pnl": float(position.get("unrealizedPnl", 0)),
                        "mark_price": float(position.get("markPrice", 0))
                    })
                    
                    self.active_position = True
                    return True

            # Если мы дошли до этой точки, значит активной позиции нет
            self.active_position = False
            self.current_position = None
            return False

        except Exception as e:
            error_msg = f"Ошибка при проверке позиций: {e}"
            logging.error(error_msg)
            send_telegram_message(f"⚠ {error_msg}")
            self.active_position = False
            self.current_position = None
            return False

    def run(self):
        send_telegram_message("Бот запущен (демо-счёт Bybit).")
        while True:
            try:
                self.check_positions()  # Проверяем текущие позиции
                self.scalping_strategy()
                self.check_pnl()
                time.sleep(CHECK_INTERVAL)
            except Exception as e:
                logging.error(f"Ошибка в основном цикле: {e}")
                send_telegram_message(f"Ошибка в основном цикле: {e}")
                time.sleep(CHECK_INTERVAL)

    def analyze_orderbook(self, side):
        """
        Анализирует стакан заявок для определения дисбаланса
        """
        try:
            orderbook = self.api.get_orderbook(category="linear", symbol=SYMBOL, limit=ORDERBOOK_DEPTH)
            
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

    def analyze_volume(self):
        """Анализирует объемы торгов для определения импульса"""
        try:
            trades = self.api.get_executions(category="linear", symbol=SYMBOL, limit=50)

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

            logging.debug(f"Объем Buy: {buy_volume}, Объем Sell: {sell_volume}")

            if abs(buy_volume - sell_volume) < 0.05 * (buy_volume + sell_volume):
                return "Флет"

            return None
        except Exception as e:
            logging.error(f"Ошибка при анализе объемов: {e}")
            return None

    def calculate_vwap(self):
        """ Рассчитывает VWAP (Volume Weighted Average Price) """
        try:
            candles = self.api.get_kline(category="linear", symbol=SYMBOL, interval=1, limit=VWAP_PERIOD)

            if not candles or "result" not in candles or "list" not in candles["result"]:
                logging.error("❌ API Bybit не вернул данные свечей для VWAP")
                return None

            total_volume = 0
            total_price_volume = 0

            for candle in candles["result"]["list"]:
                try:
                    volume = float(candle[5])  # Индекс 5 обычно соответствует объему
                    close_price = float(candle[4])  # Индекс 4 — цена закрытия
                    total_volume += volume
                    total_price_volume += close_price * volume
                except (ValueError, IndexError, TypeError) as e:
                    logging.warning(f"⚠ Пропущена свеча из-за ошибки: {e}")
                    continue

            if total_volume == 0:
                logging.error("⚠ Недостаточно данных для расчета VWAP (нулевой объем)")
                return None

            return total_price_volume / total_volume

        except Exception as e:
            logging.error(f"❌ Ошибка при расчете VWAP: {e}")
            return None

    def calculate_position_size(self, stop_loss_price, entry_price):
        """
        Рассчитывает размер позиции с учетом риска и минимального размера
        """
        try:
            # Получаем баланс аккаунта
            account_info = self.api.get_wallet_balance(accountType="UNIFIED")
            if "result" not in account_info or "list" not in account_info["result"]:
                logging.error("Не удалось получить информацию о балансе")
                return None

            # Получаем доступный баланс USDT
            available_balance = None
            for coin in account_info["result"]["list"][0].get("coin", []):
                if coin["coin"].upper() == "USDT":
                    available_balance = float(coin["availableBalance"])
                    break

            if available_balance is None:
                logging.error("Не удалось получить баланс USDT")
                return None

            # Рассчитываем риск в USDT
            risk_amount = available_balance * (RISK_PERCENTAGE / 100)

            # Рассчитываем расстояние до стоп-лосса
            stop_distance = abs(entry_price - stop_loss_price)
            if stop_distance == 0:
                logging.error("Нулевое расстояние до стоп-лосса")
                return None

            # Рассчитываем размер позиции с учетом плеча
            position_size = (risk_amount * LEVERAGE) / stop_distance

            # Получаем минимальный размер позиции для текущей пары
            min_size = MIN_POSITION_SIZES.get(SYMBOL, 0.001)
            
            # Проверяем, достаточен ли размер позиции
            if position_size < min_size:
                error_msg = f"Недостаточно средств для минимальной позиции. Требуется минимум {min_size} {SYMBOL}"
                logging.warning(error_msg)
                send_telegram_message(f"⚠️ {error_msg}")
                return None

            # Округляем размер позиции до допустимого значения
            # Для BTC и ETH используем 3 знака после запятой, для остальных - 2
            if SYMBOL in ["BTCUSDT", "ETHUSDT"]:
                position_size = round(position_size, 3)
            else:
                position_size = round(position_size, 2)

            return position_size if position_size >= min_size else None

        except Exception as e:
            error_msg = f"Ошибка при расчете размера позиции: {e}"
            logging.error(error_msg)
            send_telegram_message(f"⚠️ {error_msg}")
            return None

    TIMEFRAME_MAPPING = {
        "1D": "D",
        "1W": "W",
        "1M": "M"
    }

    def fetch_historical_data(self, timeframe="1D", limit=200):
        """
        Запрашивает исторические свечи по заданному таймфрейму.
        Возвращает список цен закрытия (close).
        """
        try:
            interval = self.TIMEFRAME_MAPPING.get(timeframe, "D")  # По умолчанию "D" (день)
            response = self.api.get_kline(category="linear", symbol=SYMBOL, interval=interval, limit=limit)
            
            if response is None:
                logging.warning("⚠ API Bybit не вернул данные. Возможно, перегрузка.")
                return []

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

    def get_support_resistance(self, closes):
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

    def market_structure_analysis(self, closes):
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

    def perform_long_term_analysis(self):
        """
        Выполняет долгосрочный анализ рынка.
        """
        # 📊 Получаем исторические данные
        closes_1D = self.fetch_historical_data("1D", 200)
        closes_1W = self.fetch_historical_data("1W", 100)
        closes_1M = self.fetch_historical_data("1M", 50)

        # 🏆 Определяем уровни поддержки и сопротивления
        support_1D, resistance_1D = self.get_support_resistance(closes_1D)
        support_1W, resistance_1W = self.get_support_resistance(closes_1W)
        support_1M, resistance_1M = self.get_support_resistance(closes_1M)

        # 🔥 Анализируем тренды
        trend_1D = self.analyze_trend(closes_1D)
        trend_1W = self.analyze_trend(closes_1W)
        trend_1M = self.analyze_trend(closes_1M)

        # 📈 Анализируем объемы
        volume_trend = self.analyze_volume()

        # 📊 Определяем рыночную структуру
        market_trend_1D, last_high_1D, last_low_1D = self.market_structure_analysis(closes_1D)

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

    def analyze_trend(self, closes):
        """
        Анализирует тренд с использованием SMA, EMA и RSI.
        """
        if len(closes) < 50:
            logging.warning("⚠️ Недостаточно данных для анализа тренда.")
            return None

        closes_np = np.array(closes)

        # Расчёт индикаторов
        sma_50 = talib.SMA(closes_np, timeperiod=50)[-1]
        sma_200 = None  # Инициализируем пустым значением
        if len(closes) >= 200:
            sma_200 = talib.SMA(closes_np, timeperiod=200)[-1]
        else:
            logging.warning("⚠ Недостаточно данных для SMA200. Используем только SMA50 и EMA21.")
            sma_200 = sma_50

        ema_21 = talib.EMA(closes_np, timeperiod=21)[-1]
        rsi = talib.RSI(closes_np, timeperiod=14)[-1]

        # Проверяем, есть ли достаточно данных для SMA200
        if sma_200 is None:
            logging.warning("⚠ Недостаточно данных для SMA200. Используем только SMA50 и EMA21.")
            sma_200 = sma_50  # Временно заменяем, чтобы избежать ошибки

        if sma_200 is not None and sma_50 is not None:
            if sma_50 > sma_200:
                trend = "Бычий"
            else:
                trend = "Медвежий"
        else:
            trend = "Недостаточно данных"

        if abs(sma_50 - sma_200) < 0.5 * sma_50 and 45 <= rsi <= 55:
            trend = "Нейтральный"

        logging.info(f"📈 Тренд: {trend} (SMA50: {sma_50}, SMA200: {sma_200}, EMA21: {ema_21}, RSI: {rsi})")
        return trend

    def get_kline_data(self, category, symbol, interval, limit=50):
        """Получение свечных данных с обработкой ошибок"""
        response = self.api.get_kline(category=category, symbol=symbol, interval=interval, limit=limit)
        if response and response.get("result"):
            return response["result"]
        logging.warning("⚠ Bybit API не вернул данные, пропускаем шаг.")
        return None

# ======================== Запуск бота ========================
if __name__ == "__main__":
    bot = TradingBot()

    logging.info("🔄 Тест API Bybit...")
    
    # Тест получения цены
    price = bot.get_latest_price()
    logging.info(f"📌 Текущая цена {SYMBOL}: {price}")

    # Тест стакана
    orderbook = bot.api.get_orderbook()
    logging.info(f"📌 Тест стакана: {orderbook}")

    # Тест анализа объемов
    volume_direction = bot.analyze_volume()
    logging.info(Fore.GREEN + f"📌 Направление объемов: {volume_direction}" + Style.RESET_ALL)
    logging.warning(Fore.YELLOW + "⚠ Недостаточно данных для SMA200!" + Style.RESET_ALL)

    # Проверка баланса
    balance = bot.api.get_wallet_balance()
    logging.info(f"💰 Баланс аккаунта: {balance}")

    # Проверка плеча
    leverage = bot.api.set_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    logging.info(f"⚙️ Установлено плечо: {leverage}")

    logging.info("✅ Тест API Bybit завершён успешно!")

    # Симуляция направлений объема
    test_volume_direction = bot.analyze_volume() or "Buy"
    logging.info(f"📌 Тестируемый сигнал объема: {test_volume_direction}")

    # Симуляция входа в сделку
    test_price = bot.get_latest_price()
    test_stop_loss = bot.calculate_stop_loss(test_volume_direction, test_price)
    test_take_profit_1 = test_price * 1.02
    test_take_profit_2 = test_price * 1.03
    test_take_profit_3 = test_price * 1.05

    logging.info(f"🛠 Симуляция сделки: {test_volume_direction} на {test_price}")
    logging.info(f"🔴 SL: {test_stop_loss}, 🟢 TP1: {test_take_profit_1}, TP2: {test_take_profit_2}, TP3: {test_take_profit_3}")

    if DEMO_MODE:
        logging.info("✅ Тест сделки успешно выполнен (демо)")
    else:
        success = bot.api.place_order(
            side=test_volume_direction,
            qty=0.001,
            stop_loss=test_stop_loss,
            take_profit_1=test_take_profit_1,
            take_profit_2=test_take_profit_2,
            take_profit_3=test_take_profit_3
        )
        logging.info(f"✅ Сделка {'успешно размещена' if success else 'не удалась'}")

    logging.info("📊 Запуск анализа рынка...")

    # Проверка SMA, RSI, VWAP
    closes = bot.fetch_historical_data("1D", 50)
    trend = bot.analyze_trend(closes)
    vwap = bot.calculate_vwap()

    logging.info(f"📈 Тренд на 1D: {trend}")
    logging.info(f"📊 VWAP: {vwap}")

    bot.run()
