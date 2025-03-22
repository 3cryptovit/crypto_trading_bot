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
    [InlineKeyboardButton(text="📉 Продать", callback_data="sell")]
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
            types.KeyboardButton(text="🔄 Обновить")
        ],
        [
            types.KeyboardButton(text="🛒 Купить"),
            types.KeyboardButton(text="📉 Продать")
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

def format_positions(positions: list) -> str:
    """Форматирует список позиций в читаемый текст"""
    if not positions:
        return "📂 Нет открытых позиций"
    
    message = "📂 Открытые позиции:\n"
    for pos in positions:
        try:
            # Безопасное получение значений с преобразованием типов
            symbol = pos.get('symbol', 'Unknown')
            side = pos.get('side', 'Unknown')
            qty = float(pos.get('size', 0))  # 🔥 Преобразуем в float
            entry = float(pos.get('avgPrice', 0))  # 🔥 Исправлено на avgPrice
            leverage = int(pos.get('leverage', 1))  # 🔥 Преобразуем в int
            unrealized_pnl = float(pos.get('unrealisedPnl', 0))  # 🔥 Добавляем unrealized PnL
            
            # Пропускаем пустые позиции
            if qty == 0 or not side:
                logging.warning(f"⚠️ Пропущена пустая позиция: {pos}")
                continue
            
            message += f"🔹 {symbol}: {side} {qty} @ {entry:.2f} (x{leverage})\n"
            message += f"   📊 PnL: {unrealized_pnl:.2f} USDT\n"
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
async def refresh_data_handler(callback: types.CallbackQuery):
    """Обработчик кнопки обновления данных"""
    try:
        signals = {"refresh_data": True, "last_signal": datetime.now().isoformat()}
        if write_json_file('signals.json', signals):
            await callback.message.answer("🔄 Запрос на обновление данных отправлен")
        else:
            await callback.message.answer("❌ Ошибка при отправке запроса на обновление")
    except Exception as e:
        logging.error(f"❌ Ошибка при обновлении данных: {e}")
        await callback.message.answer("❌ Ошибка при обновлении данных")
    finally:
        await callback.answer()

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
    await force_buy(message)

@dp.message(F.text == "📉 Продать")
async def sell_handler(message: types.Message):
    """Обработчик кнопки продажи"""
    await force_sell(message)

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
