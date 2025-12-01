import os
import io
import json
import hashlib
import requests
from dotenv import load_dotenv
import vk_api
from telegram import Bot, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, ContextTypes, CallbackQueryHandler

load_dotenv()

VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID"))
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHANNEL = os.getenv("TG_CHANNEL")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

vk = vk_api.VkApi(token=VK_TOKEN)
bot = Bot(token=TG_BOT_TOKEN)

# словарь для хранения хэшей постов
POST_HASHES_FILE = "post_hashes.json"
if os.path.exists(POST_HASHES_FILE):
    with open(POST_HASHES_FILE, "r") as f:
        post_hashes = json.load(f)
else:
    post_hashes = {}


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
    if not post:
        return

    post_id = str(post['id'])
    current_hash = get_post_hash(post)

    # если пост новый или изменился — отправляем на подтверждение
    if post_id not in post_hashes or post_hashes[post_id] != current_hash:
        await send_post_for_confirmation(post, context)
        post_hashes[post_id] = current_hash

        # сохраняем хэши в файл
        with open(POST_HASHES_FILE, "w") as f:
            json.dump(post_hashes, f)


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


if __name__ == "__main__":
    app = ApplicationBuilder().token(TG_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(button_callback))

    # проверка новых постов каждые 5 минут
    job_queue = app.job_queue
    job_queue.run_repeating(check_vk_posts, interval=1, first=1)

    print("Бот запущен...")
    app.run_polling()
