from contextlib import contextmanager
import os
import sqlite3
from services.logger_service import get_logger

logger = get_logger("DBService")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database.db")


@contextmanager
def _get_conn(db_path: str | None = None):
    """Открывает SQLite-соединение и гарантированно закрывает его при выходе из контекста."""
    conn = sqlite3.connect(db_path or DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | None = None):
    """Инициализация локальной базы данных SQLite (WAL-режим и все рабочие таблицы)."""
    try:
        with _get_conn(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")

            # Таблица соответствия пользователей и компьютеров
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_pc_sessions (
                    username TEXT PRIMARY KEY,
                    pc_name TEXT NOT NULL,
                    dns_name TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_pc_sessions_pc_name
                ON user_pc_sessions (pc_name)
            """)

            # Таблица сохранения исходного OU пользователя перед перемещением
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_original_ou (
                    username TEXT PRIMARY KEY,
                    original_ou TEXT NOT NULL,
                    moved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица отслеживания отправленных алертов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sent_alerts (
                    alert_key TEXT PRIMARY KEY,
                    alert_type TEXT NOT NULL,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Настройки бота (тумблеры алертов и параметров)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Журнал переносов *.id файлов IBM Lotus Notes
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lotus_id_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pc_name TEXT NOT NULL,
                    dns_name TEXT,
                    win_profile TEXT NOT NULL,
                    ad_username TEXT,
                    original_filename TEXT NOT NULL,
                    original_unc_dir TEXT NOT NULL,
                    storage_filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'extracted',
                    admin_id INTEGER,
                    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    returned_at TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_lotus_pc_status
                ON lotus_id_transfers (pc_name, status)
            """)

            conn.commit()
        logger.info("База данных успешно инициализирована (WAL, сессии, OU, настройки, Lotus ID)")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")


# --- Сессии Пользователь <-> ПК ---

def save_user_pc_session(username: str, pc_name: str, dns_name: str = ""):
    """Сохранение или обновление последней активной рабочей станции пользователя."""
    if not username or not pc_name:
        return
    username_clean = username.strip().lower()
    pc_clean = pc_name.strip().upper()
    try:
        with _get_conn() as conn:
            conn.execute("""
                INSERT INTO user_pc_sessions (username, pc_name, dns_name, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(username) DO UPDATE SET
                    pc_name = excluded.pc_name,
                    dns_name = excluded.dns_name,
                    updated_at = CURRENT_TIMESTAMP
            """, (username_clean, pc_clean, dns_name))
            conn.commit()
        logger.debug(f"Сессия сохранена: {username_clean} -> {pc_clean}")
    except Exception as e:
        logger.error(f"Ошибка сохранения сессии в БД: {e}")


def get_user_pc_session(username: str) -> dict | None:
    """Получение сохраненного ПК пользователя из БД."""
    if not username:
        return None
    username_clean = username.strip().lower()
    try:
        with _get_conn() as conn:
            row = conn.execute("""
                SELECT pc_name, dns_name, updated_at
                FROM user_pc_sessions
                WHERE username = ?
            """, (username_clean,)).fetchone()
        if row:
            return {
                "pc_name": row["pc_name"],
                "dns_name": row["dns_name"],
                "updated_at": row["updated_at"],
            }
        return None
    except Exception as e:
        logger.error(f"Ошибка получения сессии из БД: {e}")
        return None


def get_pc_user_session(pc_name: str) -> dict | None:
    """Получение сохраненного пользователя ПК из БД (обратный поиск по имени ПК)."""
    if not pc_name:
        return None
    pc_clean = pc_name.strip()
    try:
        with _get_conn() as conn:
            row = conn.execute("""
                SELECT username, dns_name, updated_at
                FROM user_pc_sessions
                WHERE UPPER(pc_name) = UPPER(?) OR UPPER(dns_name) LIKE UPPER(?)
                ORDER BY updated_at DESC
                LIMIT 1
            """, (pc_clean, f"{pc_clean}.%")).fetchone()
        if row:
            return {
                "username": row["username"],
                "dns_name": row["dns_name"],
                "updated_at": row["updated_at"],
            }
        return None
    except Exception as e:
        logger.error(f"Ошибка получения сессии по ПК из БД: {e}")
        return None


def count_user_pc_sessions() -> int:
    """Количество известных связок пользователь <-> ПК в БД."""
    try:
        with _get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM user_pc_sessions").fetchone()
        return int(row["cnt"]) if row else 0
    except Exception as e:
        logger.error(f"Ошибка подсчета сессий: {e}")
        return 0


# --- Хранение исходного OU для сертификации ---

def save_original_ou(username: str, original_ou: str):
    """Сохранение исходного OU пользователя."""
    username_clean = username.strip().lower()
    try:
        with _get_conn() as conn:
            conn.execute("""
                INSERT INTO user_original_ou (username, original_ou, moved_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(username) DO UPDATE SET
                    original_ou = excluded.original_ou,
                    moved_at = CURRENT_TIMESTAMP
            """, (username_clean, original_ou))
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка сохранения исходного OU: {e}")


def get_original_ou(username: str) -> str | None:
    """Получение сохраненного исходного OU."""
    username_clean = username.strip().lower()
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT original_ou FROM user_original_ou WHERE username = ?",
                (username_clean,),
            ).fetchone()
        return row["original_ou"] if row else None
    except Exception as e:
        logger.error(f"Ошибка получения исходного OU: {e}")
        return None


