# VK/TG AutoPoster Bot 🚀

![Python](https://img.shields.io/badge/Python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.123-green)
![Telegram](https://img.shields.io/badge/Telegram-Bot-lightblue)
![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-yellow)
![Render](https://img.shields.io/badge/Render-Deployment-orange)

---

## 📌 О проекте

**VK/TG AutoPoster Bot** — автопостер, который берёт новые посты из группы **ВКонтакте** и публикует их в **Telegram-канал** и **Discord**, с подтверждением администратора.  

- Асинхронный код (`asyncio`, `aiohttp`)  
- Хранение постов и хэшей в **Supabase (PostgreSQL)**  
- Деплой на **Render** с автоматическим обновлением из GitHub  

---

## ⚡ Основные возможности

- ✅ Автоматическая проверка новых постов VK  
- ✅ Фильтрация закреплённых, рекламных и репостов  
- ✅ Отправка постов на подтверждение администратору Telegram  
- ✅ Публикация постов в Telegram и Discord  
- ✅ Хранение постов и хэшей в Supabase  
- ✅ Очистка старых постов для актуальности базы  
- ✅ Продакшен-деплой на Render  

---

## 🛠 Технологии

| Backend      | База данных          | API / Bot       | Асинхронность | Деплой      |
|-------------|--------------------|----------------|---------------|------------|
| Python 3.11 | Supabase (PostgreSQL) | VK API          | asyncio, aiohttp | Render     |
| FastAPI     |                    | Telegram Bot    |               |            |
| requests    |                    | Discord Webhook |               |            |

---

## 🚀 Запуск локально

Клонируем репозиторий:

```cmd
git clone https://github.com/yourusername/vk-tg-autoposter.git
cd vk-tg-autoposter
```


# Добавьте ключи в .env:

# VK
```
VK_TOKEN=ваш_токен
VK_GROUP_ID=ID_группы
```
# Telegram
```
TG_BOT_TOKEN=токен_бота
TG_CHANNEL=@название_канала
ADMIN_CHAT_ID=ваш_id_админа
```
# Supabase
```
SUPABASE_URL=ваш_supabase_url
SUPABASE_KEY=ваш_supabase_key
```
# Discord
```
DISCORD_WEBHOOK_URL=ваш_webhook
```
🌐 Деплой

Репозиторий → Render Web Service

Переменные окружения через Render Dashboard

Supabase используется как продакшен-база данных для хранения постов и хэшей

Автоматический запуск и проверка VK каждые 5 секунд
