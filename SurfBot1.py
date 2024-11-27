import asyncio
import logging
import os
import re
from pathlib import Path
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext
from telethon import TelegramClient, events

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен бота и Telegram API ключи
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")

if not TOKEN or not api_id or not api_hash:
    logger.error("Необходимо указать TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID и TELEGRAM_API_HASH в переменных окружения.")
    exit(1)

# Инициализация переменных для управления ботом
bot_started_chats = set()
start_cooldown = 3
last_start_times = {}

# Загрузка ID чатов для мониторинга
chat_ids_file = Path("chat_ids.txt")
if not chat_ids_file.exists():
    logger.error("Файл chat_ids.txt не найден. Укажите список ID чатов в этом файле.")
    exit(1)

with open(chat_ids_file, "r") as file:
    group_chat_ids = [int(line.strip()) for line in file if line.strip()]
logger.info(f"Загружены чаты для мониторинга: {group_chat_ids}")

# Загрузка ключевых слов из файла
def load_keywords(file_path):
    path = Path(file_path)
    if not path.exists():
        logger.error(f"Файл {file_path} не найден. Укажите список ключевых слов в этом файле.")
        exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

keywords = load_keywords("keywords.txt")
exclude_keywords = load_keywords("exclude_keywords.txt")

logger.info(f"Ключевые слова: {keywords}")
logger.info(f"Слова исключения: {exclude_keywords}")

# Инициализация Telethon клиента
client = TelegramClient('user_session', api_id, api_hash)

logger.info("Инициализация Telethon клиента завершена.")

# Экранирование MarkdownV2
def escape_markdown(text):
    escape_chars = r"_*[]()~`>#+-=|{}.!<>"
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

# Проверка на ключевые слова
def contains_keywords(message, keywords_list):
    for word in keywords_list:
        if re.search(rf'\b{re.escape(word)}\b', message, re.IGNORECASE):
            logger.debug(f"Найдено ключевое слово: {word}")
            return True
    return False

# Проверка на слова исключения
def contains_exclude_keywords(message, exclude_keywords_list):
    for word in exclude_keywords_list:
        if re.search(rf'\b{re.escape(word)}\b', message, re.IGNORECASE):
            logger.debug(f"Найдено слово исключение: {word}")
            return True
    return False

async def forward_message_to_bot_chats(text, user_link, chat_title, chat_link):
    """Пересылка сообщения в активные чаты через Telegram Bot API."""
    if not bot_started_chats:
        logger.info("Нет активных чатов. Сообщения не будут пересылаться.")
        return

    text = escape_markdown(text)
    keyboard = []
    if user_link:
        user_link = escape_markdown(user_link)
        keyboard.append([{"text": "👤 Перейти к пользователю", "url": user_link}])
    if chat_link:
        chat_link = escape_markdown(chat_link)
        keyboard.append([{"text": "🌐 Перейти в группу", "url": chat_link}])

    reply_markup = {"inline_keyboard": keyboard}

    for chat_id in bot_started_chats:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "reply_markup": reply_markup,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()
            logger.info(f"Сообщение успешно отправлено в {chat_id}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при отправке сообщения в {chat_id}: {e}")

@client.on(events.NewMessage(chats=group_chat_ids))
async def handle_new_message(event):
    """Обработчик новых сообщений."""
    sender = await event.get_sender()
    if sender.bot:
        return

    message_text = event.message.message
    user_link = f"https://t.me/{sender.username}" if sender.username else None
    chat_title = event.chat.title or "Unknown Chat"

    if event.chat.username:
        chat_link = f"https://t.me/{event.chat.username}/{event.id}"
    else:
        chat_link = f"https://t.me/c/{str(event.chat_id)[4:]}/{event.id}"

    logger.info(f"Новое сообщение из чата {chat_title} (ID: {event.chat_id})")

    if contains_exclude_keywords(message_text, exclude_keywords):
        logger.info("Сообщение содержит исключающее слово. Не пересылается.")
        return

    if contains_keywords(message_text, keywords):
        logger.info("Сообщение содержит ключевое слово. Пересылаем...")
        await forward_message_to_bot_chats(message_text, user_link, chat_title, chat_link)
    else:
        logger.info("Сообщение не содержит ключевых слов. Не пересылается.")

async def async_run_telegram_bot():
    """Запуск Telegram Bot API."""
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    await application.initialize()
    logger.info("Бот Telegram инициализирован.")
    await application.start()
    await application.updater.start_polling()

async def async_run_telethon_client():
    """Запуск Telethon."""
    logger.info("Запуск Telethon для мониторинга сообщений...")
    await client.start()
    await client.run_until_disconnected()

async def start(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    bot_started_chats.add(chat_id)
    await update.message.reply_text("✨ Бот активирован!")
    logger.info(f"Бот активирован в чате {chat_id}.")

async def stop(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    bot_started_chats.discard(chat_id)
    await update.message.reply_text("🚫 Бот остановлен.")
    logger.info(f"Бот остановлен в чате {chat_id}.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(async_run_telegram_bot())
    loop.create_task(async_run_telethon_client())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Остановка бота...")
    finally:
        if client.is_connected():
            client.disconnect()
        logger.info("Программа завершена.")
