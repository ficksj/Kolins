import asyncio
import ssl
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

import config
from handlers import routers
from middlewares.auth_middleware import AdminAuthMiddleware
from middlewares.delete_message_middleware import DeleteUserMessageMiddleware
from services.logger_service import get_logger

logger = get_logger("Main")

def create_bot_session() -> AiohttpSession:
    """Создает сессию aiohttp с поддержкой корпоративных SSL-прокси/DLP"""
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    session = AiohttpSession()
    session._connector_init["ssl"] = ssl_ctx
    return session

async def main():
    logger.info(f"Проверка конфигурации токена бота...")

    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN не найден в .env!")
        return

    session = create_bot_session()
    bot = Bot(
        token=config.BOT_TOKEN.strip(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # Подключаем миддлвары авторизации
    dp.message.outer_middleware(AdminAuthMiddleware())
    dp.callback_query.outer_middleware(AdminAuthMiddleware())

    # Удаляем сообщения пользователя после обработки
    dp.message.middleware(DeleteUserMessageMiddleware())

    # Подключаем роутеры из списка
    for r in routers:
        dp.include_router(r)

    me = await bot.get_me()
    logger.info(f"Бот успешно авторизован в Telegram: {me.first_name} (@{me.username})")
    logger.info("Запуск бота Kolins (Polling)...")
    await bot.delete_webhook(drop_pending_updates=True)
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
