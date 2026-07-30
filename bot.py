import os
import json
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from groq import Groq
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from io import BytesIO
from datetime import datetime

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация Groq
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Админ ID
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Память для хранения истории сообщений
memory = {}

# История карточек для каждого пользователя
card_history = {}

# Хранение изображений
pending_photos = {}

# Счётчики статистики
stats = {
    "total_messages": 0,
    "total_cards": 0,
    "total_voice": 0,
    "total_photos": 0
}

# ====== АСИНХРОННЫЕ ОПЕРАЦИИ С БД ======

def _log_action_sync(user_id, action_type, details=""):
    try:
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_actions (
                id SERIAL PRIMARY KEY, user_id BIGINT, action_type TEXT, 
                details TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "INSERT INTO user_actions (user_id, action_type, details) VALUES (%s, %s, %s)",
            (user_id, action_type, details)
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка логирования: {e}")

async def log_action(user_id, action_type, details=""):
    await asyncio.to_thread(_log_action_sync, user_id, action_type, details)

def _save_memory_sync(memory_dict):
    try:
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS user_memory (user_id BIGINT PRIMARY KEY, history JSONB)")
        for user_id, history in memory_dict.items():
            cursor.execute(
                "INSERT INTO user_memory (user_id, history) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET history = %s",
                (user_id, json.dumps(history), json.dumps(history))
            )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка сохранения памяти: {e}")

async def save_memory(memory_dict):
    await asyncio.to_thread(_save_memory_sync, memory_dict)

def _save_card_sync(user_id, card_text):
    try:
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS product_cards (id SERIAL PRIMARY KEY, user_id BIGINT, card_text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute(
            "INSERT INTO product_cards (user_id, card_text) VALUES (%s, %s)",
            (user_id, card_text)
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        if user_id
