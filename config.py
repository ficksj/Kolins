import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
AD_SERVER = os.getenv("AD_SERVER")
AD_DOMAIN = os.getenv("AD_DOMAIN")
AD_USER = os.getenv("AD_USER")
AD_PASSWORD = os.getenv("AD_PASSWORD")
AD_SEARCH_BASE = os.getenv("AD_SEARCH_BASE", "DC=kdb,DC=kz")

LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID")) if os.getenv("LOG_CHAT_ID") else None
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]