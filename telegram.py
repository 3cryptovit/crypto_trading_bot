import asyncio
import sys
import json
from datetime import datetime

# ✅ Исправляем ошибку "aiodns needs a SelectorEventLoop on Windows"
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram import Router
from dotenv import load_dotenv
import os
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('telegram_bot.log'),
        logging.StreamHandler()
    ]
)

load_dotenv()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")

if not all([TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN]):
    logging.error("❌ Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID в .env файле")
    sys.exit(1)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot=bot, storage=MemoryStorage())
router = Router()

# 📌 Кнопки для управления ботом
keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📂 Посмотреть открытые позиции", callback_data="open_positions")],
    [InlineKeyboardButton(text="💰 PnL за день и сделку", callback_data="pnl_info")],
    [InlineKeyboardButton(text="🔄 Обновить данные", callback_data="refresh_data")],
    [InlineKeyboardButton(text="🛒 Купить", callback_data="buy")],
    [InlineKeyboardButton(text="📉 Продать", callback_data="sell")],
    [InlineKeyboardButton(text="📊 Индикаторы", callback_data="indicators")],
    [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
    [InlineKeyboardButton(text="❓ Помощь", callback_data="help")]
])

# Добавляем клавиатуру с командами
commands_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [
            types.KeyboardButton(text="📊 Статус"),
            types.KeyboardButton(text="📂 Позиции")
        ],
        [
            types.KeyboardButton(text="💰 PnL"),
            types.KeyboardButton(text="📊 Индикаторы")
        ],
        [
            types.KeyboardButton(text="🛒 Купить"),
            types.KeyboardButton(text="📉 Продать")
        ],
        [
            types.KeyboardButton(text="🔄 Обновить данные"),
            types.KeyboardButton(text="⚙️ Настройки")
        ]
    ],
    resize_keyboard=True
)

def read_json_file(filename: str) -> dict:
    """Читает JSON файл и создаёт его, если его нет"""
    try:
        with open(filename, 'r') as f:
            data = json.load(f)
        
        # 🔥 Логируем содержимое файла перед возвратом
        logging.info(f"📂 Данные из {filename}: {data}")
        return data
    except FileNotFoundError:
        logging.warning(f"⚠️ Файл {filename} не найден, создаём пустой.")
        # Создаём файл с начальными значениями в зависимости от типа файла
        default_data = {}
        if filename == 'balance.json':
            default_data = {
                "balance": 0,
                "used_margin": 0,
                "free_margin": 0,
                "last_updated": "Неизвестно"
            }
        elif filename == 'price.json':
            default_data = {"price": 0}
        elif filename == 'positions.json':
            default_data = {"positions": [], "last_updated": "Неизвестно"}
        elif filename == 'pnl.json':
            default_data = {"daily": 0, "trades": [], "last_updated": "Неизвестно"}
        
        with open(filename, 'w') as f:
            json.dump(default_data, f, indent=2)
        return default_data
    except json.JSONDecodeError:
        logging.error(f"❌ Ошибка чтения JSON из файла {filename}, сбрасываем содержимое.")
        with open(filename, 'w') as f:
            json.dump({}, f)
        return {}

def write_json_file(filename: str, data: dict) -> bool:
    """Записывает данные в JSON файл"""
    try:
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка записи в файл {filename}: {e}")
        return False

def update_json_file(filename: str, data: dict) -> bool:
    """Обновляет JSON файл, добавляя временную метку"""
    try:
        # Добавляем временную метку к данным
        data['last_updated'] = datetime.now().isoformat()
        
        # Записываем обновленные данные
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
            
        logging.info(f"✅ Файл {filename} успешно обновлен")
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка обновления файла {filename}: {e}")
        return False

