import aiohttp
import asyncio
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes
from colorama import init, Fore

# === Инициализация colorama ===
init(autoreset=True)

# === Настройки по умолчанию ===
TELEGRAM_TOKEN = "8341770970:AAGGlKJIu7LKmNVyCQ4ZRZoLv0oczjyv0KM"
CHAT_ID = "775111334"

TRIGGER_TIMEFRAME = "1m"
TRIGGER_THRESHOLD = 2.0
UPDATE_INTERVAL = 30
MIN_VOLUME_24H = 10000
MONITORING_JOB = None

WINDOWS = {
    "1m": 60,
    "5m": 5*60,
    "15m": 15*60,
    "1h": 60*60,
    "4h": 4*60*60,
    "24h": 24*60*60
}

price_history = {}
volume_24h = {}

# === Telegram клавиатура ===
def build_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"ТФ: {TRIGGER_TIMEFRAME}", callback_data="timeframe"),
         InlineKeyboardButton(f"Порог: {TRIGGER_THRESHOLD}%", callback_data="threshold")],
        [InlineKeyboardButton(f"Интервал: {UPDATE_INTERVAL}s", callback_data="interval"),
         InlineKeyboardButton(f"Мин. объем: {MIN_VOLUME_24H}", callback_data="minvol")],
        [InlineKeyboardButton("🚀 Старт", callback_data="start_monitor"),
         InlineKeyboardButton("⏹ Стоп", callback_data="stop_monitor")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Привет! 👋\n"
        "Я MEXC Price Watcher Bot.\n"
        "Настрой параметры с помощью кнопок ниже и запускай мониторинг."
    )
    await update.message.reply_text(msg, reply_markup=build_keyboard())

# === Форматирование цены ===
def format_price(price):
    if price >= 1:
        return f"{price:,.2f}"
    else:
        return f"{price:.8f}".rstrip('0').rstrip('.')  # убираем лишние нули

# === Обработка нажатий ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TRIGGER_TIMEFRAME, TRIGGER_THRESHOLD, UPDATE_INTERVAL, MIN_VOLUME_24H, MONITORING_JOB
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "timeframe":
        options = ["1m","5m","15m","1h","4h","24h"]
        idx = options.index(TRIGGER_TIMEFRAME)
        TRIGGER_TIMEFRAME = options[(idx + 1) % len(options)]
        await query.edit_message_text(f"Таймфрейм: {TRIGGER_TIMEFRAME}", reply_markup=build_keyboard())

    elif data == "threshold":
        options = [1, 2, 5, 10]
        idx = options.index(int(TRIGGER_THRESHOLD))
        TRIGGER_THRESHOLD = options[(idx + 1) % len(options)]
        await query.edit_message_text(f"Порог изменения цены: {TRIGGER_THRESHOLD}%", reply_markup=build_keyboard())

    elif data == "interval":
        options = [10, 30, 60]
        idx = options.index(UPDATE_INTERVAL)
        UPDATE_INTERVAL = options[(idx + 1) % len(options)]
        await query.edit_message_text(f"Интервал обновления: {UPDATE_INTERVAL}s", reply_markup=build_keyboard())

    elif data == "minvol":
        options = [10000, 50000, 100000]
        idx = options.index(MIN_VOLUME_24H)
        MIN_VOLUME_24H = options[(idx + 1) % len(options)]
        await query.edit_message_text(f"Мин. объем 24ч: ${MIN_VOLUME_24H}", reply_markup=build_keyboard())

    elif data == "start_monitor":
        if MONITORING_JOB is None:
            MONITORING_JOB = context.job_queue.run_repeating(monitor_job, interval=UPDATE_INTERVAL, first=0)
            await query.edit_message_text("🚀 Мониторинг запущен!", reply_markup=build_keyboard())
        else:
            await query.edit_message_text("Мониторинг уже запущен.", reply_markup=build_keyboard())

    elif data == "stop_monitor":
        if MONITORING_JOB:
            MONITORING_JOB.schedule_removal()
            MONITORING_JOB = None
            await query.edit_message_text("⏹ Мониторинг остановлен.", reply_markup=build_keyboard())
        else:
            await query.edit_message_text("Мониторинг не запущен.", reply_markup=build_keyboard())

