import os
import json
import logging
import asyncio
import time
import re
import urllib.parse
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from groq import Groq, RateLimitError, APIError
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from io import BytesIO
from datetime import datetime

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

memory = {}
card_history = {}
pending_photos = {}
pending_broadcast = {}

stats = {
    "total_messages": 0,
    "total_cards": 0,
    "total_voice": 0,
    "total_photos": 0
}

# ====== Система режимов ======
user_modes = {}

MODE_PROMPTS = {
    "marketplace": """Ты — профессиональный копирайтер для маркетплейсов (Wildberries, Ozon, Яндекс.Маркет).
🚫 СТРОГИЕ ПРАВИЛА (НЕ НАРУШАЙ):
1. НИКОГДА не пиши вступлений типа "Я вижу...", "На основе...", "Вот карточка..."
2. НИКОГДА не пиши завершений типа "Надеюсь...", "Если нужно изменить..."
3. Верни ТОЛЬКО текст карточки — без лишних слов до и после
✅ ПРАВИЛА РАБОТЫ:
1. Сначала задай 3 вопроса (цвет/версия, аудитория, SEO). Жди ответов.
2. Только после получения всех ответов создавай карточку.
📋 ФОРМАТ КАРТОЧКИ (строго придерживайся):
**Название:** [Краткое, цепляющее название с ключевыми словами]
**Описание:** [2-3 предложения о товаре, его преимуществах и пользе для покупателя]
**Характеристики:** (5 пунктов)
**Функции и преимущества:** (3 пункта)
**Для кого подходит:** [описание целевой аудитории]
**SEO-ключи:** [список ключевых слов через запятую]
✍️ СТИЛЬ: Русский язык, продающие формулировки, конкретика, без воды, умеренные эмодзи.""",

    "finance": """Ты — опытный финансовый аналитик и инвестиционный советник.
Твоя задача: давать четкие, структурированные и полезные ответы о финансах, инвестициях, криптовалютах и экономике.
Правила:
1. Всегда добавляй дисклеймер: "⚠️ Это не индивидуальная инвестиционная рекомендация".
2. Используй факты, цифры и структурированный формат (списки, жирный шрифт).
3. Отвечай на русском языке, профессионально, но доступно.""",

    "cooking": """Ты — шеф-повар мирового уровня и кулинарный эксперт.
Твоя задача: создавать вкусные, подробные и понятные рецепты, давать советы по приготовлению.
Правила:
1. Всегда указывай ингредиенты с точными граммовками.
2. Описывай пошаговый процесс приготовления.
3. Давай профессиональные советы (чем заменить ингредиент, как красиво подать).""",

    "universal": """Ты — универсальный, эрудированный и полезный AI-помощник.
Твоя задача: отвечать на любые вопросы пользователя (политика, спорт, культура, медицина, техника и т.д.) максимально точно, объективно и структурированно.
Правила:
1. Если вопрос касается медицины или права, добавляй дисклеймер о необходимости консультации со специалистом.
2. Используй структурированный формат (заголовки, списки).
3. Отвечай на русском языке.""",

    "screenwriter": """Ты — профессиональный сценарист для YouTube, TikTok и коротких видео.
Твоя задача: создавать цепляющие сценарии, хуки, структуры видео.
Правила:
1. Всегда начинай с сильного хука (первые 3 секунды решают).
2. Используй структуру: Хук → Проблема → Решение → Призыв к действию.
3. Пиши разговорным языком, короткими предложениями.
4. Добавляй пометки для монтажа [СМЕНА КАДРА], [ТЕКСТ НА ЭКРАНЕ], [ЗВУК].""",

    "video_editor": """Ты — профессиональный видеомонтажер и режиссер монтажа.
Твоя задача: давать советы по монтажу, переходы, эффекты, ритм видео.
Правила:
1. Рекомендуй конкретные программы (Premiere Pro, DaVinci Resolve, CapCut).
2. Давай советы по ритму монтажа (когда резать, когда держать кадр).
3. Объясняй переходы, эффекты, цветокоррекцию простым языком.
4. Добавляй примеры таймкодов и структуры монтажа.""",

    "neuro_photoshoot": """Ты — профессиональный AI-фотограф и креативный директор нейрофотосессий.
Твоя задача: помогать пользователю создавать концепции для нейрофотосессий и генерировать промпты для AI-генерации изображений.
Правила:
1. Сначала уточни: кто на фото (мужчина/женщина/ребенок), возраст, стиль (деловой, casual, спортивный, вечерний), локация (студия, улица, природа, интерьер), настроение.
2. Предложи 3-5 разных концепций для фотосессии.
3. Для каждой концепции дай подробный промпт на английском для генерации изображения.
4. Давай советы по позированию, ракурсам, освещению.
5. Формат ответа:
**Концепция 1: [Название]**
📍 Локация: ...
👗 Стиль: ...
💡 Освещение: ...
🎨 Промпт: [английский промпт для генерации]""",

    "digital_avatar": """Ты — профессиональный дизайнер цифровых аватаров и AI-художник.
Твоя задача: помогать создавать уникальные цифровые аватары и персонажей.
Правила:
1. Сначала уточни: стиль (реалистичный, аниме, пиксель-арт, 3D, фэнтези, киберпанк), назначение (соцсети, игры, бизнес), цветовая гамма.
2. Предложи 3 варианта аватара с разными стилями.
3. Для каждого варианта дай подробный промпт на английском.
4. Давай советы по использованию аватара (где лучше смотрится, какие размеры).
5. Формат ответа:
**Вариант 1: [Название стиля]**
🎨 Стиль: ...
🎯 Назначение: ...
🎨 Промпт: [английский промпт для генерации]"""
}

