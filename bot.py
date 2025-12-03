import os
import io
import hashlib
import aiohttp
import requests
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

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


# ------------ GLOBALS ------------------
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
vk = vk_api.VkApi(token=VK_TOKEN)

aiohttp_session: aiohttp.ClientSession | None = None


async def get_session():
    global aiohttp_session
    if aiohttp_session is None:
        aiohttp_session = aiohttp.ClientSession()
    return aiohttp_session


# ------------ DISCORD ------------------
def send_to_discord(text: str, photos: list[str]):
    import mimetypes

    # Начинаем контент с @everyone
    content = f"@everyone\n{text}" if text else "@everyone"

    if not photos:
        # Если картинок нет, просто отправляем текст
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content})
        return

    # Отправка файлов
    files = {}
    for i, url in enumerate(photos[:10]):  # до 10 файлов
        response = requests.get(url)
        response.raise_for_status()
        ext = mimetypes.guess_extension(response.headers.get('Content-Type', 'image/png')) or ".png"
        files[f"file{i}"] = (f"image{i}{ext}", response.content)

    requests.post(DISCORD_WEBHOOK_URL, data={"content": content}, files=files)



# ------------ VK UTILS ------------------
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


# ------------ HASHING ------------------
def get_post_hash(post):
    text = post.get("text", "")

    photo_ids = [
        str(att["photo"]["id"])
        for att in post.get("attachments", [])
        if att["type"] == "photo"
    ]

    raw = text + "|" + "|".join(sorted(photo_ids))
    return hashlib.md5(raw.encode()).hexdigest()


# ------------ PHOTOS / MEDIA ------------------
def extract_photo_urls(post):
    return [
        max(att["photo"]["sizes"], key=lambda s: s["width"])["url"]
        for att in post.get("attachments", [])
        if att["type"] == "photo"
    ]


async def make_media_group(text, photos):
    session = await get_session()
    media = []

    for i, url in enumerate(photos):
        async with session.get(url) as resp:
            resp.raise_for_status()
            image = io.BytesIO(await resp.read())

        caption = text[:1024] if i == 0 and text else None
        media.append(InputMediaPhoto(image, caption=caption))

    return media


# ------------ SUPABASE LOGIC ------------------
def is_post_new_or_changed(post):
    post_id = str(post["id"])
    new_hash = get_post_hash(post)

    res = supabase.table("vk_posts_hashes").select("hash").eq("post_id", post_id).execute()
    data = res.data

    if not data:
        supabase.table("vk_posts_hashes").insert({
            "post_id": post_id,
            "hash": new_hash
        }).execute()
        return True

    old_hash = data[0]["hash"]

    if old_hash != new_hash:
        supabase.table("vk_posts_hashes").update({
            "hash": new_hash,
            "updated_at": "now()"
        }).eq("post_id", post_id).execute()
        return True

    return False


def save_post_to_db(post):
    supabase.table("vk_posts").upsert({
        "post_id": str(post["id"]),
        "text": post.get("text", ""),
        "photos": extract_photo_urls(post),
        "post_hash": get_post_hash(post)
    }).execute()


# ------------ CLEANERS ------------------
MAX_POSTS = 20
MAX_HASHES = 20


def clean_old(table, limit):
    latest = (
        supabase.table(table)
        .select("post_id")
        .order("updated_at", desc=True)
        .limit(limit)
        .execute()
    )

    keep_ids = [row["post_id"] for row in latest.data]

    supabase.table(table)\
        .delete()\
        .not_.in_("post_id", keep_ids)\
        .execute()


def clean_old_posts():
    clean_old("vk_posts", MAX_POSTS)


def clean_old_hashes():
    clean_old("vk_posts_hashes", MAX_HASHES)


# ------------ ADMIN CONFIRMATION ------------------
async def send_post_for_confirmation(post, app: Application):
    save_post_to_db(post)

    text = post.get("text", "")
    photos = extract_photo_urls(post)
    media = await make_media_group(text, photos)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Опубликовать ✅", callback_data=f"publish_{post['id']}"),
        InlineKeyboardButton("Пропустить ❌", callback_data=f"skip_{post['id']}")
    ]])

    if media:
        await app.bot.send_media_group(ADMIN_CHAT_ID, media)
        await app.bot.send_message(ADMIN_CHAT_ID, "Опубликовать?", reply_markup=keyboard)
    else:
        await app.bot.send_message(ADMIN_CHAT_ID, text, reply_markup=keyboard)


# ------------ CRON CHECKER ------------------
async def check_vk_posts(app: Application):
    clean_old_posts()
    clean_old_hashes()

    post = get_latest_valid_post()
    if post and is_post_new_or_changed(post):
        await send_post_for_confirmation(post, app)


# ------------ CALLBACK ------------------
async def button_callback(update: Update, context):
    query = update.callback_query
    await query.answer()

    action, post_id = query.data.split("_")
    post_id = str(post_id)

    row = supabase.table("vk_posts").select("*").eq("post_id", post_id).single().execute().data
    if not row:
        await query.edit_message_text("Не удалось найти пост в БД.")
        return

    text = row.get("text")
    photos = row.get("photos") or []

    if action == "publish":
        media = await make_media_group(text, photos)

        if media:
            await context.bot.send_media_group(TG_CHANNEL, media)
        elif text:
            await context.bot.send_message(TG_CHANNEL, text)

        send_to_discord(text, photos)

        await query.edit_message_text("Пост опубликован!")
    else:
        await query.edit_message_text("Пост пропущен.")


# ------------ APP INIT ------------------
def get_telegram_app():
    app = Application.builder().token(TG_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(button_callback))
    return app




