import asyncio
from fastapi import FastAPI, Request
from bot import get_telegram_app, check_vk_posts, clean_old_hashes
from telegram import Update

app = FastAPI()
telegram_app = get_telegram_app()  # создаём приложение Telegram


@app.on_event("startup")
async def startup_event():
    """Инициализация Telegram и запуск цикла проверки VK"""
    # Инициализируем Telegram Application
    await telegram_app.initialize()
    await telegram_app.start()

    # Запуск цикла проверки VK
    async def vk_loop():
        while True:
            try:
                #await clean_old_hashes()       # ← добавить
                await check_vk_posts(telegram_app)
            except Exception as e:
                print("Ошибка проверки VK:", e)
            await asyncio.sleep(5)

    asyncio.create_task(vk_loop())


@app.on_event("shutdown")
async def shutdown_event():
    """Корректное завершение Telegram"""
    await telegram_app.stop()
    await telegram_app.shutdown()


@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Обработка вебхука Telegram"""
    data = await request.json()
    telegram_update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(telegram_update)
    return {"ok": True}


@app.get("/check_vk")
async def check_vk_endpoint():
    try:
        await clean_old_hashes()               # ← добавить
        await check_vk_posts(telegram_app)
    except Exception as e:
        print("check_vk endpoint error:", str(e)[:200])
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"status": "ok"}
