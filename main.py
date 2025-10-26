import asyncio
import aiohttp
import json
import time
import pandas as pd
import numpy as np
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.filters import Command

API_TOKEN = "7791300927:AAEkaDIU95ETWTMYnsGKvF855yn6ZZ15FJs"

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- Настройки стратегии (по умолчанию) ---
HMA_LEN = 50
RSI_LEN = 14
RSI_BUY = 55
RSI_SELL = 45
VOL_MULT = 1.2
INTERVAL = "1m"

# --- Индикаторы ---
def HMA(series, length=HMA_LEN):
    half = int(length / 2)
    sqrt_len = int(np.sqrt(length))
    wma1 = series.rolling(half).mean()
    wma2 = series.rolling(length).mean()
    hma = (2 * wma1 - wma2).rolling(sqrt_len).mean()
    return hma

def RSI(series, length=RSI_LEN):
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(length).mean()
    avg_loss = pd.Series(loss).rolling(length).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# --- Получаем все USDT фьючерсные пары ---
async def get_all_symbols():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://fapi.binance.com/fapi/v1/exchangeInfo") as resp:
            info = await resp.json()
    symbols = [s['symbol'] for s in info['symbols'] if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING']
    return symbols

# --- Получаем свечи ---
async def get_klines(session, symbol, interval=INTERVAL, limit=200):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    async with session.get(url, timeout=10) as resp:
        data = await resp.json()
    df = pd.DataFrame(data, columns=['open_time','open','high','low','close','volume','close_time','qav','trades','tb_base','tb_quote','ignore'])
    df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
    return df

# --- Анализируем сигнал ---
def analyze(df):
    df['hma'] = HMA(df['close'])
    df['rsi'] = RSI(df['close'])
    df['avg_vol'] = df['volume'].rolling(20).mean()
    df['trend'] = np.where(df['close'] > df['hma'], 1, -1)
    df['vol_ok'] = df['volume'] > df['avg_vol'] * VOL_MULT

    buy = (df['trend'] == 1) & (df['trend'].shift() == -1) & (df['rsi'] > RSI_BUY) & df['vol_ok']
    sell = (df['trend'] == -1) & (df['trend'].shift() == 1) & (df['rsi'] < RSI_SELL) & df['vol_ok']

    if buy.iloc[-1]:
        return "BUY"
    elif sell.iloc[-1]:
        return "SELL"
    else:
        return None

# --- Реальный поток WebSocket ---
async def stream_signals(chat_id):
    symbols = await get_all_symbols()
    streams = "/".join([f"{s.lower()}@kline_{INTERVAL}" for s in symbols])
    url = f"wss://fstream.binance.com/stream?streams={streams}"

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            cache = {}
            async for msg in ws:
                data = json.loads(msg.data)
                if 'data' not in data: 
                    continue
                k = data['data']['k']
                if not k['x']:  # если свеча не закрыта
                    continue

                symbol = k['s']
                close = float(k['c'])
                close_time = datetime.fromtimestamp(k['T']/1000).strftime('%Y-%m-%d %H:%M:%S')

                # Пересчет раз в минуту на последней свече
                if symbol not in cache or time.time() - cache[symbol]['ts'] > 60:
                    try:
                        df = await get_klines(session, symbol)
                        signal = analyze(df)
                        if signal:
                            await bot.send_message(chat_id, f"{'🟢' if signal=='BUY' else '🔴'} {symbol} | {signal} | Цена: {close:.2f} | Время: {close_time}")
                        cache[symbol] = {'ts': time.time()}
                    except:
                        pass

# --- Кнопки Telegram ---
def get_main_keyboard():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton("M15"), KeyboardButton("H1"), KeyboardButton("H4")],
        [KeyboardButton("HMA+"), KeyboardButton("RSI+"), KeyboardButton("Vol+")],
        [KeyboardButton("Старт 🚀")]
    ], resize_keyboard=True)
    return kb

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Выберите таймфрейм и настройки:", reply_markup=get_main_keyboard())

@dp.message()
async def main_handler(message: types.Message):
    global INTERVAL
    if message.text in ["M15","H1","H4"]:
        INTERVAL = message.text.lower()
        await message.answer(f"Таймфрейм установлен: {INTERVAL}")
    elif message.text == "Старт 🚀":
        await message.answer("Запуск анализа топовых фьючерсных пар...")
        asyncio.create_task(stream_signals(message.chat.id))

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    asyncio.run(dp.start_polling(bot))
