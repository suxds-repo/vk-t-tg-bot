import os
import io
import hashlib
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import vk_api
from telegram import Bot, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, ContextTypes, CallbackQueryHandler

# загружаем .env
load_dotenv()

# VK
VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID"))

# Telegram
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHANNEL = os.getenv("TG_CHANNEL")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

# PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# создаём таблицу для хэшей постов, если нет
cursor.execute("""
CREATE TABLE IF NOT EXISTS vk_posts_hashes (
    post_id TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# VK и Telegram клиенты
vk = vk_api.VkApi(token=VK_TOKEN)
bot = Bot(token=TG_BOT_TOKEN)


# ------------------- функции -------------------

def should_post(post):
    """Фильтр: не закреплённые, не реклама, не репосты"""
    if post.get("is_pinned") == 1:
        return False
    if post.get("marked_as_ads") == 1:
        return False
    if "copy_history" in post:
        return False
    return True


def get_latest_valid_post():
    """Получаем последний валидный пост VK"""
    wall = vk.method('wall.get', {'owner_id': VK_GROUP_ID, 'count': 5})
    items = wall.get('items', [])
    for post in items:
        if should_post(post):
            return post
    return None


def download_photo_bytes(url):
    """Скачиваем фото и возвращаем объект BytesIO"""
    r = requests.get(url)
    r.raise_for_status()
    return io.BytesIO(r.content)


def get_post_hash(post):
    """Создаём хэш текста + ссылок на фото"""
    text = post.get("text", "")
    attachments = post.get("attachments", [])
    photo_urls = [att['photo']['sizes'][-1]['url'] for att in attachments if att['type'] == 'photo']
    full_str = text + "".join(photo_urls)
    return hashlib.md5(full_str.encode("utf-8")).hexdigest()


def is_post_new_or_changed(post):
    """Проверяем, новый ли пост или изменился ли хэш"""
    post_id = str(post['id'])
    current_hash = get_post_hash(post)

    cursor.execute("SELECT hash FROM vk_posts_hashes WHERE post_id=%s", (post_id,))
    result = cursor.fetchone()

    if not result:
        # новый пост
        cursor.execute("INSERT INTO vk_posts_hashes(post_id, hash) VALUES (%s, %s)", (post_id, current_hash))
        conn.commit()
        return True
    elif result['hash'] != current_hash:
        # изменился
        cursor.execute("UPDATE vk_posts_hashes SET hash=%s, updated_at=NOW() WHERE post_id=%s", (current_hash, post_id))
        conn.commit()
        return True

    return False


async def send_post_for_confirmation(post, context: ContextTypes.DEFAULT_TYPE):
    """Отправляем пост на подтверждение админу"""
    text = post.get('text', '')
    attachments = post.get('attachments', [])
    media_group = []

    for i, att in enumerate(attachments):
        if att['type'] == 'photo':
            sizes = att['photo']['sizes']
            largest = max(sizes, key=lambda x: x['height'] * x['width'])
            photo_bytes = download_photo_bytes(largest['url'])
            if i == 0:
                media_group.append(InputMediaPhoto(photo_bytes, caption=text))
            else:
                media_group.append(InputMediaPhoto(photo_bytes))

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Опубликовать ✅", callback_data=f"publish_{post['id']}"),
         InlineKeyboardButton("Пропустить ❌", callback_data=f"skip_{post['id']}")]
    ])

    if media_group:
        await context.bot.send_media_group(chat_id=ADMIN_CHAT_ID, media=media_group)
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="Выберите действие:", reply_markup=keyboard)
    elif text:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, reply_markup=keyboard)


async def check_vk_posts(context: ContextTypes.DEFAULT_TYPE):
    """Проверка последних постов VK"""
    post = get_latest_valid_post()
    if post and is_post_new_or_changed(post):
        await send_post_for_confirmation(post, context)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок подтверждения / пропуска"""
    query = update.callback_query
    await query.answer()

    data = query.data
    post_id = int(data.split("_")[1])

    if data.startswith("publish"):
        wall = vk.method('wall.get', {'owner_id': VK_GROUP_ID, 'count': 5})
        for post in wall.get('items', []):
            if post['id'] == post_id:
                text = post.get('text', '')
                attachments = post.get('attachments', [])
                media_group = []

                for i, att in enumerate(attachments):
                    if att['type'] == 'photo':
                        sizes = att['photo']['sizes']
                        largest = max(sizes, key=lambda x: x['height'] * x['width'])
                        photo_bytes = download_photo_bytes(largest['url'])
                        if i == 0:
                            media_group.append(InputMediaPhoto(photo_bytes, caption=text))
                        else:
                            media_group.append(InputMediaPhoto(photo_bytes))

                if media_group:
                    await context.bot.send_media_group(chat_id=TG_CHANNEL, media=media_group)
                elif text:
                    await context.bot.send_message(chat_id=TG_CHANNEL, text=text)

                await query.edit_message_text("Пост опубликован ✅")
                return

    elif data.startswith("skip"):
        await query.edit_message_text("Пост пропущен ❌")


# ------------------- запуск бота -------------------

if __name__ == "__main__":
    app = ApplicationBuilder().token(TG_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(button_callback))

    # проверка новых постов каждые 5 минут
    job_queue = app.job_queue
    job_queue.run_repeating(check_vk_posts, interval=1, first=1)

    print("Бот запущен...")
    app.run_polling()
