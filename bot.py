import os
import io
import hashlib
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import vk_api

from fastapi import FastAPI, Request
from telegram import (
    Bot,
    InputMediaPhoto,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    AIORateLimiter,
)

load_dotenv()

# ---------------- ENV -------------------
VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID"))

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHANNEL = os.getenv("TG_CHANNEL")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://your-app.onrender.com/webhook

# ---------------- DB -------------------
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

# ---------------- CLIENTS -------------
vk = vk_api.VkApi(token=VK_TOKEN)
bot = Bot(TG_BOT_TOKEN)

# ---------------- FASTAPI APP ----------
app = FastAPI()


# ------------------- UTILS -------------------
def should_post(post):
    if post.get("is_pinned") == 1:
        return False
    if post.get("marked_as_ads") == 1:
        return False
    if "copy_history" in post:
        return False
    return True


def get_latest_valid_post():
    wall = vk.method("wall.get", {"owner_id": VK_GROUP_ID, "count": 5})
    for post in wall.get("items", []):
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
            (post_id, new_hash),
        )
        conn.commit()
        return True

    if row["hash"] != new_hash:
        cursor.execute(
            "UPDATE vk_posts_hashes SET hash=%s, updated_at=NOW() WHERE post_id=%s",
            (new_hash, post_id),
        )
        conn.commit()
        return True

    return False


# ------------------- CRON CHECK -------------------
async def send_post_for_confirmation(post):
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

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Опубликовать", callback_data=f"publish_{post['id']}"
                ),
                InlineKeyboardButton("Пропустить", callback_data=f"skip_{post['id']}"),
            ]
        ]
    )

    if media:
        await bot.send_media_group(ADMIN_CHAT_ID, media)
        await bot.send_message(
            ADMIN_CHAT_ID, "Опубликовать?", reply_markup=keyboard
        )
    else:
        await bot.send_message(
            ADMIN_CHAT_ID, text, reply_markup=keyboard
        )


async def check_vk_posts():
    post = get_latest_valid_post()
    if post and is_post_new_or_changed(post):
        await send_post_for_confirmation(post)


# -------------- TELEGRAM BUTTON HANDLER -------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, post_id = query.data.split("_")
    post_id = int(post_id)

    wall = vk.method("wall.get", {"owner_id": VK_GROUP_ID, "count": 5})
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
            await bot.send_media_group(TG_CHANNEL, media)
        else:
            await bot.send_message(TG_CHANNEL, text)

        await query.edit_message_text("Пост опубликован!")
    else:
        await query.edit_message_text("Пост пропущен.")


# ----------------- TELEGRAM WEBHOOK -----------------
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot)
    await application.update_queue.put(update)
    return {"ok": True}


# ----------------- CRON ENDPOINT --------------------
@app.get("/check")
async def cron_check():
    await check_vk_posts()
    return {"status": "ok"}


# ----------------- START TELEGRAM BOT ----------------
application = (
    ApplicationBuilder()
    .token(TG_BOT_TOKEN)
    .rate_limiter(AIORateLimiter())
    .build()
)

application.add_handler(CallbackQueryHandler(button_callback))


@app.on_event("startup")
async def startup():
    print("Setting webhook...")
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)


