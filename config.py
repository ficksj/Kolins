import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
AD_SERVER = os.getenv("AD_SERVER")
AD_DOMAIN = os.getenv("AD_DOMAIN")
AD_USER = os.getenv("AD_USER")
AD_PASSWORD = os.getenv("AD_PASSWORD")
AD_SEARCH_BASE = os.getenv("AD_SEARCH_BASE", "DC=kdb,DC=kz")

# Учетные данные администратора рабочих станций (RemoteAdmins / KdbPCAdm) для WinRM и SMB C$
# Если не заданы в .env — используется fallback на AD_USER / AD_PASSWORD
PC_ADMIN_USER = os.getenv("PC_ADMIN_USER") or AD_USER
PC_ADMIN_PASSWORD = os.getenv("PC_ADMIN_PASSWORD") or AD_PASSWORD

# Настройки IBM Lotus Notes ID (извлечение и возврат *.id)
LOTUS_STORAGE_UNC = os.getenv("LOTUS_STORAGE_UNC", r"\\storage\public\id")
LOTUS_NOTES_SUBPATH = os.getenv("LOTUS_NOTES_SUBPATH", r"AppData\Local\IBM\Notes\Data")
LOTUS_STORAGE_USER = os.getenv("LOTUS_STORAGE_USER") or AD_USER
LOTUS_STORAGE_PASSWORD = os.getenv("LOTUS_STORAGE_PASSWORD") or AD_PASSWORD

# Целевые контейнеры (OU) Active Directory
_default_pc_ous = [
    "OU=KDB Computers,DC=kdb,DC=kz",
    "OU=KDB Nout,DC=kdb,DC=kz",
    "CN=Servers,DC=kdb,DC=kz",
]
_raw_target_ous = os.getenv("TARGET_PC_OUS", "").strip()
TARGET_PC_OUS = (
    [ou.strip() for ou in _raw_target_ous.split(";") if ou.strip()]
    if _raw_target_ous
    else _default_pc_ous
)
TARGET_PC_MOVE_OU = os.getenv("TARGET_PC_MOVE_OU", "OU=KDB notebooklenovo,OU=KDB Nout,DC=kdb,DC=kz")
CERT_USER_OU = os.getenv("CERT_USER_OU", "OU=notebooklenovo,OU=Bank Divisions,OU=KDB Users,DC=kdb,DC=kz")

# Критические группы AD, защищенные от изменения через бота
PROTECTED_AD_GROUPS = {
    "domain admins",
    "enterprise admins",
    "schema admins",
    "administrators",
    "account operators",
    "backup operators",
}

# Zoom Server-to-Server OAuth (опционально)
ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID", "")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID", "")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET", "")

LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID")) if os.getenv("LOG_CHAT_ID") else None
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
