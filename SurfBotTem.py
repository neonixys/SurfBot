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
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Токен бота и Telegram API ключи
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")

if not TOKEN or not api_id or not api_hash:
    logger.error("Необходимо указать TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID и TELEGRAM_API_HASH в переменных окружения.")
    exit(1)

# Инициализация переменных для управления ботом
active_target_chats = set()  # Чаты, где пересылка активирована командой /start
start_cooldown = 3
last_start_times = {}

# Пути к файлам
KEYWORDS_FILE = "keywords.txt"
EXCLUDE_KEYWORDS_FILE = "exclude_keywords.txt"
CHAT_IDS_FILE = "chat_ids.txt"
TARGET_CHAT_IDS_FILE = "target_chat_ids.txt"

# Загрузка данных из файлов
def load_keywords(file_path):
    """Загрузка списка ключевых слов из файла."""
    path = Path(file_path)
    if not path.exists():
        logger.error(f"Файл {file_path} не найден. Проверьте файл.")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

# Загрузка начальных данных
keywords = load_keywords(KEYWORDS_FILE)
exclude_keywords = load_keywords(EXCLUDE_KEYWORDS_FILE)
group_chat_ids = [int(line.strip()) for line in load_keywords(CHAT_IDS_FILE) if line.strip()]
target_chat_ids = {int(line.strip()) for line in load_keywords(TARGET_CHAT_IDS_FILE) if line.strip()}

logger.info(f"Ключевые слова: {keywords}")
logger.info(f"Слова исключения: {exclude_keywords}")
logger.info(f"Чаты для мониторинга: {group_chat_ids}")
logger.info(f"Целевые чаты для пересылки: {target_chat_ids}")

# Инициализация Telethon клиента
client = TelegramClient('user_session', api_id, api_hash)

logger.info("Инициализация Telethon клиента завершена.")

def escape_markdown(text):
    """Экранирование текста для Telegram MarkdownV2."""
    escape_chars = r"_*[]()~`>#+-=|{}.!<>"
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

def contains_keywords(message, keywords_list):
    """Проверка наличия ключевых слов."""
    for word in keywords_list:
        if re.search(rf'\b{re.escape(word)}\b', message, re.IGNORECASE):
            logger.debug(f"Найдено ключевое слово: {word}")
            return True
    return False

def contains_exclude_keywords(message, exclude_keywords_list):
    """Проверка наличия слов исключений."""
    for word in exclude_keywords_list:
        if re.search(rf'\b{re.escape(word)}\b', message, re.IGNORECASE):
            logger.debug(f"Найдено слово исключение: {word}")
            return True
    return False

async def forward_message_to_bot_chats(text, user_link, chat_title, chat_link):
    """Пересылка сообщения через Telegram Bot API."""
    # Пересылка только в активные чаты из target_chat_ids
    active_and_valid_chats = active_target_chats.intersection(target_chat_ids)
    if not active_and_valid_chats:
        logger.info("Нет активных чатов для пересылки. Сообщения не будут пересылаться.")
        return

    # Экранируем текст
    try:
        text = escape_markdown(text)
        logger.debug(f"Экранированный текст: {text}")
    except Exception as e:
        logger.error(f"Ошибка экранирования текста: {e}")
        return

    # Подготовка кнопок
    keyboard = []
    if user_link:
        try:
            user_link = user_link.strip()
            assert user_link.startswith("https://"), "Некорректный формат ссылки пользователя"
            keyboard.append([{"text": "👤 Перейти к пользователю", "url": user_link}])
        except Exception as e:
            logger.error(f"Ошибка в ссылке пользователя: {e}")

    if chat_link:
        try:
            chat_link = chat_link.strip()
            assert chat_link.startswith("https://"), "Некорректный формат ссылки чата"
            keyboard.append([{"text": "🌐 Перейти в группу", "url": chat_link}])
        except Exception as e:
            logger.error(f"Ошибка в ссылке чата: {e}")

    reply_markup = {"inline_keyboard": keyboard}

    # Отправка сообщения в активированные и валидные чаты
    for chat_id in active_and_valid_chats:
        logger.debug(f"Попытка отправки сообщения в чат chat_id={chat_id}")
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
            logger.info(f"Сообщение успешно отправлено в chat_id={chat_id}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при отправке сообщения в chat_id={chat_id}: {e}")

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

    logger.info(f"Новое сообщение из чата {chat_title} (ID: {event.chat_id}): {message_text}")

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
    logger.info("Telegram Bot API инициализирован.")
    await application.start()
    await application.updater.start_polling()

