import asyncio
import logging
import os
import re
import time
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
bot_started_chats = set()  # Хранение ID активных чатов
start_cooldown = 3
last_start_times = {}

# Загрузка ID чатов для мониторинга
chat_ids_file = Path("chat_ids.txt")
if not chat_ids_file.exists():
    logger.error("Файл chat_ids.txt не найден. Укажите список ID чатов в этом файле.")
    exit(1)

with open(chat_ids_file, "r") as file:
    group_chat_ids = [int(line.strip()) for line in file if line.strip()]


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

# Инициализация Telethon клиента
client = TelegramClient('user_session', api_id, api_hash)

logger.info("Инициализация Telethon клиента завершена.")


def escape_markdown(text):
    """Экранирование текста для Telegram MarkdownV2, избегая экранирования URL."""
    escape_chars = r"_*[]()~`>#+-=|{}.!<>"

    def is_url(string):
        return bool(re.match(r'https?://[^\s]+', string))

    parts = re.split(r'(https?://[^\s]+)', text)

    escaped_parts = [
        ''.join(f'\\{char}' if char in escape_chars else char for char in part) if not is_url(part) else part
        for part in parts
    ]

    return ''.join(escaped_parts)


def contains_keywords(message, keywords_list):
    """Проверка наличия хотя бы одного ключевого слова в сообщении (по полному совпадению)."""
    for word in keywords_list:
        if re.search(rf'\b{re.escape(word)}\b', message, re.IGNORECASE):
            logger.debug(f"Найдено ключевое слово: {word}")
            return True
    return False


def contains_exclude_keywords(message, exclude_keywords_list):
    """Проверка наличия хотя бы одного слова из списка исключений в сообщении (по полному совпадению)."""
    for word in exclude_keywords_list:
        if re.search(rf'\b{re.escape(word)}\b', message, re.IGNORECASE):
            logger.debug(f"Найдено слово исключение: {word}")
            return True
    return False



async def forward_message_to_bot_chats(text, user_link, chat_title, chat_link):
    """Пересылка сообщения в активные целевые чаты через Telegram Bot API."""
    if not bot_started_chats:
        logger.info("Нет активных чатов. Сообщения не будут пересылаться.")
        return

    # Экранируем текст и ссылки для безопасной отправки
    text = escape_markdown(text)
    if user_link:
        user_link = escape_markdown(user_link)
    if chat_link:
        chat_link = escape_markdown(chat_link)

    # Создаем кнопки для перехода в чат пользователя и в группу
    keyboard = []
    if user_link:
        keyboard.append([{"text": "👤 Перейти к пользователю", "url": user_link}])
    keyboard.append([{"text": "🌐 Перейти в группу", "url": chat_link}])

    reply_markup = {"inline_keyboard": keyboard}

    # Отправка сообщения в каждый активный чат
    for chat_id in bot_started_chats:
        send_message_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,  # Пересылаем только текст сообщения
            "reply_markup": reply_markup,  # Добавляем кнопки
            "parse_mode": "MarkdownV2",  # Форматирование текста
            "disable_web_page_preview": True,  # Отключаем предпросмотр ссылок
        }

        response = requests.post(send_message_url, json=data)
        if response.status_code == 200:
            logger.info(f"Сообщение успешно отправлено в {chat_id}")
        else:
            logger.error(f"Ошибка при отправке сообщения в {chat_id}: {response.text}")


@client.on(events.NewMessage(chats=group_chat_ids))
async def handle_new_message(event):
    """Обработчик новых сообщений."""
    sender = await event.get_sender()
    if sender.bot:
        return

    # Преобразование текста сообщения
    message_text = event.message.message
    user_link = f"https://t.me/{sender.username}" if sender.username else None
    chat_title = event.chat.title or "Unknown Chat"

    if event.chat.username:  # Публичная группа
        chat_link = f"https://t.me/{event.chat.username}/{event.id}"
    else:  # Приватная группа
        chat_link = f"https://t.me/c/{str(event.chat_id)[4:]}/{event.id}"

    logger.info(f"Обработка сообщения из чата: {chat_title} (ID: {event.chat_id}), Ссылка: {chat_link}")

    # 1. Проверка на исключающие слова
    if contains_exclude_keywords(message_text, exclude_keywords):
        logger.info("Сообщение содержит исключающее слово. Не пересылается.")
        return

    logger.info("Сообщение не содержит исключающих слов.")

    # 2. Проверка на ключевые слова (если нет исключающих)
    if contains_keywords(message_text, keywords):
        logger.info("Сообщение содержит ключевое слово и будет переслано.")
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
    logger.info("Бот Telegram успешно запущен и готов к работе.")
    await application.updater.start_polling()


async def async_run_telethon_client():
    """Запуск Telethon."""
    logger.info("Запуск Telethon для мониторинга групповых сообщений...")
    await client.start()
    await client.run_until_disconnected()


async def start(update: Update, context: CallbackContext):
    """Команда /start для активации бота."""
    global last_start_times

    chat_id = update.effective_chat.id
    current_time = time.time()

    if chat_id in last_start_times and (current_time - last_start_times[chat_id] < start_cooldown):
        logger.info(
            f"Команда /start получена, но бот уже активирован недавно для чата {chat_id}. Игнорируем повторное выполнение.")
        return

    last_start_times[chat_id] = current_time
    bot_started_chats.add(chat_id)

    await update.message.reply_text("✨ Бот активирован! Теперь он пересылает сообщения по ключевым словам.")
    logger.info(f"Бот активирован командой /start в чате {chat_id}.")


async def stop(update: Update, context: CallbackContext):
    """Команда /stop для остановки бота."""
    chat_id = update.effective_chat.id

    if chat_id in bot_started_chats:
        bot_started_chats.remove(chat_id)
        await update.message.reply_text("🚫 Бот остановлен.")
        logger.info(f"Бот остановлен командой /stop в чате {chat_id}.")
    else:
        await update.message.reply_text("Бот не был активирован в этом чате.")
        logger.info(f"Попытка остановки бота в чате {chat_id}, но он не был активирован.")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(async_run_telegram_bot())
    loop.create_task(async_run_telethon_client())
    loop.run_forever()
