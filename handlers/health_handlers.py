import asyncio
from aiogram import Router, F
from aiogram.types import CallbackQuery

from services.ad_service import ad_service
from services.db_service import (
    get_setting,
    set_setting,
    list_users_in_cert_ou,
    remove_original_ou,
    list_pending_lotus_transfers,
)
from services.ad_monitor_worker import reset_lockout_baseline
from services.audit_service import send_audit_log
from services.rich_message_service import (
    render_bubble_card,
    button_row,
    callback_button,
    html_text,
    edit_rich,
)

router = Router()

ITEMS_PER_PAGE = 7


def _parse_page(data: str, default: int = 0) -> int:
    parts = data.split(":")
    if len(parts) >= 3 and parts[2].isdigit():
        return int(parts[2])
    return default


async def _render_health_dashboard(callback: CallbackQuery):
    loading_card = render_bubble_card(
        title="🛡 Здоровье Active Directory",
        badge="Анализ",
        body_lines=["⏳ Сбор актуальных метрик безопасности и чистоты AD..."],
    )
    await edit_rich(callback.message, loading_card)

    locked, expiring, cert_users, stale_pcs, pending_lotus = await asyncio.gather(
        asyncio.to_thread(ad_service.get_locked_users),
        asyncio.to_thread(ad_service.get_expiring_passwords, 7),
        asyncio.to_thread(list_users_in_cert_ou),
        asyncio.to_thread(ad_service.get_stale_computers, 90),
        asyncio.to_thread(list_pending_lotus_transfers),
    )

    alerts_on = get_setting("lockout_alerts_enabled", "0") == "1"
    alert_label = "Включены ✅" if alerts_on else "Выключены 🔕"
    toggle_btn_text = "🔕 Выключить алерты блокировок" if alerts_on else "🔔 Включить алерты блокировок"
    toggle_style = "danger" if alerts_on else "success"

    card = render_bubble_card(
        title="🛡 Здоровье Active Directory",
        badge="Мониторинг и гигиена",
        body_lines=[
            f"🔒 Активные блокировки УЗ: <b>{len(locked)}</b>",
            f"⏳ Пароль истекает (≤7 дн.): <b>{len(expiring)}</b>",
            f"📜 Временный выпуск сертификата (OU): <b>{len(cert_users)}</b>",
            f"💤 Неактивные ПК (&gt;90 дн.): <b>{len(stale_pcs)}</b>",
            f"🪪 Lotus ID ожидают возврата: <b>{len(pending_lotus)}</b>",
            f"🔔 Алерты о новых блокировках: <b>{alert_label}</b>",
        ],
        footer_note="Выберите категорию для детального разбора или быстрого действия:",
        buttons=[
            button_row(
                callback_button(f"🔒 Блокировки ({len(locked)})", "health:locked:0", "danger" if locked else "secondary"),
                callback_button(f"⏳ Пароли ({len(expiring)})", "health:expiring:0", "primary" if expiring else "secondary"),
            ),
            button_row(
                callback_button(f"📜 Сертификат OU ({len(cert_users)})", "health:cert_ou", "primary" if cert_users else "secondary"),
                callback_button(f"💤 Старые ПК ({len(stale_pcs)})", "health:stale:0", "primary" if stale_pcs else "secondary"),
            ),
            button_row(
                callback_button(f"🪪 Lotus ID на хранении ({len(pending_lotus)})", "lotus:list", "primary" if pending_lotus else "secondary"),
            ),
            button_row(
                callback_button(toggle_btn_text, "health:toggle_alerts", toggle_style),
            ),
            button_row(
                callback_button("🔄 Обновить", "health:main", "secondary"),
                callback_button("⬅️ Главная", "home:main", "secondary"),
            ),
        ],
    )
    await edit_rich(callback.message, card)


@router.callback_query(F.data == "health:main")
async def show_health_dashboard(callback: CallbackQuery):
    await callback.answer()
    await _render_health_dashboard(callback)


@router.callback_query(F.data == "health:toggle_alerts")
async def toggle_lockout_alerts(callback: CallbackQuery):
    current = get_setting("lockout_alerts_enabled", "0") == "1"
    new_val = "0" if current else "1"
    set_setting("lockout_alerts_enabled", new_val)
    reset_lockout_baseline()

    status_text = "Включены (только новые события)" if new_val == "1" else "Выключены"
    await send_audit_log(callback.bot, callback.from_user, f"Алерты блокировок AD: {status_text}", "BotSettings")
    await callback.answer(f"🔔 Алерты: {status_text}", show_alert=False)
    await _render_health_dashboard(callback)