# ====== Rate limiting ======
user_last_request = {}
RATE_LIMIT_SECONDS = 5
MAX_REQUESTS_PER_MINUTE = 10
user_request_count = {}

# ====== Таймауты ======
AI_TIMEOUT = 60
DB_TIMEOUT = 10

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
    global card_history
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
        
        cursor.close()
        conn.close()
        logger.info("Память загружена из базы данных")
    except Exception as e:
        logger.error(f"Ошибка загрузки памяти: {e}")

# ====== Rate limiting ======

def check_rate_limit(user_id: int) -> tuple:
    now = time.time()
    
    if user_id in user_last_request:
        time_since_last = now - user_last_request[user_id]
        if time_since_last < RATE_LIMIT_SECONDS:
            wait_time = max(1, int(RATE_LIMIT_SECONDS - time_since_last) + 1)
            return False, f"⏳ Пожалуйста, подожди {wait_time} сек. перед следующим запросом."
    
    if user_id not in user_request_count:
        user_request_count[user_id] = []
    
    user_request_count[user_id] = [t for t in user_request_count[user_id] if now - t < 60]
    
    if len(user_request_count[user_id]) >= MAX_REQUESTS_PER_MINUTE:
        return False, "⚠️ Слишком много запросов. Подожди минуту и попробуй снова."
    
    user_last_request[user_id] = now
    user_request_count[user_id].append(now)
    return True, ""

# ====== Проверка пустых сообщений ======

def is_empty_message(text: str) -> bool:
    if not text:
        return True
    cleaned = re.sub(r'[\s\U00010000-\U0010ffff\u200d\u20e3\ufe0f\u2600-\u27bf]', '', text)
    cleaned = re.sub(r'[^\wа-яА-ЯёЁa-zA-Z]', '', cleaned)
    return len(cleaned) == 0

# ====== Проверка команд смены режима ======

def check_mode_command(text: str) -> bool:
    mode_keywords = [
        "сменить режим", "поменять режим", "выбрать режим", "смена режима",
        "переключить режим", "изменить режим", "mode", "/mode"
    ]
    text_lower = text.lower().strip()
    return any(keyword in text_lower for keyword in mode_keywords)

# ====== Проверка команд генерации изображений ======

def check_image_generation_command(text: str) -> bool:
    image_keywords = [
        "сгенерировать фото", "создать изображение", "нарисуй картинку",
        "сделай фото", "сгенерируй картинку", "создай фото", "нарисуй фото",
        "сгенерировать изображение", "создать картинку"
    ]
    text_lower = text.lower().strip()
    return any(keyword in text_lower for keyword in image_keywords)

# ====== AI И ПРОГРЕСС ======

async def show_progress_simple(update: Update, message: str):
    await send_message_fallback(update, f"⏳ {message}")
    await asyncio.sleep(0.3)

async def show_progress(update: Update, step: int, total_steps: int):
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
        await send_message_fallback(update, message)
        await asyncio.sleep(0.3)

