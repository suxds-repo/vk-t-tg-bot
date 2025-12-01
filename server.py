import asyncio
from fastapi import FastAPI, Request
from bot import get_telegram_app, check_vk_posts

app = FastAPI()
telegram_app = get_telegram_app()

@app.on_event("startup")
async def startup_event():
    """Запускаем цикл проверки VK постов каждые 5 секунд"""
    async def loop():
        while True:
            try:
                await check_vk_posts(telegram_app)
            except Exception as e:
                print("Ошибка проверки VK:", e)
            await asyncio.sleep(5)  # 5 секунд
    asyncio.create_task(loop())

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Вебхук Telegram"""
    update = await request.json()
    from telegram import Update
    telegram_update = Update.de_json(update, telegram_app.bot)
    await telegram_app.process_update(telegram_update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "ok"}