# === Price Watcher функции ===
async def get_usdt_pairs():
    url = "https://api.mexc.com/api/v3/ticker/24hr"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            data = await r.json()
            pairs = []
            for x in data:
                if x["symbol"].endswith("USDT") and float(x["lastPrice"]) > 0 and float(x["quoteVolume"]) > 0:
                    vol = float(x["quoteVolume"])
                    if vol >= MIN_VOLUME_24H:
                        pairs.append(x["symbol"])
                        volume_24h[x["symbol"]] = vol
            return pairs

async def get_prices(session):
    url = "https://api.mexc.com/api/v3/ticker/price"
    async with session.get(url) as r:
        data = await r.json()
        return {x["symbol"]: float(x["price"]) for x in data if x["symbol"].endswith("USDT") and float(x["price"]) > 0}

async def get_recent_trades(symbol, limit=500):
    url = f"https://api.mexc.com/api/v3/trades?symbol={symbol}&limit={limit}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            trades = []
            for t in data:
                trades.append({
                    "price": float(t["price"]),
                    "qty": float(t["qty"]),
                    "isBuyerMaker": t["isBuyerMaker"]
                })
            return trades

def calculate_volume(trades):
    buy_volume = sum(t["qty"] * t["price"] for t in trades if not t["isBuyerMaker"])
    sell_volume = sum(t["qty"] * t["price"] for t in trades if t["isBuyerMaker"])
    return buy_volume, sell_volume

def calculate_change(symbol, current_time, current_price):
    if symbol not in price_history:
        price_history[symbol] = []
    price_history[symbol].append((current_time, current_price))
    price_history[symbol] = [(t, p) for t, p in price_history[symbol] if current_time - t <= WINDOWS["24h"]]

    changes = {}
    for label, seconds in WINDOWS.items():
        old_prices = [p for (t, p) in price_history[symbol] if current_time - t >= seconds]
        if old_prices:
            old_price = old_prices[-1]
            if old_price > 0:
                changes[label] = round((current_price - old_price) / old_price * 100, 2)
    return changes

# === Мониторинг Job ===
async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    pairs = await get_usdt_pairs()
    async with aiohttp.ClientSession() as session:
        prices = await get_prices(session)
        current_time = time.time()
        active_pairs = [s for s in pairs if s in prices and prices[s] > 0]

        for symbol in active_pairs:
            current_price = prices[symbol]

            # --- Автообновление сообщений без спама ---
            last_price = price_history.get(symbol, [(0, 0)])[-1][1] if symbol in price_history else 0
            if current_price == last_price:
                continue

            changes = calculate_change(symbol, current_time, current_price)
            if TRIGGER_TIMEFRAME not in changes:
                continue

            trigger_change = changes[TRIGGER_TIMEFRAME]
            if abs(trigger_change) >= TRIGGER_THRESHOLD:
                trades = await get_recent_trades(symbol)
                buy_vol, sell_vol = calculate_volume(trades)
                vol_24h = volume_24h.get(symbol, 0)
                direction = "🚀" if trigger_change > 0 else "💣"
                msg = (
                    f"{direction} {symbol}: {trigger_change:+.2f}% за {TRIGGER_TIMEFRAME}\n"
                    f"💰 Цена: {format_price(price_history[symbol][0][1])} → {format_price(current_price)}\n"
                    f"📊 Объём {TRIGGER_TIMEFRAME}: Покупки=${buy_vol:,.0f} | Продажи=${sell_vol:,.0f} | Объём 24h=${vol_24h:,.0f}"
                )
                await context.bot.send_message(chat_id=CHAT_ID, text=msg)

# === Запуск бота ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()
