import asyncio
import datetime
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
import config
from services.ad_service import ad_service
from services.db_service import is_alert_sent, record_alert_sent
from services.logger_service import get_logger
from services.rich_message_service import (
    render_bubble_card,
    button_row,
    callback_button,
    html_text,
    send_rich_to_chat
)

logger = get_logger("ADMonitorWorker")

async def check_locked_accounts(bot: Bot):
    """Проверка заблокированных учетных записей и отправка алертов с ограничением частоты (rate limit)"""
    if not config.LOG_CHAT_ID:
        return
    try:
        locked_users = await asyncio.to_thread(ad_service.get_locked_users)
        if not locked_users:
            return

        for u in locked_users:
            username = u["username"]
            alert_key = f"lockout:{username}:{datetime.date.today().isoformat()}"
            
            # Проверяем, отправляли ли уже алерт сегодня
            if is_alert_sent(alert_key):
                continue

            card = render_bubble_card(
                title="🚨 Внимание: Учетная запись заблокирована!",
                badge="Безопасность AD",
                body_lines=[
                    f"Сотрудник: <b>{html_text(u['display_name'])}</b>",
                    f"Логин: <code>{html_text(username)}</code>",
                    f"Подразделение: <i>{html_text(u['department'])}</i>",
                    f"Неверных попыток ввода: <b>{u['bad_pwd_count']}</b>",
                ],
                footer_note="Сотрудник не может войти на компьютер. Нажмите кнопку ниже для разблокировки:",
                buttons=[
                    button_row(
                        callback_button('🔓 Разблокировать учетную запись', f'user:unlock:{username}', 'success'),
                        callback_button('👤 Профиль', f'user:show:{username}', 'link'),
                    )
                ]
            )

            try:
                await send_rich_to_chat(bot, config.LOG_CHAT_ID, card)
                record_alert_sent(alert_key, "lockout")
                logger.info(f"Отправлен алерт о блокировке пользователя: {username}")
                # Пауза 1.5 секунды между алертами, чтобы не превысить лимиты Telegram Flood Control
                await asyncio.sleep(1.5)
            except TelegramRetryAfter as e:
                logger.warning(f"Flood limit Telegram, ожидание {e.retry_after} сек...")
                await asyncio.sleep(e.retry_after + 1)
                break
            except Exception as e:
                logger.error(f"Не удалось отправить алерт для {username}: {e}")

    except Exception as e:
        logger.error(f"Ошибка в фоновой проверке блокировок: {e}")

async def start_ad_monitor_worker(bot: Bot, interval_seconds: int = 900):
    """
    Фоновый рабочий цикл мониторинга Active Directory.
    По умолчанию запускается каждые 15 минут (900 секунд).
    """
    logger.info(f"Фоновый мониторинг AD запущен (интервал: {interval_seconds} сек)")
    # Первичная задержка 15 секунд после старта бота
    await asyncio.sleep(15)
    
    while True:
        try:
            await check_locked_accounts(bot)
        except asyncio.CancelledError:
            logger.info("Фоновый мониторинг AD остановлен.")
            break
        except Exception as e:
            logger.error(f"Исключение в цикле ADMonitorWorker: {e}")

        await asyncio.sleep(interval_seconds)