async def transcribe_voice(voice_file):
    try:
        voice_data = await voice_file.download_as_bytearray()
        voice_file_obj = BytesIO(voice_data)
        voice_file_obj.name = "voice.ogg"
        
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
    if user_id not in memory:
        memory[user_id] = []
    
    if photo_analysis:
        memory[user_id].append({"role": "user", "content": f"На изображении видно: {photo_analysis}"})
    
    memory[user_id].append({"role": "user", "content": user_message})
    if len(memory[user_id]) > 10:
        memory[user_id] = memory[user_id][-10:]
    
    current_mode = user_modes.get(user_id, "marketplace")
    system_prompt = MODE_PROMPTS.get(current_mode, MODE_PROMPTS["universal"])
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            def _call_groq():
                return client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "system", "content": system_prompt}, *memory[user_id]],
                    timeout=AI_TIMEOUT
                )
            
            response = await asyncio.wait_for(
                asyncio.to_thread(_call_groq),
                timeout=AI_TIMEOUT
            )
            ai_message = response.choices[0].message.content
            
            memory[user_id].append({"role": "assistant", "content": ai_message})
            await save_memory(memory)
            await save_card(user_id, ai_message)
            
            return ai_message
            
        except asyncio.TimeoutError:
            logger.warning(f"Таймаут AI (попытка {attempt + 1}/{max_retries})")
            if attempt == max_retries - 1:
                return "⏱️ **Превышено время ожидания.**\n\nAI слишком долго думает. Попробуй ещё раз через минуту или напиши `/newchat`."
            await asyncio.sleep(2)
            
        except RateLimitError as e:
            logger.warning(f"Rate limit от Groq (попытка {attempt + 1}/{max_retries})")
            if attempt == max_retries - 1:
                return "⚠️ **Превышен лимит запросов к AI.**\n\nПодожди 1-2 минуты и попробуй снова."
            await asyncio.sleep(5 * (attempt + 1))
            
        except APIError as e:
            logger.error(f"API ошибка Groq: {e}")
            if attempt == max_retries - 1:
                return f"❌ **Ошибка AI-сервиса:** {str(e)[:100]}\n\nПопробуй позже или напиши `/newchat`."
            await asyncio.sleep(2)
            
        except Exception as e:
            logger.error(f"Неожиданная ошибка AI: {e}")
            return f"❌ **Неожиданная ошибка:** {str(e)[:100]}\n\nПопробуй позже или напиши `/newchat`."
    
    return "❌ **Не удалось получить ответ от AI.** Попробуй позже."

# ====== Генерация изображений ======

def get_image_prompt_by_mode(user_id: str) -> str:
    mode_prompts = {
        "marketplace": "Professional product photography, white background, studio lighting, highly detailed, 8k resolution, photorealistic, commercial photography",
        "neuro_photoshoot": "Professional portrait photography, fashion editorial, studio lighting, highly detailed, 8k resolution, photorealistic, cinematic",
        "digital_avatar": "Digital avatar portrait, stylized character design, highly detailed, 8k resolution, vibrant colors, professional quality",
        "cooking": "Professional food photography, restaurant quality, appetizing, warm lighting, 8k resolution, highly detailed",
        "finance": "Abstract financial concept art, digital visualization, futuristic, blue and gold color scheme, 3D render, highly detailed",
        "screenwriter": "Cinematic movie still, dramatic lighting, film grain, professional cinematography, 8k resolution",
        "video_editor": "Video editing workspace, professional setup, multiple monitors, creative environment, highly detailed",
        "universal": "High quality professional photography, studio lighting, 4k resolution, highly detailed"
    }
    
    return mode_prompts.get(user_modes.get(user_id, "marketplace"), mode_prompts["universal"])

async def generate_image(prompt: str):
    try:
        safe_prompt = urllib.parse.quote(prompt[:200].replace("\n", " "))
        url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1024&height=1024&nologo=true&seed={int(time.time())}"
        
        def _download():
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.content
        
        image_bytes = await asyncio.to_thread(_download)
        return BytesIO(image_bytes)
    except Exception as e:
        logger.error(f"Ошибка генерации изображения: {e}")
        return None

async def generate_image_from_text(update: Update, user_id: int, text: str):
    await log_action(user_id, "image_generation_text")
    
    status_msg = await update.message.reply_text("⏳ **Генерирую изображение...**\nЭто может занять 10-20 секунд.")
    
    base_prompt = get_image_prompt_by_mode(user_id)
    
    description = text
    for keyword in ["сгенерировать фото", "создать изображение", "нарисуй картинку", 
                    "сделай фото", "сгенерируй картинку", "создай фото", "нарисуй фото",
                    "сгенерировать изображение", "создать картинку"]:
        description = description.lower().replace(keyword, "").strip()
    
    context_addition = ""
    if user_id in card_history and card_history[user_id]:
        last_card = card_history[user_id][-1]
        match = re.search(r'\*\*Название:\*\*\s*(.+)', last_card)
        if match:
            context_addition = f" of {match.group(1)}"
    elif user_id in memory and memory[user_id]:
        last_msg = memory[user_id][-1]['content']
        context_addition = f" based on: {last_msg[:80]}"
    
    if description:
        final_prompt = f"{base_prompt}, {description}{context_addition}"
    else:
        final_prompt = base_prompt + context_addition
    
    image_bytes = await generate_image(final_prompt)
    
    if image_bytes:
        await status_msg.delete()
        await update.message.reply_photo(
            photo=image_bytes,
            caption="️ **Изображение готово!**\n(Сгенерировано AI на основе твоего запроса)",
            reply_markup=get_card_keyboard()
        )
    else:
        await status_msg.edit_text("❌ Не удалось сгенерировать изображение. Попробуй еще раз.")

