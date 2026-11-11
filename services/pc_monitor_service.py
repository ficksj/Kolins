import asyncio
import subprocess
import json
from services.logger_service import get_logger
from services.db_service import save_user_pc_session

logger = get_logger("PCMonitor")

def check_pc_critical_events(pc_name: str):
    """Сбор критических ошибок (Level 1, 2) из журнала System за последний час"""
    safe_pc_name = pc_name.replace("'", "''")
    script = f"""
    try {{
        Get-WinEvent -ComputerName '{safe_pc_name}' -FilterHashtable @{{LogName='System'; Level=1,2; StartTime=(Get-Date).AddHours(-1)}} -ErrorAction Stop |
        Select-Object TimeCreated, Id, Message |
        ConvertTo-Json -Compress
    }} catch {{
        Write-Output "__REMOTE_QUERY_ERROR__"
    }}
    """
    try:
        result = subprocess.run(["powershell", "-Command", script], capture_output=True, text=True, timeout=10)
        if "__REMOTE_QUERY_ERROR__" in result.stdout:
            return None
        if not result.stdout.strip():
            return []

        events = json.loads(result.stdout)
        if isinstance(events, dict):
            events = [events]
        return events
    except Exception as e:
        logger.error(f"Не удалось опросить ПК {pc_name}: {e}")
        return None

def check_pc_online(dns_name: str) -> bool:
    """Проверка доступности ПК через ping"""
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", "500", dns_name],
            capture_output=True,
            timeout=2
        )
        return result.returncode == 0
    except Exception:
        return False

def get_pc_logged_user_quser(target_pc: str) -> str | None:
    """Быстрое получение активного пользователя через команду quser"""
    try:
        res = subprocess.run(
            ["quser", f"/server:{target_pc}"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0 and res.stdout:
            lines = res.stdout.strip().splitlines()
            for line in lines[1:]:  # пропускаем заголовок
                parts = line.split()
                if parts:
                    username = parts[0].lstrip(">").strip()
                    if username:
                        return username.lower()
        return None
    except Exception:
        return None

def get_pc_logged_user(pc_name: str, dns_name: str = "") -> str | None:
    """Получение текущего залогиненного пользователя (quser -> WMI PowerShell) с кэшированием в БД"""
    target = dns_name if dns_name and dns_name != "—" else pc_name
    
    # 1. Сначала пробуем сверхбыстрый quser (работает за доли секунды)
    user = get_pc_logged_user_quser(target)
    if not user and target != pc_name:
        user = get_pc_logged_user_quser(pc_name)

    # 2. Если quser не вернул пользователя (или недоступен), опрашиваем через WMI
    if not user:
        safe_pc_name = pc_name.replace("'", "''")
        script = f"""
        try {{
            $s = Get-WmiObject -Class Win32_ComputerSystem -ComputerName '{safe_pc_name}' -ErrorAction Stop
            if ($s.UserName) {{ Write-Output $s.UserName }} else {{ Write-Output "__NOBODY__" }}
        }} catch {{
            Write-Output "__ERROR__"
        }}
        """
        try:
            result = subprocess.run(
                ["powershell", "-Command", script],
                capture_output=True, text=True, timeout=8
            )
            output = result.stdout.strip()
            if output and output not in ("__ERROR__", "__NOBODY__"):
                user = output.split("\\")[-1] if "\\" in output else output
                user = user.strip().lower()
        except Exception as e:
            logger.debug(f"WMI опрос ПК {pc_name} завершился ошибкой: {e}")

    # 3. Если пользователь обнаружен, обновляем сессию в БД
    if user:
        save_user_pc_session(user, pc_name, dns_name)

    return user

async def find_pc_by_username_scan(target_username: str, computer_list: list[str], max_concurrency: int = 25) -> str | None:
    """Параллельный опрос компьютеров для поиска ПК сотрудника"""
    target = target_username.strip().lower()
    semaphore = asyncio.Semaphore(max_concurrency)

    async def probe(pc_dns: str):
        pc_name = pc_dns.split(".")[0]
        async with semaphore:
            # Сначала быстрый пинг
            is_online = await asyncio.to_thread(check_pc_online, pc_dns)
            if not is_online:
                return None
            # Опрос залогиненного пользователя
            user = await asyncio.to_thread(get_pc_logged_user, pc_name, pc_dns)
            if user and user.lower() == target:
                return pc_name
            return None

    tasks = [asyncio.create_task(probe(pc)) for pc in computer_list]
    for completed_task in asyncio.as_completed(tasks):
        result = await completed_task
        if result:
            # Отменяем остальные таски, как только нашли
            for t in tasks:
                if not t.done():
                    t.cancel()
            return result

    return None
