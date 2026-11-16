import asyncio
import datetime
import json
import os
import platform
import re
import socket
import subprocess

try:
    from pypsrp.client import Client as WinRMClient
    from pypsrp.exceptions import AuthenticationError, WinRMError
except ImportError:
    WinRMClient = None

    class AuthenticationError(Exception):
        pass

    class WinRMError(Exception):
        pass

import config
from services.db_service import get_pc_user_session, save_user_pc_session
from services.logger_service import get_logger

logger = get_logger("PCMonitor")


def _resolve_target(pc_name: str, dns_name: str = "") -> str:
    """Выбирает валидный сетевой адрес ПК (FQDN или NetBIOS-имя)."""
    if dns_name and dns_name.strip() and dns_name.strip() != "—":
        return dns_name.strip()
    return pc_name.strip()


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def run_ps_cmd(cmd: str, timeout: int = 10) -> str:
    """Выполняет локальную команду PowerShell (только на Windows-хостах)."""
    if not _is_windows():
        return ""
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return res.stdout.strip()
    except Exception as e:
        logger.debug(f"Local PowerShell error: {e}")
        return ""


def _run_winrm_ps(target_pc: str, ps_script: str, timeout: int = 8) -> tuple[bool, str, str | None]:
    """
    Кроссплатформенное выполнение PowerShell на удаленном Windows ПК через WinRM (порт 5985).
    Возвращает (ok, stdout, error_code).
    """
    if WinRMClient is None:
        return False, "", "no_pypsrp"
    username = config.PC_ADMIN_USER
    password = config.PC_ADMIN_PASSWORD
    if not username or not password:
        return False, "", "no_credentials"

    try:
        client = WinRMClient(
            target_pc,
            username=username,
            password=password,
            ssl=False,
            auth="negotiate",
            encryption="auto",
            connection_timeout=4,
            read_timeout=timeout,
        )
        stdout, streams, had_errors = client.execute_ps(ps_script)
        out_clean = (stdout or "").strip()
        if had_errors and not out_clean:
            err_msgs = []
            if streams and hasattr(streams, "error") and streams.error:
                err_msgs = [str(e) for e in streams.error]
            logger.debug(f"WinRM PS error on {target_pc}: {'; '.join(err_msgs)[:200]}")
            return False, "", "ps_error"
        return True, out_clean, None
    except AuthenticationError as e:
        logger.debug(f"WinRM auth denied on {target_pc} ({username}): {e}")
        return False, "", "auth_denied"
    except (WinRMError, TimeoutError, ConnectionError, OSError) as e:
        msg = str(e).lower()
        if "401" in msg or "access is denied" in msg or "unauthorized" in msg:
            return False, "", "auth_denied"
        logger.debug(f"WinRM connection/exec error on {target_pc}: {e}")
        return False, "", "offline"
    except Exception as e:
        logger.debug(f"WinRM unexpected error on {target_pc}: {e}")
        return False, "", "error"


def _exec_remote_ps(target_pc: str, ps_script: str, timeout: int = 10) -> tuple[bool, str, str | None]:
    """
    Универсальное выполнение PowerShell:
    - На Linux: всегда через WinRM (pypsrp)
    - На Windows: локальный PowerShell (если не задан отдельный PC_ADMIN_USER), либо fallback на WinRM
    """
    if _is_windows() and not os.getenv("PC_ADMIN_USER"):
        out = run_ps_cmd(ps_script, timeout=timeout)
        return bool(out), out, None if out else "error"
    return _run_winrm_ps(target_pc, ps_script, timeout=timeout)