def format_positions(positions: list) -> str:
    """Форматирует список позиций в читаемый текст"""
    if not positions:
        return "📂 Нет открытых позиций"
    
    message = "📂 Открытые позиции:\n"
    
    # Если positions это словарь (одна позиция)
    if isinstance(positions, dict):
        try:
            # Безопасное получение значений с преобразованием типов
            symbol = positions.get('symbol', 'Unknown')
            side = positions.get('side', 'Unknown')
            qty = float(positions.get('size', 0))
            entry = float(positions.get('avgPrice', 0))
            leverage = int(positions.get('leverage', 1))
            unrealized_pnl = float(positions.get('unrealisedPnl', 0))
            stop_loss = float(positions.get('stopLoss', 0))
            take_profit_1 = float(positions.get('takeProfit1', 0))
            take_profit_2 = float(positions.get('takeProfit2', 0))
            take_profit_3 = float(positions.get('takeProfit3', 0))
            
            # Пропускаем пустые позиции
            if qty == 0 or not side:
                logging.warning(f"⚠️ Пропущена пустая позиция: {positions}")
                return "📂 Нет открытых позиций"
            
            message += f"🔹 {symbol}: {side} {qty} @ {entry:.2f} (x{leverage})\n"
            message += f"   📊 PnL: {unrealized_pnl:.2f} USDT\n"
            message += f"   🛑 Стоп-лосс: {stop_loss:.2f}\n"
            message += f"   🎯 Тейк-профиты:\n"
            message += f"      TP1: {take_profit_1:.2f}\n"
            message += f"      TP2: {take_profit_2:.2f}\n"
            message += f"      TP3: {take_profit_3:.2f}\n"
            
            # Добавляем информацию о трейлинг-стопе
            if positions.get('trailing_stop'):
                message += f"   📈 Трейлинг-стоп: {positions['trailing_stop']:.2f}\n"
            
            # Добавляем информацию о частичном закрытии
            if positions.get('partial_closes'):
                message += f"   🔄 Частичные закрытия:\n"
                for close in positions['partial_closes']:
                    message += f"      {close['size']} @ {close['price']:.2f}\n"
            
        except Exception as e:
            logging.error(f"❌ Ошибка при форматировании позиции: {e}")
            return "📂 Нет открытых позиций"
    
    # Если positions это список (несколько позиций)
    elif isinstance(positions, list):
        for pos in positions:
            try:
                # Безопасное получение значений с преобразованием типов
                symbol = pos.get('symbol', 'Unknown')
                side = pos.get('side', 'Unknown')
                qty = float(pos.get('size', 0))
                entry = float(pos.get('avgPrice', 0))
                leverage = int(pos.get('leverage', 1))
                unrealized_pnl = float(pos.get('unrealisedPnl', 0))
                stop_loss = float(pos.get('stopLoss', 0))
                take_profit_1 = float(pos.get('takeProfit1', 0))
                take_profit_2 = float(pos.get('takeProfit2', 0))
                take_profit_3 = float(pos.get('takeProfit3', 0))
                
                # Пропускаем пустые позиции
                if qty == 0 or not side:
                    logging.warning(f"⚠️ Пропущена пустая позиция: {pos}")
                    continue
                
                message += f"🔹 {symbol}: {side} {qty} @ {entry:.2f} (x{leverage})\n"
                message += f"   📊 PnL: {unrealized_pnl:.2f} USDT\n"
                message += f"   🛑 Стоп-лосс: {stop_loss:.2f}\n"
                message += f"   🎯 Тейк-профиты:\n"
                message += f"      TP1: {take_profit_1:.2f}\n"
                message += f"      TP2: {take_profit_2:.2f}\n"
                message += f"      TP3: {take_profit_3:.2f}\n"
                
                # Добавляем информацию о трейлинг-стопе
                if pos.get('trailing_stop'):
                    message += f"   📈 Трейлинг-стоп: {pos['trailing_stop']:.2f}\n"
                
                # Добавляем информацию о частичном закрытии
                if pos.get('partial_closes'):
                    message += f"   🔄 Частичные закрытия:\n"
                    for close in pos['partial_closes']:
                        message += f"      {close['size']} @ {close['price']:.2f}\n"
                
                message += "\n"  # Добавляем пустую строку между позициями
                
            except Exception as e:
                logging.error(f"❌ Ошибка при форматировании позиции: {e}")
                continue
    
    return message if message != "📂 Открытые позиции:\n" else "📂 Нет открытых позиций"

def format_pnl(pnl_data: dict) -> str:
    """Форматирует PnL данные в читаемый текст"""
    try:
        # ✅ Преобразуем значения в float
        daily_pnl = float(pnl_data.get('daily', 0))
        trades = pnl_data.get('trades', [])

        message = f"💰 Дневной PnL: {daily_pnl:.2f} USDT\n\n"

        if trades:
            message += "Последние сделки:\n"
            for trade in trades[:5]:  # ✅ Выводим только последние 5 сделок
                try:
                    symbol = trade.get('symbol', 'Unknown')
                    pnl = float(trade.get('closedPnl', 0))  # 🔥 Преобразуем в float
                    side = trade.get('side', 'Unknown')
                    entry_price = float(trade.get('avgEntryPrice', 0))  # 🔥 Добавляем цену входа
                    exit_price = float(trade.get('avgExitPrice', 0))  # 🔥 Добавляем цену выхода

                    if symbol and pnl is not None:
                        message += f"🔸 {symbol} ({side}):\n"
                        message += f"   PnL: {pnl:.2f} USDT\n"
                        message += f"   Вход: {entry_price:.2f} | Выход: {exit_price:.2f}\n"
                    else:
                        logging.warning(f"⚠️ Пропущена сделка с неполными данными: {trade}")
                except Exception as e:
                    logging.error(f"❌ Ошибка при форматировании сделки: {e}")
                    continue
        else:
            message += "Нет завершенных сделок"

        return message
    except Exception as e:
        logging.error(f"❌ Ошибка при форматировании PnL: {e}")
        return "❌ Ошибка при форматировании PnL"

