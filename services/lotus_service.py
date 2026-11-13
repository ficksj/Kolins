import datetime
from html import escape
try:
    import smbclient
    from smbprotocol.exceptions import SMBException
except ImportError:
    smbclient = None

    class SMBException(Exception):
        pass

import config
from services.db_service import (
    get_lotus_transfer_by_id,
    mark_lotus_returned,
    save_lotus_transfer,
)
from services.logger_service import get_logger

logger = get_logger("LotusService")

TZ_LOCAL = datetime.timezone(datetime.timedelta(hours=5))
SYSTEM_USER_DIRS = {
    "public",
    "default",
    "default user",
    "all users",
    "defaultapppool",
    "administrator.bak",
}
DEFAULT_SUBPATHS = [
    config.LOTUS_NOTES_SUBPATH,
    r"AppData\Local\HCL\Notes\Data",
    r"AppData\Local\Lotus\Notes\Data",
]


def _parse_unc(unc_path: str) -> tuple[str, str, list[str]]:
    """Разбирает UNC вида \\\\server\\share\\sub\\dir на (server, share, [sub, dir])."""
    cleaned = unc_path.strip().replace("/", "\\").lstrip("\\")
    parts = [p for p in cleaned.split("\\") if p]
    if len(parts) < 2:
        raise ValueError(f"Некорректный UNC-путь: {unc_path}")
    return parts[0], parts[1], parts[2:]


def _build_unc(server: str, share: str, parts: list[str]) -> str:
    if parts:
        return "\\\\" + server + "\\" + share + "\\" + "\\".join(parts)
    return "\\\\" + server + "\\" + share


def _ensure_session(server: str, username: str | None, password: str | None, timeout: int = 5):
    """Регистрирует SMB-сессию с защитой от таймаутов."""
    if smbclient is None:
        raise RuntimeError("Пакет smbprotocol не установлен.")
    smbclient.register_session(
        server,
        username=username or "",
        password=password or "",
        connection_timeout=timeout,
    )


def _classify_smb_error(err: Exception) -> tuple[str, str]:
    """Преобразует исключения SMB в понятный код и текст для администратора."""
    msg = str(err)
    lower = msg.lower()
    if "0xc0000022" in lower or "status_access_denied" in lower or "access is denied" in lower:
        return (
            "access_denied",
            "Нет прав доступа (STATUS_ACCESS_DENIED). Убедитесь, что учетная запись "
            f"<code>{escape(str(config.PC_ADMIN_USER or ''))}</code> входит в группу <code>RemoteAdmins</code> / локальных администраторов ПК "
            "или имеет права записи в целевую сетевую папку.",
        )
    if "0xc0000043" in lower or "sharing_violation" in lower:
        return (
            "in_use",
            "Файл сейчас заблокирован запущенным клиентом IBM/Lotus Notes на компьютере. "
            "Закройте Notes на ПК и повторите попытку.",
        )
    if "0xc000006d" in lower or "logon_failure" in lower:
        return (
            "auth_failed",
            f"Ошибка авторизации SMB для <code>{escape(str(config.PC_ADMIN_USER or ''))}</code> (неверный логин/пароль или запрет политики).",
        )
    if isinstance(err, (TimeoutError, ConnectionError)) or "timed out" in lower or "unreachable" in lower or "name resolution" in lower:
        return ("offline", "Удаленный хост недоступен по сети (SMB порт 445 не отвечает).")
    return ("smb_error", f"Ошибка SMB: {escape(msg[:200])}")


def _ensure_smb_dir(unc_dir: str, username: str | None, password: str | None):
    """Создает целевой подкаталог на SMB-шаре, если он еще не существует."""
    server, share, parts = _parse_unc(unc_dir)
    _ensure_session(server, username, password)
    current_parts: list[str] = []
    for part in parts:
        current_parts.append(part)
        candidate = _build_unc(server, share, current_parts)
        try:
            if not smbclient.path.isdir(candidate):
                smbclient.mkdir(candidate)
                logger.info(f"Создана папка на SMB: {candidate}")
        except FileExistsError:
            continue
        except SMBException as e:
            # Если path.isdir вернул ошибку отсутствия родителя, пробуем mkdir напрямую
            if "0xc0000035" not in str(e).lower():  # STATUS_OBJECT_NAME_COLLISION
                try:
                    smbclient.mkdir(candidate)
                    logger.info(f"Создана папка на SMB: {candidate}")
                except FileExistsError:
                    pass


def _unique_subpaths() -> list[str]:
    seen = set()
    res = []
    for sp in DEFAULT_SUBPATHS:
        norm = sp.strip("\\/").lower()
        if norm and norm not in seen:
            seen.add(norm)
            res.append(sp.strip("\\/"))
    return res


