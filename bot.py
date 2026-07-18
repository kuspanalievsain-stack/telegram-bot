from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import json
import os
import requests
import psycopg
from psycopg import Cursor
from psycopg.rows import dict_row

# Токен бота от BotFather
TOKEN = os.environ.get("TELEGRAM_TOKEN")

# Ключ Groq API
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# База данных PostgreSQL
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    """Получить подключение к базе данных"""
    return psycopg.connect(DATABASE_URL)

def init_db():
    """Инициализировать базу данных (создать таблицу)"""
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role VARCHAR(50) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_id ON user_messages(user_id)
        """)
    conn.commit()
    conn.close()

def get_user_messages(user_id, limit=10):
    """Получить последние сообщения пользователя"""
    conn = get_db_connection()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT role, content FROM user_messages 
            WHERE user_id = %s 
            ORDER BY created_at DESC 
            LIMIT %s
        """, (user_id, limit))
        messages = cur.fetchall()
    conn.close()
    return list(reversed(messages))

def save_user_message(user_id, role, content):
    """Сохранить сообщение пользователя в базу"""
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO user_messages (user_id, role, content) 
            VALUES (%s, %s, %s)
        """, (user_id, role, content))
    conn.commit()
    conn.close()

def clear_user_messages(user_id):
    """Удалить все сообщения пользователя"""
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_messages WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()

def get_user_messages(user_id, limit=10):
    """Получить последние сообщения пользователя"""
    conn = get_db_connection()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT role, content FROM user_messages 
            WHERE user_id = %s 
            ORDER BY created_at DESC 
            LIMIT %s
        """, (user_id, limit))
        messages = cur.fetchall()
    conn.close()
    # Переворачиваем, чтобы было в хронологическом порядке
    return list(reversed(messages))

def save_user_message(user_id, role, content):
    """Сохранить сообщение пользователя в базу"""
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO user_messages (user_id, role, content) 
            VALUES (%s, %s, %s)
        """, (user_id, role, content))
    conn.commit()
    conn.close()

def clear_user_messages(user_id):
    """Удалить все сообщения пользователя"""
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_messages WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()

def get_groq_response(messages):
    """Получить ответ от Groq API через HTTP запрос"""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Усиленный системный промпт
    system_prompt = {
        "role": "system",
        "content": """Ты талантливый AI-помощник. 
ВАЖНЫЕ ПРАВИЛА:
1. НИКОГДА не выдумывай факты о пользователе (возраст, имена, даты), если он их сам не указал.
2. Если просят стихотворение — пиши красиво, в запрошенном стиле.
3. Будь креативным, но точным."""
    }
    
    data = {
        "messages": [system_prompt] + messages,
        "model": "llama-3.1-8b-instant",
        "temperature": 0.7,
        "max_tokens": 800
    }
    
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=data
        )
        
        if response.status_code != 200:
            return f"Ошибка API: {response.status_code} - {response.text}"
        
        response_data = response.json()
        
        if "choices" not in response_data or len(response_data["choices"]) == 0:
            return f"Ошибка: неожиданный ответ API: {response_data}"
        
        return response_data["choices"][0]["message"]["content"]
        
    except Exception as e:
        return f"Ошибка: {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    clear_user_messages(user_id)  # Очищаем старую историю
    save_user_message(user_id, "system", "Пользователь начал диалог")
    await update.message.reply_text("Привет! Я твой AI-ассистент с постоянной памятью. Задай любой вопрос!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Доступные команды:\n/start - Начать заново\n/help - Помощь\n/clear - Очистить память\n\nПросто напиши мне что-нибудь!")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    clear_user_messages(user_id)
    await update.message.reply_text("🧹 Память очищена! Мы начинаем с чистого листа.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_text = update.message.text
    
    # Сохраняем сообщение пользователя
    save_user_message(user_id, "user", user_text)
    
    # Получаем историю сообщений
    messages_history = get_user_messages(user_id, limit=10)
    
    # Формируем сообщения для Groq
    groq_messages = []
    for msg in messages_history:
        groq_messages.append({"role": msg["role"], "content": msg["content"]})
    
    try:
        ai_response = get_groq_response(groq_messages)
        # Сохраняем ответ AI
        save_user_message(user_id, "assistant", ai_response)
        await update.message.reply_text(ai_response)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

def main():
    print("Инициализация базы данных...")
    init_db()
    
    print("Бот запускается...")
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear_command))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Бот запущен! Жду сообщений...")
    # Добавляем обработку ошибок polling
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
