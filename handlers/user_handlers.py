import asyncio
import datetime
import secrets
import string

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

import config
from handlers.states import GroupSearchState, PCBindState, UserSearchState
from services.ad_service import TZ_LOCAL, ad_service
from services.audit_service import send_audit_log
from services.db_service import (
    get_original_ou,
    get_user_pc_session,
    remove_original_ou,
    save_original_ou,
    save_user_pc_session,
)
from services.logger_service import get_logger
from services.pc_monitor_service import (
    check_pc_online,
    find_pc_by_username_scan,
    get_pc_diagnostics,
    get_pc_logged_user_detailed,
)
from services.print_service import generate_equipment_act
from services.rich_message_service import (
    button_row,
    callback_button,
    copy_button,
    edit_rich,
    edit_rich_by_ids,
    html_text,
    render_bubble_card,
    send_rich,
)

logger = get_logger("UserHandlers")
router = Router()

USER_SEARCH_PAGE_SIZE = 8
GROUP_PAGE_SIZE = 6


def _generate_strong_password(length: int = 12) -> str:
    """Генерация криптостойкого пароля, гарантированно проходящего требования AD."""
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower = "abcdefghijkmnopqrstuvwxyz"
    digits = "23456789"
    special = "!@#$%&*+-=?"
    chars = [
        secrets.choice(upper),
        secrets.choice(lower),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    pool = upper + lower + digits + special
    chars.extend(secrets.choice(pool) for _ in range(max(4, length - 4)))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _user_status_icon(u: dict) -> str:
    if u.get("is_disabled"):
        return "🔴"
    if u.get("is_locked"):
        return "🔒"
    return "🟢"


def render_user_profile(
    details: dict,
    cached_pc: dict | None = None,
    cert_pending: bool = False,
) -> str:
    username = details["username"]
    is_disabled = bool(details.get("is_disabled"))

    if is_disabled:
        status_badge = "🔴 Отключена"
    elif details.get("is_locked"):
        status_badge = "🔒 Заблокирована"
    else:
        status_badge = "🟢 Активна"

    body = [
        f"<b>{html_text(details['display_name'])}</b> · <code>{html_text(username)}</code>",
        f"💼 <b>{html_text(details.get('title', '—'))}</b>",
        f"🏢 <i>{html_text(details.get('department', '—'))}</i>",
        f"📊 Статус учетной записи: <b>{status_badge}</b>",
    ]
    if cached_pc and cached_pc.get("pc_name"):
        body.append(f"💻 Известный ПК (кэш): <code>{html_text(cached_pc['pc_name'])}</code>")

    table_rows = [
        ("Внутр. телефон", f"<code>{html_text(details.get('telephone_number', '—'))}</code>"),
        ("Мобильный", f"<code>{html_text(details.get('mobile', '—'))}</code>"),
        ("Email", f"<code>{html_text(details.get('mail', '—'))}</code>"),
        ("Кабинет", html_text(details.get("physical_delivery_office_name", "—"))),
        ("Руководитель", html_text(details.get("manager_name", "—"))),
        ("Срок пароля", html_text(details.get("pwd_expiry", "—"))),
    ]

    groups = details.get("groups", [])
    if groups:
        groups_html = "<br>".join(f"• <code>{html_text(g)}</code>" for g in groups[:10])
        if len(groups) > 10:
            groups_html += f"<br><i>... и еще {len(groups) - 10} групп</i>"
    else:
        groups_html = "<i>Не состоит в группах</i>"

    details_content = (
        f"<b>Дата создания в AD:</b> {html_text(details.get('created_at', '—'))}<br>"
        f"<b>Смена пароля:</b> {html_text(details.get('pwd_last_set', '—'))}<br>"
        f"<b>Истечение пароля:</b> {html_text(details.get('pwd_expiry', '—'))}<br>"
        f"<b>Последний вход:</b> {html_text(details.get('last_logon', '—'))}<br>"
        f"<b>Ошибок пароля:</b> <code>{details.get('bad_pwd_count', 0)}</code><br>"
        f"<b>Logon Workstation:</b> <code>{html_text(details.get('logon_workstation', '—'))}</code><br>"
        f"<b>Скрипт входа:</b> <code>{html_text(details.get('script_path', '—'))}</code><br>"
        f"<b>Distinguished Name:</b><br><code>{html_text(details.get('dn', '—'))}</code><br><br>"
        f"<b>👥 Группы безопасности ({len(groups)}):</b><br>{groups_html}"
    )

    toggle_btn = (
        callback_button("🟢 Включить УЗ", f"user:toggle_ask:{username}", "success")
        if is_disabled
        else callback_button("⛔ Отключить УЗ", f"user:toggle_ask:{username}", "danger")
    )
    cert_btn = (
        callback_button("↩️ Вернуть из Cert OU", f"user:revert:{username}", "success")
        if cert_pending
        else callback_button("📜 Сертификат", f"user:cert:{username}", "link")
    )

    buttons = [
        button_row(
            callback_button("💻 ПК сотрудника", f"user:pc:{username}", "primary"),
            callback_button("🪪 Lotus ID", f"lotus:user:{username}", "primary"),
        ),
        button_row(
            callback_button("🔑 Сбросить пароль", f"user:password:{username}", "danger"),
            callback_button("🔓 Разблокировать", f"user:unlock:{username}", "success"),
        ),
        button_row(
            toggle_btn,
            callback_button("👥 Группы AD", f"ugrp:view:{username}", "primary"),
        ),
        button_row(
            cert_btn,
            callback_button("📄 Акт техники", f"user:act:{username}", "primary"),
        ),
        button_row(
            callback_button("🖼 Фото AD", f"user:photo:{username}", "secondary"),
            callback_button("🔄 Обновить", f"user:show:{username}", "link"),
        ),
        button_row(callback_button("⬅️ Главное меню", "home:main", "link")),
    ]

    return render_bubble_card(
        title="👤 Карточка сотрудника",
        badge="Active Directory",
        body_lines=body,
        table_rows=table_rows,
        details_title="📂 Безопасность и группы безопасности AD",
        details_content=details_content,
        buttons=buttons,
    )


async def show_user_profile(message: Message, username: str, edit: bool = False, origin: dict | None = None):
    details, cached_pc, orig_ou = await asyncio.gather(
        asyncio.to_thread(ad_service.get_user_details, username),
        asyncio.to_thread(get_user_pc_session, username),
        asyncio.to_thread(get_original_ou, username),
    )

    if not details:
        error_card = render_bubble_card(
            title="❌ Сотрудник не найден",
            badge="Ошибка",
            body_lines=[f"Пользователь <code>{html_text(username)}</code> не найден в Active Directory."],
            buttons=[button_row(callback_button("⬅️ Главное меню", "home:main", "link"))],
        )
        if origin and origin.get("rich_chat_id") and origin.get("rich_message_id"):
            await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], error_card)
        elif edit:
            await edit_rich(message, error_card)
        else:
            await send_rich(message, error_card)
        return

    cert_pending = bool(orig_ou) or (config.CERT_USER_OU.lower() in details.get("dn", "").lower())
    rich = render_user_profile(details, cached_pc=cached_pc, cert_pending=cert_pending)
    if origin and origin.get("rich_chat_id") and origin.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], rich)
    elif edit:
        await edit_rich(message, rich)
    else:
        await send_rich(message, rich)


