import sqlite3
import os
from services.logger_service import get_logger

logger = get_logger("DBService")

DB_PATH = "database.db"

def init_db():
    """Инициализация локальной базы данных SQLite"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Таблица соответствия пользователей и компьютеров
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_pc_sessions (
                username TEXT PRIMARY KEY,
                pc_name TEXT NOT NULL,
                dns_name TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Индекс по имени компьютера для обратного поиска
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pc_sessions_pc_name ON user_pc_sessions (pc_name)
        """)

        # Таблица сохранения исходного OU пользователя перед перемещением
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_original_ou (
                username TEXT PRIMARY KEY,
                original_ou TEXT NOT NULL,
                moved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Таблица отслеживания отправленных алертов (чтобы не спамить)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sent_alerts (
                alert_key TEXT PRIMARY KEY,
                alert_type TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()
        logger.info("База данных успешно инициализирована (таблицы user_pc_sessions, user_original_ou, sent_alerts)")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")

def save_user_pc_session(username: str, pc_name: str, dns_name: str = ""):
    """Сохранение/обновление привязки пользователя к ПК"""
    if not username or not pc_name:
        return
    username_clean = username.strip().lower()
    pc_clean = pc_name.strip()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_pc_sessions (username, pc_name, dns_name, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(username) DO UPDATE SET
                pc_name = excluded.pc_name,
                dns_name = excluded.dns_name,
                updated_at = CURRENT_TIMESTAMP
        """, (username_clean, pc_clean, dns_name))
        conn.commit()
        conn.close()
        logger.debug(f"Сессия сохранена: {username_clean} -> {pc_clean}")
    except Exception as e:
        logger.error(f"Ошибка сохранения сессии в БД: {e}")

def get_user_pc_session(username: str) -> dict | None:
    """Получение сохраненного ПК пользователя из БД"""
    if not username:
        return None
    username_clean = username.strip().lower()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pc_name, dns_name, updated_at
            FROM user_pc_sessions
            WHERE username = ?
        """, (username_clean,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "pc_name": row[0],
                "dns_name": row[1],
                "updated_at": row[2]
            }
        return None
    except Exception as e:
        logger.error(f"Ошибка получения сессии из БД: {e}")
        return None

def save_original_ou(username: str, original_ou: str):
    """Сохранение исходного OU пользователя"""
    username_clean = username.strip().lower()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_original_ou (username, original_ou, moved_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(username) DO UPDATE SET
                original_ou = excluded.original_ou,
                moved_at = CURRENT_TIMESTAMP
        """, (username_clean, original_ou))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка сохранения исходного OU: {e}")

def get_original_ou(username: str) -> str | None:
    """Получение сохраненного исходного OU"""
    username_clean = username.strip().lower()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT original_ou FROM user_original_ou WHERE username = ?", (username_clean,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Ошибка получения исходного OU: {e}")
        return None

def remove_original_ou(username: str):
    """Удаление записи исходного OU после возврата"""
    username_clean = username.strip().lower()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_original_ou WHERE username = ?", (username_clean,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка удаления исходного OU: {e}")

def is_alert_sent(alert_key: str) -> bool:
    """Проверяет, отправлялся ли алерт за последние 12 часов"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM sent_alerts 
            WHERE alert_key = ? AND datetime(sent_at) > datetime('now', '-12 hours')
        """, (alert_key,))
        row = cursor.fetchone()
        conn.close()
        return row is not None
    except Exception as e:
        logger.error(f"Ошибка проверки алерта: {e}")
        return False

def record_alert_sent(alert_key: str, alert_type: str = "lockout"):
    """Фиксирует факт отправки алерта"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sent_alerts (alert_key, alert_type, sent_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(alert_key) DO UPDATE SET
                sent_at = CURRENT_TIMESTAMP
        """, (alert_key, alert_type))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка записи отправленного алерта: {e}")

# Инициализируем БД при импорте
init_db()
