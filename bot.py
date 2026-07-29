import os
import json
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    "total_photos": 0,
    "total_users": set()
}

# ====== АСИНХРОННЫЕ ОПЕРАЦИИ С БД (Шаг 5.1) ======

def _log_action_sync(user_id, action_type, details=""):
    """Синхронная функция логирования (для запуска в потоке)"""
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
    """Асинхронная обертка для логирования"""
    await asyncio.to_thread(_log_action_sync, user_id, action_type, details)

def _save_memory_sync(memory_dict):
    """Синхронное сохранение памяти"""
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
    """Синхронное сохранение карточки"""
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
        
        if user_id not in card_history:
            card_history[user_id] = []
        card_history[user_id].append(card_text)
        
        logger.info(f"Карточка сохранена для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка сохранения карточки: {e}")

async def save_card(user_id, card_text):
    await asyncio.to_thread(_save_card_sync, user_id, card_text)
    await log_action(user_id, "card_created", card_text[:100])
    stats["total_cards"] += 1

def load_memory():
    """Загрузка памяти при старте (синхронно, т.к. бот еще не запущен)"""
    global card_history, stats
    try:
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cursor = conn.cursor()
        
        cursor.execute("CREATE TABLE IF NOT EXISTS user_memory (user_id BIGINT PRIMARY KEY, history JSONB)")
        cursor.execute("SELECT user_id, history FROM user_memory")
        for row in cursor.fetchall():
            memory[row['user_id']] = row['history']
        
        cursor.execute("CREATE TABLE IF NOT EXISTS product_cards (id SERIAL PRIMARY KEY, user_id BIGINT, card_text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("SELECT user_id, card_text FROM product_cards ORDER BY created_at DESC")
        for row in cursor.fetchall():
            if row['user_id'] not in card_history:
                card_history[row['user_id']] = []
            card_history[row['user_id']].append(row['card_text'])
        
        cursor.execute("SELECT COUNT(DISTINCT user_id) as total FROM user_actions")
        row = cursor.fetchone()
        if row: stats["total_users"] = {row['total']}
        
        cursor.close()
        conn.close()
        logger.info("Память загружена из базы данных")
    except Exception as e:
        logger.error(f"Ошибка загрузки памяти: {e}")

# ====== AI И ПРОГРЕСС ======

async def show_progress(update: Update, step: int, total_steps: int):
    progress_messages = {
        1: "⏳ **Анализирую запрос...**\n_Понимаю, что нужно описать_",
        2: "✅ **Понял задачу!**\n_Готовлю уточняющие вопросы_",
        3: "️ **Задаю вопросы...**\n_Нужно уточнить детали_",
        4: "✅ **Вопросы заданы!**\n_Жду твои ответы_",
        5: "🔍 **Проверяю ответы...**\n_Анализирую информацию_",
        6: "✅ **Ответы получены!**\n_Начинаю создавать карточку_",
        7: "✍️ **Создаю карточку...**\n_Пишу продающее описание_",
        8: "✅ **Карточка готова!**\n_Сохраняю и отправляю_"
    }
    if step in progress_messages:
        progress_bar = "█" * step + "░" * (total_steps - step)
        message = f"**Прогресс:** [{progress_bar}] {step}/{total_steps}\n\n{progress_messages[step]}"
        await send_message_fallback(update, message)
        await asyncio.sleep(0.3)

async def transcribe_voice(voice_file):
    try:
        voice_data = await voice_file.download_as_bytearray()
        voice_file_obj = BytesIO(voice_data)
        voice_file_obj.name = "voice.ogg"
        
        # Запускаем распознавание в потоке, чтобы не блокировать бота
        def _transcribe():
            return client.audio.transcriptions.create(
                file=voice_file_obj, model="whisper-large-v3-turbo", language="ru"
            )
        transcription = await asyncio.to_thread(_transcribe)
        return transcription.text
    except Exception as e:
        logger.error(f"Ошибка распознавания голоса: {e}")
        return None

async def get_ai_response_async(user_id, user_message, photo_analysis=""):
    """Асинхронное получение ответа от AI"""
    if user_id not in memory:
        memory[user_id] = []
    
    if photo_analysis:
        memory[user_id].append({"role": "user", "content": f"На изображении видно: {photo_analysis}"})
    
    memory[user_id].append({"role": "user", "content": user_message})
    if len(memory[user_id]) > 10:
        memory[user_id] = memory[user_id][-10:]
    
    system_prompt = """Ты — профессиональный копирайтер для маркетплейсов (Wildberries, Ozon, Яндекс.Маркет).
ПРАВИЛА: 1. Сначала задай 3 вопроса (цвет/версия, аудитория, SEO). 2. Жди ответов. 3. Создавай карточку.
ФОРМАТ: **Название:** ...\n**Описание:** ...\n**Характеристики:**\n- **Параметр:** значение\n**Функции:**\n- **Функция:** описание\n**Для кого:** ...\n**SEO-ключи:** ...
СТИЛЬ: Русский язык, продающие формулировки, конкретика, умеренные эмодзи."""
    
    try:
        def _call_groq():
            return client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": system_prompt}, *memory[user_id]]
            )
        
        response = await asyncio.to_thread(_call_groq)
        ai_message = response.choices[0].message.content
        
        memory[user_id].append({"role": "assistant", "content": ai_message})
        await save_memory(memory)
        await save_card(user_id, ai_message)
        
        return ai_message
    except Exception as e:
        logger.error(f"Ошибка AI: {e}")
        return f"❌ Ошибка при генерации: {str(e)}"

