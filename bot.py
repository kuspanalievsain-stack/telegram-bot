import os
import json
import requests
import logging
import subprocess
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

# ===== НАСТРОЙКИ =====
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TOKEN or not GROQ_API_KEY:
    raise ValueError("Не установлены TELEGRAM_TOKEN или GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

# Твой Telegram ID (ЗАМЕНИ НА СВОЙ!)
ADMIN_ID = "501464319"

# Файлы
MEMORY_FILE = "memory.json"
USERS_FILE = "users.json"
LOG_FILE = "bot.log"

# ===== ЛОГИРОВАНИЕ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===== ФУНКЦИИ =====
def load_json(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def log_action(user_id, username, action):
    logger.info(f"User {user_id} ({username}) - {action}")

user_memory = load_json(MEMORY_FILE)
users_db = load_json(USERS_FILE)

def is_admin(user_id):
    return str(user_id) == ADMIN_ID

# ===== КОМАНДЫ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"
    
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
        " Просто пиши мне — я запомню наш разговор.\n"
        "🎨 /gen <описание> — сгенерирую картинку\n"
        "🎤 Отправь голосовое — распознаю речь\n"
        " /clear — очистить память\n"
        "📋 /help — помощь"
    )
    log_action(user_id, username, "START")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Мои возможности:**\n\n"
        "💬 **Обычный чат** — пиши что угодно, я запомню контекст\n"
        " **/gen <текст>** — сгенерирую изображение по описанию\n"
        "🎤 **Голосовые сообщения** — распознаю речь и отвечу\n"
        "🗑 **/clear** — очистить историю разговора\n\n"
        "💡 Примеры:\n"
        "• /gen кот в космосе\n"
        "• /gen закат над морем\n"
        "• Просто напиши или отправь голосовое!",
        parse_mode="Markdown"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in user_memory:
        del user_memory[user_id]
        save_json(MEMORY_FILE, user_memory)
        await update.message.reply_text("🗑 Память очищена!")
    else:
        await update.message.reply_text("Память уже пуста.")

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    prompt = " ".join(context.args) if context.args else None
    
    if not prompt:
        await update.message.reply_text("❗ Укажи описание: /gen <что нарисовать>")
        return
    
    await update.message.reply_text(f"🎨 Генерирую: {prompt}...")
    
    try:
        encoded_prompt = requests.utils.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        await update.message.reply_photo(photo=image_url, caption=f"✅ Готово!\nЗапрос: {prompt}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка генерации: {str(e)}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return
    
    total_users = len(users_db)
    today = datetime.now().date().isoformat()
    
    active_today = sum(1 for data in users_db.values() 
                       if data.get("last_seen", "").startswith(today))
    total_messages = sum(data.get("message_count", 0) for data in users_db.values())
    
    stats_text = f"""
 **Статистика бота:**

 **Всего пользователей:** {total_users}
🟢 **Активных сегодня:** {active_today}
💬 **Всего сообщений:** {total_messages}

📅 **Дата:** {datetime.now().strftime('%d.%m.%Y %H:%M')}
"""
    await update.message.reply_text(stats_text, parse_mode="Markdown")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
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
    
    if len(users_list) > 4000:
        users_list = users_list[:4000] + "\n... (список обрезан)"
    
    await update.message.reply_text(users_list, parse_mode="Markdown")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            logger.error(f"Broadcast failed to {uid}: {e}")
    
    await update.message.reply_text(f"✅ Рассылка завершена!\n\n Успешно: {success_count}\n❌ Ошибок: {fail_count}")

# ===== ОБРАБОТКА СООБЩЕНИЙ =====
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"
    
    await update.message.reply_text("🎤 Распознаю голосовое сообщение...")
    log_action(user_id, username, "VOICE_MESSAGE")
    
    ogg_path = f"voice_{user_id}_{int(datetime.now().timestamp())}.ogg"
    mp3_path = ogg_path.replace(".ogg", ".mp3")
    
    try:
        voice = update.message.voice
        voice_file = await context.bot.get_file(voice.file_id)
        await voice_file.download_to_drive(ogg_path)
        
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", mp3_path],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            raise Exception(f"ffmpeg error: {result.stderr}")
        
        with open(mp3_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file,
                language="ru"
            )
        
        recognized_text = transcript.text
        await update.message.reply_text(f"📝 Распознано: {recognized_text}")
        log_action(user_id, username, f"VOICE TRANSCRIBED: {recognized_text[:50]}")
        
        await process_message(update, context, recognized_text, user_id, username)
        
    except subprocess.TimeoutExpired:
        await update.message.reply_text("️ Превышено время обработки.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка распознавания: {str(e)}")
        logger.error(f"Voice recognition error: {e}")
    finally:
        for path in [ogg_path, mp3_path]:
            if os.path.exists(path):
                os.remove(path)

async def process_message(update, context, user_message, user_id, username):
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
        logger.error(f"Chat error: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"
    await process_message(update, context, update.message.text, user_id, username)

# ===== ЗАПУСК БОТА =====
async def post_init(application):
    """Инициализация после создания приложения"""
    logger.info("✅ Инициализация бота...")
    # Удаляем webhook, чтобы избежать конфликта 409
    await application.bot.delete_webhook()
    logger.info("✅ Webhook удалён")

def main():
    """Основная функция"""
    try:
        logger.info("🤖 Создаю приложение...")
        
        # Создаём приложение с post_init для удаления webhook
        application = Application.builder().token(TOKEN).post_init(post_init).build()
        
        logger.info("📝 Регистрирую обработчики...")
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("clear", clear))
        application.add_handler(CommandHandler("gen", generate_image))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("users", users_command))
        application.add_handler(CommandHandler("broadcast", broadcast_command))
        application.add_handler(MessageHandler(filters.VOICE, handle_voice))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("🚀 Запускаю polling...")
        logger.info("🤖 Бот запущен и готов к работе!")
        
        # Запуск polling (blocking)
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            timeout=30,
            drop_pending_updates=True
        )
        
    except KeyboardInterrupt:
        logger.info(" Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()
