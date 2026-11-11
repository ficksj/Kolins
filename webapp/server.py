import asyncio
import os
import sys

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiohttp import web
from services.ad_service import ad_service
from services.db_service import get_user_pc_session, save_original_ou, get_original_ou, remove_original_ou
from services.pc_monitor_service import check_pc_online, get_pc_logged_user, find_pc_by_username_scan
from services.zoom_service import zoom_service
from services.logger_service import get_logger

logger = get_logger("WebAppServer")

TARGET_PC_OU = "OU=KDB notebooklenovo,OU=KDB Nout,DC=kdb,DC=kz"
CERT_USER_OU = "OU=notebooklenovo,OU=Bank Divisions,OU=KDB Users,DC=kdb,DC=kz"

async def handle_index(request):
    """Отдача главного HTML-интерфейса Mini App"""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html", charset="utf-8")
    return web.Response(text="<h1>Mini App Template Not Found</h1>", content_type="text/html")

async def api_stats(request):
    """Сводка по Active Directory"""
    stats = await asyncio.to_thread(ad_service.get_domain_stats)
    return web.json_response({"ok": bool(stats), "stats": stats})

async def api_search_users(request):
    """Поиск пользователей с подробными данными"""
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"ok": True, "items": []})
    
    users = await asyncio.to_thread(ad_service.search_users, query)
    detailed_users = []
    # Загружаем подробности первых 8 совпадений
    for u in users[:8]:
        details = await asyncio.to_thread(ad_service.get_user_details, u["username"])
        if details:
            detailed_users.append(details)
        else:
            detailed_users.append(u)
            
    return web.json_response({"ok": True, "items": detailed_users})

async def api_search_pcs(request):
    """Поиск компьютеров с проверкой online/offline и текущего пользователя"""
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"ok": True, "items": []})
    
    pcs = await asyncio.to_thread(ad_service.search_computers, query)
    detailed_pcs = []
    for pc in pcs[:8]:
        details = await asyncio.to_thread(ad_service.get_computer_details, pc["Name"])
        if details:
            dns_name = details.get("dns_name", pc["Name"])
            if dns_name != "—":
                is_online, logged_user = await asyncio.gather(
                    asyncio.to_thread(check_pc_online, dns_name),
                    asyncio.to_thread(get_pc_logged_user, pc["Name"], dns_name),
                )
            else:
                is_online, logged_user = False, None
            details["is_online"] = is_online
            details["logged_user"] = logged_user
            detailed_pcs.append(details)
        else:
            detailed_pcs.append(pc)

    return web.json_response({"ok": True, "items": detailed_pcs})

async def api_pc_details(request):
    """Детальная информация по конкретному компьютеру"""
    pc_name = request.match_info.get("name", "").strip()
    details = await asyncio.to_thread(ad_service.get_computer_details, pc_name)
    if not details:
        return web.json_response({"ok": False, "error": "Not found"})

    dns_name = details.get("dns_name", pc_name)
    is_online, logged_user = await asyncio.gather(
        asyncio.to_thread(check_pc_online, dns_name),
        asyncio.to_thread(get_pc_logged_user, pc_name, dns_name),
    )
    details["is_online"] = is_online
    details["logged_user"] = logged_user
    return web.json_response({"ok": True, "pc": details})

async def api_user_pc(request):
    """Определение ПК пользователя"""
    username = request.match_info.get("username", "").strip()
    if not username:
        return web.json_response({"ok": False, "error": "No username"})

    # 1. Проверяем кэш БД
    session = get_user_pc_session(username)
    if session:
        cached_pc = session["pc_name"]
        cached_dns = session.get("dns_name") or cached_pc
        is_online = await asyncio.to_thread(check_pc_online, cached_dns)
        if is_online:
            active_user = await asyncio.to_thread(get_pc_logged_user, cached_pc, cached_dns)
            if active_user and active_user.lower() == username.lower():
                return web.json_response({"ok": True, "pc_name": cached_pc, "dns_name": cached_dns})

    # 2. Сканируем активные машины домена
    target_computers = await asyncio.to_thread(ad_service.get_target_computers)
    found_pc = await find_pc_by_username_scan(username, target_computers)
    if found_pc:
        return web.json_response({"ok": True, "pc_name": found_pc, "dns_name": f"{found_pc}.{ad_service.domain}"})

    return web.json_response({"ok": False, "message": "PC not found"})