# ====== БЕЗОПАСНАЯ ОТПРАВКА СООБЩЕНИЙ (Защита от Markdown) ======

async def send_message_fallback(update: Update, text: str, reply_markup=None):
    """Отправляет сообщение. Если Markdown сломан, отправляет как обычный текст."""
    try:
        if update.callback_query:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        if "Markdown" in str(e) or "parse mode" in str(e).lower():
            logger.warning("Markdown error, sending as plain text")
            if update.callback_query:
                await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
            else:
                await update.message.reply_text(text, reply_markup=reply_markup)
        else:
            logger.error(f"Send error: {e}")

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# ====== КЛАВИАТУРЫ ======

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Новая карточка", callback_data="new_card"),
         InlineKeyboardButton("📋 Мои карточки", callback_data="my_cards")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_last"),
         InlineKeyboardButton("🖼️ Загрузить фото", callback_data="upload_photo")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ])

def get_edit_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Цвет", callback_data="edit_color"),
         InlineKeyboardButton("👥 ЦА", callback_data="edit_audience")],
        [InlineKeyboardButton("🔑 SEO", callback_data="edit_seo"),
         InlineKeyboardButton("📝 Свой запрос", callback_data="edit_custom")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
         InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("🔥 Топ товаров", callback_data="admin_top"),
         InlineKeyboardButton("📜 Логи", callback_data="admin_logs")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ])

# ====== ОБРАБОТЧИКИ КОМАНД ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats["total_users"].add(user_id)
    await log_action(user_id, "start")
    await send_message_fallback(update, 
        "👋 **Привет! Я AI-бот для карточек товаров.**\n\n"
        "Я помогу создать карточку для WB/Ozon за пару минут!\n\n"
        "🎯 **Ты можешь:**\n"
        "• ✍️ Написать текст\n"
        "• 🖼️ Отправить фото\n"
        "• 🎤 Отправить голосовое\n\n"
        "👇 **Выбери действие:**",
        reply_markup=get_main_keyboard())

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in memory:
        del memory[user_id]
        await save_memory(memory)
    await log_action(user_id, "clear")
    await send_message_fallback(update, "✅ **Память очищена!**\nНачнём с чистого листа.", reply_markup=get_main_keyboard())

