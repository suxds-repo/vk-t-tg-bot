from fastapi import FastAPI, Request
from bot_webhook import dispatcher, check_vk_posts
from telegram import Update

app = FastAPI()

# Telegram Webhook
@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, dispatcher.bot)
    await dispatcher.process_update(update)
    return {"ok": True}

# CRON / Ping endpoint
@app.get("/check")
async def cron_check():
    await check_vk_posts()
    return {"status": "ok"}