async def _render_locked_users(callback: CallbackQuery, page: int = 0):
    locked = await asyncio.to_thread(ad_service.get_locked_users)

    if not locked:
        card = render_bubble_card(
            title="🔒 Заблокированные учетные записи",
            badge="Чисто",
            body_lines=["✅ Активных заблокированных учетных записей не обнаружено (отключенные УЗ исключены)."],
            buttons=[
                button_row(callback_button("🔄 Обновить", "health:locked:0", "secondary")),
                button_row(callback_button("⬅️ К здоровью AD", "health:main", "secondary")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    total_pages = max(1, (len(locked) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = locked[page * ITEMS_PER_PAGE : (page + 1) * ITEMS_PER_PAGE]

    lines = [f"Всего активных блокировок: <b>{len(locked)}</b> (стр. {page + 1}/{total_pages}):\n"]
    buttons = []
    for u in chunk:
        uname = u["username"]
        lines.append(
            f"• <b>{html_text(u['display_name'])}</b> (<code>{html_text(uname)}</code>)\n"
            f"  <i>{html_text(u['department'])}</i> • Ошибок: <b>{u['bad_pwd_count']}</b> • {html_text(u['lockout_time'])}"
        )
        buttons.append(
            button_row(
                callback_button(f"🔓 {uname}", f"health:unlock:{uname}", "success"),
                callback_button(f"👤 {uname}", f"user:show:{uname}", "primary"),
            )
        )

    nav = []
    if page > 0:
        nav.append(callback_button("◀️", f"health:locked:{page - 1}", "secondary"))
    if page < total_pages - 1:
        nav.append(callback_button("▶️", f"health:locked:{page + 1}", "secondary"))
    if nav:
        buttons.append(button_row(*nav))

    buttons.append(
        button_row(
            callback_button("🔄 Обновить", f"health:locked:{page}", "secondary"),
            callback_button("⬅️ К здоровью AD", "health:main", "secondary"),
        )
    )

    card = render_bubble_card(
        title="🔒 Заблокированные учетные записи",
        badge=f"Найдено: {len(locked)}",
        body_lines=lines,
        buttons=buttons,
    )
    await edit_rich(callback.message, card)


@router.callback_query(F.data.startswith("health:locked"))
async def show_locked_users(callback: CallbackQuery):
    await callback.answer()
    await _render_locked_users(callback, _parse_page(callback.data, 0))


@router.callback_query(F.data.startswith("health:unlock:"))
async def quick_unlock_user(callback: CallbackQuery):
    username = callback.data.split(":", 2)[2]
    ok = await asyncio.to_thread(ad_service.unlock_user, username)
    await send_audit_log(callback.bot, callback.from_user, "Быстрая разблокировка (Health AD)", username, status=ok)
    if ok:
        await callback.answer(f"✅ {username} разблокирован", show_alert=False)
    else:
        await callback.answer(f"❌ Не удалось разблокировать {username}", show_alert=True)
    await _render_locked_users(callback, 0)


@router.callback_query(F.data.startswith("health:expiring"))
async def show_expiring_passwords(callback: CallbackQuery):
    page = _parse_page(callback.data, 0)
    await callback.answer()
    items = await asyncio.to_thread(ad_service.get_expiring_passwords, 7)

    if not items:
        card = render_bubble_card(
            title="⏳ Истекающие пароли (≤7 дн.)",
            badge="Порядок",
            body_lines=["✅ Сотрудников с истекающим или недавно истекшим паролем не найдено."],
            buttons=[button_row(callback_button("⬅️ К здоровью AD", "health:main", "secondary"))],
        )
        await edit_rich(callback.message, card)
        return

    total_pages = max(1, (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * ITEMS_PER_PAGE : (page + 1) * ITEMS_PER_PAGE]

    lines = [f"Найдено записей: <b>{len(items)}</b> (стр. {page + 1}/{total_pages}):\n"]
    buttons = []
    for u in chunk:
        d = u["days_left"]
        status_str = f"Истек ({abs(d)} дн. назад)" if d < 0 else (f"Сегодня ({u['expires_at']})" if d == 0 else f"Через {d} дн. ({u['expires_at']})")
        lines.append(
            f"• <b>{html_text(u['display_name'])}</b> (<code>{html_text(u['username'])}</code>) — <b>{html_text(status_str)}</b>"
        )
        buttons.append(button_row(callback_button(f"👤 {u['display_name']} ({u['username']})", f"user:show:{u['username']}", "primary")))

    nav = []
    if page > 0:
        nav.append(callback_button("◀️", f"health:expiring:{page - 1}", "secondary"))
    if page < total_pages - 1:
        nav.append(callback_button("▶️", f"health:expiring:{page + 1}", "secondary"))
    if nav:
        buttons.append(button_row(*nav))

    buttons.append(button_row(callback_button("⬅️ К здоровью AD", "health:main", "secondary")))
    card = render_bubble_card(
        title="⏳ Истекающие пароли (≤7 дн.)",
        badge=f"Всего: {len(items)}",
        body_lines=lines,
        buttons=buttons,
    )
    await edit_rich(callback.message, card)


async def _render_cert_ou_users(callback: CallbackQuery):
    users = await asyncio.to_thread(list_users_in_cert_ou)
    if not users:
        card = render_bubble_card(
            title="📜 Временный перевод для сертификата",
            badge="Пусто",
            body_lines=["✅ Все сотрудники возвращены в свои исходные подразделения (OU)."],
            buttons=[button_row(callback_button("⬅️ К здоровью AD", "health:main", "secondary"))],
        )
        await edit_rich(callback.message, card)
        return

    lines = [f"Ожидают возврата в исходный OU: <b>{len(users)}</b>\n"]
    buttons = []
    for item in users[:10]:
        uname = item["username"]
        orig_ou = item["original_ou"]
        short_ou = orig_ou.split(",")[0]
        lines.append(f"• <code>{html_text(uname)}</code> → вернуть в <i>{html_text(short_ou)}</i> ({html_text(item['moved_at'])})")
        buttons.append(
            button_row(
                callback_button(f"↩️ Вернуть {uname}", f"health:revert:{uname}", "success"),
                callback_button(f"👤 {uname}", f"user:show:{uname}", "primary"),
            )
        )

    buttons.append(button_row(callback_button("⬅️ К здоровью AD", "health:main", "secondary")))
    card = render_bubble_card(
        title="📜 Ожидающие возврата в OU",
        badge=f"Всего: {len(users)}",
        body_lines=lines,
        buttons=buttons,
    )
    await edit_rich(callback.message, card)


@router.callback_query(F.data == "health:cert_ou")
async def show_cert_ou_users(callback: CallbackQuery):
    await callback.answer()
    await _render_cert_ou_users(callback)


@router.callback_query(F.data.startswith("health:revert:"))
async def quick_revert_cert_ou(callback: CallbackQuery):
    username = callback.data.split(":", 2)[2]
    details = await asyncio.to_thread(ad_service.get_user_details, username)
    if not details:
        await callback.answer("❌ Пользователь не найден в AD", show_alert=True)
        return
    users = await asyncio.to_thread(list_users_in_cert_ou)
    match = next((x for x in users if x["username"].lower() == username.lower()), None)
    if not match:
        await callback.answer("ℹ️ Запись об исходном OU не найдена", show_alert=True)
        await _render_cert_ou_users(callback)
        return

    ok = await asyncio.to_thread(ad_service.move_user, details["dn"], match["original_ou"])
    if ok:
        await asyncio.to_thread(remove_original_ou, username)
    await send_audit_log(callback.bot, callback.from_user, "Возврат из Cert OU (Health AD)", username, status=ok)
    await callback.answer("✅ Возвращен в исходное подразделение" if ok else "❌ Ошибка перемещения", show_alert=not ok)
    await _render_cert_ou_users(callback)


@router.callback_query(F.data.startswith("health:stale"))
async def show_stale_pcs(callback: CallbackQuery):
    page = _parse_page(callback.data, 0)
    await callback.answer()
    pcs = await asyncio.to_thread(ad_service.get_stale_computers, 90)

    if not pcs:
        card = render_bubble_card(
            title="💤 Неактивные ПК (>90 дней)",
            badge="Порядок",
            body_lines=["✅ Компьютеров, не выходивших на связь более 90 дней, не обнаружено."],
            buttons=[button_row(callback_button("⬅️ К здоровью AD", "health:main", "secondary"))],
        )
        await edit_rich(callback.message, card)
        return

    total_pages = max(1, (len(pcs) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = pcs[page * ITEMS_PER_PAGE : (page + 1) * ITEMS_PER_PAGE]

    lines = [f"Всего неактивных ПК: <b>{len(pcs)}</b> (стр. {page + 1}/{total_pages}):\n"]
    buttons = []
    for p in chunk:
        ago_str = "Никогда" if p["days_ago"] >= 9999 else f"{p['days_ago']} дн. назад"
        lines.append(f"• <code>{html_text(p['name'])}</code> — {html_text(p['os'])} | {html_text(ago_str)}")
        buttons.append(button_row(callback_button(f"💻 {p['name']}", f"pc:show:{p['name']}", "primary")))

    nav = []
    if page > 0:
        nav.append(callback_button("◀️", f"health:stale:{page - 1}", "secondary"))
    if page < total_pages - 1:
        nav.append(callback_button("▶️", f"health:stale:{page + 1}", "secondary"))
    if nav:
        buttons.append(button_row(*nav))

    buttons.append(button_row(callback_button("⬅️ К здоровью AD", "health:main", "secondary")))
    card = render_bubble_card(
        title="💤 Неактивные ПК (>90 дней)",
        badge=f"Всего: {len(pcs)}",
        body_lines=lines,
        buttons=buttons,
    )
    await edit_rich(callback.message, card)