async def newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in memory:
        del memory[user_id]
        await save_memory(memory)
    await log_action(user_id, "newchat")
    await send_message_fallback(update, "🔄 **Новый диалог!**\nНапиши, какой товар описать:", reply_markup=get_main_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "help")
    text = "🤖 **AI-бот для карточек**\n\n"
    text += "/start, /help, /newchat, /clear, /edit, /mycards\n\n"
    if is_admin(user_id):
        text += "🔐 **Админ:** /stats, /users, /top, /admin\n\n"
    text += "💡 Напиши товар, ответь на 3 вопроса, получи карточку!"
    await send_message_fallback(update, text)

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "edit_command")
    
    if user_id not in card_history or not card_history[user_id]:
        await send_message_fallback(update, "😕 Сначала создай карточку!", reply_markup=get_main_keyboard())
        return
    
    edit_text = " ".join(context.args) if context.args else ""
    if not edit_text:
        await send_message_fallback(update, "✏️ Напиши: `/edit измени цвет на синий`\nИли выбери кнопку:", reply_markup=get_edit_keyboard())
        return
    
    last_card = card_history[user_id][-1]
    prompt = f"Карточка:\n{last_card}\n\nИзменения: {edit_text}\n\nВерни полную обновленную карточку в том же формате."
    
    await update.message.chat.send_action(action="typing")
    try:
        def _call_groq_edit():
            return client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": prompt}])
        response = await asyncio.to_thread(_call_groq_edit)
        edited = response.choices[0].message.content
        await save_card(user_id, edited)
        await log_action(user_id, "edit_card", edit_text)
        await send_message_fallback(update, "✅ **Обновлено!**\n\n" + edited, reply_markup=get_main_keyboard())
    except Exception as e:
        await send_message_fallback(update, f"❌ Ошибка: {str(e)}")

async def mycards_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "mycards")
    
    if user_id not in card_history or not card_history[user_id]:
        await send_message_fallback(update, "📭 Карточек пока нет.", reply_markup=get_main_keyboard())
        return
    
    cards = card_history[user_id][-5:]
    msg = f"📋 **Последние карточки** ({len(cards)} из {len(card_history[user_id])}):\n\n"
    for i, card in enumerate(reversed(cards), 1):
        preview = card[:100].replace("\n", " ").replace("**", "") + "..."
        msg += f"**{i}.** {preview}\n\n"
    await send_message_fallback(update, msg, reply_markup=get_main_keyboard())