def _render_user_search_page(users: list[dict], page: int = 0) -> str:
    total = len(users)
    total_pages = max(1, (total + USER_SEARCH_PAGE_SIZE - 1) // USER_SEARCH_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = users[page * USER_SEARCH_PAGE_SIZE : (page + 1) * USER_SEARCH_PAGE_SIZE]

    rows = [
        (f"{_user_status_icon(u)} {html_text(u['display_name'])}", f"<code>{html_text(u['username'])}</code>")
        for u in chunk
    ]
    buttons = [
        button_row(callback_button(f"{_user_status_icon(u)} {u['display_name']}"[:46], f"user:show:{u['username']}"))
        for u in chunk
    ]

    nav = []
    if page > 0:
        nav.append(callback_button("◀️", f"upage:{page - 1}", "secondary"))
    if page < total_pages - 1:
        nav.append(callback_button("▶️", f"upage:{page + 1}", "secondary"))
    if nav:
        buttons.append(button_row(*nav))

    buttons.append(button_row(callback_button("⬅️ Главное меню", "home:main", "link")))

    return render_bubble_card(
        title="🔎 Результаты поиска",
        badge=f"Всего: {total} · стр. {page + 1}/{total_pages}",
        body_lines=["Выберите сотрудника из списка (🟢 активен · 🔒 заблокирован · 🔴 отключен):"],
        table_rows=rows,
        buttons=buttons,
    )


@router.message(UserSearchState.waiting_for_query)
async def process_user_search(message: Message, state: FSMContext):
    query = (message.text or "").strip()
    origin = await state.get_data()
    await state.clear()

    users = await asyncio.to_thread(ad_service.search_users, query)
    if not users:
        result = render_bubble_card(
            title="🔎 Результаты поиска",
            badge="Не найдено",
            body_lines=[f"По запросу <b>{html_text(query)}</b> совпадений не обнаружено."],
            buttons=[
                button_row(callback_button("🔄 Искать снова", "home:user", "primary")),
                button_row(callback_button("🏠 Главное меню", "home:main", "link")),
            ],
        )
        if origin.get("rich_chat_id") and origin.get("rich_message_id"):
            await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], result)
        else:
            await send_rich(message, result)
        return

    if len(users) == 1:
        await show_user_profile(message, users[0]["username"], origin=origin)
        return

    await state.update_data(user_search_results=users)
    result = _render_user_search_page(users, 0)

    if origin.get("rich_chat_id") and origin.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], result)
    else:
        await send_rich(message, result)


