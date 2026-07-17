from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
import json
import os

# Токен бота от BotFather
TOKEN = os.environ.get("TELEGRAM_TOKEN")

# Ключ Groq API
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Инициализация Groq
client = Groq(api_key=GROQ_API_KEY)

# Память бота
MEMORY_FILE = "memory.json"

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    return {}

def save_memory():
    with open(MEMORY_FILE, "w", encoding="utf-8") as file:
        json.dump(user_memories, file, ensure_ascii=False, indent=4)

user_memories = load_memory()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_memories[user_id] = []
    save_memory()
    await update.message.reply_text("Привет! Я твой AI-ассистент с памятью. Задай любой вопрос!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Доступные команды:\n/start - Начать заново\n/help - Помощь\n/clear - Очистить память\n\nПросто напиши мне что-нибудь!")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in user_memories:
        user_memories[user_id] = []
        save_memory()
        await update.message.reply_text(" Память очищена! Мы начинаем с чистого листа.")
    else:
        await update.message.reply_text("У меня и так нет воспоминаний о тебе 🤷‍♂️")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_text = update.message.text
    
    if user_id not in user_memories:
        user_memories[user_id] = []
    user_memories[user_id].append({"role": "user", "content": user_text})
    
    if len(user_memories[user_id]) > 10:
        user_memories[user_id] = user_memories[user_id][-10:]
    
    try:
        chat_completion = client.chat.completions.create(
            messages=user_memories[user_id],
            model="llama-3.1-8b-instant",
            temperature=0.7,
            max_tokens=500
        )
        ai_response = chat_completion.choices[0].message.content
        user_memories[user_id].append({"role": "assistant", "content": ai_response})
        save_memory()
        await update.message.reply_text(ai_response)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

def main():
    print("Бот запускается...")
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear_command))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Бот запущен! Жду сообщений...")
    application.run_polling()

if __name__ == "__main__":
    main()