async def send_message_fallback(update: Update, text: str, reply_markup=None):
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
        [InlineKeyboardButton("➕ Новый запрос", callback_data="new_card"),
         InlineKeyboardButton("📋 История", callback_data="my_cards")],
        [InlineKeyboardButton("🔄 Сменить режим", callback_data="show_modes")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ])

def get_edit_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Цвет", callback_data="edit_color"),
         InlineKeyboardButton("👥 ЦА", callback_data="edit_audience")],
        [InlineKeyboardButton("🔑 SEO", callback_data="edit_seo"),
         InlineKeyboardButton(" Свой запрос", callback_data="edit_custom")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
         InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("🔥 Топ товаров", callback_data="admin_top"),
         InlineKeyboardButton("📜 Логи", callback_data="admin_logs")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ])

def get_card_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Сгенерировать фото", callback_data="generate_image")],
        [InlineKeyboardButton("➕ Новый", callback_data="new_card"),
         InlineKeyboardButton("📋 Мои", callback_data="my_cards")],
        [InlineKeyboardButton("📥 Скачать TXT", callback_data="export_last_card")]
    ])

# ====== ОБРАБОТЧИКИ КОМАНД ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "start")
    await send_message_fallback(update, 
        "👋 **Привет! Я твой универсальный AI-помощник.**\n\n"
        " **Что я умею:**\n"
        "• 📦 Создавать карточки для маркетплейсов\n"
        "• 💰 Давать советы по финансам и крипто\n"
        "• 🍳 Придумывать рецепты\n"
        "• 🎬 Писать сценарии для видео\n"
        "• ✂️ Советы по монтажу видео\n"
        "•  Нейрофотосессии\n"
        "• 🧑‍🎨 Цифровые аватары\n"
        "• 🌐 Отвечать на любые вопросы\n"
        "• 🖼️ Генерировать изображения\n\n"
        "💡 **Используй /mode или кнопку 'Сменить режим' для выбора темы!**",
        reply_markup=get_main_keyboard())

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "mode_command")
    
    current = user_modes.get(user_id, "marketplace")
    mode_names = {
        "marketplace": "📦 Маркетплейсы", "finance": "💰 Финансы", "cooking": "🍳 Кулинария", 
        "universal": "🌐 Универсал", "screenwriter": "🎬 Сценарист", "video_editor": "✂️ Видеомонтаж",
        "neuro_photoshoot": " Нейрофотосессии", "digital_avatar": "🧑‍🎨 Цифровые аватары"
    }
    
    keyboard = []
    for mode_key, mode_name in mode_names.items():
        check = "✅ " if current == mode_key else ""
        keyboard.append([InlineKeyboardButton(f"{check}{mode_name}", callback_data=f"set_mode_{mode_key}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")])
    
    await send_message_fallback(update, "🔄 **Выбери режим работы бота:**\n\nЭто изменит стиль и правила ответов AI.", 
                                reply_markup=InlineKeyboardMarkup(keyboard))

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
    await send_message_fallback(update, "🔄 **Новый диалог!**\nНапиши свой запрос:", reply_markup=get_main_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "help")
    text = "🤖 **Универсальный AI-помощник**\n\n"
    text += "**Команды:**\n"
    text += "/start - Запуск бота\n"
    text += "/mode - Сменить режим\n"
    text += "/newchat - Новый диалог\n"
    text += "/clear - Очистить память\n"
    text += "/help - Помощь\n\n"
    if is_admin(user_id):
        text += "🔐 **Админ:** /stats, /users, /top, /admin, /broadcast\n\n"
    text += "💡 Также можешь написать 'Сменить режим' текстом!"
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
        await send_message_fallback(update, "✅ **Обновлено!**\n\n" + edited, reply_markup=get_card_keyboard())
    except Exception as e:
        await send_message_fallback(update, f"❌ Ошибка: {str(e)}")

async def mycards_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "mycards")
    
    if user_id not in card_history or not card_history[user_id]:
        await send_message_fallback(update, "📭 Запросов пока нет.", reply_markup=get_main_keyboard())
        return
    
    cards = card_history[user_id][-5:]
    msg = f"📋 **Последние ответы** ({len(cards)} из {len(card_history[user_id])}):\n\n"
    for i, card in enumerate(reversed(cards), 1):
        preview = card[:100].replace("\n", " ").replace("**", "") + "..."
        msg += f"**{i}.** {preview}\n\n"
    
    msg += "\n💡 Чтобы скачать ответ, используй кнопку 'Скачать TXT'."
    await send_message_fallback(update, msg, reply_markup=get_main_keyboard())

