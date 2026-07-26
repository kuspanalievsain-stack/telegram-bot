import os
import json
import requests
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TOKEN or not GROQ_API_KEY:
    raise ValueError("Не установлены TELEGRAM_TOKEN или GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

# Файлы для хранения данных
MEMORY_FILE = "memory.json"
USERS_FILE = "users.json"
LOG_FILE = "bot.log"

# Твой Telegram user_id (администратор)
# Узнать можно через @userinfobot в Telegram
ADMIN_ID = "501464319"  # ЗАМЕНИ НА СВОЙ ID!

# Настройка логирования
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def log_action(user_id, username, action):
    """Логирование действий"""
    user_info = f"User {user_id} ({username})"
    logging.info(f"{user_info} - {action}")

user_memory = load_json(MEMORY_FILE)
users_db = load_json(USERS_FILE)

def is_admin(user_id):
    """Проверка, является ли пользователь админом"""
    return str(user_id) == ADMIN_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"
    
    # Добавляем пользователя в базу
    if user_id not in users_db:
        users_db[user_id] = {
            "username": username,
            "first_seen": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
            "message_count": 0
        }
        save_json(USERS_FILE, users_db)
        log_action(user_id, username, "FIRST JOIN")
    
    users_db[user_id]["last_seen"] = datetime.now().isoformat()
    save_json(USERS_FILE, users_db)
    
    await update.message.reply_text(
        "👋 Привет! Я ИИ-бот с памятью.\n\n"
        "📝 Просто пиши мне — я запомню наш разговор.\n"
        "🎨 /gen <описание> — сгенерирую картинку\n"
        "🗑 /clear — очистить память\n"
        "❓ /help — помощь"
    )
    log_action(user_id, username, "START")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"
    
    await update.message.reply_text(
        "🤖 **Мои возможности:**\n\n"
        "💬 **Обычный чат** — пиши что угодно, я запомню контекст\n"
        "🎨 **/gen <текст>** — сгенерирую изображение по описанию\n"
        " **/clear** — очистить историю разговора\n\n"
        "💡 Примеры:\n"
        "• /gen кот в космосе\n"
        "• /gen закат над морем\n"
        "• Просто напиши что-нибудь!",
        parse_mode="Markdown"
    )
    log_action(user_id, username, "HELP")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"
    
    if user_id in user_memory:
        del user_memory[user_id]
        save_json(MEMORY_FILE, user_memory)
        await update.message.reply_text("🗑 Память очищена!")
    else:
        await update.message.reply_text("Память уже пуста.")
    log_action(user_id, username, "CLEAR")

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"
    prompt = " ".join(context.args) if context.args else None
    
    if not prompt:
        await update.message.reply_text("❗ Укажи описание: /gen <что нарисовать>")
        return
    
    await update.message.reply_text(f"🎨 Генерирую: {prompt}...")
    log_action(user_id, username, f"GEN: {prompt}")
    
    try:
        encoded_prompt = requests.utils.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        await update.message.reply_photo(photo=image_url, caption=f"✅ Готово!\nЗапрос: {prompt}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка генерации: {str(e)}")
        logging.error(f"Image generation error: {e}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика (только для админа)"""
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return
    
    total_users = len(users_db)
    today = datetime.now().date().isoformat()
    
    # Считаем активных сегодня
    active_today = 0
    for uid, data in users_db.items():
        if data.get("last_seen", "").startswith(today):
            active_today += 1
    
    # Общее количество сообщений
    total_messages = sum(data.get("message_count", 0) for data in users_db.values())
    
    stats_text = f"""
📊 **Статистика бота:**

 **Всего пользователей:** {total_users}
🟢 **Активных сегодня:** {active_today}
💬 **Всего сообщений:** {total_messages}

📅 **Дата:** {datetime.now().strftime('%d.%m.%Y %H:%M')}
"""
    
    await update.message.reply_text(stats_text, parse_mode="Markdown")
    logging.info(f"Admin {user_id} viewed stats")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список пользователей (только для админа)"""
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        await update.message.reply_text(" Эта команда доступна только администратору.")
        return
    
    if not users_db:
        await update.message.reply_text("📭 База пользователей пуста.")
        return
    
    users_list = "👥 **Пользователи:**\n\n"
    for uid, data in users_db.items():
        username = data.get("username", "unknown")
        first_seen = data.get("first_seen", "unknown")[:10]
        msg_count = data.get("message_count", 0)
        users_list += f"• @{username} (ID: {uid})\n  Первое посещение: {first_seen}\n  Сообщений: {msg_count}\n\n"
    
    # Ограничиваем длину сообщения
    if len(users_list) > 4000:
        users_list = users_list[:4000] + "\n... (список обрезан)"
    
    await update.message.reply_text(users_list, parse_mode="Markdown")
    logging.info(f"Admin {user_id} viewed users list")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка всем пользователям (только для админа)"""
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return
    
    message = " ".join(context.args) if context.args else None
    
    if not message:
        await update.message.reply_text("❗ Укажи текст: /broadcast <сообщение>")
        return
    
    await update.message.reply_text(f"📢 Начинаю рассылку: {message[:50]}...")
    
    success_count = 0
    fail_count = 0
    
    for uid in users_db.keys():
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            success_count += 1
        except Exception as e:
            fail_count += 1
            logging.error(f"Broadcast failed to {uid}: {e}")
    
    result = f"✅ Рассылка завершена!\n\n📤 Успешно: {success_count}\n Ошибок: {fail_count}"
    await update.message.reply_text(result)
    logging.info(f"Admin {user_id} broadcasted: {message[:100]}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"
    user_message = update.message.text
    
    # Обновляем статистику
    if user_id in users_db:
        users_db[user_id]["message_count"] = users_db[user_id].get("message_count", 0) + 1
        users_db[user_id]["last_seen"] = datetime.now().isoformat()
        save_json(USERS_FILE, users_db)
    
    if user_id not in user_memory:
        user_memory[user_id] = []
    
    user_memory[user_id].append({"role": "user", "content": user_message})
    if len(user_memory[user_id]) > 20:
        user_memory[user_id] = user_memory[user_id][-20:]
    
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=user_memory[user_id],
            max_tokens=1000,
            temperature=0.7
        )
        
        bot_reply = response.choices[0].message.content
        user_memory[user_id].append({"role": "assistant", "content": bot_reply})
        save_json(MEMORY_FILE, user_memory)
        await update.message.reply_text(bot_reply)
        log_action(user_id, username, f"MESSAGE: {user_message[:50]}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
        logging.error(f"Chat error: {e}")

def main():
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("gen", generate_image))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print(" Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
