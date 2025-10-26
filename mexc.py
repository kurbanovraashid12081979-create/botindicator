import ccxt
import pandas as pd
import pandas_ta as ta
import time

# --------- Настройки ---------
symbol = 'BTC/USDT'
timeframe = '5m'
limit = 500

# --------- Функции для индикаторов ---------

def calculate_ut_bot(df, key_value=1, atr_period=10):
    atr = ta.atr(df['high'], df['low'], df['close'], length=atr_period)
    nLoss = key_value * atr
    ts = [df['close'][0]]

    pos = [0]
    for i in range(1, len(df)):
        prev_ts = ts[-1]
        src = df['close'][i]
        prev_src = df['close'][i-1]
        if src > prev_ts and prev_src > prev_ts:
            new_ts = max(prev_ts, src - nLoss[i])
        elif src < prev_ts and prev_src < prev_ts:
            new_ts = min(prev_ts, src + nLoss[i])
        else:
            new_ts = src - nLoss[i] if src > prev_ts else src + nLoss[i]
        ts.append(new_ts)

        # позиция
        if prev_src < prev_ts and src > prev_ts:
            pos.append(1)
        elif prev_src > prev_ts and src < prev_ts:
            pos.append(-1)
        else:
            pos.append(pos[-1])
    df['UT_TS'] = ts
    df['UT_Pos'] = pos
    df['UT_Buy'] = (df['close'] > df['UT_TS']) & ((df['close'].shift(1) < df['UT_TS'].shift(1)))
    df['UT_Sell'] = (df['close'] < df['UT_TS']) & ((df['close'].shift(1) > df['UT_TS'].shift(1)))
    return df

def calculate_qqe(df):
    # Primary QQE
    rsi = ta.rsi(df['close'], length=6)
    smoothed_rsi = ta.ema(rsi, length=5)
    atr_rsi = smoothed_rsi.diff().abs()
    smoothed_atr_rsi = ta.ema(atr_rsi, length=11)
    dynamic_atr = smoothed_atr_rsi * 3.0
    df['QQE_Trend'] = smoothed_rsi - dynamic_atr
    df['QQE_Buy'] = smoothed_rsi > smoothed_rsi.shift(1)
    df['QQE_Sell'] = smoothed_rsi < smoothed_rsi.shift(1)
    return df

def calculate_supertrend(df, period=10, multiplier=3.0):
    atr = ta.atr(df['high'], df['low'], df['close'], length=period)
    hl2 = (df['high'] + df['low']) / 2
    up = hl2 - multiplier * atr
    dn = hl2 + multiplier * atr

    up_adj = up.copy()
    dn_adj = dn.copy()
    trend = pd.Series(1, index=df.index)

    for i in range(1, len(df)):
        up_adj.iloc[i] = max(up.iloc[i], up_adj.iloc[i-1]) if df['close'].iloc[i-1] > up_adj.iloc[i-1] else up.iloc[i]
        dn_adj.iloc[i] = min(dn.iloc[i], dn_adj.iloc[i-1]) if df['close'].iloc[i-1] < dn_adj.iloc[i-1] else dn.iloc[i]
        if trend.iloc[i-1] == -1 and df['close'].iloc[i] > dn_adj.iloc[i-1]:
            trend.iloc[i] = 1
        elif trend.iloc[i-1] == 1 and df['close'].iloc[i] < up_adj.iloc[i-1]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i-1]

    df['Supertrend_Trend'] = trend
    df['Supertrend_Buy'] = (trend == 1) & (trend.shift(1) == -1)
    df['Supertrend_Sell'] = (trend == -1) & (trend.shift(1) == 1)
    return df

# --------- Получение данных с Binance ---------
exchange = ccxt.binance()
ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

# --------- Расчёт индикаторов ---------
df = calculate_ut_bot(df)
df = calculate_qqe(df)
df = calculate_supertrend(df)

# --------- Проверка сигналов ---------
latest = df.iloc[-1]
if latest['UT_Buy'] and latest['QQE_Buy'] and latest['Supertrend_Buy']:
    print(f"[BUY] {symbol} │ Цена входа: {latest['close']}")
elif latest['UT_Sell'] and latest['QQE_Sell'] and latest['Supertrend_Sell']:
    print(f"[SELL] {symbol} │ Цена входа: {latest['close']}")
else:
    print(f"[WAIT] {symbol} │ Нет сигнала")