@router.callback_query(F.data.startswith("upage:"))
async def paginate_user_search(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split(":", 1)[1])
    await callback.answer()
    data = await state.get_data()
    users = data.get("user_search_results", [])
    if not users:
        await edit_rich(
            callback.message,
            render_bubble_card(
                title="⚠️ Список поиска устарел",
                body_lines=["Повторите поиск сотрудника заново."],
                buttons=[button_row(callback_button("🔎 Найти сотрудника", "home:user", "primary"))],
            ),
        )
        return
    await edit_rich(callback.message, _render_user_search_page(users, page))


@router.callback_query(F.data.startswith("user_select_"))
async def select_user_from_list(callback: CallbackQuery):
    username = callback.data.removeprefix("user_select_")
    await callback.answer()
    await show_user_profile(callback.message, username, edit=True)


async def _render_user_pc_lookup(callback: CallbackQuery, username: str, force_scan: bool = False):
    loading_card = render_bubble_card(
        title="💻 Поиск рабочего места...",
        badge="Сканирование" if force_scan else "Проверка кэша и сети",
        body_lines=[
            f"Проверяем сессию сотрудника <code>{html_text(username)}</code>...",
            "<i>Проверка кэша SQLite и опрос онлайн-компьютеров домена.</i>",
        ],
    )
    await edit_rich(callback.message, loading_card)

    session = await asyncio.to_thread(get_user_pc_session, username)
    found_pc = None
    dns_target = ""
    is_live = False
    is_online = False
    cached_pc_note = None

    if session and not force_scan:
        cached_pc = session["pc_name"]
        cached_dns = session.get("dns_name") or cached_pc
        is_online = await asyncio.to_thread(check_pc_online, cached_dns)
        if is_online:
            res = await asyncio.to_thread(get_pc_logged_user_detailed, cached_pc, cached_dns, False)
            active_user = res.get("username")
            if active_user and active_user.lower() == username.lower():
                found_pc = cached_pc
                dns_target = cached_dns
                is_live = True
            elif active_user and active_user.lower() != username.lower():
                cached_pc_note = (
                    f"На ранее привязанном ПК <code>{html_text(cached_pc)}</code> сейчас работает "
                    f"<code>{html_text(active_user)}</code>. Ищем новый ПК..."
                )
            else:
                # ПК онлайн, но никто не залогинен или auth_denied -> помним кэш на случай неудачи скана
                found_pc = cached_pc
                dns_target = cached_dns
        else:
            # ПК выключен — показываем кэш! Но при желании админ может нажать «Сканировать сеть»
            found_pc = cached_pc
            dns_target = cached_dns

    if (not is_live and force_scan) or (not found_pc):
        target_computers = await asyncio.to_thread(ad_service.get_target_computers)
        if target_computers:
            live_pc = await find_pc_by_username_scan(username, target_computers)
            if live_pc:
                found_pc = live_pc.split(".")[0]
                dns_target = live_pc if "." in live_pc else f"{found_pc}.{ad_service.domain}"
                is_live = True
                is_online = True
                await asyncio.to_thread(save_user_pc_session, username, found_pc, dns_target)

    if found_pc:
        if is_live:
            badge = "Сессия активна"
            status_text = "🟢 <b>Статус:</b> Активная сессия подтверждена в реальном времени"
            net_text = "🟢 Online (WinRM OK)"
        else:
            badge = "Последний известный (кэш)"
            ts = session.get("updated_at") if session else "—"
            status_text = f"🟡 <b>Статус:</b> Данные из кэша (обновлено: <i>{html_text(str(ts))}</i>)"
            net_text = "🟢 Online (сессия не активна / нет прав WinRM)" if is_online else "🔴 Offline (ПК выключен)"

        body = [
            f"👤 <b>Сотрудник:</b> <code>{html_text(username)}</code>",
            f"💻 <b>Компьютер:</b> <b>{html_text(found_pc)}</b>",
            status_text,
        ]
        if cached_pc_note:
            body.append(f"<i>{cached_pc_note}</i>")

        result_card = render_bubble_card(
            title="💻 Рабочее место сотрудника",
            badge=badge,
            body_lines=body,
            table_rows=[
                ("Имя ПК", f"<code>{html_text(found_pc)}</code>"),
                ("FQDN", f"<code>{html_text(dns_target or found_pc)}</code>"),
                ("Сетевой статус", net_text),
            ],
            buttons=[
                button_row(
                    callback_button("💻 Карточка ПК", f"pc:show:{found_pc}", "primary"),
                    callback_button("🪪 Lotus ID", f"lotus:pc:{found_pc}", "primary"),
                ),
                button_row(
                    callback_button("🔍 Сканировать сеть", f"user:pcscan:{username}", "secondary"),
                    callback_button("🔗 Привязать другой ПК", f"user:bindpc:{username}", "secondary"),
                ),
                button_row(
                    callback_button("👤 К сотруднику", f"user:show:{username}", "link"),
                    callback_button("🏠 Главное меню", "home:main", "link"),
                ),
            ],
        )
    else:
        body = [
            f"Сотрудник <code>{html_text(username)}</code> не найден в кэше и в данный момент не залогинен ни на одной доступной машине."
        ]
        if cached_pc_note:
            body.append(cached_pc_note)
        result_card = render_bubble_card(
            title="💻 Компьютер не найден",
            badge="Оффлайн / Нет данных",
            body_lines=body,
            buttons=[
                button_row(
                    callback_button("🔗 Привязать ПК вручную", f"user:bindpc:{username}", "primary"),
                    callback_button("🔄 Повторить скан", f"user:pcscan:{username}", "secondary"),
                ),
                button_row(
                    callback_button("👤 К сотруднику", f"user:show:{username}", "link"),
                    callback_button("🏠 Главное меню", "home:main", "link"),
                ),
            ],
        )

    await edit_rich(callback.message, result_card)


