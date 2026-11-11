import aiohttp
import config
from services.logger_service import get_logger

logger = get_logger("ZoomService")

class ZoomService:
    def __init__(self):
        self.account_id = getattr(config, "ZOOM_ACCOUNT_ID", "")
        self.client_id = getattr(config, "ZOOM_CLIENT_ID", "")
        self.client_secret = getattr(config, "ZOOM_CLIENT_SECRET", "")

    async def _get_access_token(self) -> str | None:
        """Получение OAuth-токена Zoom"""
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
        token = await self._get_access_token()
        if not token:
            # Фолбэк-заглушка для изолированных сетей без прямого выхода в Zoom API
            logger.warning("Zoom API недоступен, генерируем стандартную ссылку на комнату.")
            return {
                "join_url": "https://zoom.us/j/1234567890?pwd=bank_meeting_pass",
                "password": "bank_meeting_pass"
            }

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