def scan_pc_lotus_ids(pc_host: str) -> dict:
    """
    Сканирует C$\\Users\\*\\AppData\\Local\\IBM\\Notes\\Data (и HCL/Lotus fallback)
    на удаленном ПК и возвращает найденные *.id файлы.
    """
    users_root = f"\\\\{pc_host}\\C$\\Users"
    try:
        _ensure_session(pc_host, config.PC_ADMIN_USER, config.PC_ADMIN_PASSWORD, timeout=5)
        entries = list(smbclient.scandir(users_root))
    except Exception as e:
        code, human = _classify_smb_error(e)
        logger.warning(f"Ошибка сканирования Lotus ID на {pc_host}: {code} -> {e}")
        return {"ok": False, "error_code": code, "error": human, "files": []}

    subpaths = _unique_subpaths()
    found_files: list[dict] = []

    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except Exception:
            continue

        profile_name = entry.name
        if profile_name.lower() in SYSTEM_USER_DIRS or profile_name.startswith("."):
            continue

        for rel_subpath in subpaths:
            notes_dir = f"{users_root}\\{profile_name}\\{rel_subpath}"
            try:
                if not smbclient.path.isdir(notes_dir):
                    continue
                dir_items = list(smbclient.scandir(notes_dir))
            except Exception:
                continue

            for item in dir_items:
                try:
                    if item.is_file() and item.name.lower().endswith(".id"):
                        stat = item.stat()
                        dt_local = datetime.datetime.fromtimestamp(stat.st_mtime, tz=TZ_LOCAL)
                        found_files.append({
                            "win_profile": profile_name,
                            "filename": item.name,
                            "unc_dir": notes_dir,
                            "full_unc_path": f"{notes_dir}\\{item.name}",
                            "size_bytes": int(stat.st_size),
                            "size_kb": round(stat.st_size / 1024.0, 1),
                            "modified_str": dt_local.strftime("%d.%m.%Y %H:%M"),
                            "modified_ts": float(stat.st_mtime),
                            "rel_subpath": rel_subpath,
                        })
                except Exception:
                    continue

            # Если нашли файлы в основном пути (IBM\Notes\Data), не дублируем fallback-поиск для этого профиля
            if any(f["win_profile"] == profile_name for f in found_files):
                break

    found_files.sort(key=lambda x: x["modified_ts"], reverse=True)
    return {"ok": True, "error_code": None, "error": None, "files": found_files}


def check_storage_collision(target_filename: str) -> dict:
    """
    Проверяет доступность \\\\storage\\public\\id (создаёт папку если её нет)
    и проверяет, существует ли уже файл с таким именем в хранилище.
    """
    storage_dir = config.LOTUS_STORAGE_UNC.rstrip("\\")
    try:
        _ensure_smb_dir(storage_dir, config.LOTUS_STORAGE_USER, config.LOTUS_STORAGE_PASSWORD)
        target_path = f"{storage_dir}\\{target_filename}"
        if smbclient.path.isfile(target_path):
            st = smbclient.stat(target_path)
            dt_local = datetime.datetime.fromtimestamp(st.st_mtime, tz=TZ_LOCAL)
            return {
                "ok": True,
                "exists": True,
                "size_kb": round(st.st_size / 1024.0, 1),
                "modified_str": dt_local.strftime("%d.%m.%Y %H:%M"),
                "storage_path": target_path,
            }
        return {"ok": True, "exists": False, "storage_path": target_path}
    except Exception as e:
        code, human = _classify_smb_error(e)
        logger.error(f"Ошибка проверки хранилища Lotus {storage_dir}: {e}")
        return {"ok": False, "exists": False, "error_code": code, "error": human}


def extract_id_to_storage(
    pc_host: str,
    pc_name: str,
    win_profile: str,
    unc_dir: str,
    original_filename: str,
    storage_filename: str | None = None,
    ad_username: str | None = None,
    admin_id: int | None = None,
) -> dict:
    """
    Копирует *.id с ПК сотрудника в \\\\storage\\public\\id (создавая папку при необходимости)
    и запоминает оригинальное имя файла и путь в SQLite.
    """
    target_name = (storage_filename or original_filename).strip()
    storage_dir = config.LOTUS_STORAGE_UNC.rstrip("\\")
    src_path = f"{unc_dir}\\{original_filename}"
    dst_path = f"{storage_dir}\\{target_name}"

    try:
        _ensure_session(pc_host, config.PC_ADMIN_USER, config.PC_ADMIN_PASSWORD, timeout=5)
        with smbclient.open_file(src_path, mode="rb") as f_src:
            payload = f_src.read()
    except Exception as e:
        code, human = _classify_smb_error(e)
        return {"ok": False, "error_code": code, "error": human}

    if not payload:
        return {"ok": False, "error_code": "empty_file", "error": f"Файл {original_filename} пустой (0 байт)."}

    try:
        _ensure_smb_dir(storage_dir, config.LOTUS_STORAGE_USER, config.LOTUS_STORAGE_PASSWORD)
        with smbclient.open_file(dst_path, mode="wb") as f_dst:
            f_dst.write(payload)
    except Exception as e:
        code, human = _classify_smb_error(e)
        return {"ok": False, "error_code": code, "error": human}

    transfer_id = save_lotus_transfer(
        pc_name=pc_name,
        dns_name=pc_host,
        win_profile=win_profile,
        original_filename=original_filename,
        original_unc_dir=unc_dir,
        storage_filename=target_name,
        ad_username=ad_username,
        admin_id=admin_id,
    )

    logger.info(
        f"Lotus ID скопирован: {src_path} -> {dst_path} ({len(payload)} bytes), transfer_id={transfer_id}"
    )
    return {
        "ok": True,
        "transfer_id": transfer_id,
        "src_path": src_path,
        "dst_path": dst_path,
        "storage_path": dst_path,
        "original_filename": original_filename,
        "storage_filename": target_name,
        "bytes_copied": len(payload),
        "size_kb": round(len(payload) / 1024.0, 1),
    }