async def export_last_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in card_history or not card_history[user_id]:
        await send_message_fallback(update, " У тебя нет сохраненных ответов.", reply_markup=get_main_keyboard())
        return
    
    last_card = card_history[user_id][-1]
    
    filename = f"response_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    file_content = last_card.replace("**", "").replace("##", "")
    
    file_io = BytesIO(file_content.encode('utf-8'))
    file_io.name = filename
    
    if update.callback_query:
        await update.callback_query.message.reply_document(
            document=InputFile(file_io),
            filename=filename,
            caption=" **Текст экспортирован!**"
        )
    else:
        await update.message.reply_document(
            document=InputFile(file_io),
            filename=filename,
            caption=" **Текст экспортирован!**"
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_message_fallback(update, " Только для админа.")
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
                f"🎴 Ответов: {tc} (сегодня: {tdc})\n"
                f"🎤 Голос: {stats['total_voice']} | 🖼️ Фото: {stats['total_photos']}")
        await send_message_fallback(update, text, reply_markup=get_admin_keyboard())
    except Exception as e:
        await send_message_fallback(update, f"❌ Ошибка: {str(e)}")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_message_fallback(update, " Только для админа.")
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
        msg = "🔥 **Топ запросов:**\n\n"
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

# ====== Рассылка ======

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /broadcast для начала создания рассылки"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_message_fallback(update, "⛔ Только для админа.")
        return
    
    await log_action(user_id, "broadcast_start")
    pending_broadcast[user_id] = {"status": "waiting_text"}
    
    await send_message_fallback(update, 
        "📢 **Создание рассылки**\n\n"
        "Напиши текст, который нужно отправить всем пользователям.\n\n"
        "💡 **Советы:**\n"
        "• Используй Markdown (**жирный**, *курсив*)\n"
        "• Добавь эмодзи для привлечения внимания\n"
        "• Не делай текст слишком длинным\n\n"
        "❌ Чтобы отменить, напиши: отмена",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отменить", callback_data="cancel_broadcast")]]))

async def process_broadcast_text(update: Update, user_id: int, text: str):
    """Обрабатывает текст рассылки и показывает превью"""
    if user_id not in pending_broadcast:
        return False
    
    if pending_broadcast[user_id].get("status") != "waiting_text":
        return False
    
    if text.lower().strip() == "отмена":
        del pending_broadcast[user_id]
        await send_message_fallback(update, "❌ **Рассылка отменена.**", reply_markup=get_admin_keyboard())
        return True
    
    # Сохраняем текст и показываем превью
    pending_broadcast[user_id]["text"] = text
    pending_broadcast[user_id]["status"] = "waiting_confirm"
    
    preview = text[:500] + ("..." if len(text) > 500 else "")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить всем", callback_data="confirm_broadcast"),
         InlineKeyboardButton("❌ Отмена", callback_data="cancel_broadcast")]
    ])
    
    await send_message_fallback(update, 
        f"📋 **Превью рассылки:**\n\n{preview}\n\n"
        f"📊 **Длина:** {len(text)} символов\n\n"
        f"⚠️ **Внимание!** Это сообщение будет отправлено **всем пользователям** бота.\n\n"
        f"Подтверди отправку или отмени:",
        reply_markup=keyboard)
    
    return True

