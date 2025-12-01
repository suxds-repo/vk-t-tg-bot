import os
import requests
from fastapi import FastAPI, Request
from bot import bot, get_telegram_app, check_vk_posts

app = FastAPI()

# ------------------- Telegram Application -------------------
telegram_app = get_telegram_app()

@app.on_event("startup")
async def startup():
    # Инициализация приложения Telegram
    await telegram_app.initialize()

    # Установка webhook
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # например https://your-app.onrender.com/webhook
    TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
    requests.get(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}")

    # Запуск VK проверки в фоне
    from asyncio import create_task
    async def vk_loop():
        import asyncio
        while True:
            try:
                await check_vk_posts()
            except Exception as e:
                print("VK check error:", e)
            await asyncio.sleep(5)
    create_task(vk_loop())

# ------------------- Webhook endpoint -------------------

@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    await telegram_app.process_update(update)
    return {"ok": True}