def restore_id_from_storage(transfer_id: int, cleanup_storage: bool = False) -> dict:
    """
    Возвращает *.id из \\\\storage\\public\\id обратно на компьютер строго в исходный каталог
    и под запомненным оригинальным именем файла.
    """
    rec = get_lotus_transfer_by_id(transfer_id)
    if not rec:
        return {"ok": False, "error_code": "not_found", "error": "Запись о переносе не найдена в базе данных."}

    storage_dir = config.LOTUS_STORAGE_UNC.rstrip("\\")
    src_storage = f"{storage_dir}\\{rec['storage_filename']}"
    dst_pc_dir = rec["original_unc_dir"].rstrip("\\")
    dst_pc_file = f"{dst_pc_dir}\\{rec['original_filename']}"
    pc_host = rec.get("dns_name") or rec["pc_name"]

    # 1. Читаем обновленный ID из хранилища
    try:
        storage_server, _, _ = _parse_unc(storage_dir)
        _ensure_session(storage_server, config.LOTUS_STORAGE_USER, config.LOTUS_STORAGE_PASSWORD, timeout=5)
        if not smbclient.path.isfile(src_storage):
            return {
                "ok": False,
                "error_code": "storage_missing",
                "error": f"Файл <code>{escape(src_storage)}</code> не найден в сетевом хранилище.",
            }
        with smbclient.open_file(src_storage, mode="rb") as f_src:
            payload = f_src.read()
    except Exception as e:
        code, human = _classify_smb_error(e)
        return {"ok": False, "error_code": code, "error": human}

    if not payload:
        return {"ok": False, "error_code": "empty_file", "error": "Файл в хранилище поврежден или пуст (0 байт)."}

    # 2. Записываем обратно на ПК пользователя под оригинальным именем
    try:
        _ensure_session(pc_host, config.PC_ADMIN_USER, config.PC_ADMIN_PASSWORD, timeout=5)
        _ensure_smb_dir(dst_pc_dir, config.PC_ADMIN_USER, config.PC_ADMIN_PASSWORD)
        with smbclient.open_file(dst_pc_file, mode="wb") as f_dst:
            f_dst.write(payload)
    except Exception as e:
        code, human = _classify_smb_error(e)
        return {"ok": False, "error_code": code, "error": human}

    # 3. Опционально удаляем копию из \\storage\public\id
    if cleanup_storage:
        try:
            storage_server, _, _ = _parse_unc(storage_dir)
            _ensure_session(storage_server, config.LOTUS_STORAGE_USER, config.LOTUS_STORAGE_PASSWORD, timeout=5)
            smbclient.remove(src_storage)
        except Exception as clean_err:
            logger.warning(f"ID возвращен, но не удалось удалить копию в storage: {clean_err}")

    mark_lotus_returned(transfer_id)
    logger.info(f"Lotus ID возвращен: {src_storage} -> {dst_pc_file} (transfer_id={transfer_id})")
    return {
        "ok": True,
        "transfer": rec,
        "src_path": src_storage,
        "dst_path": dst_pc_file,
        "target_path": dst_pc_file,
        "bytes_copied": len(payload),
        "size_kb": round(len(payload) / 1024.0, 1),
        "cleaned_storage": cleanup_storage,
    }


def delete_storage_copy(transfer_id: int) -> dict:
    """Удаляет временную копию ID-файла из \\\\storage\\public\\id."""
    rec = get_lotus_transfer_by_id(transfer_id)
    if not rec:
        return {"ok": False, "error": "Запись о переносе не найдена."}

    storage_dir = config.LOTUS_STORAGE_UNC.rstrip("\\")
    target_path = f"{storage_dir}\\{rec['storage_filename']}"
    try:
        storage_server, _, _ = _parse_unc(storage_dir)
        _ensure_session(storage_server, config.LOTUS_STORAGE_USER, config.LOTUS_STORAGE_PASSWORD, timeout=5)
        if smbclient.path.isfile(target_path):
            smbclient.remove(target_path)
            return {"ok": True, "deleted": True, "path": target_path}
        return {"ok": True, "deleted": False, "path": target_path}
    except Exception as e:
        _, human = _classify_smb_error(e)
        return {"ok": False, "error": human}