def _send_broadcast_message(user_id: int, text: str, token: str):
    """Отправляет сообщение конкретному пользователю (синхронная версия)"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # Сначала пробуем с Markdown
    payload = {
        "chat_id": user_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    response = requests.post(url, json=payload, timeout=10)
    
    # Если Markdown не сработал, отправляем как plain text
    if response.status_code != 200:
        payload["parse_mode"] = None
        response = requests.post(url, json=payload, timeout=10)
    
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")
    
    return response.json()

async def execute_broadcast(update: Update, user_id: int):
    """Выполняет рассылку всем пользователям"""
    if user_id not in pending_broadcast:
        await send_message_fallback(update, "📭 Нет активной рассылки.")
        return
    
    broadcast_text = pending_broadcast[user_id].get("text")
    if not broadcast_text:
        await send_message_fallback(update, "❌ Текст рассылки не найден.")
        return
    
    # Получаем список всех пользователей из БД
    def _get_all_users():
        conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT user_id FROM user_actions")
        users = [row['user_id'] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return users
    
    try:
        users = await asyncio.to_thread(_get_all_users)
    except Exception as e:
        logger.error(f"Ошибка получения пользователей: {e}")
        await send_message_fallback(update, f"❌ Ошибка получения списка пользователей: {str(e)}")
        return
    
    if not users:
        await send_message_fallback(update, "📭 Нет пользователей для рассылки.")
        del pending_broadcast[user_id]
        return
    
    total_users = len(users)
    sent_count = 0
    error_count = 0
    token = os.getenv("TELEGRAM_TOKEN")
    
    # Отправляем сообщение о начале рассылки
    status_msg = await update.message.reply_text(
        f"📢 **Начинаю рассылку...**\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Отправлено: 0\n"
        f"❌ Ошибок: 0\n\n"
        f"⏳ Пожалуйста, подожди..."
    )
    
    await log_action(user_id, "broadcast_execute", f"total={total_users}")
    
    # Отправляем сообщения с задержкой
    for i, target_user_id in enumerate(users):
        try:
            await asyncio.sleep(0.1)  # Задержка 100мс
            
            # Передаем токен явно
            await asyncio.to_thread(_send_broadcast_message, target_user_id, broadcast_text, token)
            sent_count += 1
            
            # Обновляем статус каждые 10 сообщений
            if (i + 1) % 10 == 0 or i == total_users - 1:
                progress = int((i + 1) / total_users * 100)
                progress_bar = "█" * (progress // 10) + "░" * (10 - progress // 10)
                
                try:
                    await status_msg.edit_text(
                        f"📢 **Рассылка в процессе...**\n\n"
                        f"**Прогресс:** [{progress_bar}] {i + 1}/{total_users} ({progress}%)\n"
                        f"✅ Отправлено: {sent_count}\n"
                        f" Ошибок: {error_count}"
                    )
                except Exception:
                    pass  # Игнорируем ошибки обновления статуса
        
        except Exception as e:
            error_count += 1
            logger.warning(f"Ошибка отправки пользователю {target_user_id}: {e}")
    
    # Финальный отчет
    del pending_broadcast[user_id]
    
    await status_msg.edit_text(
        f"✅ **Рассылка завершена!**\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Успешно отправлено: {sent_count}\n"
        f"❌ Ошибок: {error_count}\n\n"
        f"📊 **Процент доставки:** {int(sent_count / total_users * 100)}%"
    )
    
    await log_action(user_id, "broadcast_complete", f"sent={sent_count}, errors={error_count}")

# ====== ОБРАБОТЧИКИ МЕДИА И СООБЩЕНИЙ ======

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats["total_photos"] += 1
    await log_action(user_id, "photo")
    
    photo = await update.message.photo[-1].get_file()
    pending_photos[user_id] = await photo.download_as_bytearray()
    
    await send_message_fallback(update, 
        "🖼️ **Фото получил!** Напиши или надиктуй, что нужно сделать с этим изображением.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👉 Продолжить", callback_data="fill_photo")]]))

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

async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "sticker")
    await send_message_fallback(update, "🎨 **Стикеры я не понимаю.**\n\nЯ работаю с текстом, голосом и фото.", reply_markup=get_main_keyboard())

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "video")
    await send_message_fallback(update, " **Видео я пока не обрабатываю.**", reply_markup=get_main_keyboard())

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "document")
    await send_message_fallback(update, "📄 **Документы я не обрабатываю.**", reply_markup=get_main_keyboard())

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "location")
    await send_message_fallback(update, "📍 **Геолокация мне не нужна.**", reply_markup=get_main_keyboard())

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "contact")
    await send_message_fallback(update, "📞 **Контакты сохранять не нужно.**", reply_markup=get_main_keyboard())

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "audio")
    await send_message_fallback(update, "🎵 **Аудиофайлы я не обрабатываю.** Используй голосовые 🎤", reply_markup=get_main_keyboard())

async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "video_note")
    await send_message_fallback(update, "📹 **Видео-кружочки я не обрабатываю.**", reply_markup=get_main_keyboard())

async def handle_animation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "animation")
    await send_message_fallback(update, "🎭 **GIF-анимации я не понимаю.**", reply_markup=get_main_keyboard())

async def handle_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await log_action(user_id, "poll")
    await send_message_fallback(update, " **Опросы я не поддерживаю.**", reply_markup=get_main_keyboard())

async def process_user_input(update: Update, user_id: int, text: str):
    # Проверяем, не является ли это текстом рассылки
    if await process_broadcast_text(update, user_id, text):
        return
    
    if is_empty_message(text):
        await send_message_fallback(update, "🤔 **Сообщение пустое.**\n\nНапиши текст или отправь голосовое 🎤 / фото ️.", reply_markup=get_main_keyboard())
        return
    
    if check_mode_command(text):
        await log_action(user_id, "mode_change_request")
        await mode_command(update, None)
        return
    
    if check_image_generation_command(text):
        await log_action(user_id, "image_generation_request")
        await generate_image_from_text(update, user_id, text)
        return
    
    allowed, message = check_rate_limit(user_id)
    if not allowed:
        await send_message_fallback(update, message)
        return
    
    text_lower = text.lower()
    stats["total_messages"] += 1
    await log_action(user_id, "message", text[:100])
    
    has_history = user_id in memory and len(memory[user_id]) > 0
    current_mode = user_modes.get(user_id, "marketplace")
    
    if current_mode == "marketplace" and has_history:
        last_ai = next((m["content"] for m in reversed(memory[user_id]) if m["role"] == "assistant"), "")
        if any(w in last_ai.lower() for w in ["вопрос", "уточнить", "детал"]):
            await show_progress(update, 5, 8)
            
            has_color = any(w in text_lower for w in ["цвет", "версия", "бел", "чёрн", "черн", "син", "красн", "зелён", "серебрист", "золот", "сер", "коричн", "фиолет", "розов", "оранж", "жёлт", "премиум", "базов", "стандарт", "размер", "s", "m", "l", "xl", "накладн", "вкладыш", "проводн", "беспроводн", "микрофон", "металл", "пластик", "силикон", "кож", "стекл", "матов", "глянц", "универс", "унисекс", "мужск", "женск", "детск"])
            has_audience = any(w in text_lower for w in ["аудитори", "спортсмен", "дет", "взросл", "професс", "любитель", "геймер", "музык", "фитнес", "бег", "трениров", "офис", "работ", "дом", "улица", "18+", "16+", "подрост", "мужчин", "женщин", "универсал", "все", "кажд", "пользовател", "клиент", "покупател", "люди", "человек", "парень", "девушк", "мальчик", "девочк", "студент", "школьник", "пенсионер", "мам", "пап", "ребён", "семь", "активн", "начинающ", "опытн", "категори", "любой", "кажд", "предназначен", "подходит", "для"])
            has_seo = any(w in text_lower for w in ["seo", "ключ", "водостойк", "мониторинг", "отслеживан", "шаг", "пульс", "сердечн", "ритм", "bluetooth", "wifi", "gps", "наушник", "звук", "бас", "шум", "автоном", "батаре", "заряд", "время", "работа", "поддержк", "совместим", "android", "ios", "iphone", "качество", "hi", "hd", "стерео", "мощн", "громк", "тих", "функци", "возможност", "особенност", "характеристик", "параметр", "давлен", "калори", "сон", "активн", "тренировк", "упражнен", "бего", "плаван", "велосипед", "ходьб", "прыжк", "йога", "лёгк", "тяжёл", "компакт", "удобн", "прочн", "надёжн", "быстр", "медлен", "точн", "умн", "интеллек", "автомат", "подавлен", "связ", "подключен", "передач", "воспроизведен"])
            
            missing = []
            if not has_color: missing.append("цвет/версию")
            if not has_audience: missing.append("аудиторию")
            if not has_seo: missing.append("особенности/SEO")
            
            if missing:
                kb = [[InlineKeyboardButton(" Пример", callback_data="show_example")]]
                await send_message_fallback(update, f"🙏 Спасибо! Не хватает: **{', '.join(missing)}**.\n\n💡 Пример: «1. Чёрные. 2. Для геймеров. 3. Bluetooth, шумоподавление»", reply_markup=InlineKeyboardMarkup(kb))
                return
            
            await show_progress(update, 6, 8)
            await show_progress(update, 7, 8)

    if current_mode != "marketplace":
        await show_progress_simple(update, "Думаю...")
    else:
        await update.message.chat.send_action(action="typing")
        await show_progress(update, 1, 8)
        await show_progress(update, 2, 8)
    
    ai_response = await get_ai_response_async(user_id, text)
    
    if ai_response.startswith("❌") or ai_response.startswith("⏱️") or ai_response.startswith("⚠️"):
        await send_message_fallback(update, ai_response, reply_markup=get_main_keyboard())
        return
    
    if current_mode == "marketplace":
        await show_progress(update, 8, 8)
    
    await send_message_fallback(update, ai_response, reply_markup=get_card_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await process_user_input(update, user_id, update.message.text)

# ====== CALLBACK (КНОПКИ) ======

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    if data == "admin_stats":
        await stats_command(update, context)
        return
    if data == "admin_users":
        await users_command(update, context)
        return
    if data == "admin_top":
        await top_command(update, context)
        return
    if data == "admin_logs":
        await query.edit_message_text("📜 Логи пишутся в БД (`user_actions`). Используй `/users`.", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
        return
    if data == "admin_broadcast":
        await broadcast_command(update, context)
        return
    
    if data == "confirm_broadcast":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ Только для админа.")
            return
        await execute_broadcast(update, user_id)
        return
    
    if data == "cancel_broadcast":
        if user_id in pending_broadcast:
            del pending_broadcast[user_id]
        await query.edit_message_text("❌ **Рассылка отменена.**", reply_markup=get_admin_keyboard(), parse_mode='Markdown')
        return
    
    if data == "export_last_card":
        await export_last_card(update, context)
        return
    
    if data == "new_card":
        await query.edit_message_text("➕ **Новый запрос**\nНапиши или надиктуй, что нужно сделать:", parse_mode='Markdown')
    elif data == "my_cards":
        await mycards_command(update, context)
    elif data == "edit_last":
        if user_id not in card_history or not card_history[user_id]:
            await query.edit_message_text("😕 Сначала создай карточку!", reply_markup=get_main_keyboard(), parse_mode='Markdown')
        else:
            await query.edit_message_text("️ Напиши изменения или выбери кнопку:", reply_markup=get_edit_keyboard(), parse_mode='Markdown')
    elif data in ["edit_color", "edit_audience", "edit_seo", "edit_custom"]:
        await query.edit_message_text("✏️ Напиши или надиктуй, что изменить:", parse_mode='Markdown')
    elif data == "help":
        await help_command(update, context)
    elif data == "back_to_main":
        await query.edit_message_text("🏠 **Главное меню**\nЧто делаем?", reply_markup=get_main_keyboard(), parse_mode='Markdown')
    elif data == "upload_photo":
        await query.edit_message_text("🖼️ Загрузи фото товара (скрепка -> Фото).", parse_mode='Markdown')
    elif data == "fill_photo":
        await query.edit_message_text("🖼️ Напиши или надиктуй, что нужно сделать с фото:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]]), parse_mode='Markdown')
    elif data == "show_example":
        await query.edit_message_text("💡 **Пример:**\n1. Чёрные, с микрофоном\n2. Для геймеров\n3. Bluetooth, шумоподавление\n\nЖду твой ответ! 👇", parse_mode='Markdown')
    
    elif data == "show_modes":
        current = user_modes.get(user_id, "marketplace")
        mode_names = {
            "marketplace": "📦 Маркетплейсы", "finance": "💰 Финансы", "cooking": "🍳 Кулинария", 
            "universal": "🌐 Универсал", "screenwriter": "🎬 Сценарист", "video_editor": "✂️ Видеомонтаж",
            "neuro_photoshoot": "📸 Нейрофотосессии", "digital_avatar": "🧑‍ Цифровые аватары"
        }
        
        keyboard = []
        for mode_key, mode_name in mode_names.items():
            check = "✅ " if current == mode_key else ""
            keyboard.append([InlineKeyboardButton(f"{check}{mode_name}", callback_data=f"set_mode_{mode_key}")])
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")])
        
        await query.edit_message_text("🔄 **Выбери режим работы бота:**\n\nЭто изменит стиль и правила ответов AI.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("set_mode_"):
        new_mode = data.replace("set_mode_", "")
        user_modes[user_id] = new_mode
        mode_names = {
            "marketplace": "📦 Маркетплейсы", "finance": "💰 Финансы", "cooking": "🍳 Кулинария", 
            "universal": "🌐 Универсал", "screenwriter": "🎬 Сценарист", "video_editor": "✂️ Видеомонтаж",
            "neuro_photoshoot": "📸 Нейрофотосессии", "digital_avatar": "🧑‍🎨 Цифровые аватары"
        }
        
        if user_id in memory:
            del memory[user_id]
            
        await query.edit_message_text(f"✅ **Режим изменен на:** {mode_names[new_mode]}\n\nТеперь я отвечаю как эксперт в этой области. Задай свой вопрос!", reply_markup=get_main_keyboard(), parse_mode='Markdown')

    elif data == "generate_image":
        await query.answer("🎨 Генерирую изображение...")
        await query.message.reply_text("⏳ **Генерирую изображение...**\nЭто может занять 10-20 секунд.")
        
        base_prompt = get_image_prompt_by_mode(user_id)
        
        context_addition = ""
        if user_id in card_history and card_history[user_id]:
            last_card = card_history[user_id][-1]
            match = re.search(r'\*\*Название:\*\*\s*(.+)', last_card)
            if match:
                context_addition = f" of {match.group(1)}"
        elif user_id in memory and memory[user_id]:
            last_msg = memory[user_id][-1]['content']
            context_addition = f" based on: {last_msg[:80]}"
        
        final_prompt = base_prompt + context_addition
        
        image_bytes = await generate_image(final_prompt)
        
        if image_bytes:
            await query.message.reply_photo(
                photo=image_bytes,
                caption="️ **Изображение готово!**\n(Сгенерировано AI на основе твоего запроса)",
                reply_markup=get_card_keyboard()
            )
        else:
            await query.message.reply_text("❌ Не удалось сгенерировать изображение. Попробуй еще раз.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and update.effective_user:
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="⚠️ **Произошла ошибка.**\n\nПопробуй ещё раз или напиши `/newchat`.",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e}")

async def post_init(application):
    logger.info("✅ Инициализация бота...")
    await application.bot.delete_webhook()
    logger.info("✅ Webhook удалён")

def main():
    load_memory()
    
    logger.info("🤖 Создаю приложение...")
    application = Application.builder().token(os.getenv("TELEGRAM_TOKEN")).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("mode", mode_command))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("newchat", newchat))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("edit", edit_command))
    application.add_handler(CommandHandler("mycards", mycards_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))

    application.add_handler(CallbackQueryHandler(button_callback))
    
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))
    application.add_handler(MessageHandler(filters.ANIMATION, handle_animation))
    application.add_handler(MessageHandler(filters.POLL, handle_poll))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    application.add_error_handler(error_handler)

    logger.info("🚀 Запускаю polling...")
    logger.info("🤖 Бот запущен и готов к работе!")
    
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        timeout=30,
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()