@router.callback_query(F.data.startswith("user:"))
async def handle_user_action(callback: CallbackQuery, state: FSMContext):
    action, _, username = callback.data.removeprefix("user:").partition(":")
    if not username:
        await callback.answer("Не удалось определить пользователя", show_alert=True)
        return

    if action == "show":
        await callback.answer()
        await show_user_profile(callback.message, username, edit=True)
        return

    if action == "pc":
        await callback.answer()
        await _render_user_pc_lookup(callback, username, force_scan=False)
        return

    if action == "pcscan":
        await callback.answer()
        await _render_user_pc_lookup(callback, username, force_scan=True)
        return

    if action == "bindpc":
        await callback.answer()
        await state.update_data(
            bind_username=username,
            rich_chat_id=callback.message.chat.id,
            rich_message_id=callback.message.message_id,
        )
        await state.set_state(PCBindState.waiting_for_pc)
        card = render_bubble_card(
            title=f"🔗 Привязка ПК — {html_text(username)}",
            badge="Режим ввода",
            body_lines=[
                f"Отправьте точным или частичным именем компьютер для закрепления за <code>{html_text(username)}</code>.",
                "<i>Например: <code>N10453-1</code></i>",
            ],
            buttons=[button_row(callback_button("❌ Отмена", f"user:show:{username}", "link"))],
        )
        await edit_rich(callback.message, card)
        return

    if action == "password":
        await callback.answer()
        card = render_bubble_card(
            title="🔑 Сброс пароля (подтверждение)",
            badge="Требуется выбор",
            body_lines=[
                f"Вы собираетесь сбросить пароль для <code>{html_text(username)}</code>.",
                "После сброса будет включено требование смены пароля при следующем входе (<code>pwdLastSet=0</code>) и снята блокировка УЗ (если была).",
                "\nВыберите вариант нового пароля:",
            ],
            buttons=[
                button_row(callback_button("🎲 Случайный стойкий (12 симв.)", f"upw:rand:{username}", "danger")),
                button_row(callback_button("🔑 Стандартный (ASDfg123+)", f"upw:def:{username}", "primary")),
                button_row(callback_button("❌ Отмена", f"user:show:{username}", "link")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "unlock":
        await callback.answer()
        success = await asyncio.to_thread(ad_service.unlock_user, username)
        await send_audit_log(callback.bot, callback.from_user, "Разблокировка учётной записи", username, status=success)

        result_str = "Учётная запись успешно разблокирована." if success else "Не удалось разблокировать учётную запись."
        badge = "Успешно" if success else "Ошибка"

        card = render_bubble_card(
            title="🔓 Разблокировка аккаунта",
            badge=badge,
            body_lines=[f"Пользователь: <code>{html_text(username)}</code>", result_str],
            buttons=[
                button_row(
                    callback_button("👤 К карточке", f"user:show:{username}", "link"),
                    callback_button("🏠 Главное меню", "home:main", "link"),
                )
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "toggle_ask":
        await callback.answer()
        details = await asyncio.to_thread(ad_service.get_user_details, username)
        if not details:
            await edit_rich(
                callback.message,
                render_bubble_card(title="❌ Ошибка", body_lines=[f"Пользователь <code>{html_text(username)}</code> не найден."]),
            )
            return
        currently_disabled = bool(details.get("is_disabled"))
        action_word = "ВКЛЮЧИТЬ" if currently_disabled else "ОТКЛЮЧИТЬ (Disable)"
        btn_label = "🟢 Да, включить УЗ" if currently_disabled else "⛔ Да, отключить УЗ"
        btn_style = "success" if currently_disabled else "danger"

        card = render_bubble_card(
            title="⚙️ Изменение статуса УЗ",
            badge="Подтверждение",
            body_lines=[
                f"Вы действительно хотите <b>{action_word}</b> учетную запись сотрудника:",
                f"<b>{html_text(details['display_name'])}</b> (<code>{html_text(username)}</code>)?",
            ],
            buttons=[
                button_row(callback_button(btn_label, f"user:toggle_do:{username}", btn_style)),
                button_row(callback_button("❌ Отмена", f"user:show:{username}", "link")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "toggle_do":
        await callback.answer()
        details = await asyncio.to_thread(ad_service.get_user_details, username)
        if not details:
            await edit_rich(
                callback.message,
                render_bubble_card(title="❌ Ошибка", body_lines=[f"Пользователь <code>{html_text(username)}</code> не найден."]),
            )
            return
        target_enabled = bool(details.get("is_disabled"))
        ok = await asyncio.to_thread(ad_service.set_user_enabled, username, target_enabled)
        act_name = "Включение УЗ" if target_enabled else "Отключение УЗ"
        await send_audit_log(callback.bot, callback.from_user, act_name, username, status=ok)

        res_msg = (
            f"Учетная запись <code>{html_text(username)}</code> успешно <b>{'включена' if target_enabled else 'отключена'}</b>."
            if ok
            else f"Не удалось изменить статус учетной записи <code>{html_text(username)}</code>."
        )
        card = render_bubble_card(
            title=f"⚙️ {act_name}",
            badge="Успешно" if ok else "Ошибка",
            body_lines=[res_msg],
            buttons=[
                button_row(
                    callback_button("👤 К карточке", f"user:show:{username}", "link"),
                    callback_button("🏠 Главное меню", "home:main", "link"),
                )
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "cert":
        await callback.answer()
        details = await asyncio.to_thread(ad_service.get_user_details, username)
        if not details:
            await edit_rich(
                callback.message,
                render_bubble_card(title="❌ Ошибка", body_lines=[f"Пользователь <code>{html_text(username)}</code> не найден."]),
            )
            return

        target_ou = config.CERT_USER_OU
        if target_ou.lower() in details["dn"].lower():
            existing_orig = await asyncio.to_thread(get_original_ou, username)
            lines = [
                f"<b>{html_text(details['display_name'])}</b> уже находится в целевом OU <code>notebooklenovo</code>.",
                "Исходный OU сохранен без перезаписи.",
            ]
            btns = []
            if existing_orig:
                btns.append(button_row(callback_button("↩️ Вернуть в исходный OU", f"user:revert:{username}", "success")))
            btns.append(button_row(callback_button("👤 К карточке", f"user:show:{username}", "link")))
            await edit_rich(
                callback.message,
                render_bubble_card(
                    title="📜 Пользователь уже в Cert OU",
                    badge="Информация",
                    body_lines=lines,
                    buttons=btns,
                ),
            )
            return

        original_ou = details["dn"].split(",", 1)[1]
        success = await asyncio.to_thread(ad_service.move_user, details["dn"], target_ou)
        if success:
            await asyncio.to_thread(save_original_ou, username, original_ou)
        await send_audit_log(callback.bot, callback.from_user, "Перемещение для выдачи сертификата", username, status=success)

        if success:
            body = [
                f"<b>{html_text(details['display_name'])}</b> перемещён в <code>notebooklenovo</code>.",
                "После генерации сертификата нажмите кнопку возврата.",
            ]
            buttons = [
                button_row(callback_button("↩️ Вернуть в исходный OU", f"user:revert:{username}", "success")),
                button_row(callback_button("👤 К карточке", f"user:show:{username}", "link")),
            ]
            badge = "Готово к сертификации"
        else:
            body = ["Не удалось переместить пользователя в целевое подразделение AD."]
            buttons = [button_row(callback_button("👤 К карточке", f"user:show:{username}", "link"))]
            badge = "Ошибка"

        await edit_rich(
            callback.message,
            render_bubble_card(title="📜 Подготовка сертификата", badge=badge, body_lines=body, buttons=buttons),
        )
        return

    if action == "revert":
        await callback.answer()
        original_ou = await asyncio.to_thread(get_original_ou, username)
        if not original_ou:
            card = render_bubble_card(
                title="⚠️ Возврат отменён",
                badge="Предупреждение",
                body_lines=["Исходный OU не найден в базе данных бота."],
                buttons=[button_row(callback_button("👤 К карточке", f"user:show:{username}", "link"))],
            )
            await edit_rich(callback.message, card)
            return

        details = await asyncio.to_thread(ad_service.get_user_details, username)
        success = bool(details and await asyncio.to_thread(ad_service.move_user, details["dn"], original_ou))
        await send_audit_log(callback.bot, callback.from_user, "Возврат сотрудника в исходный OU", username, status=success)
        if success:
            await asyncio.to_thread(remove_original_ou, username)

        result_str = "Пользователь возвращён в исходный OU." if success else "Не удалось выполнить возврат."
        badge = "Успешно" if success else "Ошибка"

        card = render_bubble_card(
            title="↩️ Возврат в исходный OU",
            badge=badge,
            body_lines=[
                f"Сотрудник: <code>{html_text(username)}</code>",
                result_str,
                f"Целевой OU: <code>{html_text(original_ou)}</code>",
            ],
            buttons=[
                button_row(
                    callback_button("👤 К карточке", f"user:show:{username}", "link"),
                    callback_button("🏠 Главное меню", "home:main", "link"),
                )
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "photo":
        photo_bytes = await asyncio.to_thread(ad_service.get_user_photo, username)
        if not photo_bytes:
            await callback.answer("ℹ️ Фото сотрудника (thumbnailPhoto) не загружено в AD", show_alert=True)
            return
        await callback.answer()
        photo_file = BufferedInputFile(photo_bytes, filename=f"{username}.jpg")
        await callback.message.answer_photo(
            photo=photo_file,
            caption=f"🖼 Фото профиля AD: <code>{html_text(username)}</code>",
            parse_mode="HTML",
        )
        return

    if action == "act":
        user_det, session = await asyncio.gather(
            asyncio.to_thread(ad_service.get_user_details, username),
            asyncio.to_thread(get_user_pc_session, username),
        )
        if not user_det:
            await callback.answer("❌ Сотрудник не найден в AD", show_alert=True)
            return

        await callback.answer("⏳ Генерация официального PDF-акта...")

        pc_det = None
        hw_info = None
        if session and session.get("pc_name"):
            pc_name = session["pc_name"]
            pc_det = await asyncio.to_thread(ad_service.get_computer_details, pc_name)
            dns = (pc_det.get("dns_name") if pc_det and pc_det.get("dns_name") != "—" else None) or session.get("dns_name") or pc_name
            if await asyncio.to_thread(check_pc_online, dns):
                diag = await asyncio.to_thread(get_pc_diagnostics, pc_name, dns)
                if isinstance(diag, dict) and not diag.get("_error"):
                    hw_info = diag

        pdf_buf = await asyncio.to_thread(generate_equipment_act, user_det, pc_det, hw_info)
        pdf_file = BufferedInputFile(pdf_buf.read(), filename=f"Акт_приема_передачи_{username}.pdf")

        fio = user_det.get("display_name", username)
        pc_caption = f"💻 Компьютер: <code>{html_text(pc_det['name'])}</code>" if pc_det else "💻 Оборудование: Рабочая станция / Ноутбук"
        today_str = datetime.datetime.now(TZ_LOCAL).strftime("%d.%m.%Y")

        await callback.message.answer_document(
            document=pdf_file,
            caption=(
                f"📄 <b>Официальный Акт приема-передачи оборудования</b>\n\n"
                f"👤 Сотрудник: <b>{html_text(fio)}</b>\n"
                f"{pc_caption}\n"
                f"📅 Дата: <b>{today_str}</b>\n\n"
                f"<i>Документ готов к печати и подписанию.</i>"
            ),
            parse_mode="HTML",
        )
        await send_audit_log(callback.bot, callback.from_user, "Генерация Акта приема-передачи техники", username, status=True)
        return

    await callback.answer()


@router.message(PCBindState.waiting_for_pc)
async def process_manual_pc_bind(message: Message, state: FSMContext):
    pc_query = (message.text or "").strip()
    data = await state.get_data()
    username = data.get("bind_username", "")
    await state.clear()

    if not username:
        return

    details = await asyncio.to_thread(ad_service.get_computer_details, pc_query)
    if not details:
        found = await asyncio.to_thread(ad_service.search_computers, pc_query)
        if len(found) == 1:
            details = await asyncio.to_thread(ad_service.get_computer_details, found[0]["Name"])

    if not details:
        card = render_bubble_card(
            title="❌ Компьютер не найден",
            badge="Привязка ПК",
            body_lines=[f"Компьютер <code>{html_text(pc_query)}</code> не найден (или найдено несколько). Уточните точное имя ПК."],
            buttons=[
                button_row(callback_button("🔄 Повторить ввод", f"user:bindpc:{username}", "primary")),
                button_row(callback_button("👤 К сотруднику", f"user:show:{username}", "link")),
            ],
        )
    else:
        pc_name = details["name"]
        dns_name = details["dns_name"] if details.get("dns_name") != "—" else ""
        await asyncio.to_thread(save_user_pc_session, username, pc_name, dns_name)
        await send_audit_log(message.bot, message.from_user, f"Ручная привязка ПК ({pc_name})", username, status=True)
        card = render_bubble_card(
            title="✅ ПК привязан к сотруднику",
            badge="Сохранено в кэш",
            body_lines=[
                f"👤 Сотрудник: <code>{html_text(username)}</code>",
                f"💻 Закрепленный ПК: <b>{html_text(pc_name)}</b> (<code>{html_text(dns_name or pc_name)}</code>)",
            ],
            buttons=[
                button_row(
                    callback_button("💻 Карточка ПК", f"pc:show:{pc_name}", "primary"),
                    callback_button("🪪 Lotus ID", f"lotus:pc:{pc_name}", "primary"),
                ),
                button_row(callback_button("👤 К сотруднику", f"user:show:{username}", "link")),
            ],
        )

    if data.get("rich_chat_id") and data.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, data["rich_chat_id"], data["rich_message_id"], card)
    else:
        await send_rich(message, card)


@router.callback_query(F.data.startswith("upw:"))
async def handle_password_reset_choice(callback: CallbackQuery):
    _, mode, username = callback.data.split(":", 2)
    await callback.answer()

    new_password = _generate_strong_password(12) if mode == "rand" else "ASDfg123+"
    success = await asyncio.to_thread(ad_service.reset_password, username, new_password, True)
    await send_audit_log(callback.bot, callback.from_user, f"Сброс пароля ({mode})", username, status=success)

    if success:
        body = [
            f"Пароль пользователя <code>{html_text(username)}</code> успешно сброшен.",
            f"Новый временный пароль: <code>{html_text(new_password)}</code>",
            "🔐 Включено требование смены пароля при следующем входе.",
        ]
        badge = "Успешно"
        buttons = [
            button_row(copy_button("📋 Скопировать пароль", new_password)),
            button_row(
                callback_button("👤 К карточке", f"user:show:{username}", "link"),
                callback_button("🏠 Главное меню", "home:main", "link"),
            ),
        ]
    else:
        body = [f"Не удалось сбросить пароль пользователя <code>{html_text(username)}</code> в Active Directory."]
        badge = "Ошибка"
        buttons = [
            button_row(
                callback_button("👤 К карточке", f"user:show:{username}", "link"),
                callback_button("🏠 Главное меню", "home:main", "link"),
            )
        ]

    card = render_bubble_card(
        title="🔑 Сброс пароля",
        badge=badge,
        body_lines=body,
        buttons=buttons,
    )
    await edit_rich(callback.message, card)


async def _render_user_groups_panel(callback_or_msg, username: str, state: FSMContext, page: int = 0, via_ids: dict | None = None):
    details = await asyncio.to_thread(ad_service.get_user_details, username)
    if not details:
        card = render_bubble_card(
            title="❌ Пользователь не найден",
            body_lines=[f"Сотрудник <code>{html_text(username)}</code> не найден."],
            buttons=[button_row(callback_button("🏠 Главное меню", "home:main", "link"))],
        )
        if via_ids:
            await edit_rich_by_ids(callback_or_msg.bot, via_ids["rich_chat_id"], via_ids["rich_message_id"], card)
        else:
            await edit_rich(callback_or_msg.message, card)
        return

    group_dns = details.get("group_dns", [])
    await state.update_data(ugrp_username=username, ugrp_groups=group_dns)

    total = len(group_dns)
    total_pages = max(1, (total + GROUP_PAGE_SIZE - 1) // GROUP_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * GROUP_PAGE_SIZE
    chunk = group_dns[start : start + GROUP_PAGE_SIZE]

    lines = [
        f"👤 Сотрудник: <b>{html_text(details['display_name'])}</b> (<code>{html_text(username)}</code>)",
        f"Всего групп безопасности: <b>{total}</b> (стр. {page + 1}/{total_pages})",
        "<i>Группы со значком 🔒 защищены политикой безопасности бота.</i>\n",
    ]
    buttons = [
        button_row(callback_button("➕ Добавить в группу", f"ugrp:add:{username}", "success"))
    ]

    for offset, g in enumerate(chunk):
        idx = start + offset
        gname = g["name"]
        is_prot = gname.lower() in config.PROTECTED_AD_GROUPS
        if is_prot:
            lines.append(f"• 🔒 <code>{html_text(gname)}</code>")
        else:
            lines.append(f"• <code>{html_text(gname)}</code>")
            buttons.append(button_row(callback_button(f"➖ Удалить: {gname}"[:46], f"ugrp:rm:{idx}", "danger")))

    if total == 0:
        lines.append("<i>Пользователь не состоит ни в одной дополнительной группе.</i>")

    nav = []
    if page > 0:
        nav.append(callback_button("◀️", f"ugrp:p:{page - 1}", "secondary"))
    if page < total_pages - 1:
        nav.append(callback_button("▶️", f"ugrp:p:{page + 1}", "secondary"))
    if nav:
        buttons.append(button_row(*nav))

    buttons.append(button_row(callback_button("⬅️ К профилю сотрудника", f"user:show:{username}", "link")))

    card = render_bubble_card(
        title="👥 Управление группами AD",
        badge=f"Групп: {total}",
        body_lines=lines,
        buttons=buttons,
    )
    if via_ids:
        await edit_rich_by_ids(callback_or_msg.bot, via_ids["rich_chat_id"], via_ids["rich_message_id"], card)
    else:
        await edit_rich(callback_or_msg.message, card)


@router.callback_query(F.data.startswith("ugrp:"))
async def handle_user_groups(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 2)
    sub = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    if sub == "view":
        await callback.answer()
        await _render_user_groups_panel(callback, arg, state, page=0)
        return

    if sub == "p":
        data = await state.get_data()
        username = data.get("ugrp_username")
        if not username:
            await callback.answer("⚠️ Сессия устарела", show_alert=True)
            return
        await callback.answer()
        await _render_user_groups_panel(callback, username, state, page=int(arg or 0))
        return

    if sub == "add":
        await callback.answer()
        await state.update_data(
            ugrp_username=arg,
            rich_chat_id=callback.message.chat.id,
            rich_message_id=callback.message.message_id,
        )
        await state.set_state(GroupSearchState.waiting_for_query)
        card = render_bubble_card(
            title=f"➕ Добавление в группу AD ({html_text(arg)})",
            badge="Поиск группы",
            body_lines=[
                "Отправьте сообщением часть названия группы безопасности AD.",
                "<i>Например: <code>VPN</code>, <code>1C</code>, <code>USB</code>.</i>",
            ],
            buttons=[button_row(callback_button("❌ Отмена", f"ugrp:view:{arg}", "link"))],
        )
        await edit_rich(callback.message, card)
        return

    if sub == "rm":
        data = await state.get_data()
        username = data.get("ugrp_username")
        groups = data.get("ugrp_groups", [])
        idx = int(arg)
        if not username or idx < 0 or idx >= len(groups):
            await callback.answer("⚠️ Список групп устарел", show_alert=True)
            return
        await callback.answer()
        g = groups[idx]
        card = render_bubble_card(
            title="⚠️ Исключение из группы AD",
            badge="Подтверждение",
            body_lines=[
                f"Удалить <code>{html_text(username)}</code> из группы:",
                f"<b>{html_text(g['name'])}</b>?",
            ],
            buttons=[
                button_row(callback_button("⛔ Да, удалить из группы", f"ugrp:rmdo:{idx}", "danger")),
                button_row(callback_button("❌ Отмена", f"ugrp:view:{username}", "link")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    if sub == "rmdo":
        data = await state.get_data()
        username = data.get("ugrp_username")
        groups = data.get("ugrp_groups", [])
        idx = int(arg)
        if not username or idx < 0 or idx >= len(groups):
            await callback.answer("⚠️ Список групп устарел", show_alert=True)
            return
        g = groups[idx]
        ok, msg = await asyncio.to_thread(ad_service.remove_user_from_group, username, g["dn"])
        await send_audit_log(callback.bot, callback.from_user, f"Удаление из группы ({g['name']})", username, status=ok)
        await callback.answer(f"✅ Удален из {g['name']}" if ok else f"❌ {msg}", show_alert=not ok)
        await _render_user_groups_panel(callback, username, state, page=0)
        return

    if sub == "pick":
        data = await state.get_data()
        username = data.get("ugrp_username")
        search_groups = data.get("ugrp_search", [])
        idx = int(arg)
        if not username or idx < 0 or idx >= len(search_groups):
            await callback.answer("⚠️ Результаты поиска устарели", show_alert=True)
            return
        g = search_groups[idx]
        ok, msg = await asyncio.to_thread(ad_service.add_user_to_group, username, g["dn"])
        await send_audit_log(callback.bot, callback.from_user, f"Добавление в группу ({g['name']})", username, status=ok)
        await callback.answer(f"✅ Добавлен в {g['name']}" if ok else f"❌ {msg}", show_alert=not ok)
        await _render_user_groups_panel(callback, username, state, page=0)


@router.message(GroupSearchState.waiting_for_query)
async def process_group_search(message: Message, state: FSMContext):
    query = (message.text or "").strip()
    data = await state.get_data()
    username = data.get("ugrp_username", "")
    await state.set_state(None)

    if not username:
        await state.clear()
        return

    groups = await asyncio.to_thread(ad_service.search_groups, query, 12)
    await state.update_data(ugrp_search=groups)

    if not groups:
        card = render_bubble_card(
            title="👥 Поиск группы AD",
            badge="Не найдено",
            body_lines=[f"По запросу <b>{html_text(query)}</b> подходящих групп не найдено."],
            buttons=[
                button_row(callback_button("🔄 Искать снова", f"ugrp:add:{username}", "primary")),
                button_row(callback_button("⬅️ К группам", f"ugrp:view:{username}", "link")),
            ],
        )
    else:
        lines = [f"Выберите группу для добавления сотрудника <code>{html_text(username)}</code>:\n"]
        buttons = []
        for idx, g in enumerate(groups[:8]):
            desc = f" — <i>{html_text(g['description'])}</i>" if g.get("description") and g["description"] != "—" else ""
            lines.append(f"• <b>{html_text(g['name'])}</b>{desc}")
            buttons.append(button_row(callback_button(f"➕ {g['name']}"[:46], f"ugrp:pick:{idx}", "success")))
        buttons.append(button_row(callback_button("⬅️ Отмена", f"ugrp:view:{username}", "link")))

        card = render_bubble_card(
            title="👥 Найденные группы AD",
            badge=f"Найдено: {len(groups)}",
            body_lines=lines,
            buttons=buttons,
        )

    if data.get("rich_chat_id") and data.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, data["rich_chat_id"], data["rich_message_id"], card)
    else:
        await send_rich(message, card)
