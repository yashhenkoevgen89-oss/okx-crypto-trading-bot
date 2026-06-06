import sqlite3

DB_NAME = "bot.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT,
        symbol TEXT,
        side TEXT,
        entry_price REAL,
        exit_price REAL,
        pnl REAL,
        score INTEGER,
        reason TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT,
        symbol TEXT,
        signal TEXT,
        price REAL,
        score INTEGER
    )
    """)

    conn.commit()
    conn.close()


def add_trade(time, symbol, side, entry_price, exit_price, pnl, score, reason):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO trades(time, symbol, side, entry_price, exit_price, pnl, score, reason)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (time, symbol, side, entry_price, exit_price, pnl, score, reason))

    conn.commit()
    conn.close()


def add_signal(time, symbol, signal, price, score):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO signals(time, symbol, signal, price, score)
    VALUES (?, ?, ?, ?, ?)
    """, (time, symbol, signal, price, score))

    conn.commit()
    conn.close()
