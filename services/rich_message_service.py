from html import escape
import math
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, Message

from services.logger_service import get_logger

logger = get_logger("RichMessageService")


def html_text(value) -> str:
    """Escape dynamic values for Telegram Rich HTML."""
    return escape(str(value if value is not None and value != "" else "—"), quote=True)


def rich_message(html: str) -> InputRichMessage:
    """Create the native Bot API rich-message payload."""
    return InputRichMessage(html=html, skip_entity_detection=True)


async def send_rich(message: Message, html: str) -> Message:
    """Send a native Rich Message to the current chat."""
    return await message.bot.send_rich_message(
        chat_id=message.chat.id,
        rich_message=rich_message(html),
        message_thread_id=getattr(message, "message_thread_id", None),
    )


async def edit_rich(message: Message, html: str):
    """Replace the contents of the same message with a new Rich Message."""
    try:
        return await message.edit_text(rich_message=rich_message(html), parse_mode=None)
    except TelegramBadRequest as error:
        err_lower = str(error).lower()
        if "message is not modified" in err_lower:
            return message
        if "message can't be edited" not in err_lower:
            raise
        logger.warning(
            "Rich message %s in chat %s is not editable; sending updated card instead",
            message.message_id,
            message.chat.id if message.chat else "unknown",
        )
        return await send_rich(message, html)


async def edit_rich_by_ids(bot, chat_id: int | str, message_id: int, html: str):
    """Edit a Rich Message when only its stored chat/message IDs are available."""
    try:
        return await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            rich_message=rich_message(html),
            parse_mode=None,
        )
    except TelegramBadRequest as error:
        err_lower = str(error).lower()
        if "message is not modified" in err_lower:
            return None
        if "message can't be edited" not in err_lower:
            raise
        logger.warning(
            "Rich message %s in chat %s is not editable; sending updated card instead",
            message_id,
            chat_id,
        )
        return await send_rich_to_chat(bot, chat_id, html)


async def send_rich_to_chat(bot, chat_id: int | str, html: str):
    """Send a native Rich Message when there is no incoming Message object."""
    return await bot.send_rich_message(chat_id=chat_id, rich_message=rich_message(html))


def callback_button(label: str, data: str, style: str = "primary") -> str:
    """Build a callback button with escaped visible label and payload."""
    if not data or len(data.encode("utf-8")) > 64:
        raise ValueError("Telegram callback_data must be 1-64 UTF-8 bytes")
    return (
        f'<tg-button type="callback_data" style="{style}" '
        f'data="{html_text(data)}">{html_text(label)}</tg-button>'
    )


def button_row(*buttons: str, align: str = "center") -> str:
    return f'<tg-button-row align="{align}">{"".join(buttons)}</tg-button-row>'


def copy_button(label: str, text: str) -> str:
    return f'<tg-button type="copy_text" text="{html_text(text)}">{html_text(label)}</tg-button>'


def webapp_button(label: str, url: str, style: str = "primary") -> str:
    """Build a WebApp button that opens a Mini App inside Telegram."""
    return f'<tg-button type="web_app" url="{html_text(url)}" style="{style}">{html_text(label)}</tg-button>'


# --- ДИЗАЙН-СИСТЕМА: HTML WIDGETS & BUBBLES ---

def render_progress_bar(percent: float, length: int = 10) -> str:
    """Генерация текстового графического прогресс-бара."""
    pct = max(0.0, min(100.0, percent))
    filled_len = int(round((pct / 100.0) * length))
    bar = "█" * filled_len + "░" * (length - filled_len)
    return f"[{bar}] {int(pct)}%"


def render_bubble_header(title: str, subtitle: str = "", badge: str = "") -> str:
    """Верхняя акцентная плашка-заголовок виджета."""
    badge_part = f" <b>{badge}</b>" if badge else ""
    sub_part = f"<div><i>{html_text(subtitle)}</i></div>" if subtitle else ""
    return f"<h3>{title}{badge_part}</h3>{sub_part}"


def render_bubble_card(
    title: str,
    body_lines: list[str],
    badge: str = "",
    table_rows: list[tuple[str, str]] | None = None,
    details_title: str | None = None,
    details_content: str | None = None,
    footer_note: str | None = None,
    buttons: list[str] | None = None,
) -> str:
    """
    Генератор целостного HTML-виджета (Bubble Widget):
    - Акцентный заголовок с бейджем статуса
    - Бабл-блок (<blockquote>) с основной сводкой
    - Компактная таблица характеристик (<table bordered compact striped>)
    - Сворачиваемые детали (<details><summary>)
    - Кнопки быстрых действий (<tg-button-row>)
    """
    html_parts = []
    
    # 1. Заголовок
    badge_html = f" · {badge}" if badge else ""
    html_parts.append(f"<h2>{title}{badge_html}</h2>")
    
    # 2. Основной бабл (blockquote)
    if body_lines:
        content = "<br>".join(body_lines)
        html_parts.append(f"<blockquote>{content}</blockquote>")
        
    # 3. Таблица параметров
    if table_rows:
        rows_html = "".join(
            f"<tr><td><b>{html_text(label)}</b></td><td>{val}</td></tr>"
            for label, val in table_rows
        )
        html_parts.append(f"<table bordered compact striped>{rows_html}</table>")
        
    # 4. Сворачиваемый блок деталей
    if details_title and details_content:
        html_parts.append(
            f"<details><summary>{html_text(details_title)}</summary>{details_content}</details>"
        )
        
    # 5. Футер / примечание
    if footer_note:
        html_parts.append(f"<p><small>ℹ️ {html_text(footer_note)}</small></p>")
        
    # 6. Кнопки действий
    if buttons:
        html_parts.extend(buttons)
        
    return "".join(html_parts)