def format_indicators(indicators: dict) -> str:
    """Форматирует индикаторы в читаемый текст"""
    try:
        message = "📊 Текущие индикаторы:\n\n"
        
        # Цена и VWAP
        message += f"💰 Цена: {indicators['last_close']:.2f}\n"
        message += f"📈 VWAP: {indicators['VWAP']:.2f}\n"
        
        # RSI
        message += f"📊 RSI: {indicators['RSI']:.2f}\n"
        
        # ATR
        message += f"📏 ATR: {indicators['ATR']:.2f}\n"
        
        # SMA
        message += f"📉 SMA20: {indicators['SMA20']:.2f}\n"
        message += f"📉 SMA50: {indicators['SMA50']:.2f}\n"
        
        # Поддержка и сопротивление
        if indicators.get('support'):
            message += f"🛑 Поддержка: {indicators['support']:.2f}\n"
        if indicators.get('resistance'):
            message += f"🎯 Сопротивление: {indicators['resistance']:.2f}\n"
        
        return message
    except Exception as e:
        logging.error(f"❌ Ошибка при форматировании индикаторов: {e}")
        return "❌ Ошибка при форматировании индикаторов"

def get_main_keyboard():
    """Создает основную клавиатуру"""
    keyboard = [
        [InlineKeyboardButton("📊 Индикаторы", callback_data="indicators")],
        [InlineKeyboardButton("📈 Позиции", callback_data="positions")],
        [InlineKeyboardButton("🛑 Стоп-лосс", callback_data="stop_loss")],
        [InlineKeyboardButton("🎯 Тейк-профит", callback_data="take_profit")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("📉 График", callback_data="chart")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton("🔄 Обновить данные", callback_data="refresh_data")]
    ]
    return InlineKeyboardMarkup(keyboard)

@dp.callback_query(F.data == "open_positions")
async def open_positions_handler(callback: types.CallbackQuery):
    """Обработчик кнопки просмотра открытых позиций"""
    try:
        data = read_json_file('positions.json')
        positions = data.get('positions', [])
        message = format_positions(positions)
        if data.get('last_updated'):
            message += f"\n\nОбновлено: {data['last_updated']}"
            
        await callback.message.answer(message)
    except Exception as e:
        logging.error(f"❌ Ошибка при получении позиций: {e}")
        await callback.message.answer("❌ Ошибка при получении позиций")
    finally:
        await callback.answer()

@dp.callback_query(F.data == "pnl_info")
async def pnl_info_handler(callback: types.CallbackQuery):
    """Обработчик кнопки просмотра PnL"""
    try:
        pnl_data = read_json_file('pnl.json')
        message = format_pnl(pnl_data)
        if pnl_data.get('last_updated'):
            message += f"\n\nОбновлено: {pnl_data['last_updated']}"
            
        await callback.message.answer(message)
    except Exception as e:
        logging.error(f"❌ Ошибка при получении PnL: {e}")
        await callback.message.answer("❌ Ошибка при получении PnL")
    finally:
        await callback.answer()

@dp.callback_query(F.data == "refresh_data")
async def refresh_data_callback(callback: types.CallbackQuery):
    """Обработчик обновления всех JSON файлов"""
    try:
        await callback.answer("🔄 Начинаем обновление данных...")
        
        # Обновляем баланс
        balance_info = await bot.api.get_wallet_balance(accountType="UNIFIED")
        if balance_info and "result" in balance_info and "list" in balance_info["result"]:
            wallet = balance_info["result"]["list"][0]
            balance_data = {
                "balance": float(wallet.get("totalWalletBalance", 0)),
                "equity": float(wallet.get("totalEquity", 0)),
                "unrealized_pnl": float(wallet.get("totalUnrealizedPnl", 0)),
                "used_margin": float(wallet.get("totalUsedMargin", 0)),
                "free_margin": float(wallet.get("totalAvailableBalance", 0)),
                "last_updated": datetime.now().isoformat()
            }
            update_json_file("balance.json", balance_data)
            logging.info("✅ balance.json обновлен")

        # Обновляем позиции
        positions = await bot.api.get_positions(category="linear", symbol=SYMBOL)
        if positions and positions.get("result", {}).get("list"):
            positions_data = {"positions": positions["result"]["list"], "last_updated": datetime.now().isoformat()}
            update_json_file("positions.json", positions_data)
            logging.info("✅ positions.json обновлен")

        # Обновляем PnL
        pnl = await bot.api.get_closed_pnl(category="linear", symbol=SYMBOL)
        if pnl and pnl.get("result", {}).get("list"):
            pnl_data = {
                "trades": pnl["result"]["list"],
                "daily_pnl": sum(float(trade["closedPnl"]) for trade in pnl["result"]["list"]),
                "last_updated": datetime.now().isoformat()
            }
            update_json_file("pnl.json", pnl_data)
            logging.info("✅ pnl.json обновлен")

        # Обновляем цену
        price = await bot.api.get_latest_price()
        if price:
            price_data = {"price": price, "last_updated": datetime.now().isoformat()}
            update_json_file("price.json", price_data)
            logging.info("✅ price.json обновлен")

        # Обновляем индикаторы
        indicators = await bot.calculate_indicators()
        if indicators:
            indicators["last_updated"] = datetime.now().isoformat()
            update_json_file("indicators.json", indicators)
            logging.info("✅ indicators.json обновлен")

        # Обновляем сигналы
        signals_data = {
            "refresh_data": False,
            "last_updated": datetime.now().isoformat()
        }
        update_json_file("signals.json", signals_data)
        logging.info("✅ signals.json обновлен")

        await callback.message.edit_text(
            "✅ Все данные успешно обновлены!\n"
            "Последнее обновление: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            reply_markup=get_main_keyboard()
        )
        
    except Exception as e:
        error_msg = f"❌ Ошибка при обновлении данных: {str(e)}"
        logging.error(error_msg)
        await callback.message.edit_text(
            error_msg,
            reply_markup=get_main_keyboard()
        )