async def async_run_telethon_client():
    """Запуск Telethon."""
    logger.info("Запуск Telethon для мониторинга сообщений...")
    await client.start()
    logger.info("Telethon клиент успешно запущен.")
    await client.run_until_disconnected()

async def monitor_file_changes():
    """Мониторинг изменений в файлах."""
    last_modified_keywords = os.path.getmtime(KEYWORDS_FILE)
    last_modified_exclude_keywords = os.path.getmtime(EXCLUDE_KEYWORDS_FILE)
    last_modified_chat_ids = os.path.getmtime(CHAT_IDS_FILE)
    last_modified_target_chat_ids = os.path.getmtime(TARGET_CHAT_IDS_FILE)

    global keywords, exclude_keywords, group_chat_ids, target_chat_ids

    while True:
        await asyncio.sleep(10)

        # Проверка изменений в keywords.txt
        if os.path.getmtime(KEYWORDS_FILE) > last_modified_keywords:
            last_modified_keywords = os.path.getmtime(KEYWORDS_FILE)
            keywords = load_keywords(KEYWORDS_FILE)
            logger.info(f"Обновлены ключевые слова: {keywords}")

        # Проверка изменений в exclude_keywords.txt
        if os.path.getmtime(EXCLUDE_KEYWORDS_FILE) > last_modified_exclude_keywords:
            last_modified_exclude_keywords = os.path.getmtime(EXCLUDE_KEYWORDS_FILE)
            exclude_keywords = load_keywords(EXCLUDE_KEYWORDS_FILE)
            logger.info(f"Обновлены слова исключения: {exclude_keywords}")

        # Проверка изменений в chat_ids.txt
        if os.path.getmtime(CHAT_IDS_FILE) > last_modified_chat_ids:
            last_modified_chat_ids = os.path.getmtime(CHAT_IDS_FILE)
            try:
                new_group_chat_ids = [int(line.strip()) for line in load_keywords(CHAT_IDS_FILE) if line.strip()]
                if group_chat_ids != new_group_chat_ids:
                    group_chat_ids = new_group_chat_ids
                    logger.info(f"Обновлены чаты для мониторинга: {group_chat_ids}")
                else:
                    logger.debug("Изменений в chat_ids.txt не обнаружено.")
            except ValueError as e:
                logger.error(f"Ошибка при обновлении chat_ids.txt: {e}")

        # Проверка изменений в target_chat_ids.txt
        if os.path.getmtime(TARGET_CHAT_IDS_FILE) > last_modified_target_chat_ids:
            last_modified_target_chat_ids = os.path.getmtime(TARGET_CHAT_IDS_FILE)
            try:
                new_target_chat_ids = {int(line.strip()) for line in load_keywords(TARGET_CHAT_IDS_FILE) if line.strip()}
                if target_chat_ids != new_target_chat_ids:
                    target_chat_ids = new_target_chat_ids
                    logger.info(f"Обновлены целевые чаты для пересылки: {target_chat_ids}")
                else:
                    logger.debug("Изменений в target_chat_ids.txt не обнаружено.")
            except ValueError as e:
                logger.error(f"Ошибка при обновлении target_chat_ids.txt: {e}")


async def start(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if chat_id in target_chat_ids:
        active_target_chats.add(chat_id)
        await update.message.reply_text("✨ Бот активирован! Теперь этот чат будет получать пересылаемые сообщения.")
        logger.info(f"Бот активирован в чате {chat_id}.")
    else:
        await update.message.reply_text("🚫 Этот чат не входит в список доступных для пересылки.")
        logger.warning(f"Попытка активации бота в неразрешённом чате {chat_id}.")

async def stop(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if chat_id in active_target_chats:
        active_target_chats.discard(chat_id)
        await update.message.reply_text("🚫 Бот остановлен. Этот чат больше не будет получать пересылаемые сообщения.")
        logger.info(f"Бот остановлен в чате {chat_id}.")
    else:
        await update.message.reply_text("Бот уже был остановлен в этом чате.")
        logger.warning(f"Попытка остановки бота в чате {chat_id}, где он не был активирован.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(async_run_telegram_bot())
    loop.create_task(async_run_telethon_client())
    loop.create_task(monitor_file_changes())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Остановка бота...")
    finally:
        if client.is_connected():
            client.disconnect()
        logger.info("Программа завершена.")