# ====== АДМИН-КОМАНДЫ ======

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_message_fallback(update, "⛔ Только для админа.")
        return
    await log_action(user_id, "stats")
    
    def _get_stats():
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as t FROM user_actions"); ta = cur.fetchone()['t']
        cur.execute("SELECT COUNT(DISTINCT user_id) as t FROM user_actions"); tu = cur.fetchone()['t']
        cur.execute("SELECT COUNT(*) as t FROM product_cards"); tc = cur.fetchone()['t']
        cur.execute("SELECT COUNT(*) as t FROM user_actions WHERE created_at >= CURRENT_DATE"); tda = cur.fetchone()['t']
        cur.execute("SELECT COUNT(*) as t FROM product_cards WHERE created_at >= CURRENT_DATE"); tdc = cur.fetchone()['t']
        cur.close(); conn.close()
        return ta, tu, tc, tda, tdc

    try:
        ta, tu, tc, tda, tdc = await asyncio.to_thread(_get_stats)
        text = (f"📊 **Статистика**\n\n"
                f" Юзеров: {tu}\n"
                f"📝 Действий: {ta} (сегодня: {tda})\n"
                f"🎴 Карточек: {tc} (сегодня: {tdc})\n"
                f"🎤 Голос: {stats['total_voice']} | 🖼️ Фото: {stats['total_photos']}")
        await send_message_fallback(update, text, reply_markup=get_admin_keyboard())
    except Exception as e:
        await send_message_fallback(update, f"❌ Ошибка: {str(e)}")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_message_fallback(update, "⛔ Только для админа.")
        return
    await log_action(user_id, "users")
    
    def _get_users():
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cur = conn.cursor()
        cur.execute("SELECT user_id, COUNT(*) as c, MAX(created_at) as l FROM user_actions GROUP BY user_id ORDER BY c DESC LIMIT 10")
        rows = cur.fetchall()
        cur.close(); conn.close()
        return rows

    try:
        rows = await asyncio.to_thread(_get_users)
        if not rows:
            await send_message_fallback(update, "📭 Пусто.")
            return
        msg = "👥 **Топ-10 юзеров:**\n\n"
        for i, r in enumerate(rows, 1):
            last = r['l'].strftime('%d.%m %H:%M') if r['l'] else '?'
            msg += f"**{i}.** `{r['user_id']}` — {r['c']} дейст. ({last})\n"
        await send_message_fallback(update, msg, reply_markup=get_admin_keyboard())
    except Exception as e:
        await send_message_fallback(update, f"❌ Ошибка: {str(e)}")

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_message_fallback(update, "⛔ Только для админа.")
        return
    await log_action(user_id, "top")
    
    def _get_top():
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cur = conn.cursor()
        cur.execute("SELECT details, COUNT(*) as c FROM user_actions WHERE action_type='card_created' AND details IS NOT NULL GROUP BY details ORDER BY c DESC LIMIT 5")
        rows = cur.fetchall()
        cur.close(); conn.close()
        return rows

    try:
        rows = await asyncio.to_thread(_get_top)
        if not rows:
            await send_message_fallback(update, "📭 Топ пуст.")
            return
        msg = "🔥 **Топ товаров:**\n\n"
        for i, r in enumerate(rows, 1):
            msg += f"**{i}.** {r['details'][:50]}... ({r['c']} шт.)\n"
        await send_message_fallback(update, msg, reply_markup=get_admin_keyboard())
    except Exception as e:
        await send_message_fallback(update, f"❌ Ошибка: {str(e)}")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await send_message_fallback(update, "⛔ Только для админа.")
        return
    await send_message_fallback(update, "🔐 **Админ-панель**\nВыбери раздел:", reply_markup=get_admin_keyboard())