async def api_reset_password(request):
    """Сброс пароля"""
    username = request.match_info.get("username", "").strip()
    fixed_password = "ASDfg123+"
    success = await asyncio.to_thread(ad_service.reset_password, username, fixed_password)
    return web.json_response({"ok": success, "password": fixed_password})

async def api_unlock_user(request):
    """Разблокировка пользователя"""
    username = request.match_info.get("username", "").strip()
    success = await asyncio.to_thread(ad_service.unlock_user, username)
    return web.json_response({"ok": success})

async def api_move_user_cert(request):
    """Перемещение пользователя для выдачи сертификата"""
    username = request.match_info.get("username", "").strip()
    details = await asyncio.to_thread(ad_service.get_user_details, username)
    if not details:
        return web.json_response({"ok": False, "error": "User not found"})
    
    original_ou = ",".join(details["dn"].split(",")[1:])
    success = await asyncio.to_thread(ad_service.move_user, details["dn"], CERT_USER_OU)
    if success:
        save_original_ou(username, original_ou)
    return web.json_response({"ok": success, "target_ou": CERT_USER_OU})

async def api_revert_user(request):
    """Возврат пользователя в исходный OU"""
    username = request.match_info.get("username", "").strip()
    original_ou = get_original_ou(username)
    if not original_ou:
        return web.json_response({"ok": False, "error": "No original OU found"})
    
    details = await asyncio.to_thread(ad_service.get_user_details, username)
    if not details:
        return web.json_response({"ok": False, "error": "User not found"})

    success = await asyncio.to_thread(ad_service.move_user, details["dn"], original_ou)
    if success:
        remove_original_ou(username)
    return web.json_response({"ok": success, "original_ou": original_ou})

async def api_move_pc(request):
    """Перемещение ПК в целевое OU ноутбуков"""
    pc_name = request.match_info.get("name", "").strip()
    success = await asyncio.to_thread(ad_service.move_computer, pc_name, TARGET_PC_OU)
    return web.json_response({"ok": success, "target_ou": TARGET_PC_OU})

async def api_delete_pc(request):
    """Удаление ПК из домена"""
    pc_name = request.match_info.get("name", "").strip()
    success = await asyncio.to_thread(ad_service.delete_computer, pc_name)
    return web.json_response({"ok": success})

async def api_create_zoom(request):
    """Создание Zoom встречи"""
    data = await request.json() if request.can_read_body else {}
    topic = data.get("topic", "Техподдержка AD")
    meeting = await zoom_service.create_meeting(topic=topic)
    if meeting:
        return web.json_response({"ok": True, "meeting": meeting})
    return web.json_response({"ok": False, "error": "Failed to create meeting"})

def create_webapp() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/search/users", api_search_users)
    app.router.add_get("/api/search/pcs", api_search_pcs)
    app.router.add_get("/api/pc/{name}", api_pc_details)
    app.router.add_get("/api/user/{username}/pc", api_user_pc)
    app.router.add_post("/api/user/{username}/reset_password", api_reset_password)
    app.router.add_post("/api/user/{username}/unlock", api_unlock_user)
    app.router.add_post("/api/user/{username}/cert", api_move_user_cert)
    app.router.add_post("/api/user/{username}/revert", api_revert_user)
    app.router.add_post("/api/pc/{name}/move", api_move_pc)
    app.router.add_post("/api/pc/{name}/delete", api_delete_pc)
    app.router.add_post("/api/zoom", api_create_zoom)
    return app

if __name__ == "__main__":
    app = create_webapp()
    web.run_app(app, host="127.0.0.1", port=5000)
