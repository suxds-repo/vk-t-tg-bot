from fastapi import FastAPI
import asyncio
from bot import check_vk_posts

app = FastAPI()

@app.get("/check")
async def check():
    await check_vk_posts()
    return {"status": "ok"}
