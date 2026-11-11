from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message

from services.logger_service import get_logger

logger = get_logger("DeleteMessageMiddleware")


class DeleteUserMessageMiddleware(BaseMiddleware):
    """Удаляет входящее сообщение пользователя после обработки хэндлером."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        try:
            await event.delete()
        except Exception as e:
            # Бот может не иметь права удалять сообщения в некоторых чатах — просто логируем
            logger.debug(f"Не удалось удалить сообщение {event.message_id}: {e}")
        return result
