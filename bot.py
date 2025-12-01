import os
import io
import hashlib
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import vk_api

from telegram import Bot, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CallbackQueryHandler
)

# ------------ ENV ------------------
load_dotenv()

VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID"))

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHANNEL = os.getenv("TG_CHANNEL")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

DATABASE_URL = os.getenv("DATABASE_URL")

# ------------ DB -------------------
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS vk_posts_hashes (
    post_id TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# ------------ CLIENTS -------------
vk = vk_api.VkApi(token=VK_TOKEN)
bot = Bot(token=TG_BOT_TOKEN)


# ------------------- UTILS -------------------

def should_post(post):
    """Фильтр: исключаем закреп, рекламу, репосты"""
    if post.get("is_pinned") == 1:
        return False
    if post.get("marked_as_ads") == 1:
        return False
    if "copy_history" in post:
        return False
    return True


def get_latest_valid_post():
    wall = vk.method('wall.get', {'owner_id': VK_GROUP_ID, 'count': 5})
    for post in wall.get('items', []):
        if should_post(post):
            return post
    return None


def download_photo_bytes(url):
    r = requests.get(url)
    r.raise_for_status()
    return io.BytesIO(r.content)


def get_post_hash(post):
    text = post.get("text", "")
    photos = []

    for att in post.get("attachments", []):
        if att["type"] == "photo":
            largest = max(att["photo"]["sizes"], key=lambda s: s["width"])
            photos.append(largest["url"])

    full = text + "".join(photos)
    return hashlib.md5(full.encode()).hexdigest()


def is_post_new_or_changed(post):
    post_id = str(post["id"])
    new_hash = get_post_hash(post)

    cursor.execute("SELECT hash FROM vk_posts_hashes WHERE post_id=%s", (post_id,))
    row = cursor.fetchone()

    if not row:
        cursor.execute(
            "INSERT INTO vk_posts_hashes (post_id, hash) VALUES (%s, %s)",
            (post_id, new_hash)
        )
        conn.commit()
        return True

    if row["hash"] != new_hash:
        cursor.execute(
            "UPDATE vk_posts_hashes SET hash=%s, updated_at=NOW() WHERE post_id=%s",
            (new_hash, post_id)
        )
        conn.commit()
        return True

    return False


# ------------------- MAIN CHECK LOGIC -------------------

async def send_post_for_confirmation(post):
    """Отправляет пост админу на подтверждение — можно вызывать из CRON"""
    text = post.get("text", "")
    media = []

    for i, att in enumerate(post.get("attachments", [])):
        if att["type"] == "photo":
            largest = max(att["photo"]["sizes"], key=lambda s: s["width"])
            photo_bytes = download_photo_bytes(largest["url"])

            if i == 0:
                media.append(InputMediaPhoto(photo_bytes, caption=text))
            else:
                media.append(InputMediaPhoto(photo_bytes))

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Опубликовать", callback_data=f"publish_{post['id']}"),
        InlineKeyboardButton("Пропустить", callback_data=f"skip_{post['id']}")
    ]])

    if media:
        await bot.send_media_group(chat_id=ADMIN_CHAT_ID, media=media)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text="Опубликовать?", reply_markup=keyboard)
    else:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, reply_markup=keyboard)


async def check_vk_posts():
    """Эта функция вызывается через CRON (из server.py)"""
    post = get_latest_valid_post()
    if post and is_post_new_or_changed(post):
        await send_post_for_confirmation(post)


# ------------------- TELEGRAM CALLBACKS -------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, post_id = query.data.split("_")
    post_id = int(post_id)

    wall = vk.method('wall.get', {'owner_id': VK_GROUP_ID, 'count': 5})

    post = next((p for p in wall["items"] if p["id"] == post_id), None)
    if not post:
        await query.edit_message_text("Не удалось найти пост.")
        return

    if action == "publish":
        text = post.get("text", "")
        media = []

        for i, att in enumerate(post.get("attachments", [])):
            if att["type"] == "photo":
                largest = max(att["photo"]["sizes"], key=lambda s: s["width"])
                photo_bytes = download_photo_bytes(largest["url"])

                if i == 0:
                    media.append(InputMediaPhoto(photo_bytes, caption=text))
                else:
                    media.append(InputMediaPhoto(photo_bytes))

        if media:
            await bot.send_media_group(chat_id=TG_CHANNEL, media=media)
        elif text:
            await bot.send_message(chat_id=TG_CHANNEL, text=text)

        await query.edit_message_text("Пост опубликован!")
    else:
        await query.edit_message_text("Пост пропущен.")


# ------------------- LOCAL BOT START -------------------

def start_bot_polling():
    """Ты запускаешь это только локально: python bot.py"""
    app = ApplicationBuilder().token(TG_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot polling started…")
    app.run_polling()


if __name__ == "__main__":
    # запуск локально
    start_bot_polling()