def remove_original_ou(username: str):
    """Удаление записи исходного OU после возврата."""
    username_clean = username.strip().lower()
    try:
        with _get_conn() as conn:
            conn.execute("DELETE FROM user_original_ou WHERE username = ?", (username_clean,))
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка удаления исходного OU: {e}")


def list_users_in_cert_ou() -> list[dict]:
    """Список пользователей, перемещенных для выпуска сертификата и ожидающих возврата."""
    try:
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT username, original_ou, moved_at
                FROM user_original_ou
                ORDER BY moved_at DESC
            """).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Ошибка чтения списка cert OU: {e}")
        return []


# --- Алерты и настройки ---

def is_alert_sent(alert_key: str, hours: int = 12) -> bool:
    """Проверяет, отправлялся ли алерт за последние N часов."""
    try:
        with _get_conn() as conn:
            row = conn.execute("""
                SELECT 1 FROM sent_alerts
                WHERE alert_key = ? AND datetime(sent_at) > datetime('now', ?)
            """, (alert_key, f"-{int(hours)} hours")).fetchone()
        return row is not None
    except Exception as e:
        logger.error(f"Ошибка проверки алерта: {e}")
        return False


def record_alert_sent(alert_key: str, alert_type: str = "lockout"):
    """Фиксирует факт отправки алерта."""
    try:
        with _get_conn() as conn:
            conn.execute("""
                INSERT INTO sent_alerts (alert_key, alert_type, sent_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(alert_key) DO UPDATE SET
                    sent_at = CURRENT_TIMESTAMP
            """, (alert_key, alert_type))
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка записи отправленного алерта: {e}")


def get_setting(key: str, default: str | None = None) -> str | None:
    """Чтение настройки бота из БД."""
    try:
        with _get_conn() as conn:
            row = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    except Exception as e:
        logger.error(f"Ошибка чтения настройки {key}: {e}")
        return default


def set_setting(key: str, value: str):
    """Сохранение настройки бота в БД."""
    try:
        with _get_conn() as conn:
            conn.execute("""
                INSERT INTO bot_settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
            """, (key, str(value)))
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка сохранения настройки {key}: {e}")


# --- IBM Lotus Notes ID Transfers ---

def save_lotus_transfer(
    pc_name: str,
    dns_name: str,
    win_profile: str,
    original_filename: str,
    original_unc_dir: str,
    storage_filename: str,
    ad_username: str | None = None,
    admin_id: int | None = None,
) -> int | None:
    """Сохраняет запись об извлеченном Lotus ID файле (или обновляет активную запись для того же профиля/файла)."""
    pc_clean = pc_name.strip().upper()
    try:
        with _get_conn() as conn:
            # Помечаем старые незавершенные переносы для того же (pc_name, win_profile, original_filename) как замененные
            conn.execute("""
                UPDATE lotus_id_transfers
                SET status = 'superseded'
                WHERE UPPER(pc_name) = ? AND LOWER(win_profile) = LOWER(?)
                  AND LOWER(original_filename) = LOWER(?) AND status = 'extracted'
            """, (pc_clean, win_profile, original_filename))

            cursor = conn.execute("""
                INSERT INTO lotus_id_transfers (
                    pc_name, dns_name, win_profile, ad_username,
                    original_filename, original_unc_dir, storage_filename,
                    status, admin_id, extracted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'extracted', ?, CURRENT_TIMESTAMP)
            """, (
                pc_clean, dns_name, win_profile, ad_username,
                original_filename, original_unc_dir, storage_filename, admin_id
            ))
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        logger.error(f"Ошибка сохранения переноса Lotus ID: {e}")
        return None


def get_lotus_transfer_by_id(transfer_id: int) -> dict | None:
    """Возвращает запись о переносе Lotus ID по его ID."""
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM lotus_id_transfers WHERE id = ?",
                (transfer_id,),
            ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Ошибка чтения переноса Lotus ID #{transfer_id}: {e}")
        return None


def get_active_lotus_transfers_by_pc(pc_name: str) -> list[dict]:
    """Возвращает все активные (невозвращенные) Lotus ID для указанного ПК."""
    pc_clean = pc_name.strip().upper()
    try:
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM lotus_id_transfers
                WHERE UPPER(pc_name) = ? AND status = 'extracted'
                ORDER BY extracted_at DESC
            """, (pc_clean,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Ошибка поиска Lotus ID для ПК {pc_name}: {e}")
        return []


def get_latest_lotus_transfer_by_pc(pc_name: str) -> dict | None:
    """Последняя запись (активная или уже возвращенная) для ПК — чтобы помнить имя файла."""
    pc_clean = pc_name.strip().upper()
    try:
        with _get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM lotus_id_transfers
                WHERE UPPER(pc_name) = ? AND status IN ('extracted', 'returned')
                ORDER BY extracted_at DESC
                LIMIT 1
            """, (pc_clean,)).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Ошибка получения истории Lotus ID для {pc_name}: {e}")
        return None


def list_pending_lotus_transfers() -> list[dict]:
    """Список всех Lotus ID файлов, ожидающих возврата на ПК."""
    try:
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM lotus_id_transfers
                WHERE status = 'extracted'
                ORDER BY extracted_at DESC
            """).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Ошибка получения списка ожидающих Lotus ID: {e}")
        return []


def mark_lotus_returned(transfer_id: int):
    """Отмечает Lotus ID как возвращенный на компьютер."""
    try:
        with _get_conn() as conn:
            conn.execute("""
                UPDATE lotus_id_transfers
                SET status = 'returned', returned_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (transfer_id,))
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка обновления статуса Lotus ID #{transfer_id}: {e}")


# Инициализируем БД при импорте
init_db()
