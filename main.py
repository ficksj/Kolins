import asyncio
import ssl
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

import config
from aiogram.types import ErrorEvent
from handlers import routers
from middlewares.auth_middleware import AdminAuthMiddleware
from middlewares.delete_message_middleware import DeleteUserMessageMiddleware
from services.ad_monitor_worker import start_ad_monitor_worker
from services.pc_background_worker import start_pc_background_worker
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

    @dp.errors()
    async def global_error_handler(event: ErrorEvent):
        logger.exception(f"Необработанная ошибка бота: {event.exception}", exc_info=event.exception)
        upd = event.update
        err_text = "⚠️ Произошла внутренняя ошибка при обработке запроса. Подробности записаны в журнал."
        try:
            if upd.callback_query:
                try:
                    await upd.callback_query.answer("⚠️ Ошибка выполнения", show_alert=False)
                except Exception:
                    pass
                if upd.callback_query.message:
                    await upd.callback_query.message.answer(err_text)
            elif upd.message:
                await upd.message.answer(err_text)
        except Exception:
            pass
        return True

    me = await bot.get_me()
    logger.info(f"Бот успешно авторизован в Telegram: {me.first_name} (@{me.username})")
    logger.info("Запуск бота Kolins (Polling)...")
    await bot.delete_webhook(drop_pending_updates=True)

    ad_monitor_task = asyncio.create_task(start_ad_monitor_worker(bot, interval_seconds=900))
    pc_worker_task = asyncio.create_task(start_pc_background_worker(interval_seconds=2700))

    try:
        try:
            await dp.start_polling(bot)
        finally:
            for t in (ad_monitor_task, pc_worker_task):
                t.cancel()
            await asyncio.gather(ad_monitor_task, pc_worker_task, return_exceptions=True)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