@dp.message(CommandStart())
async def send_welcome(message: types.Message):
    """Обработчик команды /start"""
    await message.answer(
        "✅ Бот запущен! Выберите действие:",
        reply_markup=commands_keyboard
    )

@dp.message(F.text == "📊 Статус")
async def status_handler(message: types.Message):
    """Обработчик кнопки статуса"""
    await check_status(message)

@dp.message(F.text == "📂 Позиции")
async def positions_handler(message: types.Message):
    """Обработчик кнопки позиций"""
    try:
        data = read_json_file('positions.json')
        positions = data.get('positions', [])
        message_text = format_positions(positions)
        if data.get('last_updated'):
            message_text += f"\n\nОбновлено: {data['last_updated']}"
            
        await message.answer(message_text)
    except Exception as e:
        logging.error(f"❌ Ошибка при получении позиций: {e}")
        await message.answer("❌ Ошибка при получении позиций")

@dp.message(F.text == "💰 PnL")
async def pnl_handler(message: types.Message):
    """Обработчик кнопки PnL"""
    try:
        pnl_data = read_json_file('pnl.json')
        message_text = format_pnl(pnl_data)
        if pnl_data.get('last_updated'):
            message_text += f"\n\nОбновлено: {pnl_data['last_updated']}"
            
        await message.answer(message_text)
    except Exception as e:
        logging.error(f"❌ Ошибка при получении PnL: {e}")
        await message.answer("❌ Ошибка при получении PnL")

@dp.message(F.text == "📊 Индикаторы")
async def indicators_handler(message: types.Message):
    """Обработчик кнопки индикаторов"""
    try:
        indicators = read_json_file('indicators.json')
        message_text = format_indicators(indicators)
        if indicators.get('last_updated'):
            message_text += f"\n\nОбновлено: {indicators['last_updated']}"
            
        await message.answer(message_text)
    except Exception as e:
        logging.error(f"❌ Ошибка при получении индикаторов: {e}")
        await message.answer("❌ Ошибка при получении индикаторов")

@dp.message(F.text == "🔄 Обновить")
async def refresh_handler(message: types.Message):
    """Обработчик кнопки обновления"""
    try:
        signals = {"refresh_data": True, "last_signal": datetime.now().isoformat()}
        if write_json_file('signals.json', signals):
            await message.answer("🔄 Запрос на обновление данных отправлен")
        else:
            await message.answer("❌ Ошибка при отправке запроса на обновление")
    except Exception as e:
        logging.error(f"❌ Ошибка при обновлении данных: {e}")
        await message.answer("❌ Ошибка при обновлении данных")

@dp.message(F.text == "🛒 Купить")
async def buy_handler(message: types.Message):
    """Обработчик кнопки покупки"""
    try:
        # Получаем текущие индикаторы
        indicators = read_json_file('indicators.json')
        if not indicators:
            await message.answer("❌ Нет данных индикаторов. Нажмите /Обновить и попробуйте снова.")
            return
            
        # Проверяем условия для входа
        if indicators.get('RSI', 0) > 65:
            await message.answer("❌ RSI слишком высокий (>65). Не рекомендуется входить в длинную позицию.")
            return
            
        if indicators.get('last_close', 0) < indicators.get('VWAP', 0):
            await message.answer("❌ Цена ниже VWAP. Не рекомендуется входить в длинную позицию.")
            return
            
        # Создаем сигнал на покупку
        signal_data = {
            "force_trade": True,
            "side": "Buy",
            "price": indicators['last_close'],
            "last_signal": datetime.now().isoformat()
        }
        
        if write_json_file('signals.json', signal_data):
            await message.answer(f"✅ Сигнал на покупку отправлен по цене {indicators['last_close']:.2f}")
        else:
            await message.answer("❌ Ошибка при отправке сигнала")
            
    except Exception as e:
        logging.error(f"❌ Ошибка при обработке сигнала покупки: {e}")
        await message.answer("❌ Произошла ошибка при обработке сигнала")

@dp.message(F.text == "📉 Продать")
async def sell_handler(message: types.Message):
    """Обработчик кнопки продажи"""
    try:
        # Получаем текущие индикаторы
        indicators = read_json_file('indicators.json')
        if not indicators:
            await message.answer("❌ Нет данных индикаторов. Нажмите /Обновить и попробуйте снова.")
            return
            
        # Проверяем условия для входа
        if indicators.get('RSI', 0) < 35:
            await message.answer("❌ RSI слишком низкий (<35). Не рекомендуется входить в короткую позицию.")
            return
            
        if indicators.get('last_close', 0) > indicators.get('VWAP', 0):
            await message.answer("❌ Цена выше VWAP. Не рекомендуется входить в короткую позицию.")
            return
            
        # Создаем сигнал на продажу
        signal_data = {
            "force_trade": True,
            "side": "Sell",
            "price": indicators['last_close'],
            "last_signal": datetime.now().isoformat()
        }
        
        if write_json_file('signals.json', signal_data):
            await message.answer(f"✅ Сигнал на продажу отправлен по цене {indicators['last_close']:.2f}")
        else:
            await message.answer("❌ Ошибка при отправке сигнала")
            
    except Exception as e:
        logging.error(f"❌ Ошибка при обработке сигнала продажи: {e}")
        await message.answer("❌ Произошла ошибка при обработке сигнала")

