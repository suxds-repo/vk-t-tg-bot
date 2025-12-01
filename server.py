import os
import requests
from fastapi import FastAPI, Request
from fastapi_utils.tasks import repeat_every
from bot import bot, get_telegram_app, check_vk_posts

app = FastAPI()

# ------------------- Telegram Webhook -------------------

telegram_app = get_telegram_app()

@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    await telegram_app.process_update(update)
    return {"ok": True}

# ------------------- VK CHECK CRON -------------------

@app.on_event("startup")
@repeat_every(seconds=60)  # каждые 60 секунд
async def periodic_vk_check():
    await check_vk_posts()

# ------------------- WEBHOOK SETUP -------------------

@app.on_event("startup")
async def setup_webhook():
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://your-app.onrender.com/webhook
    TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
    requests.get(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}")
