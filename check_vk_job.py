import asyncio
from bot import get_telegram_app, check_vk_posts

async def main():
    telegram_app = get_telegram_app()
    try:
        await check_vk_posts(telegram_app)
        print("Проверка VK выполнена успешно")
    except Exception as e:
        print("Ошибка проверки VK:", e)

if __name__ == "__main__":
    asyncio.run(main())
