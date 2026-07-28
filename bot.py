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

def log_action(user_id, action_type, details=""):
    """Логирование действия пользователя в БД"""
    try:
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_actions (
                id SERIAL PRIMARY KEY, 
                user_id BIGINT, 
                action_type TEXT, 
                details TEXT, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

def load_memory():
    """Загрузка памяти из базы данных"""
    global card_history, stats
    try:
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cursor = conn.cursor()
        
        cursor.execute("CREATE TABLE IF NOT EXISTS user_memory (user_id BIGINT PRIMARY KEY, history JSONB)")
        cursor.execute("SELECT user_id, history FROM user_memory")
        rows = cursor.fetchall()
        for row in rows:
            memory[row['user_id']] = row['history']
        
        cursor.execute("CREATE TABLE IF NOT EXISTS product_cards (id SERIAL PRIMARY KEY, user_id BIGINT, card_text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("SELECT user_id, card_text FROM product_cards ORDER BY created_at DESC")
        rows = cursor.fetchall()
        for row in rows:
            if row['user_id'] not in card_history:
                card_history[row['user_id']] = []
            card_history[row['user_id']].append(row['card_text'])
        
        # Загрузка статистики
        cursor.execute("SELECT COUNT(DISTINCT user_id) as total_users FROM user_actions")
        row = cursor.fetchone()
        if row:
            stats["total_users"] = {row['total_users']}
        
        cursor.execute("SELECT COUNT(*) as total FROM user_actions WHERE action_type = 'message'")
        row = cursor.fetchone()
        if row:
            stats["total_messages"] = row['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM product_cards")
        row = cursor.fetchone()
        if row:
            stats["total_cards"] = row['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM user_actions WHERE action_type = 'voice'")
        row = cursor.fetchone()
        if row:
            stats["total_voice"] = row['total']
        
        cursor.execute("SELECT COUNT(*) as total FROM user_actions WHERE action_type = 'photo'")
        row = cursor.fetchone()
        if row:
            stats["total_photos"] = row['total']
        
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

def save_card(user_id, card_text):
    """Сохранение карточки в базу данных"""
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
        
        # Логируем создание карточки
        log_action(user_id, "card_created", card_text[:100])
        stats["total_cards"] += 1
        
        logger.info(f"Карточка сохранена для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка сохранения карточки: {e}")

async def show_progress(update: Update, step: int, total_steps: int):
    """Показывает прогресс создания карточки"""
    progress_messages = {
        1: "⏳ **Анализирую запрос...**\n_Понимаю, что нужно описать_",
        2: "✅ **Понял задачу!**\n_Готовлю уточняющие вопросы_",
        3: "✍️ **Задаю вопросы...**\n_Нужно уточнить детали_",
        4: "✅ **Вопросы заданы!**\n_Жду твои ответы_",
        5: "🔍 **Проверяю ответы...**\n_Анализирую информацию_",
        6: "✅ **Ответы получены!**\n_Начинаю создавать карточку_",
        7: "✍️ **Создаю карточку...**\n_Пишу продающее описание_",
        8: "✅ **Карточка готова!**\n_Сохраняю и отправляю_"
    }
    
    if step in progress_messages:
        progress_bar = "█" * step + "░" * (total_steps - step)
        message = f"**Прогресс:** [{progress_bar}] {step}/{total_steps}\n\n{progress_messages[step]}"
        await update.message.reply_text(message, parse_mode='Markdown')
        await asyncio.sleep(0.5)

async def transcribe_voice(voice_file):
    """Распознавание голосового сообщения через Groq Whisper"""
    try:
        voice_data = await voice_file.download_as_bytearray()
        voice_file_obj = BytesIO(voice_data)
        voice_file_obj.name = "voice.ogg"
        
        transcription = client.audio.transcriptions.create(
            file=voice_file_obj,
            model="whisper-large-v3-turbo",
            language="ru"
        )
        
        return transcription.text
    except Exception as e:
        logger.error(f"Ошибка распознавания голоса: {e}")
        return None

def get_ai_response(user_id, user_message, photo_analysis=""):
    """Получение ответа от AI с системным промптом"""
    if user_id not in memory:
        memory[user_id] = []
    
    if photo_analysis:
        memory[user_id].append({"role": "user", "content": f"На изображении видно: {photo_analysis}"})
    
    memory[user_id].append({"role": "user", "content": user_message})
    
    if len(memory[user_id]) > 10:
        memory[user_id] = memory[user_id][-10:]
    
    try:
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

**SEO-ключи:** [список ключевых слов через запятую]

СТИЛЬ:
- Пиши на русском языке
- Используй продающие формулировки
- Будь конкретен (цифры, факты)
- Избегай воды и общих фраз
- Используй эмодзи умеренно (1-2 на раздел)"""
        
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                *memory[user_id]
            ]
        )
        
        ai_message = response.choices[0].message.content
        memory[user_id].append({"role": "assistant", "content": ai_message})
        save_memory(memory)
        
        save_card(user_id, ai_message)
        
        return ai_message
    except Exception as e:
        logger.error(f"Ошибка при получении ответа от AI: {e}")
        return f"Ошибка: {str(e)}"

# ====== Вспомогательные функции ======

async def send_long_message(update: Update, text: str, reply_markup=None):
    """Отправка длинного сообщения с разбиением на части"""
    max_length = 4000
    
    if len(text) <= max_length:
        if reply_markup:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, parse_mode='Markdown')
        return
    
    parts = []
    while len(text) > max_length:
        split_pos = text.rfind('\n\n', 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind('\n', 0, max_length)
        if split_pos == -1:
            split_pos = max_length
        
        parts.append(text[:split_pos])
        text = text[split_pos:].lstrip()
    
    if text:
        parts.append(text)
    
    for i, part in enumerate(parts):
        if i == len(parts) - 1 and reply_markup:
            await update.message.reply_text(part, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(part, parse_mode='Markdown')

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом"""
    return user_id == ADMIN_ID

# ====== Клавиатуры ======

def get_main_keyboard():
    """Главная клавиатура с основными действиями"""
    keyboard = [
        [
            InlineKeyboardButton("➕ Новая карточка", callback_data="new_card"),
            InlineKeyboardButton(" Мои карточки", callback_data="my_cards")
        ],
        [
            InlineKeyboardButton("️ Редактировать", callback_data="edit_last"),
            InlineKeyboardButton("🖼️ Загрузить фото", callback_data="upload_photo")
        ],
        [
            InlineKeyboardButton("❓ Помощь", callback_data="help")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_edit_keyboard():
    """Клавиатура для редактирования"""
    keyboard = [
        [
            InlineKeyboardButton("💬 Изменить цвет", callback_data="edit_color"),
            InlineKeyboardButton(" Изменить ЦА", callback_data="edit_audience")
        ],
        [
            InlineKeyboardButton("🔑 Изменить SEO", callback_data="edit_seo"),
            InlineKeyboardButton(" Свой запрос", callback_data="edit_custom")
        ],
        [
            InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_help_keyboard():
    """Клавиатура для справки"""
    keyboard = [
        [
            InlineKeyboardButton("➕ Создать карточку", callback_data="new_card"),
            InlineKeyboardButton("📋 Мои карточки", callback_data="my_cards")
        ],
        [
            InlineKeyboardButton("✏️ Редактировать", callback_data="edit_last"),
            InlineKeyboardButton("🖼️ Загрузить фото", callback_data="upload_photo")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    """Клавиатура админ-панели"""
    keyboard = [
        [
            InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")
        ],
        [
            InlineKeyboardButton("🔥 Топ товаров", callback_data="admin_top"),
            InlineKeyboardButton("📜 Логи", callback_data="admin_logs")
        ],
        [
            InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ====== Обработчики команд ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.message.from_user.id
    stats["total_users"].add(user_id)
    log_action(user_id, "start")
    
    await update.message.reply_text(
        "👋 **Привет! Я AI-бот для создания карточек товаров.**\n\n"
        "Я помогу тебе создать профессиональную карточку для маркетплейса за пару минут!\n\n"
        " **Ты можешь:**\n"
        "• ✍️ Написать текст\n"
        "• 🖼️ Отправить фото товара\n"
        "• 🎤 **Отправить голосовое сообщение** (я распознаю!)\n\n"
        "👇 **Выбери действие:**",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /clear"""
    user_id = update.message.from_user.id
    if user_id in memory:
        del memory[user_id]
        save_memory(memory)
    log_action(user_id, "clear")
    await update.message.reply_text(
        "✅ **Память очищена!**\n\n"
        "Теперь мы начнём с чистого листа. Что будем создавать?",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

async def newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /newchat"""
    user_id = update.message.from_user.id
    if user_id in memory:
        del memory[user_id]
        save_memory(memory)
    log_action(user_id, "newchat")
    await update.message.reply_text(
        "🔄 **Начинаем новый диалог!**\n\n"
        "Напиши, какой товар нужно описать, или выбери действие:",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    user_id = update.message.from_user.id
    log_action(user_id, "help")
    
    help_text = """
🤖 **AI-бот для создания карточек товаров**

Я помогаю создавать профессиональные карточки для маркетплейсов (Wildberries, Ozon, Яндекс.Маркет).

📋 **Доступные команды:**
/start — Приветствие и начало работы
/help — Показать эту справку
/newchat — Начать новый диалог
/clear — Очистить историю сообщений
/edit — Редактировать последнюю карточку
/mycards — Посмотреть все мои карточки
"""
    
    # Добавляем админ-команды для админа
    if is_admin(user_id):
        help_text += """
🔐 **Админ-команды:**
/stats — Статистика использования
/users — Список пользователей
/top — Топ популярных товаров
/admin — Админ-панель
"""
    
    help_text += """
💡 **Как работать со мной:**
1. Напиши текст, отправь фото или **голосовое сообщение**
2. Я задам 3 уточняющих вопроса
3. Ответь на вопросы (можно тоже голосом!)
4. Получи готовую карточку!

🎯 **Пример запроса:** "Напиши карточку для фитнес-браслета"
 **Пример ответа:** "1. Чёрный, премиум. 2. Для спортсменов. 3. Водостойкий, Bluetooth 5.0"

👇 **Выбери действие:**
"""
    await update.message.reply_text(
        help_text,
        reply_markup=get_help_keyboard(),
        parse_mode='Markdown'
    )

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /edit"""
    user_id = update.message.from_user.id
    log_action(user_id, "edit_command")
    
    if user_id not in card_history or len(card_history[user_id]) == 0:
        await update.message.reply_text(
            "😕 **У тебя ещё нет созданных карточек.**\n\n"
            "Сначала создай карточку, а потом редактируй её!",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    edit_text = " ".join(context.args) if context.args else ""
    
    if not edit_text:
        await update.message.reply_text(
            "️ **Редактирование карточки**\n\n"
            "Напиши, что нужно изменить. Например:\n\n"
            "• `/edit измени цвет на синий`\n"
            "• `/edit добавь информацию о гарантии`\n"
            "• `/edit сделай описание короче`\n\n"
            "Или выбери быстрое действие:",
            reply_markup=get_edit_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    last_card = card_history[user_id][-1]
    
    await update.message.chat.send_action(action="typing")
    
    edit_prompt = f"""Ты — профессиональный копирайтер. 

Вот последняя созданная карточка:
{last_card}

Пользователь просит внести следующие изменения: {edit_text}

Внеси изменения в карточку, сохранив общий стиль и формат. Верни полную обновлённую карточку."""
    
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": edit_prompt}]
        )
        
        edited_card = response.choices[0].message.content
        
        save_card(user_id, edited_card)
        log_action(user_id, "edit_card", edit_text)
        
        await send_long_message(
            update,
            "✅ **Карточка обновлена!**\n\n" + edited_card,
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при редактировании: {e}")
        await update.message.reply_text(f"❌ Ошибка при редактировании: {str(e)}")

async def mycards_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /mycards"""
    user_id = update.message.from_user.id
    log_action(user_id, "mycards")
    
    if user_id not in card_history or len(card_history[user_id]) == 0:
        await update.message.reply_text(
            "📭 **У тебя ещё нет созданных карточек.**\n\n"
            "Создай первую карточку, и она появится здесь!",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    cards = card_history[user_id][-5:]
    
    message = f"📋 **Твои последние карточки** ({len(cards)} из {len(card_history[user_id])}):\n\n"
    
    for i, card in enumerate(reversed(cards), 1):
        preview = card[:150].replace("\n", " ").replace("**", "") + "..." if len(card) > 150 else card
        message += f"**{i}.** {preview}\n\n"
    
    await update.message.reply_text(
        message,
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

# ====== АДМИН-КОМАНДЫ ======

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stats - статистика"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Эта команда доступна только администратору.")
        return
    
    log_action(user_id, "stats")
    
    try:
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cursor = conn.cursor()
        
        # Общее количество действий
        cursor.execute("SELECT COUNT(*) as total FROM user_actions")
        total_actions = cursor.fetchone()['total']
        
        # Количество уникальных пользователей
        cursor.execute("SELECT COUNT(DISTINCT user_id) as total FROM user_actions")
        total_users = cursor.fetchone()['total']
        
        # Количество созданных карточек
        cursor.execute("SELECT COUNT(*) as total FROM product_cards")
        total_cards = cursor.fetchone()['total']
        
        # Действия за сегодня
        cursor.execute("SELECT COUNT(*) as total FROM user_actions WHERE created_at >= CURRENT_DATE")
        today_actions = cursor.fetchone()['total']
        
        # Карточки за сегодня
        cursor.execute("SELECT COUNT(*) as total FROM product_cards WHERE created_at >= CURRENT_DATE")
        today_cards = cursor.fetchone()['total']
        
        cursor.close()
        conn.close()
        
        stats_text = f"""
📊 **Статистика бота**

👥 **Пользователи:**
• Всего уникальных: {total_users}

📝 **Действия:**
• Всего действий: {total_actions}
• Сегодня: {today_actions}

🎴 **Карточки:**
• Всего создано: {total_cards}
• Сегодня: {today_cards}

🎤 **Голосовые:** {stats['total_voice']}
🖼️ **Фото:** {stats['total_photos']}

_Данные обновлены: {datetime.now().strftime('%d.%m.%Y %H:%M')}_
"""
        await update.message.reply_text(
            stats_text,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /users - список пользователей"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Эта команда доступна только администратору.")
        return
    
    log_action(user_id, "users")
    
    try:
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT user_id, COUNT(*) as actions_count, MAX(created_at) as last_action 
            FROM user_actions 
            GROUP BY user_id 
            ORDER BY actions_count DESC
            LIMIT 20
        """)
        rows = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        if not rows:
            await update.message.reply_text("📭 Пользователей пока нет.")
            return
        
        message = "👥 **Топ-20 пользователей:**\n\n"
        for i, row in enumerate(rows, 1):
            last_action = row['last_action'].strftime('%d.%m %H:%M') if row['last_action'] else 'неизвестно'
            message += f"**{i}.** ID: `{row['user_id']}` — {row['actions_count']} действий (последнее: {last_action})\n"
        
        await update.message.reply_text(
            message,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка получения списка пользователей: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /top - топ популярных товаров"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Эта команда доступна только администратору.")
        return
    
    log_action(user_id, "top")
    
    try:
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT details, COUNT(*) as count 
            FROM user_actions 
            WHERE action_type = 'card_created' AND details IS NOT NULL
            GROUP BY details 
            ORDER BY count DESC 
            LIMIT 10
        """)
        rows = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        if not rows:
            await update.message.reply_text("📭 Топ товаров пока пуст.")
            return
        
        message = "🔥 **Топ-10 товаров:**\n\n"
        for i, row in enumerate(rows, 1):
            preview = row['details'][:60] + "..." if len(row['details']) > 60 else row['details']
            message += f"**{i}.** {preview} — создано: {row['count']} раз(а)\n\n"
        
        await update.message.reply_text(
            message,
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка получения топа: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /admin - админ-панель"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Эта команда доступна только администратору.")
        return
    
    log_action(user_id, "admin_panel")
    
    await update.message.reply_text(
        "🔐 **Админ-панель**\n\n"
        "Выбери раздел:",
        reply_markup=get_admin_keyboard(),
        parse_mode='Markdown'
    )

# ====== Обработчик загрузки фото ======

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик загрузки фото"""
    user_id = update.message.from_user.id
    stats["total_photos"] += 1
    log_action(user_id, "photo")
    
    photo_file = await update.message.photo[-1].get_file()
    photo_data = await photo_file.download_as_bytearray()
    
    pending_photos[user_id] = {
        "photo_data": photo_data,
        "timestamp": update.message.date
    }
    
    await update.message.reply_text(
        "️ **Фото получено!**\n\n"
        "Пожалуйста, укажи детали (можно текстом или голосом):\n"
        "• Цвет/версия\n"
        "• Целевая аудитория\n"
        "• Особенности/SEO-ключи\n\n"
        "Например:\n"
        "«1. Чёрные накладные\n"
        "2. Для спортсменов и геймеров\n"
        "3. Водостойкие, Bluetooth 5.0»",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👉 Заполнить детали", callback_data="fill_photo_details")]
        ])
    )

async def fill_photo_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Заполнение деталей после загрузки фото"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if user_id not in pending_photos:
        await query.edit_message_text(
            "⚠️ **Фото не найдено!**\n\n"
            "Пожалуйста, загрузи фото заново.",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    await query.edit_message_text(
        "🖼️ **Заполнение деталей**\n\n"
        "Пожалуйста, укажи детали (можно текстом или голосом):\n"
        "• Цвет/версия\n"
        "• Целевая аудитория\n"
        "• Особенности/SEO-ключи\n\n"
        "Например:\n"
        "«1. Чёрные накладные\n"
        "2. Для спортсменов и геймеров\n"
        "3. Водостойкие, Bluetooth 5.0»",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
        ])
    )

# ====== Обработчик голосовых сообщений ======

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик голосовых сообщений"""
    user_id = update.message.from_user.id
    stats["total_voice"] += 1
    log_action(user_id, "voice")
    
    status_msg = await update.message.reply_text("🎤 **Распознаю голосовое сообщение...**\n_Подожди несколько секунд_", parse_mode='Markdown')
    
    voice_file = await update.message.voice.get_file()
    
    recognized_text = await transcribe_voice(voice_file)
    
    if recognized_text is None:
        await status_msg.edit_text(
            "❌ **Не удалось распознать голос.**\n\n"
            "Попробуй ещё раз или напиши текст.",
            parse_mode='Markdown'
        )
        return
    
    await status_msg.edit_text(
        f"✅ **Распознал:**\n\n\"{recognized_text}\"\n\n_Обрабатываю..._",
        parse_mode='Markdown'
    )
    
    await process_user_input(update, user_id, recognized_text)

# ====== Общая логика обработки сообщения ======

async def process_user_input(update: Update, user_id: int, user_message: str):
    """Общая функция обработки текста"""
    user_message_lower = user_message.lower()
    stats["total_messages"] += 1
    log_action(user_id, "message", user_message[:100])
    
    if user_id in memory and len(memory[user_id]) > 0:
        last_ai_message = ""
        for msg in reversed(memory[user_id]):
            if msg["role"] == "assistant":
                last_ai_message = msg["content"]
                break
        
        if "вопрос" in last_ai_message.lower() or "уточнить" in last_ai_message.lower() or "детал" in last_ai_message.lower():
            
            await show_progress(update, 5, 8)
            
            color_words = ["цвет", "версия", "бел", "чёрн", "черн", "син", "красн", "зелён", 
                          "премиум", "базов", "стандарт", "размер", "s", "m", "l", "xl",
                          "накладн", "вкладыш", "проводн", "безпроводн", "микрофон",
                          "жёлт", "оранж", "фиолет", "розов", "сер", "коричн",
                          "золот", "серебр", "металл", "пластик", "силикон", "кож",
                          "универс", "унисекс", "мужск", "женск"]
            has_color = any(word in user_message_lower for word in color_words)
            
            audience_words = ["аудитори", "спортсмен", "дет", "взросл", "професс", 
                             "любитель", "геймер", "музык", "фитнес", "бег", "трениров",
                             "офис", "работ", "дом", "улица", "18+", "16+", "подрост",
                             "мужчин", "женщин", "универсал", "все", "кажд", "пользовател",
                             "клиент", "покупател", "люди", "человек", "парень", "девушк",
                             "мальчик", "девочк", "студент", "школьник", "пенсионер",
                             "мам", "пап", "ребён", "семь", "активн", "начинающ", "опытн"]
            has_audience = any(word in user_message_lower for word in audience_words)
            
            seo_words = ["seo", "ключ", "водостойк", "мониторинг", "отслеживан", "шаг",
                        "пульс", "сердечн", "ритм", "bluetooth", "wifi", "gps", "наушник",
                        "звук", "бас", "шум", "автоном", "батаре", "заряд", "время",
                        "работа", "поддержк", "совместим", "android", "ios", "iphone",
                        "качество", "hi", "hd", "стерео", "мощн", "громк", "тих",
                        "функци", "возможност", "особенност", "характеристик", "параметр",
                        "давлен", "калори", "сон", "активн", "тренировк", "упражнен",
                        "бего", "плаван", "велосипед", "ходьб", "прыжк", "йога",
                        "лёгк", "тяжёл", "компакт", "удобн", "прочн", "надёжн",
                        "быстр", "медлен", "точн", "умн", "интеллек", "автомат"]
            has_seo = any(word in user_message_lower for word in seo_words)
            
            missing = []
            if not has_color:
                missing.append("цвет/версию/размер")
            if not has_audience:
                missing.append("целевую аудиторию")
            if not has_seo:
                missing.append("ключевые особенности/функции")
            
            if missing:
                keyboard = [
                    [InlineKeyboardButton("💡 Пример ответа", callback_data="show_example")],
                    [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
                ]
                await update.message.reply_text(
                    f"🙏 **Спасибо за ответ!**\n\n"
                    f"Чтобы создать идеальную карточку, мне нужно ещё немного информации:\n\n"
                    f"️ **Не хватает:** {', '.join(missing)}\n\n"
                    f"💡 **Пример хорошего ответа:**\n"
                    f"«1. Цвет: чёрные, с микрофоном\n"
                    f"2. Для кого: для спортсменов и любителей музыки\n"
                    f"3. Особенности: водостойкие, Bluetooth 5.0, автономность 20 часов»",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return
            
            await show_progress(update, 6, 8)
            await show_progress(update, 7, 8)
    
    await update.message.chat.send_action(action="typing")
    
    await show_progress(update, 1, 8)
    await show_progress(update, 2, 8)
    
    ai_response = get_ai_response(user_id, user_message)
    
    await show_progress(update, 8, 8)
    
    await send_long_message(
        update,
        ai_response,
        reply_markup=get_main_keyboard()
    )

# ====== Обработчик callback-запросов (кнопки) ======

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на инлайн-кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    # Админ-кнопки
    if data == "admin_stats":
        await stats_command(update, context)
        return
    elif data == "admin_users":
        await users_command(update, context)
        return
    elif data == "admin_top":
        await top_command(update, context)
        return
    elif data == "admin_logs":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ Доступ запрещён.")
            return
        await query.edit_message_text(
            "📜 **Логи действий**\n\n"
            "Все действия пользователей записываются в таблицу `user_actions` в базе данных.\n\n"
            "Для просмотра используй команду `/users` или `/top`.",
            reply_markup=get_admin_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    if data == "new_card":
        await query.edit_message_text(
            "➕ **Новая карточка**\n\n"
            "Напиши, какой товар нужно описать (текстом или голосом). Например:\n"
            "• \"Напиши карточку для наушников\"\n"
            "• \"Создай карточку для фитнес-браслета\"\n"
            "• \"Опиши умные часы\""
        )
    
    elif data == "my_cards":
        await mycards_command(update, context)
    
    elif data == "edit_last":
        if user_id not in card_history or len(card_history[user_id]) == 0:
            await query.edit_message_text(
                "😕 **У тебя ещё нет карточек для редактирования.**\n\n"
                "Сначала создай карточку!",
                reply_markup=get_main_keyboard(),
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                "✏️ **Редактирование последней карточки**\n\n"
                "Напиши, что нужно изменить (текстом или голосом). Например:\n"
                "• \"измени цвет на синий\"\n"
                "• \"добавь информацию о гарантии\"\n"
                "• \"сделай описание короче\"\n\n"
                "Или выбери быстрое действие:",
                reply_markup=get_edit_keyboard(),
                parse_mode='Markdown'
            )
    
    elif data == "edit_color":
        await query.edit_message_text(
            "🎨 **Изменение цвета**\n\n"
            "Напиши или надиктуй, какой цвет должен быть у товара. Например:\n"
            "• \"синий\"\n"
            "• \"чёрный матовый\"\n"
            "• \"белый с золотыми вставками\""
        )
    
    elif data == "edit_audience":
        await query.edit_message_text(
            "👥 **Изменение целевой аудитории**\n\n"
            "Напиши или надиктуй, для кого предназначен товар. Например:\n"
            "• \"для спортсменов\"\n"
            "• \"для геймеров 18+\"\n"
            "• \"для детей от 6 лет\""
        )
    
    elif data == "edit_seo":
        await query.edit_message_text(
            "🔑 **Изменение SEO-ключей**\n\n"
            "Напиши или надиктуй ключевые слова. Например:\n"
            "• \"водостойкий, Bluetooth 5.0\"\n"
            "• \"автономность 20 часов\"\n"
            "• \"шумоподавление\""
        )
    
    elif data == "edit_custom":
        await query.edit_message_text(
            "📝 **Свой запрос на редактирование**\n\n"
            "Напиши или надиктуй, что нужно изменить. Например:\n"
            "• \"добавь раздел про гарантию\"\n"
            "• \"сделай описание более продающим\"\n"
            "• \"убери технические характеристики\""
        )
    
    elif data == "help":
        await help_command(update, context)
    
    elif data == "back_to_main":
        await query.edit_message_text(
            "🏠 **Главное меню**\n\n"
            "Что будем делать?",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
    
    elif data == "upload_photo":
        await query.edit_message_text(
            "🖼️ **Загрузка фото**\n\n"
            "Пожалуйста, загрузи фото товара. Я проанализирую его и создам карточку!\n\n"
            "💡 **Важно:**\n"
            "• Загружай фото в хорошем качестве\n"
            "• Не используй скриншоты экрана\n"
            "• Убедись, что товар хорошо виден"
        )
    
    elif data == "fill_photo_details":
        await fill_photo_details(update, context)

# ====== Обработчик примера ответа ======

async def show_example_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать пример хорошего ответа"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "💡 **Пример хорошего ответа:**\n\n"
        "«1. **Цвет:** чёрные, с микрофоном\n"
        "2. **Для кого:** для спортсменов и любителей музыки\n"
        "3. **Особенности:** водостойкие, Bluetooth 5.0, автономность 20 часов»\n\n"
        "Напиши свой ответ в таком же формате! 👇\n\n"
        "_Можно текстом или голосом 🎤_",
        parse_mode='Markdown'
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Update {update} caused error {context.error}")

# ====== Обработчик обычных текстовых сообщений ======

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик обычных текстовых сообщений"""
    user_id = update.message.from_user.id
    user_message = update.message.text
    
    await process_user_input(update, user_id, user_message)

def main():
    """Запуск бота"""
    load_memory()
    
    application = Application.builder().token(os.getenv("TELEGRAM_TOKEN")).build()
    
    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("newchat", newchat))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("edit", edit_command))
    application.add_handler(CommandHandler("mycards", mycards_command))
    
    # Админ-команды
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(CommandHandler("admin", admin_command))
    
    # Регистрация обработчика callback-запросов (кнопки)
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(CallbackQueryHandler(show_example_callback, pattern="^show_example$"))
    
    # Регистрация обработчика фото
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # Регистрация обработчика голосовых сообщений
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    # Обработчик обычных текстовых сообщений (в конце!)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    logger.info("Бот запускается...")
    application.run_polling()

if __name__ == '__main__':
    main()
