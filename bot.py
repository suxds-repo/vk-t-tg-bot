import os
import time
import hashlib
import requests
import psycopg2
from datetime import datetime
from telegram import Bot
from telegram.constants import ParseMode
from dotenv import load_dotenv

load_dotenv()

# -----------------------------------------
#  Настройки
# -----------------------------------------
VK_API_URL = "https://api.vk.com/method/wall.get"
VK_GROUP_ID = "-224038468"
VK_API_VERSION = "5.154"
MAX_POSTS = 20

# -----------------------------------------
#  Получение переменных окружения
# -----------------------------------------
vk_access_token = os.getenv("VK_ACCESS_TOKEN")
bot_token = os.getenv("BOT_TOKEN")
chat_id = os.getenv("CHAT_ID")  # telegram chat id
db_url = os.getenv("DATABASE_URL")

if not all([vk_access_token, bot_token, chat_id, db_url]):
    raise ValueError("❌ Не найдены переменные окружения! Проверь .env")

# -----------------------------------------
#  Подключение к базе
# -----------------------------------------
try:
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
except Exception as e:
    print("❌ Ошибка подключения к БД:", e)
    exit()

# -----------------------------------------
#  Проверка и создание таблиц
# -----------------------------------------
cursor.execute("""
    CREATE TABLE IF NOT EXISTS vk_posts (
        post_id BIGINT PRIMARY KEY,
        text TEXT,
        photos TEXT[],
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS vk_posts_hashes (
        post_id BIGINT PRIMARY KEY,
        content_hash TEXT
    )
""")

conn.commit()

bot = Bot(token=bot_token)

# -----------------------------------------
#  Функция очистки (исправлена!)
# -----------------------------------------
def clean_old_posts():
    cursor.execute("""
        DELETE FROM vk_posts
        WHERE post_id NOT IN (
            SELECT post_id FROM vk_posts ORDER BY updated_at DESC LIMIT %s
        )
    """, (MAX_POSTS,))
    conn.commit()
    print("🧹 Очищены старые записи vk_posts (хэши теперь не трогаем!)")

# -----------------------------------------
#  Генерация хэша поста
# -----------------------------------------
def calculate_post_hash(post):
    post_id = post.get("id", 0)
    owner_id = post.get("owner_id", 0)
    text = post.get("text", "") or ""
    attachments = post.get("attachments", [])

    photos_data = []
    for attachment in attachments:
        if attachment["type"] == "photo":
            photos_data.append(str(attachment["photo"].get("sizes", [])))

    hash_string = f"{post_id}|{owner_id}|{text}|{'|'.join(photos_data)}"
    return hashlib.md5(hash_string.encode("utf-8")).hexdigest()

# -----------------------------------------
#  Получение медиаконтента
# -----------------------------------------
def get_post_media(post):
    media = []
    attachments = post.get("attachments", [])

    for attachment in attachments:
        if attachment["type"] == "photo":
            sizes = attachment["photo"].get("sizes", [])
            if sizes:
                media.append(max(sizes, key=lambda x: x["width"])["url"])
    return media

# -----------------------------------------
#  Проверка URL (фото)
# -----------------------------------------
def is_url_valid(url):
    try:
        response = requests.head(url, timeout=5)
        return response.status_code == 200
    except:
        return False

def validate_photo_urls(photo_urls):
    return [url for url in photo_urls if is_url_valid(url)]

# -----------------------------------------
#  Парсинг поста в текст
# -----------------------------------------
def parse_vk_post(post):
    post_text = post.get("text", "") or ""
    media_urls = get_post_media(post)

    post_link = (
        f"https://vk.com/wall{post.get('from_id')}_{post.get('id')}"
    )

    text_content = f"<b>{post_text}</b>\n\n<a href='{post_link}'>Открыть пост ВКонтакте</a>"
    return text_content, media_urls

# -----------------------------------------
#  Основная логика обработки
# -----------------------------------------
while True:
    try:
        response = requests.get(
            VK_API_URL,
            params={
                "access_token": vk_access_token,
                "owner_id": VK_GROUP_ID,
                "count": 5,
                "v": VK_API_VERSION,
            }
        ).json()

        if "error" in response:
            print("⚠ VK API error:", response["error"])
            time.sleep(10)
            continue

        posts = response.get("response", {}).get("items", [])
        if not posts:
            print("⚠ Нет постов в группе")
            time.sleep(10)
            continue

        clean_old_posts()

        for post in posts:
            post_id = post.get("id", 0)
            owner_id = post.get("owner_id", 0)
            combined_id = f"{owner_id}_{post_id}"

            cursor.execute("SELECT content_hash FROM vk_posts_hashes WHERE post_id=%s", (combined_id,))
            result = cursor.fetchone()

            current_hash = calculate_post_hash(post)

            if result:
                old_hash = result[0]
                if old_hash == current_hash:
                    print(f"↩ Пропущен (без изменений): {combined_id}")
                    continue
                else:
                    print(f"♻ Обновление поста: {combined_id}")
            else:
                print(f"🆕 Новый пост: {combined_id}")

            # Парсим
            try:
                parsed_text, media_urls = parse_vk_post(post)

                # отправка без фото
                bot.send_message(
                    chat_id=chat_id,
                    text=parsed_text,
                    parse_mode=ParseMode.HTML
                )
                print(f"📨 Отправлен в Telegram post_id={post_id}")

                # Валидируем фото
                valid_media_urls = validate_photo_urls(media_urls)

                # отправляем фото по каждому валидному URL
                for url in valid_media_urls:
                    bot.send_photo(chat_id=chat_id, photo=url)

            except Exception as e:
                print(f"❌ Ошибка обработки поста: {e}")

            # записываем в БД
            cursor.execute("""
                INSERT INTO vk_posts (post_id, text, photos, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (post_id) DO UPDATE
                SET text = EXCLUDED.text,
                    photos = EXCLUDED.photos,
                    updated_at = EXCLUDED.updated_at
            """, (combined_id, post.get("text", ""), media_urls, datetime.now()))

            cursor.execute("""
                INSERT INTO vk_posts_hashes (post_id, content_hash)
                VALUES (%s, %s)
                ON CONFLICT (post_id) DO UPDATE
                SET content_hash = EXCLUDED.content_hash
            """, (combined_id, current_hash))

            conn.commit()

        print("⏳ Ожидание 10 секунд...\n")
        time.sleep(10)

    except Exception as e:
        print("❌ Глобальная ошибка:", e)
        time.sleep(5)
