import os
import io
import hashlib
import aiohttp
import vk_api
from dotenv import load_dotenv
from supabase import create_client, Client

from telegram import InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, Application

load_dotenv()

# ------------ ENV ------------------
VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID"))

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHANNEL = os.getenv("TG_CHANNEL")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ------------ SUPABASE ------------------
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------ VK CLIENT -----------------
vk = vk_api.VkApi(token=VK_TOKEN)

# ------------ UTILS ---------------------
def should_post(post):
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


async def download_photo_bytes(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return io.BytesIO(await resp.read())


def get_post_hash(post):
    text = post.get("text", "")
    photos = []
    for att in post.get("attachments", []):
        if att["type"] == "photo":
            largest = max(att["photo"]["sizes"], key=lambda s: s["width"])
            photos.append(largest["url"])
    return hashlib.md5((text + "".join(photos)).encode()).hexdigest()


def is_post_new_or_changed(post):
    post_id = str(post["id"])
    new_hash = get_post_hash(post)

    # SELECT
    result = supabase.table("vk_posts_hashes").select("hash").eq("post_id", post_id).execute()
    data = result.data

    if not data:
        # INSERT
        supabase.table("vk_posts_hashes").insert({
            "post_id": post_id,
            "hash": new_hash
        }).execute()
        return True

    old_hash = data[0]["hash"]
    if old_hash != new_hash:
        # UPDATE
        supabase.table("vk_posts_hashes").update({
            "hash": new_hash,
            "updated_at": "now()"
        }).eq("post_id", post_id).execute()
        return True

    return False


# ------------------- SAVE POST --------------------------
def save_post_to_db(post):
    post_id = str(post["id"])
    text = post.get("text", "")
    photos = [
        max(att["photo"]["sizes"], key=lambda s: s["width"])["url"]
        for att in post.get("attachments", []) if att["type"] == "photo"
    ]

    supabase.table("vk_posts").upsert({
        "post_id": post_id,
        "text": text,
        "photos": photos
    }).execute()


# ------------------- CLEAN OLD POSTS ---------------------
MAX_POSTS = 20

def clean_old_posts():
    # Получаем последние 20 id
    latest = supabase.table("vk_posts")\
        .select("post_id")\
        .order("updated_at", desc=True)\
        .limit(MAX_POSTS)\
        .execute()

    keep_ids = [row["post_id"] for row in latest.data]

    # Удаляем остальные
    supabase.table("vk_posts")\
        .delete()\
        .not_.in_("post_id", keep_ids)\
        .execute()

MAX_HASHES = 20

def clean_old_hashes():
    # Получаем последние 20 хешей
    latest = supabase.table("vk_posts_hashes")\
        .select("post_id")\
        .order("updated_at", desc=True)\
        .limit(MAX_HASHES)\
        .execute()

    keep_ids = [row["post_id"] for row in latest.data]

    if not keep_ids:
        return

    # Удаляем остальные
    supabase.table("vk_posts_hashes")\
        .delete()\
        .not_.in_("post_id", keep_ids)\
        .execute()


# ------------------- SEND TO ADMIN -----------------------
async def send_post_for_confirmation(post, app: Application):
    save_post_to_db(post)

    text = post.get("text", "")
    photos = [
        max(att["photo"]["sizes"], key=lambda s: s["width"])["url"]
        for att in post.get("attachments", []) if att["type"] == "photo"
    ]

    media = []
    for i, url in enumerate(photos):
        photo_bytes = await download_photo_bytes(url)
        caption = text[:1024] if i == 0 else None
        media.append(InputMediaPhoto(photo_bytes, caption=caption))

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Опубликовать ✅", callback_data=f"publish_{post['id']}"),
        InlineKeyboardButton("Пропустить ❌", callback_data=f"skip_{post['id']}")
    ]])

    if media:
        await app.bot.send_media_group(ADMIN_CHAT_ID, media)
        await app.bot.send_message(ADMIN_CHAT_ID, "Опубликовать?", reply_markup=keyboard)
    else:
        await app.bot.send_message(ADMIN_CHAT_ID, text, reply_markup=keyboard)


# ------------------- CRON CHECK --------------------------
async def check_vk_posts(app: Application):
    clean_old_posts()
    post = get_latest_valid_post()
    if post and is_post_new_or_changed(post):
        await send_post_for_confirmation(post, app)


# ------------------- CALLBACK -----------------------------
async def button_callback(update: Update, context):
    query = update.callback_query
    await query.answer()

    action, post_id = query.data.split("_")
    post_id = str(post_id)

    # SELECT
    row = supabase.table("vk_posts").select("*").eq("post_id", post_id).single().execute()

    if not row.data:
        await query.edit_message_text("Не удалось найти пост в БД.")
        return

    text = row.data.get("text")
    photos = row.data.get("photos") or []

    media = []
    for i, url in enumerate(photos):
        photo_bytes = await download_photo_bytes(url)
        caption = text[:1024] if i == 0 else None
        media.append(InputMediaPhoto(photo_bytes, caption=caption))

    if action == "publish":
        if media:
            await context.bot.send_media_group(TG_CHANNEL, media)
        elif text:
            await context.bot.send_message(TG_CHANNEL, text)
        await query.edit_message_text("Пост опубликован!")
    else:
        await query.edit_message_text("Пост пропущен.")


# ------------------- APP INIT -----------------------------
def get_telegram_app():
    app = Application.builder().token(TG_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(button_callback))
    return app

