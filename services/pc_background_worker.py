import asyncio
from services.ad_service import ad_service
from services.pc_monitor_service import check_pc_online, get_pc_logged_user_detailed
from services.logger_service import get_logger

logger = get_logger("PCBackgroundWorker")


async def run_pc_index_cycle(max_concurrency: int = 15) -> dict:
    """
    Один тихий цикл индексации активных пользовательских сессий по всем ПК AD.
    Ничего не отправляет в Telegram-чаты. Прекращает цикл при 3 auth_denied подряд.
    """
    computers = await asyncio.to_thread(ad_service.get_target_computers)
    if not computers:
        return {"total": 0, "online": 0, "indexed": 0, "aborted": False}

    sem = asyncio.Semaphore(max_concurrency)
    stats = {"total": len(computers), "online": 0, "indexed": 0, "aborted": False}
    consecutive_auth_denied = 0

    async def probe(pc_host: str):
        nonlocal consecutive_auth_denied
        if stats["aborted"]:
            return
        async with sem:
            if stats["aborted"]:
                return
            online = await asyncio.to_thread(check_pc_online, pc_host)
            if not online:
                return
            stats["online"] += 1

            pc_short = pc_host.split(".")[0]
            dns = pc_host if "." in pc_host else ""
            res = await asyncio.to_thread(
                get_pc_logged_user_detailed,
                pc_short,
                dns,
                False,
            )
            err = res.get("error_code")
            if err == "auth_denied":
                consecutive_auth_denied += 1
                if consecutive_auth_denied >= 3 and not stats["aborted"]:
                    stats["aborted"] = True
                    logger.warning(
                        "Фоновый индексер ПК приостановлен до следующего цикла: "
                        "3 ошибки auth_denied подряд (проверьте PC_ADMIN_USER / RemoteAdmins)."
                    )
                return

            consecutive_auth_denied = 0
            if res.get("username"):
                stats["indexed"] += 1

    await asyncio.gather(*(probe(pc) for pc in computers), return_exceptions=True)
    return stats


async def start_pc_background_worker(interval_seconds: int = 2700):
    """Тихий фоновый рабочий цикл для поддержания актуальной карты пользователь <-> ПК в SQLite."""
    logger.info(f"Фоновый индексер сессий ПК запущен (интервал: {interval_seconds} сек)")
    await asyncio.sleep(20)

    while True:
        try:
            stats = await run_pc_index_cycle()
            logger.info(
                f"Индексация ПК завершена: всего={stats['total']}, онлайн={stats['online']}, "
                f"сессий={stats['indexed']}, aborted={stats['aborted']}"
            )
        except asyncio.CancelledError:
            logger.info("Фоновый индексер сессий ПК остановлен.")
            break
        except Exception as e:
            logger.error(f"Исключение в цикле PCBackgroundWorker: {e}")

        await asyncio.sleep(interval_seconds)