@dp.message(Command("status"))
async def check_status(message: types.Message):
    """Проверка статуса аккаунта"""
    try:
        balance_data = read_json_file('balance.json')
        if not balance_data:
            await message.answer("❌ Нет данных о балансе. Попробуйте обновить данные.")
            return

        balance = balance_data.get('balance', 0)
        used_margin = balance_data.get('used_margin', 0)
        free_margin = balance_data.get('free_margin', 0)
        last_updated = balance_data.get('last_updated', 'Неизвестно')

        status_message = f"""
        📊 Статус аккаунта:
        💰 Баланс: {balance:.2f} USDT
        🔒 Использованная маржа: {used_margin:.2f} USDT
        💵 Свободная маржа: {free_margin:.2f} USDT
        ⏰ Обновлено: {last_updated}
        """
        await message.answer(status_message)
    except Exception as e:
        logging.error(f"❌ Ошибка при проверке статуса: {e}")
        await message.answer("❌ Ошибка при проверке статуса")

@dp.message(Command("buy"))
async def force_buy(message: types.Message):
    """Принудительное открытие длинной позиции"""
    try:
        price_data = read_json_file('price.json')
        current_price = float(price_data.get('price', 0))

        if current_price <= 0:
            await message.answer("❌ Ошибка: нет данных о цене, попробуйте обновить данные.")
            return

        stop_loss = current_price * 0.995
        take_profit_1 = current_price * 1.005
        take_profit_2 = current_price * 1.01
        take_profit_3 = current_price * 1.015

        signal = {
            "force_trade": True,
            "side": "Buy",
            "price": current_price,
            "stop_loss": stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
            "take_profit_3": take_profit_3,
            "qty": 0.001
        }

        if write_json_file('signals.json', signal):
            await message.answer(f"✅ Сигнал на покупку отправлен: {current_price:.2f}")
        else:
            await message.answer("❌ Ошибка при отправке сигнала")

    except Exception as e:
        logging.error(f"❌ Ошибка при открытии сделки: {e}")
        await message.answer("❌ Ошибка при открытии сделки")

@dp.message(Command("sell"))
async def force_sell(message: types.Message):
    """Принудительное открытие короткой позиции"""
    try:
        price_data = read_json_file('price.json')
        current_price = float(price_data.get('price', 0))

        if current_price <= 0:
            await message.answer("❌ Ошибка: нет данных о цене, попробуйте обновить данные.")
            return

        stop_loss = current_price * 1.005
        take_profit_1 = current_price * 0.995
        take_profit_2 = current_price * 0.99
        take_profit_3 = current_price * 0.985

        signal = {
            "force_trade": True,
            "side": "Sell",
            "price": current_price,
            "stop_loss": stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
            "take_profit_3": take_profit_3,
            "qty": 0.001
        }

        if write_json_file('signals.json', signal):
            await message.answer(f"✅ Сигнал на продажу отправлен: {current_price:.2f}")
        else:
            await message.answer("❌ Ошибка при отправке сигнала")

    except Exception as e:
        logging.error(f"❌ Ошибка при открытии сделки: {e}")
        await message.answer("❌ Ошибка при открытии сделки")