# ====== ОБРАБОТЧИКИ МЕДИА И СООБЩЕНИЙ ======

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats["total_photos"] += 1
    await log_action(user_id, "photo")
    
    photo = await update.message.photo[-1].get_file()
    pending_photos[user_id] = await photo.download_as_bytearray()
    
    await send_message_fallback(update, 
        "️ **Фото получил!** Напиши или надиктуй детали:\n"
        "1. Цвет/версия\n2. Для кого\n3. Особенности",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👉 Заполнить", callback_data="fill_photo")]]))

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats["total_voice"] += 1
    await log_action(user_id, "voice")
    
    status = await update.message.reply_text("🎤 **Распознаю голос...**")
    voice = await update.message.voice.get_file()
    text = await transcribe_voice(voice)
    
    if not text:
        await status.edit_text("❌ Не понял голос. Попробуй еще раз или напиши текстом.")
        return
    
    await status.edit_text(f"✅ **Распознал:**\n\"{text}\"\n_Обрабатываю..._", parse_mode='Markdown')
    await process_user_input(update, user_id, text)

async def process_user_input(update: Update, user_id: int, text: str):
    text_lower = text.lower()
    stats["total_messages"] += 1
    await log_action(user_id, "message", text[:100])
    
    # Умная валидация
    if user_id in memory and memory[user_id]:
        last_ai = next((m["content"] for m in reversed(memory[user_id]) if m["role"] == "assistant"), "")
        if any(w in last_ai.lower() for w in ["вопрос", "уточнить", "детал"]):
            await show_progress(update, 5, 8)
            
            has_color = any(w in text_lower for w in ["цвет", "версия", "бел", "чёрн", "син", "размер", "s", "m", "l", "xl", "накладн", "микрофон"])
            has_audience = any(w in text_lower for w in ["аудитори", "спортсмен", "дет", "взросл", "геймер", "музык", "18+", "люди", "человек", "женщин", "мужчин"])
            has_seo = any(w in text_lower for w in ["seo", "ключ", "водостойк", "bluetooth", "автоном", "батаре", "звук", "шум", "функци", "особенност"])
            
            missing = []
            if not has_color: missing.append("цвет")
            if not has_audience: missing.append("аудиторию")
            if not has_seo: missing.append("особенности")
            
            if missing:
                kb = [[InlineKeyboardButton("💡 Пример", callback_data="show_example")]]
                await send_message_fallback(update, 
                    f"🙏 Спасибо! Не хватает: **{', '.join(missing)}**.\n\n"
                    f"💡 Пример: «1. Чёрные. 2. Для геймеров. 3. Bluetooth, шумоподавление»", 
                    reply_markup=InlineKeyboardMarkup(kb))
                return
            
            await show_progress(update, 6, 8)
            await show_progress(update, 7, 8)

    await update.message.chat.send_action(action="typing")
    await show_progress(update, 1, 8)
    await show_progress(update, 2, 8)
    
    ai_response = await get_ai_response_async(user_id, text)
    
    await show_progress(update, 8, 8)
    await send_message_fallback(update, ai_response, reply_markup=get_main_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await process_user_input(update, user_id, update.message.text)

# ====== CALLBACK (КНОПКИ) ======

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    if data == "admin_stats": await stats_command(update, context); return
    if data == "admin_users": await users_command(update, context); return
    if data == "admin_top": await top_command(update, context); return
    if data == "admin_logs": 
        await query.edit_message_text("📜 Логи пишутся в БД (`user_actions`). Используй `/users`.", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
        return
    
    if data == "new_card":
        await query.edit_message_text("➕ **Новая карточка**\nНапиши или надиктуй товар:", parse_mode='Markdown')
    elif data == "my_cards": await mycards_command(update, context)
    elif data == "edit_last":
        if user_id not in card_history or not card_history[user_id]:
            await query.edit_message_text("😕 Сначала создай карточку!", reply_markup=get_main_keyboard(), parse_mode='Markdown')
        else:
            await query.edit_message_text("️ Напиши изменения или выбери кнопку:", reply_markup=get_edit_keyboard(), parse_mode='Markdown')
    elif data in ["edit_color", "edit_audience", "edit_seo", "edit_custom"]:
        await query.edit_message_text("️ Напиши или надиктуй, что изменить:", parse_mode='Markdown')
    elif data == "help": await help_command(update, context)
    elif data == "back_to_main":
        await query.edit_message_text("🏠 **Главное меню**\nЧто делаем?", reply_markup=get_main_keyboard(), parse_mode='Markdown')
    elif data == "upload_photo":
        await query.edit_message_text("🖼️ Загрузи фото товара (скрепка -> Фото).", parse_mode='Markdown')
    elif data == "fill_photo":
        await query.edit_message_text("🖼️ Напиши или надиктуй детали для фото:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]]), parse_mode='Markdown')
    elif data == "show_example":
        await query.edit_message_text("💡 **Пример:**\n1. Чёрные, с микрофоном\n2. Для геймеров\n3. Bluetooth, шумоподавление\n\nЖду твой ответ! 👇", parse_mode='Markdown')

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

def main():
    load_memory()
    application = Application.builder().token(os.getenv("TELEGRAM_TOKEN")).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("newchat", newchat))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("edit", edit_command))
    application.add_handler(CommandHandler("mycards", mycards_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(CommandHandler("admin", admin_command))
    
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    logger.info("Бот запускается (Оптимизированная версия)...")
    application.run_polling()

if __name__ == '__main__':
    main()