def _check_tcp_port(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def check_pc_online(dns_name: str) -> bool:
    """
    Проверка сетевой доступности ПК:
    - Кроссплатформенный ping (исправлен флаг -W 1 сек для Linux вместо -w 500)
    - Резервная проверка TCP портов 5985 (WinRM) / 445 (SMB), если ICMP закрыт файрволом
    """
    if not dns_name or dns_name.strip() == "—":
        return False
    host = dns_name.strip()
    try:
        if _is_windows():
            cmd = ["ping", "-n", "1", "-w", "500", host]
        else:
            cmd = ["ping", "-c", "1", "-W", "1", host]
        result = subprocess.run(cmd, capture_output=True, timeout=2)
        if result.returncode == 0:
            return True
    except Exception:
        pass

    return _check_tcp_port(host, 5985, 0.4) or _check_tcp_port(host, 445, 0.4)


def get_pc_logged_user_quser(target_pc: str) -> str | None:
    """Быстрое получение активного пользователя через локальную утилиту quser (только Windows)."""
    if not _is_windows():
        return None
    try:
        res = subprocess.run(
            ["quser", f"/server:{target_pc}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout:
            return _parse_quser_output(res.stdout)
        return None
    except Exception:
        return None


def _parse_quser_output(raw_text: str) -> str | None:
    """Извлекает имя активного пользователя из вывода quser."""
    lines = [l for l in raw_text.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    # Приоритет активной сессии (Active / Активно)
    fallback_user = None
    for line in lines[1:]:
        parts = line.split()
        if not parts:
            continue
        uname = parts[0].lstrip(">").strip().lower()
        if not uname or uname in ("services", "console"):
            continue
        lower_line = line.lower()
        if "active" in lower_line or "активн" in lower_line:
            return uname
        if not fallback_user:
            fallback_user = uname
    return fallback_user


def get_pc_logged_user_detailed(
    pc_name: str,
    dns_name: str = "",
    allow_cache_fallback: bool = True,
) -> dict:
    """
    Возвращает расширенный результат определения активного пользователя:
    {"username": str|None, "source": "live"|"cache"|"none", "updated_at": str|None, "error_code": str|None}
    """
    target = _resolve_target(pc_name, dns_name)
    user = None
    error_code = None

    # 1. На Windows сначала пробуем быстрый quser
    if _is_windows():
        user = get_pc_logged_user_quser(target)
        if not user and target != pc_name:
            user = get_pc_logged_user_quser(pc_name)

    # 2. Живой опрос (WinRM на Linux или локальный PowerShell WMI на Windows)
    if not user:
        if _is_windows() and not os.getenv("PC_ADMIN_USER"):
            safe_pc = pc_name.replace("'", "''")
            script = f"""
            try {{
                $s = Get-CimInstance -ClassName Win32_ComputerSystem -ComputerName '{safe_pc}' -ErrorAction Stop
                if ($s.UserName) {{ Write-Output $s.UserName }} else {{ Write-Output "__NOBODY__" }}
            }} catch {{
                Write-Output "__ERROR__"
            }}
            """
            out = run_ps_cmd(script, timeout=6)
            if out and out not in ("__ERROR__", "__NOBODY__"):
                user = (out.split("\\")[-1] if "\\" in out else out).strip().lower()
        else:
            # На удаленной машине через WinRM выполняем локальный запрос (без -ComputerName!)
            ps_winrm = """
            $u = (Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction SilentlyContinue).UserName
            if ($u) {
                Write-Output ("__USER__" + $u)
            } else {
                $q = quser 2>$null
                if ($q) {
                    Write-Output "__QUSER_START__"
                    $q | ForEach-Object { Write-Output $_ }
                } else {
                    Write-Output "__NOBODY__"
                }
            }
            """
            ok, out, err = _run_winrm_ps(target, ps_winrm, timeout=7)
            if ok and out:
                if "__USER__" in out:
                    raw_u = out.split("__USER__", 1)[1].splitlines()[0].strip()
                    if raw_u:
                        user = (raw_u.split("\\")[-1] if "\\" in raw_u else raw_u).strip().lower()
                elif "__QUSER_START__" in out:
                    quser_text = out.split("__QUSER_START__", 1)[1]
                    user = _parse_quser_output(quser_text)
            elif not ok:
                error_code = err

    if user:
        save_user_pc_session(user, pc_name, target if target != pc_name else dns_name)
        return {
            "username": user,
            "source": "live",
            "updated_at": None,
            "error_code": None,
        }

    # 3. Если живой опрос не дал результата — читаем кэш SQLite
    if allow_cache_fallback:
        saved = get_pc_user_session(pc_name)
        if saved and saved.get("username"):
            return {
                "username": saved["username"],
                "source": "cache",
                "updated_at": saved.get("updated_at"),
                "error_code": error_code,
            }

    return {
        "username": None,
        "source": "none",
        "updated_at": None,
        "error_code": error_code,
    }


def get_pc_logged_user(pc_name: str, dns_name: str = "", allow_cache_fallback: bool = True) -> str | None:
    """Обратно-совместимая обертка, возвращающая логин пользователя ПК или None."""
    res = get_pc_logged_user_detailed(pc_name, dns_name, allow_cache_fallback=allow_cache_fallback)
    return res.get("username")


def _parse_ps_datetime(raw: object) -> datetime.datetime | None:
    """Разбирает ISO или legacy WMI дату вида YYYYmmddHHMMSS."""
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) >= 19 and s[4] == "-" and s[10] == "T":
        try:
            return datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    digits = re.match(r"^(\d{14})", s)
    if digits:
        try:
            return datetime.datetime.strptime(digits.group(1), "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return None


def _format_uptime(boot_raw: object, now_raw: object) -> str:
    """Вычисляет аптайм на стороне Python (без .NET вызовов на удаленном ПК)."""
    boot_dt = _parse_ps_datetime(boot_raw)
    if not boot_dt:
        return "—"
    now_dt = _parse_ps_datetime(now_raw) or datetime.datetime.now()
    delta = now_dt - boot_dt
    if delta.total_seconds() < 0:
        delta = datetime.timedelta(0)
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    return f"{days} дн. {hours} ч. {minutes} мин."


def _extract_ipv4(raw_ips: object) -> str:
    """Оставляет уникальные не-loopback/non-APIPA IPv4 адреса."""
    if isinstance(raw_ips, str):
        items = [raw_ips]
    elif isinstance(raw_ips, list):
        items = [str(x).strip() for x in raw_ips if x]
    else:
        items = []

    ipv4 = []
    seen = set()
    for ip in items:
        if ":" in ip or "." not in ip:
            continue
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        if ip not in seen:
            seen.add(ip)
            ipv4.append(ip)
    return ", ".join(ipv4) if ipv4 else "—"


def _clean_text_field(val: object) -> str:
    s = str(val).strip() if val is not None else ""
    return s or "—"


def _build_diagnostics_result(raw: dict) -> dict:
    """Нормализует сырые телеметрические данные WMI/CIM в словарь диагностики."""
    if "ram_total_gb" in raw and "disks" in raw and "uptime" in raw:
        return raw

    kib_per_gb = float(1024 * 1024)
    bytes_per_gb = float(1024**3)
    mem_total_kb = float(raw.get("mem_total_kb") or 0)
    mem_free_kb = float(raw.get("mem_free_kb") or 0)
    total_ram_gb = round(mem_total_kb / kib_per_gb, 1)
    free_ram_gb = round(mem_free_kb / kib_per_gb, 1)
    used_ram_gb = round(max(0.0, total_ram_gb - free_ram_gb), 1)
    ram_pct = round((used_ram_gb / total_ram_gb) * 100, 1) if total_ram_gb > 0 else 0

    raw_disks = raw.get("disks") or []
    if isinstance(raw_disks, dict):
        raw_disks = [raw_disks]

    disk_list = []
    for d in raw_disks:
        if not isinstance(d, dict):
            continue
        size_b = float(d.get("size") or 0)
        free_b = float(d.get("free") or 0)
        tot = round(size_b / bytes_per_gb, 1)
        free = round(free_b / bytes_per_gb, 1)
        used = round(max(0.0, tot - free), 1)
        pct = round((used / tot) * 100, 1) if tot > 0 else 0
        disk_list.append(
            {
                "device": _clean_text_field(d.get("device") or d.get("DeviceID")),
                "total_gb": tot,
                "free_gb": free,
                "used_gb": used,
                "percent": pct,
            }
        )

    return {
        "uptime": _format_uptime(raw.get("boot"), raw.get("now")),
        "ram_total_gb": total_ram_gb,
        "ram_free_gb": free_ram_gb,
        "ram_used_gb": used_ram_gb,
        "ram_percent": ram_pct,
        "disks": disk_list,
        "manufacturer": _clean_text_field(raw.get("manufacturer")),
        "model": _clean_text_field(raw.get("model")),
        "serial_number": _clean_text_field(raw.get("serial_number")),
        "ip_address": _extract_ipv4(raw.get("ips")),
    }


def _normalize_critical_events(raw_events: object) -> list[dict]:
    """Приводит список событий WinEvent к компактному формату для Telegram-карточки."""
    if isinstance(raw_events, dict):
        items = [raw_events]
    elif isinstance(raw_events, list):
        items = raw_events
    else:
        return []

    normalized = []
    for ev in items:
        if not isinstance(ev, dict):
            continue
        raw_msg = ev.get("Message") if ev.get("Message") is not None else ev.get("msg", "")
        msg_clean = re.sub(r"\s+", " ", str(raw_msg or "")).strip()[:140]
        normalized.append(
            {
                "time": str(ev.get("time") or ""),
                "Id": ev.get("Id", ""),
                "ProviderName": str(ev.get("ProviderName") or ""),
                "msg": msg_clean,
            }
        )
    return normalized


def get_pc_diagnostics(pc_name: str, dns_name: str = "") -> dict | None:
    """
    Кроссплатформенная удаленная диагностика ПК через WinRM / PowerShell:
    - Uptime, RAM, Диски (C:/D:/...)
    - Модель оборудования, Производитель, Серийный номер (S/N), IPv4
    Скрипт не использует .NET-вызовы [math]/ManagementDateTimeConverter ради совместимости с ConstrainedLanguageMode.
    При ошибке авторизации возвращает {'_error': 'auth_denied'}.
    """
    target = _resolve_target(pc_name, dns_name)
    safe_target = target.replace("'", "''")

    if _is_windows() and not os.getenv("PC_ADMIN_USER"):
        comp_param = f"-ComputerName '{safe_target}'" if safe_target.lower() not in ("localhost", "127.0.0.1", ".") else ""
        wmi_prefix = f"Get-WmiObject {comp_param}"
    else:
        wmi_prefix = "Get-CimInstance"

    script = f"""
    try {{
        $os = {wmi_prefix} -Class Win32_OperatingSystem -ErrorAction Stop
        $cs = {wmi_prefix} -Class Win32_ComputerSystem -ErrorAction SilentlyContinue
        $bios = {wmi_prefix} -Class Win32_BIOS -ErrorAction SilentlyContinue
        $disks = @({wmi_prefix} -Class Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction SilentlyContinue)

        $lastBoot = $os.LastBootUpTime
        $bootIso = if ($lastBoot -is [datetime]) {{ Get-Date $lastBoot -Format "yyyy-MM-ddTHH:mm:ss" }} else {{ [string]$lastBoot }}
        $nowIso = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"

        $diskList = @()
        foreach ($d in $disks) {{
            if ($d) {{
                $diskList += @{{
                    device = $d.DeviceID
                    size = $d.Size
                    free = $d.FreeSpace
                }}
            }}
        }}

        $ips = @()
        try {{
            $ips = @({wmi_prefix} -Class Win32_NetworkAdapterConfiguration -Filter "IPEnabled=True" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddress)
        }} catch {{}}
        if (-not $ips -or $ips.Count -eq 0) {{
            try {{
                $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddress)
            }} catch {{}}
        }}

        @{{
            boot = $bootIso
            now = $nowIso
            mem_total_kb = $os.TotalVisibleMemorySize
            mem_free_kb = $os.FreePhysicalMemory
            disks = $diskList
            manufacturer = $cs.Manufacturer
            model = $cs.Model
            serial_number = $bios.SerialNumber
            ips = @($ips)
        }} | ConvertTo-Json -Compress -Depth 3
    }} catch {{
        Write-Output "__DIAG_ERROR__"
    }}
    """

    ok, output, err_code = _exec_remote_ps(target, script, timeout=14)
    if not ok:
        if err_code == "auth_denied":
            return {"_error": "auth_denied"}
        return None
    if not output or "__DIAG_ERROR__" in output:
        return None
    try:
        raw = json.loads(output)
        return _build_diagnostics_result(raw)
    except Exception as e:
        logger.error(f"Ошибка парсинга диагностики {pc_name}: {e}")
        return None


def check_pc_critical_events(pc_name: str, dns_name: str = "") -> list[dict] | dict | None:
    """Сбор последних критических ошибок (Level 1, 2) из журнала System за 24 часа."""
    target = _resolve_target(pc_name, dns_name)
    safe_target = target.replace("'", "''")
    comp_param = f"-ComputerName '{safe_target}'" if (_is_windows() and not os.getenv("PC_ADMIN_USER")) else ""

    script = f"""
    try {{
        $since = (Get-Date).AddHours(-24)
        $ev = @(Get-WinEvent {comp_param} -FilterHashtable @{{LogName='System'; Level=1,2; StartTime=$since}} -MaxEvents 8 -ErrorAction SilentlyContinue |
            Select-Object @{{Name='time';Expression={{Get-Date $_.TimeCreated -Format 'dd.MM HH:mm'}}}}, Id, ProviderName, Message)
        if (-not $ev -or $ev.Count -eq 0) {{
            Write-Output "[]"
        }} else {{
            ConvertTo-Json -InputObject $ev -Compress -Depth 2
        }}
    }} catch {{
        Write-Output "__REMOTE_QUERY_ERROR__"
    }}
    """
    ok, output, err_code = _exec_remote_ps(target, script, timeout=12)
    if not ok:
        if err_code == "auth_denied":
            return {"_error": "auth_denied"}
        return None
    if "__REMOTE_QUERY_ERROR__" in output or not output:
        return None
    try:
        events = json.loads(output)
        return _normalize_critical_events(events)
    except Exception as e:
        logger.error(f"Не удалось распарсить события для ПК {pc_name}: {e}")
        return None


def reboot_remote_pc_detailed(pc_name: str, dns_name: str = "") -> tuple[bool, str]:
    """Удаленная принудительная перезагрузка ПК с детальным статусом результата."""
    target = _resolve_target(pc_name, dns_name)
    safe_target = target.replace("'", "''")
    if _is_windows() and not os.getenv("PC_ADMIN_USER"):
        script = f"Restart-Computer -ComputerName '{safe_target}' -Force -ErrorAction Stop; Write-Output '__REBOOT_OK__'"
    else:
        script = "Restart-Computer -Force -ErrorAction Stop; Write-Output '__REBOOT_OK__'"

    ok, output, err_code = _exec_remote_ps(target, script, timeout=10)
    if ok and "__REBOOT_OK__" in output:
        return True, "ok"
    return False, err_code or "error"


def reboot_remote_pc(pc_name: str, dns_name: str = "") -> bool:
    """Удаленная принудительная перезагрузка компьютера."""
    ok, _ = reboot_remote_pc_detailed(pc_name, dns_name)
    return ok


def kill_remote_processes(
    pc_name: str,
    dns_name: str = "",
    process_names: tuple[str, ...] = ("notes", "nlnotes", "notes2", "ntaskldr"),
) -> tuple[bool, str]:
    """Завершает указанные процессы на удаленном ПК через WinRM (например, IBM Notes)."""
    target = _resolve_target(pc_name, dns_name)
    names_ps = ",".join(f"'{n}'" for n in process_names)
    script = f"""
    try {{
        Get-Process -Name {names_ps} -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Write-Output "__KILL_OK__"
    }} catch {{
        Write-Output "__KILL_ERR__"
    }}
    """
    ok, output, err_code = _exec_remote_ps(target, script, timeout=8)
    if ok and "__KILL_OK__" in output:
        return True, "ok"
    return False, err_code or "error"


async def find_pc_by_username_scan(
    target_username: str,
    computer_list: list[str],
    max_concurrency: int = 25,
) -> str | None:
    """Параллельный живой опрос онлайн-компьютеров для поиска ПК сотрудника."""
    target = target_username.strip().lower()
    semaphore = asyncio.Semaphore(max_concurrency)

    async def probe(pc_dns: str):
        pc_name = pc_dns.split(".")[0]
        async with semaphore:
            is_online = await asyncio.to_thread(check_pc_online, pc_dns)
            if not is_online:
                return None
            # В живом скане проверяем только живую сессию (чтобы не читать SQLite 481 раз впустую)
            user = await asyncio.to_thread(get_pc_logged_user, pc_name, pc_dns, False)
            if user and user.lower() == target:
                return pc_name
            return None

    tasks = [asyncio.create_task(probe(pc)) for pc in computer_list]
    for completed_task in asyncio.as_completed(tasks):
        try:
            result = await completed_task
        except asyncio.CancelledError:
            continue
        if result:
            for t in tasks:
                if not t.done():
                    t.cancel()
            return result

    return None
