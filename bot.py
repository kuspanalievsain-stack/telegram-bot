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
        return ai_message
    except Exception as e:
        logger.error(f"Ошибка при получении ответа от AI: {e}")
        return f"Ошибка: {str(e)}"

# ====== Обработчики команд ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text("Привет! Я AI-бот для создания карточек товаров. Напиши мне что-нибудь!")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /clear - очистка памяти"""
    user_id = update.message.from_user.id
    if user_id in memory:
        del memory[user_id]
        save_memory(memory)
    await update.message.reply_text("Память очищена!")

async def newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /newchat - новый чат"""
    user_id = update.message.from_user.id
    if user_id in memory:
        del memory[user_id]
        save_memory(memory)
    await update.message.reply_text("Начинаем новый диалог! Чем могу помочь?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик обычных сообщений с валидацией"""
    user_id = update.message.from_user.id
    user_message = update.message.text
    
    # Проверяем, есть ли у пользователя незавершённый диалог
    if user_id in memory and len(memory[user_id]) > 0:
        # Проверяем, задавал ли бот вопросы
        last_ai_message = ""
        for msg in reversed(memory[user_id]):
            if msg["role"] == "assistant":
                last_ai_message = msg["content"]
                break
        
        # Если бот задавал вопросы, проверяем полноту ответа
        if "вопрос" in last_ai_message.lower() or "уточнить" in last_ai_message.lower():
            # Проверяем, есть ли ответы на все 3 вопроса
            has_color = any(word in user_message.lower() for word in ["цвет", "версия", "белый", "чёрный", "синий", "красный", "зелёный", "премиум", "базовый"])
            has_audience = any(word in user_message.lower() for word in ["аудитори", "спортсмен", "дет", "взросл", "профессионал", "любитель"])
            has_seo = any(word in user_message.lower() for word in ["seo", "ключ", "водостойк", "мониторинг", "отслеживан"])
            
            missing = []
            if not has_color:
                missing.append("цвет/версию")
            if not has_audience:
                missing.append("целевую аудиторию")
            if not has_seo:
                missing.append("SEO-ключи")
            
            if missing:
                # Не хватает ответов
                await update.message.reply_text(
                    f"Спасибо за ответ! Но мне нужно ещё уточнить:\n\n"
                    f"❌ Не хватает информации о: {', '.join(missing)}\n\n"
                    f"Пожалуйста, дополни свой ответ."
                )
                return
    
    # Показываем, что бот печатает
    await update.message.chat.send_action(action="typing")
    
    # Получаем ответ от AI
    ai_response = get_ai_response(user_id, user_message)
    
    # Отправляем ответ
    await update.message.reply_text(ai_response)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Update {update} caused error {context.error}")

def main():
    """Запуск бота"""
    # Загрузка памяти
    load_memory()
    
    # Создание приложения
    application = Application.builder().token(os.getenv("TELEGRAM_TOKEN")).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("newchat", newchat))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logger.info("Бот запускается...")
    application.run_polling()

if __name__ == '__main__':
    main()
