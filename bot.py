import os
import json
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

# Инициализация
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TOKEN or not GROQ_API_KEY:
    raise ValueError("Не установлены TELEGRAM_TOKEN или GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

# Файл для хранения истории
MEMORY_FILE = "memory.json"

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

# Загрузка памяти при старте
user_memory = load_memory()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я ИИ-бот с памятью.\n\n"
        "📝 Просто пиши мне — я запомню наш разговор.\n"
        "🎨 /gen <описание> — сгенерирую картинку\n"
        "📷 Пришли фото — опишу что на нём\n"
        "🗑 /clear — очистить память\n"
        "❓ /help — помощь"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Мои возможности:**\n\n"
        "💬 **Обычный чат** — пиши что угодно, я запомню контекст\n"
        "🎨 **/gen <текст>** — сгенерирую изображение по описанию\n"
        "📷 **Отправь фото** — опишу что на нём изображено\n"
        "🗑 **/clear** — очистить историю разговора\n\n"
        "💡 Примеры:\n"
        "• /gen кот в космосе\n"
        "• /gen закат над морем\n"
        "• Просто пришли фото!"
    , parse_mode="Markdown")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in user_memory:
        del user_memory[user_id]
        save_memory(user_memory)
        await update.message.reply_text("🗑 Память очищена!")
    else:
        await update.message.reply_text("Память уже пуста.")

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерация изображений через Pollinations.ai"""
    user_id = str(update.effective_user.id)
    
    # Получаем текст после команды /gen
    prompt = " ".join(context.args) if context.args else None
    
    if not prompt:
        await update.message.reply_text("❗ Укажи описание: /gen <что нарисовать>")
        return
    
    await update.message.reply_text(f"🎨 Генерирую: {prompt}...")
    
    try:
        # Формируем URL для Pollinations.ai
        encoded_prompt = requests.utils.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        
        # Отправляем изображение
        await update.message.reply_photo(photo=image_url, caption=f"✅ Готово!\nЗапрос: {prompt}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка генерации: {str(e)}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Анализ фото через Groq Vision API"""
    user_id = str(update.effective_user.id)
    
    await update.message.reply_text("👁 Анализирую изображение...")
    
    try:
        # Получаем file_id самого большого фото
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # Скачиваем фото во временный файл
        photo_path = f"temp_photo_{user_id}.jpg"
        await file.download_to_drive(photo_path)
        
        # Читаем файл и конвертируем в base64
        import base64
        with open(photo_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")
        
        # Отправляем в Groq Vision API
        response = client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Опиши подробно что изображено на этом фото. На русском языке."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000
        )
        
        description = response.choices[0].message.content
        await update.message.reply_text(f"📷 **Описание:**\n\n{description}", parse_mode="Markdown")
        
        # Удаляем временный файл
        os.remove(photo_path)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка анализа фото: {str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обычный текстовый чат с памятью"""
    user_id = str(update.effective_user.id)
    user_message = update.message.text
    
    # Инициализируем память для нового пользователя
    if user_id not in user_memory:
        user_memory[user_id] = []
    
    # Добавляем сообщение пользователя в историю
    user_memory[user_id].append({"role": "user", "content": user_message})
    
    # Ограничиваем историю последними 20 сообщениями
    if len(user_memory[user_id]) > 20:
        user_memory[user_id] = user_memory[user_id][-20:]
    
    try:
        # Отправляем запрос в Groq с историей
        response = client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=user_memory[user_id],
            max_tokens=1000,
            temperature=0.7
        )
        
        bot_reply = response.choices[0].message.content
        
        # Добавляем ответ бота в историю
        user_memory[user_id].append({"role": "assistant", "content": bot_reply})
        
        # Сохраняем память
        save_memory(user_memory)
        
        # Отправляем ответ
        await update.message.reply_text(bot_reply)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

def main():
    app = Application.builder().token(TOKEN).build()
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("gen", generate_image))
    
    # Обработчик фото
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # Обработчик текстовых сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
