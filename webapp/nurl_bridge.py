import asyncio
import json
import os
import ssl
import struct
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import aiohttp
import websockets
from services.logger_service import get_logger

logger = get_logger("NurlBridge")

# SSL контекст для обхода корпоративного перехвата сертификатов (GTB Technologies DLP)
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

class NurlBridge:
    def __init__(self, target_port: int = 5000):
        self.target_port = target_port
        self.target_url = f"http://127.0.0.1:{target_port}"
        self.public_url = None
        self.is_running = False

    async def open_tunnel(self) -> dict:
        """Регистрация нового туннеля на сервере nurl.pw"""
        url = "https://nurl.pw/api/tunnels/open"
        payload = {"target": f"http://localhost:{self.target_port}"}
        
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    raise RuntimeError(f"Ошибка регистрации туннеля: {resp.status} - {text}")
                return await resp.json()

    async def run(self):
        """Запуск цикла моста туннелирования"""
        self.is_running = True
        while self.is_running:
            try:
                logger.info("Регистрируем публичный туннель в Nurl...")
                tunnel_info = await self.open_tunnel()
                self.public_url = tunnel_info.get("public_url")
                token = tunnel_info.get("token")
                ws_url = tunnel_info.get("ws_url", "wss://nurl.pw/api/tunnels/connect")
                
                logger.info(f"🚀 Публичный URL готов: {self.public_url}")

                headers = {"Authorization": f"Bearer {token}"}
                async with websockets.connect(ws_url, additional_headers=headers, ssl=ssl_ctx) as ws:
                    logger.info("Подключено к WebSocket Nurl. Ожидаем входящие запросы...")
                    async with aiohttp.ClientSession() as local_http:
                        while self.is_running:
                            message = await ws.recv()
                            if isinstance(message, bytes) and len(message) >= 4:
                                # Парсинг бинарного фрейма Nurl: 4 байта длины JSON + JSON + бинарное тело (если есть)
                                json_len = struct.unpack(">I", message[:4])[0]
                                json_bytes = message[4:4 + json_len]
                                body_bytes = message[4 + json_len:]
                                
                                try:
                                    frame = json.loads(json_bytes.decode("utf-8"))
                                except Exception as e:
                                    logger.error(f"Ошибка парсинга фрейма: {e}")
                                    continue

                                if frame.get("type") == "request":
                                    req_id = frame.get("id")
                                    method = frame.get("method", "GET")
                                    uri = frame.get("uri", "/")
                                    
                                    # Проксируем на локальный aiohttp сервер
                                    target_req_url = f"{self.target_url}{uri}"
                                    req_headers = {}
                                    for h in frame.get("headers", []):
                                        if len(h) == 2 and h[0].lower() not in ("host", "content-length"):
                                            req_headers[h[0]] = h[1]

                                    try:
                                        async with local_http.request(
                                            method,
                                            target_req_url,
                                            headers=req_headers,
                                            data=body_bytes if body_bytes else None,
                                            allow_redirects=False,
                                            timeout=aiohttp.ClientTimeout(total=15)
                                        ) as loc_resp:
                                            resp_body = await loc_resp.read()
                                            resp_headers = []
                                            for k, v in loc_resp.headers.items():
                                                if k.lower() not in ("transfer-encoding", "content-encoding"):
                                                    resp_headers.append([k, v])

                                            resp_frame = {
                                                "type": "response",
                                                "id": req_id,
                                                "status": loc_resp.status,
                                                "headers": resp_headers,
                                                "upstream_ms": 1
                                            }
                                            resp_json_bytes = json.dumps(resp_frame).encode("utf-8")
                                            out_msg = struct.pack(">I", len(resp_json_bytes)) + resp_json_bytes + resp_body
                                            await ws.send(out_msg)
                                    except Exception as err:
                                        logger.error(f"Ошибка локального проксирования {target_req_url}: {err}")
                                        err_frame = {
                                            "type": "error",
                                            "id": req_id,
                                            "message": f"Local error: {err}"
                                        }
                                        err_json = json.dumps(err_frame).encode("utf-8")
                                        await ws.send(struct.pack(">I", len(err_json)) + err_json)
            except Exception as e:
                logger.warning(f"Разрыв туннеля Nurl: {e}. Переподключение через 3 секунды...")
                await asyncio.sleep(3)

if __name__ == "__main__":
    bridge = NurlBridge(5000)
    asyncio.run(bridge.run())
