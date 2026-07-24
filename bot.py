import os
import json
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

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

# Память для хранения истории сообщений
memory = {}

def load_memory():
    """Загрузка памяти из базы данных"""
    try:
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS user_memory (user_id BIGINT PRIMARY KEY, history JSONB)")
        cursor.execute("SELECT user_id, history FROM user_memory")
        rows = cursor.fetchall()
        for row in rows:
            memory[row['user_id']] = row['history']
        cursor.close()
        conn.close()
        logger.info("Память загружена из базы данных")
    except Exception as e:
        logger.error(f"Ошибка загрузки памяти: {e}")

def save_memory(memory_dict):
    """Сохранение памяти в базу данных"""
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

def get_ai_response(user_id, user_message):
    """Получение ответа от AI с системным промптом"""
    if user_id not in memory:
        memory[user_id] = []
    
    memory[user_id].append({"role": "user", "content": user_message})
    
    # Ограничиваем историю последними 10 сообщениями
    if len(memory[user_id]) > 10:
        memory[user_id] = memory[user_id][-10:]
    
    try:
        # Системный промпт - инструкция для бота
        system_prompt = """Ты — профессиональный копирайтер для маркетплейсов (Wildberries, Ozon, Яндекс.Маркет).

ПРАВИЛА РАБОТЫ:
1. НИКОГДА не пиши карточку сразу при первом запросе.
2. ВСЕГДА сначала задай пользователю 3 уточняющих вопроса:
   - Какой цвет, размер или версия товара?
   - Для какой целевой аудитории товар (дети, взрослые, профессионалы, любители)?
   - Есть ли ключевые SEO-слова или особенности, которые нужно включить?
3. Жди ответов на все 3 вопроса.
4. Только после получения ответов создавай карточку.

ФОРМАТ КАРТОЧКИ (строго придерживайся):

**Название:** [Краткое, цепляющее название с ключевыми словами]

**Описание:** [2-3 предложения о товаре, его преимуществах и пользе для покупателя]

**Характеристики:**
- **Параметр 1:** значение
- **Параметр 2:** значение
- **Параметр 3:** значение

**Функции и преимущества:**
- **Функция 1:** краткое описание
- **Функция 2:** краткое описание

**Для кого подходит:** [описание целевой аудитории]

**SEO-ключи:**
