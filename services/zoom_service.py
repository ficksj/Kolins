import aiohttp
import config
from services.logger_service import get_logger

logger = get_logger("ZoomService")

class ZoomService:
    def __init__(self):
        self.account_id = getattr(config, "ZOOM_ACCOUNT_ID", "")
        self.client_id = getattr(config, "ZOOM_CLIENT_ID", "")
        self.client_secret = getattr(config, "ZOOM_CLIENT_SECRET", "")

    def is_configured(self) -> bool:
        """Проверяет, заданы ли учетные данные Zoom Server-to-Server OAuth."""
        return bool(
            str(self.account_id).strip()
            and str(self.client_id).strip()
            and str(self.client_secret).strip()
        )

    async def _get_access_token(self) -> str | None:
        """Получение OAuth-токена Zoom"""
        if not self.is_configured():
            return None
        url = "https://zoom.us/oauth/token"
        params = {"grant_type": "account_credentials", "account_id": self.account_id}
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    url, 
                    params=params, 
                    auth=aiohttp.BasicAuth(self.client_id, self.client_secret)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("access_token")
                    logger.error(f"Ошибка получения токена Zoom: {resp.status}")
                    return None
            except Exception as e:
                logger.error(f"Исключение при авторизации Zoom: {e}")
                return None

    async def create_meeting(self, topic: str) -> dict | None:
        """Создание мгновенной Zoom-конференции"""
        if not self.is_configured():
            logger.warning("Zoom API не настроен: не заданы ZOOM_ACCOUNT_ID / ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET.")
            return None

        token = await self._get_access_token()
        if not token:
            logger.warning("Zoom API недоступен: не удалось получить OAuth-токен.")
            return None

        url = "https://api.zoom.us/v2/users/me/meetings"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {
            "topic": f"Техподдержка AD: {topic}",
            "type": 1,  # Instant meeting
            "settings": {
                "host_video": True,
                "participant_video": True,
                "join_before_host": True
            }
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 201:
                        data = await resp.json()
                        return {
                            "join_url": data.get("join_url"),
                            "password": data.get("password", "Без пароля")
                        }
                    logger.error(f"Ошибка создания Zoom: {resp.status}")
                    return None
            except Exception as e:
                logger.error(f"Исключение при создании Zoom: {e}")
                return None

zoom_service = ZoomService()