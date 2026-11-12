import asyncio
import subprocess
import json
import re
from services.logger_service import get_logger
from services.db_service import save_user_pc_session, get_pc_user_session

logger = get_logger("PCMonitor")

def run_ps_cmd(cmd: str, timeout: int = 10) -> str:
    """Выполняет команду powershell с гарантированными флагами окружения"""
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return res.stdout.strip()
    except Exception as e:
        logger.debug(f"PowerShell error: {e}")
        return ""

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
    output = run_ps_cmd(script, timeout=10)
    if "__REMOTE_QUERY_ERROR__" in output or not output:
        return None
    try:
        events = json.loads(output)
        if isinstance(events, dict):
            events = [events]
        return events
    except Exception as e:
        logger.error(f"Не удалось распарсить события для ПК {pc_name}: {e}")
        return None

def check_pc_online(dns_name: str) -> bool:
    """Проверка доступности ПК через ping"""
    try:
        import platform
        count_flag = "-n" if platform.system().lower() == "windows" else "-c"
        result = subprocess.run(
            ["ping", count_flag, "1", "-w", "500", dns_name],
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
            for line in lines[1:]:
                parts = line.split()
                if parts:
                    username = parts[0].lstrip(">").strip()
                    if username:
                        return username.lower()
        return None
    except Exception:
        return None

def get_pc_logged_user(pc_name: str, dns_name: str = "") -> str | None:
    """
    Кроссплатформенное получение активного пользователя ПК:
    1. Проверка живого сеанса через quser (если Windows)
    2. WMI запрос Win32_ComputerSystem (если Windows)
    3. Персистентный кэш сессий из БД SQLite (работает 100% стабильно на Linux и Windows)
    """
    target = dns_name if dns_name and dns_name != "—" else pc_name
    user = None
    
    # 1. quser (только на Windows хостах)
    import platform
    if platform.system().lower() == "windows":
        user = get_pc_logged_user_quser(target)
        if not user and target != pc_name:
            user = get_pc_logged_user_quser(pc_name)

        # 2. WMI опрос через PowerShell
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
            output = run_ps_cmd(script, timeout=6)
            if output and output not in ("__ERROR__", "__NOBODY__"):
                user = output.split(chr(92))[-1] if chr(92) in output else output
                user = user.strip().lower()

    # 3. Если живой опрос недоступен (например, на сервере Linux), читаем сохраненную сессию из SQLite
    if not user:
        saved = get_pc_user_session(pc_name)
        if saved and saved.get("username"):
            user = saved["username"]
            logger.debug(f"Пользователь ПК {pc_name} получен из кэша БД: {user}")

    # 4. Если пользователь получен, актуализируем в БД
    if user:
        save_user_pc_session(user, pc_name, dns_name)

    return user

def get_pc_diagnostics(pc_name: str, dns_name: str = "") -> dict | None:
    """
    Удаленная диагностика состояния ПК через WMI / PowerShell:
    - Свободное место на дисках C:, D:
    - Оперативная память (Free / Total RAM)
    - Время непрерывной работы (Uptime)
    """
    target = dns_name if dns_name and dns_name != "—" else pc_name
    safe_target = target.replace("'", "''")

    # Если опрос локальной машины
    comp_param = f"-ComputerName '{safe_target}'" if safe_target.lower() not in ("localhost", "127.0.0.1", ".") else ""

    script = f"""
    try {{
        $os = Get-CimInstance -ClassName Win32_OperatingSystem {comp_param} -OperationTimeoutSec 5 -ErrorAction Stop
        $disks = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DriveType=3" {comp_param} -OperationTimeoutSec 5 -ErrorAction SilentlyContinue
        
        $diff = (Get-Date) - $os.LastBootUpTime
        $uptimeStr = "$($diff.Days) дн. $($diff.Hours) ч. $($diff.Minutes) мин."

        $totalRamGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
        $freeRamGB = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
        $usedRamGB = [math]::Round($totalRamGB - $freeRamGB, 1)
        $ramPct = if ($totalRamGB -gt 0) {{ [math]::Round(($usedRamGB / $totalRamGB) * 100) }} else {{ 0 }}

        $diskList = @()
        foreach ($d in $disks) {{
            $totalGB = [math]::Round($d.Size / 1GB, 1)
            $freeGB = [math]::Round($d.FreeSpace / 1GB, 1)
            $usedGB = [math]::Round($totalGB - $freeGB, 1)
            $pct = if ($totalGB -gt 0) {{ [math]::Round(($usedGB / $totalGB) * 100) }} else {{ 0 }}
            $diskList += @{{
                device = $d.DeviceID
                total_gb = $totalGB
                free_gb = $freeGB
                used_gb = $usedGB
                percent = $pct
            }}
        }}

        @{{
            uptime = $uptimeStr
            ram_total_gb = $totalRamGB
            ram_free_gb = $freeRamGB
            ram_used_gb = $usedRamGB
            ram_percent = $ramPct
            disks = $diskList
        }} | ConvertTo-Json -Compress
    }} catch {{
        Write-Output "__DIAG_ERROR__"
    }}
    """
    output = run_ps_cmd(script, timeout=12)
    if not output or "__DIAG_ERROR__" in output:
        return None
    try:
        return json.loads(output)
    except Exception as e:
        logger.error(f"Ошибка парсинга диагностики {pc_name}: {e}")
        return None

def reboot_remote_pc(pc_name: str, dns_name: str = "") -> bool:
    """Удаленная принудительная перезагрузка компьютера"""
    target = dns_name if dns_name and dns_name != "—" else pc_name
    safe_target = target.replace("'", "''")
    script = f"Restart-Computer -ComputerName '{safe_target}' -Force -ErrorAction Stop; Write-Output '__REBOOT_OK__'"
    output = run_ps_cmd(script, timeout=8)
    return "__REBOOT_OK__" in output

async def find_pc_by_username_scan(target_username: str, computer_list: list[str], max_concurrency: int = 25) -> str | None:
    """Параллельный опрос компьютеров для поиска ПК сотрудника"""
    target = target_username.strip().lower()
    semaphore = asyncio.Semaphore(max_concurrency)

    async def probe(pc_dns: str):
        pc_name = pc_dns.split(".")[0]
        async with semaphore:
            is_online = await asyncio.to_thread(check_pc_online, pc_dns)
            if not is_online:
                return None
            user = await asyncio.to_thread(get_pc_logged_user, pc_name, pc_dns)
            if user and user.lower() == target:
                return pc_name
            return None

    tasks = [asyncio.create_task(probe(pc)) for pc in computer_list]
    for completed_task in asyncio.as_completed(tasks):
        result = await completed_task
        if result:
            for t in tasks:
                if not t.done():
                    t.cancel()
            return result

    return None
