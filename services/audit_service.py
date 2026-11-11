from aiogram import Bot
from aiogram.types import User
import config
from services.logger_service import get_logger
from services.rich_message_service import (
    html_text,
    render_bubble_card,
    send_rich_to_chat,
)

logger = get_logger("AuditService")

async def send_audit_log(bot: Bot, admin: User, action: str, target: str, status: bool = True):
    if not config.LOG_CHAT_ID:
        logger.warning("LOG_CHAT_ID не задан в .env, аудит-лог пропущен.")
        return

    status_str = "✅ УСПЕШНО" if status else "❌ ОШИБКА"
    badge = "SUCCESS" if status else "FAILURE"
    admin_name = html_text(admin.full_name or "Администратор")
    admin_mention = html_text(f"@{admin.username}") if admin.username else f"ID: {admin.id}"

    body = [
        f"<b>Инициатор:</b> {admin_name} ({admin_mention})",
        f"<b>Действие:</b> <code>{html_text(action)}</code>",
        f"<b>Целевой объект:</b> <code>{html_text(target)}</code>",
        f"<b>Итог операции:</b> {status_str}",
    ]

    table_rows = [
        ("Администратор", f"{admin_name} ({admin_mention})"),
        ("Операция", html_text(action)),
        ("Объект", f"<code>{html_text(target)}</code>"),
        ("Статус", status_str),
    ]

    rich_html = render_bubble_card(
        title="🛡️ Аудит действия AD",
        badge=badge,
        body_lines=body,
        table_rows=table_rows,
        footer_note="Автоматический аудит-лог безопасности Kolins"
    )

    try:
        chat_id = int(config.LOG_CHAT_ID)
        await send_rich_to_chat(bot, chat_id=chat_id, html=rich_html)
        logger.info(f"Аудит-лог отправлен в чат {chat_id}")
    except Exception as e:
        logger.error(f"Не удалось отправить аудит-лог в чат {config.LOG_CHAT_ID}: {e}")