@dp.callback_query(F.data.startswith('sl_'))
async def stop_loss_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для стоп-лосса"""
    try:
        symbol = callback.data.split('_')[1]
        positions = bot.trading_bot.get_positions()
        
        # Находим позицию по символу
        position = None
        for pos in positions:
            if pos.get('symbol') == symbol:
                position = pos
                break
                
        if not position:
            await callback.answer("❌ Позиция не найдена")
            return
            
        # Получаем текущий ATR
        indicators = bot.trading_bot.calculate_indicators()
        if not indicators:
            await callback.answer("❌ Не удалось получить ATR")
            return
            
        atr = indicators['ATR']
        current_price = indicators['last_close']
        current_sl = float(position.get('stopLoss', 0))
        
        # Создаем клавиатуру с вариантами стоп-лосса
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            f"0.5 ATR ({current_price - 0.5 * atr:.2f})",
            callback_data=f"set_sl_{symbol}_0.5"
        ))
        keyboard.add(types.InlineKeyboardButton(
            f"1.0 ATR ({current_price - atr:.2f})",
            callback_data=f"set_sl_{symbol}_1.0"
        ))
        keyboard.add(types.InlineKeyboardButton(
            f"1.5 ATR ({current_price - 1.5 * atr:.2f})",
            callback_data=f"set_sl_{symbol}_1.5"
        ))
        keyboard.add(types.InlineKeyboardButton(
            f"2.0 ATR ({current_price - 2.0 * atr:.2f})",
            callback_data=f"set_sl_{symbol}_2.0"
        ))
        
        await callback.message.edit_text(
            f"🛑 Выберите новый стоп-лосс для {symbol}:\n"
            f"Текущий SL: {current_sl:.2f}\n"
            f"ATR: {atr:.2f}",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"❌ Ошибка в stop_loss_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('set_sl_'))
async def set_stop_loss_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для установки стоп-лосса"""
    try:
        _, _, symbol, atr_multiplier = callback.data.split('_')
        atr_multiplier = float(atr_multiplier)
        
        # Получаем текущие индикаторы
        indicators = bot.trading_bot.calculate_indicators()
        if not indicators:
            await callback.answer("❌ Не удалось получить ATR")
            return
            
        atr = indicators['ATR']
        current_price = indicators['last_close']
        
        # Рассчитываем новый стоп-лосс
        if current_price > 0:
            new_sl = current_price - atr_multiplier * atr
        else:
            new_sl = current_price + atr_multiplier * atr
            
        # Устанавливаем новый стоп-лосс
        success = bot.trading_bot.set_stop_loss(symbol, new_sl)
        
        if success:
            await callback.answer(f"✅ Стоп-лосс установлен на {new_sl:.2f}")
        else:
            await callback.answer("❌ Не удалось установить стоп-лосс")
            
    except Exception as e:
        logging.error(f"❌ Ошибка в set_stop_loss_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('tp'))
async def take_profit_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для тейк-профита"""
    try:
        tp_type, symbol = callback.data.split('_')
        positions = bot.trading_bot.get_positions()
        
        # Находим позицию по символу
        position = None
        for pos in positions:
            if pos.get('symbol') == symbol:
                position = pos
                break
                
        if not position:
            await callback.answer("❌ Позиция не найдена")
            return
            
        # Получаем текущий ATR
        indicators = bot.trading_bot.calculate_indicators()
        if not indicators:
            await callback.answer("❌ Не удалось получить ATR")
            return
            
        atr = indicators['ATR']
        current_price = indicators['last_close']
        current_tp = float(position.get(f'takeProfit{tp_type[2]}', 0))
        
        # Создаем клавиатуру с вариантами тейк-профита
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            f"1.0 ATR ({current_price + atr:.2f})",
            callback_data=f"set_tp_{symbol}_{tp_type[2]}_1.0"
        ))
        keyboard.add(types.InlineKeyboardButton(
            f"1.5 ATR ({current_price + 1.5 * atr:.2f})",
            callback_data=f"set_tp_{symbol}_{tp_type[2]}_1.5"
        ))
        keyboard.add(types.InlineKeyboardButton(
            f"2.0 ATR ({current_price + 2.0 * atr:.2f})",
            callback_data=f"set_tp_{symbol}_{tp_type[2]}_2.0"
        ))
        keyboard.add(types.InlineKeyboardButton(
            f"2.5 ATR ({current_price + 2.5 * atr:.2f})",
            callback_data=f"set_tp_{symbol}_{tp_type[2]}_2.5"
        ))
        
        await callback.message.edit_text(
            f"🎯 Выберите новый тейк-профит {tp_type[2]} для {symbol}:\n"
            f"Текущий TP{tp_type[2]}: {current_tp:.2f}\n"
            f"ATR: {atr:.2f}",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"❌ Ошибка в take_profit_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('set_tp_'))
async def set_take_profit_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для установки тейк-профита"""
    try:
        _, _, symbol, tp_number, atr_multiplier = callback.data.split('_')
        atr_multiplier = float(atr_multiplier)
        
        # Получаем текущие индикаторы
        indicators = bot.trading_bot.calculate_indicators()
        if not indicators:
            await callback.answer("❌ Не удалось получить ATR")
            return
            
        atr = indicators['ATR']
        current_price = indicators['last_close']
        
        # Рассчитываем новый тейк-профит
        if current_price > 0:
            new_tp = current_price + atr_multiplier * atr
        else:
            new_tp = current_price - atr_multiplier * atr
            
        # Устанавливаем новый тейк-профит
        success = bot.trading_bot.set_take_profit(symbol, tp_number, new_tp)
        
        if success:
            await callback.answer(f"✅ Тейк-профит {tp_number} установлен на {new_tp:.2f}")
        else:
            await callback.answer("❌ Не удалось установить тейк-профит")
            
    except Exception as e:
        logging.error(f"❌ Ошибка в set_take_profit_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('settings_'))
async def settings_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для настроек"""
    try:
        setting_type = callback.data.split('_')[1]
        
        if setting_type == 'notifications':
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(types.InlineKeyboardButton(
                "🔔 Включить уведомления",
                callback_data="toggle_notifications_on"
            ))
            keyboard.add(types.InlineKeyboardButton(
                "🔕 Выключить уведомления",
                callback_data="toggle_notifications_off"
            ))
            
            await callback.message.edit_text(
                "🔔 Настройки уведомлений:\n\n"
                "• Уведомления о входе в позицию\n"
                "• Уведомления о закрытии позиции\n"
                "• Уведомления о частичном закрытии\n"
                "• Уведомления о движении стоп-лосса\n"
                "• Уведомления об ошибках",
                reply_markup=keyboard
            )
            
        elif setting_type == 'indicators':
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(types.InlineKeyboardButton(
                "📊 Настройка RSI",
                callback_data="settings_rsi"
            ))
            keyboard.add(types.InlineKeyboardButton(
                "📈 Настройка ATR",
                callback_data="settings_atr"
            ))
            keyboard.add(types.InlineKeyboardButton(
                "📉 Настройка SMA",
                callback_data="settings_sma"
            ))
            
            await callback.message.edit_text(
                "📊 Настройки индикаторов:\n\n"
                "• Период RSI (по умолчанию: 14)\n"
                "• Период ATR (по умолчанию: 14)\n"
                "• Периоды SMA (по умолчанию: 20, 50)\n"
                "• Уровни поддержки и сопротивления",
                reply_markup=keyboard
            )
            
        elif setting_type == 'risk':
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(types.InlineKeyboardButton(
                "💰 Размер позиции",
                callback_data="settings_position_size"
            ))
            keyboard.add(types.InlineKeyboardButton(
                "🛑 Стоп-лосс",
                callback_data="settings_stop_loss"
            ))
            keyboard.add(types.InlineKeyboardButton(
                "🎯 Тейк-профит",
                callback_data="settings_take_profit"
            ))
            
            await callback.message.edit_text(
                "💰 Настройки риск-менеджмента:\n\n"
                "• Размер позиции (% от баланса)\n"
                "• Множитель стоп-лосса (ATR)\n"
                "• Множители тейк-профита (ATR)\n"
                "• Трейлинг-стоп",
                reply_markup=keyboard
            )
            
    except Exception as e:
        logging.error(f"❌ Ошибка в settings_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('toggle_notifications_'))
async def toggle_notifications_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для включения/выключения уведомлений"""
    try:
        state = callback.data.split('_')[2]
        success = bot.trading_bot.toggle_notifications(state == 'on')
        
        if success:
            status = "включены" if state == 'on' else "выключены"
            await callback.answer(f"✅ Уведомления {status}")
        else:
            await callback.answer("❌ Не удалось изменить настройки уведомлений")
            
    except Exception as e:
        logging.error(f"❌ Ошибка в toggle_notifications_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('settings_rsi'))
async def rsi_settings_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для настройки RSI"""
    try:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            "10",
            callback_data="set_rsi_10"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "14",
            callback_data="set_rsi_14"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "21",
            callback_data="set_rsi_21"
        ))
        
        await callback.message.edit_text(
            "📊 Выберите период RSI:\n\n"
            "• 10 - более чувствительный\n"
            "• 14 - стандартный\n"
            "• 21 - менее чувствительный",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"❌ Ошибка в rsi_settings_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('set_rsi_'))
async def set_rsi_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для установки периода RSI"""
    try:
        period = int(callback.data.split('_')[2])
        success = bot.trading_bot.set_rsi_period(period)
        
        if success:
            await callback.answer(f"✅ Период RSI установлен на {period}")
        else:
            await callback.answer("❌ Не удалось установить период RSI")
            
    except Exception as e:
        logging.error(f"❌ Ошибка в set_rsi_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('settings_atr'))
async def atr_settings_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для настройки ATR"""
    try:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            "10",
            callback_data="set_atr_10"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "14",
            callback_data="set_atr_14"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "21",
            callback_data="set_atr_21"
        ))
        
        await callback.message.edit_text(
            "📈 Выберите период ATR:\n\n"
            "• 10 - более чувствительный\n"
            "• 14 - стандартный\n"
            "• 21 - менее чувствительный",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"❌ Ошибка в atr_settings_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('set_atr_'))
async def set_atr_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для установки периода ATR"""
    try:
        period = int(callback.data.split('_')[2])
        success = bot.trading_bot.set_atr_period(period)
        
        if success:
            await callback.answer(f"✅ Период ATR установлен на {period}")
        else:
            await callback.answer("❌ Не удалось установить период ATR")
            
    except Exception as e:
        logging.error(f"❌ Ошибка в set_atr_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('settings_sma'))
async def sma_settings_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для настройки SMA"""
    try:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            "SMA20",
            callback_data="set_sma_20"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "SMA50",
            callback_data="set_sma_50"
        ))
        
        await callback.message.edit_text(
            "📉 Выберите период SMA для настройки:\n\n"
            "• SMA20 - короткий период\n"
            "• SMA50 - длинный период",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"❌ Ошибка в sma_settings_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('set_sma_'))
async def set_sma_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для установки периода SMA"""
    try:
        period = int(callback.data.split('_')[2])
        success = bot.trading_bot.set_sma_period(period)
        
        if success:
            await callback.answer(f"✅ Период SMA установлен на {period}")
        else:
            await callback.answer("❌ Не удалось установить период SMA")
            
    except Exception as e:
        logging.error(f"❌ Ошибка в set_sma_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('settings_position_size'))
async def position_size_settings_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для настройки размера позиции"""
    try:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            "0.5%",
            callback_data="set_position_size_0.5"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "1.0%",
            callback_data="set_position_size_1.0"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "2.0%",
            callback_data="set_position_size_2.0"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "5.0%",
            callback_data="set_position_size_5.0"
        ))
        
        await callback.message.edit_text(
            "💰 Выберите размер позиции (% от баланса):\n\n"
            "• 0.5% - консервативный\n"
            "• 1.0% - стандартный\n"
            "• 2.0% - агрессивный\n"
            "• 5.0% - очень агрессивный",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"❌ Ошибка в position_size_settings_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('set_position_size_'))
async def set_position_size_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для установки размера позиции"""
    try:
        size = float(callback.data.split('_')[3])
        success = bot.trading_bot.set_position_size(size)
        
        if success:
            await callback.answer(f"✅ Размер позиции установлен на {size}%")
        else:
            await callback.answer("❌ Не удалось установить размер позиции")
            
    except Exception as e:
        logging.error(f"❌ Ошибка в set_position_size_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('settings_stop_loss'))
async def stop_loss_settings_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для настройки стоп-лосса"""
    try:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            "0.5 ATR",
            callback_data="set_stop_loss_0.5"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "1.0 ATR",
            callback_data="set_stop_loss_1.0"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "1.5 ATR",
            callback_data="set_stop_loss_1.5"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "2.0 ATR",
            callback_data="set_stop_loss_2.0"
        ))
        
        await callback.message.edit_text(
            "🛑 Выберите множитель стоп-лосса (ATR):\n\n"
            "• 0.5 ATR - тесный стоп\n"
            "• 1.0 ATR - стандартный\n"
            "• 1.5 ATR - широкий стоп\n"
            "• 2.0 ATR - очень широкий стоп",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"❌ Ошибка в stop_loss_settings_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('set_stop_loss_'))
async def set_stop_loss_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для установки множителя стоп-лосса"""
    try:
        multiplier = float(callback.data.split('_')[3])
        success = bot.trading_bot.set_stop_loss_multiplier(multiplier)
        
        if success:
            await callback.answer(f"✅ Множитель стоп-лосса установлен на {multiplier} ATR")
        else:
            await callback.answer("❌ Не удалось установить множитель стоп-лосса")
            
    except Exception as e:
        logging.error(f"❌ Ошибка в set_stop_loss_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('settings_take_profit'))
async def take_profit_settings_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для настройки тейк-профита"""
    try:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            "TP1",
            callback_data="set_tp1"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "TP2",
            callback_data="set_tp2"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "TP3",
            callback_data="set_tp3"
        ))
        
        await callback.message.edit_text(
            "🎯 Выберите тейк-профит для настройки:\n\n"
            "• TP1 - первый тейк-профит\n"
            "• TP2 - второй тейк-профит\n"
            "• TP3 - третий тейк-профит",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"❌ Ошибка в take_profit_settings_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('set_tp'))
async def set_take_profit_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для установки множителя тейк-профита"""
    try:
        tp_number = callback.data.split('_')[1]
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            "1.0 ATR",
            callback_data=f"set_tp_multiplier_{tp_number}_1.0"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "1.5 ATR",
            callback_data=f"set_tp_multiplier_{tp_number}_1.5"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "2.0 ATR",
            callback_data=f"set_tp_multiplier_{tp_number}_2.0"
        ))
        keyboard.add(types.InlineKeyboardButton(
            "2.5 ATR",
            callback_data=f"set_tp_multiplier_{tp_number}_2.5"
        ))
        
        await callback.message.edit_text(
            f"🎯 Выберите множитель для TP{tp_number} (ATR):\n\n"
            "• 1.0 ATR - тесный тейк\n"
            "• 1.5 ATR - стандартный\n"
            "• 2.0 ATR - широкий тейк\n"
            "• 2.5 ATR - очень широкий тейк",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"❌ Ошибка в set_take_profit_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

@dp.callback_query(F.data.startswith('set_tp_multiplier_'))
async def set_tp_multiplier_callback(callback: types.CallbackQuery):
    """Обработчик callback-запросов для установки множителя тейк-профита"""
    try:
        _, _, tp_number, multiplier = callback.data.split('_')
        multiplier = float(multiplier)
        success = bot.trading_bot.set_take_profit_multiplier(tp_number, multiplier)
        
        if success:
            await callback.answer(f"✅ Множитель TP{tp_number} установлен на {multiplier} ATR")
        else:
            await callback.answer(f"❌ Не удалось установить множитель TP{tp_number}")
            
    except Exception as e:
        logging.error(f"❌ Ошибка в set_tp_multiplier_callback: {e}")
        await callback.answer("❌ Произошла ошибка")

async def main():
    """Главная функция запуска бота"""
    logging.info("🚀 Запуск Telegram бота...")
    
    try:
        # Отправляем стартовое сообщение
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="✅ Бот перезапущен! Выберите действие:",
            reply_markup=commands_keyboard
        )
        
        # Запускаем поллинг
        await dp.start_polling(bot)
        
    except Exception as e:
        logging.error(f"❌ Ошибка при запуске бота: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
