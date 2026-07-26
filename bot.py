import os
import json
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TOKEN or not GROQ_API_KEY:
    raise ValueError("Не установлены TELEGRAM_TOKEN или GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

MEMORY_FILE = "memory.json"

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

user_memory = load_memory()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я ИИ-бот с памятью.\n\n"
        "📝 Просто пиши мне — я запомню наш разговор.\n"
        "🎨 /gen <описание> — сгенерирую картинку\n"
        "🗑 /clear — очистить память\n"
        "❓ /help — помощь"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Мои возможности:**\n\n"
        "💬 **Обычный чат** — пиши что угодно, я запомню контекст\n"
        "🎨 **/gen <текст>** — сгенерирую изображение по описанию\n"
        "🗑 **/clear** — очистить историю разговора\n\n"
        "💡 Примеры:\n"
        "• /gen кот в космосе\n"
        "• /gen закат над морем",
        parse_mode="Markdown"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in user_memory:
        del user_memory[user_id]
        save_memory(user_memory)
        await update.message.reply_text(" Память очищена!")
    else:
        await update.message.reply_text("Память уже пуста.")

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    prompt = " ".join(context.args) if context.args else None
    
    if not prompt:
        await update.message.reply_text(" Укажи описание: /gen <что нарисовать>")
        return
    
    await update.message.reply_text(f"🎨 Генерирую: {prompt}...")
    
    try:
        encoded_prompt = requests.utils.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        await update.message.reply_photo(photo=image_url, caption=f"✅ Готово!\nЗапрос: {prompt}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка генерации: {str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_message = update.message.text
    
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
        save_memory(user_memory)
        await update.message.reply_text(bot_reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

def main():
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("gen", generate_image))
    
    # Убрали обработчик фото
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print(" Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
