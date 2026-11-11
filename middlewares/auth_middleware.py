from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
import config
from services.logger_service import get_logger

logger = get_logger("AuthMiddleware")

class AdminAuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)
        
        if config.ADMIN_IDS and user.id not in config.ADMIN_IDS:
            logger.warning(f"Несанкционированная попытка доступа от пользователя {user.id} (@{user.username})")
            if isinstance(event, Message):
                await event.answer("⛔ У вас нет прав для работы с этим ботом.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ запрещен.", show_alert=True)
            return
        
        return await handler(event, data)