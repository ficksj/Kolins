import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
from handlers.states import PCSearchState
from services.ad_service import ad_service
from services.audit_service import send_audit_log
from services.db_service import get_active_lotus_transfers_by_pc, get_pc_user_session
from services.logger_service import get_logger
from services.pc_monitor_service import (
    check_pc_critical_events,
    check_pc_online,
    get_pc_diagnostics,
    get_pc_logged_user_detailed,
    reboot_remote_pc_detailed,
)
from services.rich_message_service import (
    button_row,
    callback_button,
    edit_rich,
    edit_rich_by_ids,
    html_text,
    render_bubble_card,
    render_progress_bar,
    send_rich,
)

logger = get_logger("PCHandlers")
router = Router()

PC_SEARCH_PAGE_SIZE = 8


def render_pc_profile(
    details: dict,
    is_online: bool,
    session_info: dict,
    active_lotus_count: int = 0,
) -> str:
    name = details["name"]
    status_badge = "🟢 В сети" if is_online else "🔴 Оффлайн"

    username = session_info.get("username")
    source = session_info.get("source", "none")
    updated_at = session_info.get("updated_at")
    err_code = session_info.get("error_code")

    if username and source == "live":
        user_label = "👤 <b>Активный пользователь:</b>"
        user_info = f"<code>{html_text(username)}</code> (🟢 онлайн)"
    elif username and source == "cache":
        user_label = "👤 <b>Последний пользователь (кэш):</b>"
        ts = f" · <i>{html_text(updated_at)}</i>" if updated_at else ""
        user_info = f"<code>{html_text(username)}</code>{ts}"
    elif is_online and err_code == "auth_denied":
        user_label = "👤 <b>Активный пользователь:</b>"
        user_info = "<i>нет прав WinRM (auth_denied)</i>"
    else:
        user_label = "👤 <b>Активный пользователь:</b>"
        user_info = "—"

    body = [
        f"<b>{html_text(name)}</b> · <code>{html_text(details['dns_name'])}</code>",
        f"Сетевая доступность: <b>{status_badge}</b>",
        f"{user_label} {user_info}",
    ]

    table_rows = [
        ("Операционная система", f"{html_text(details['os'])} {html_text(details['os_version'])}"),
        ("Описание", html_text(details["description"])),
        ("Последний логон AD", html_text(details.get("last_logon", "—"))),
        ("Дата регистрации", html_text(details["created"])),
    ]

    details_content = f"<b>Distinguished Name:</b><br><code>{html_text(details['dn'])}</code>"

    lotus_btn_text = f"🪪 Lotus ID ({active_lotus_count})" if active_lotus_count > 0 else "🪪 Lotus ID"
    buttons = []
    if username:
        buttons.append(
            button_row(callback_button(f"👤 Профиль {username}", f"user:show:{username}", "success"))
        )
    buttons.extend([
        button_row(
            callback_button("📊 Диагностика ПК", f"pc:diag:{name}", "primary"),
            callback_button(lotus_btn_text, f"lotus:pc:{name}", "primary"),
        ),
        button_row(
            callback_button("⚠️ События (24ч)", f"pc:events:{name}", "secondary"),
            callback_button("📦 В notebooklenovo", f"pc:move:{name}", "primary"),
        ),
        button_row(
            callback_button("🔄 Перезагрузить", f"pc:reboot_confirm:{name}", "danger"),
            callback_button("🗑 Удалить из AD", f"pc:delete_confirm:{name}", "danger"),
        ),
        button_row(
            callback_button("🔄 Обновить статус", f"pc:show:{name}", "link"),
            callback_button("⬅️ Главное меню", "home:main", "link"),
        ),
    ])

    return render_bubble_card(
        title="💻 Карточка компьютера",
        badge="Рабочая станция",
        body_lines=body,
        table_rows=table_rows,
        details_title="📂 Иерархия в домене",
        details_content=details_content,
        buttons=buttons,
    )


async def show_pc_profile(message: Message, pc_name: str, edit: bool = False, origin: dict | None = None):
    details = await asyncio.to_thread(ad_service.get_computer_details, pc_name)
    if not details:
        card = render_bubble_card(
            title="❌ Компьютер не найден",
            badge="Ошибка",
            body_lines=[f"Компьютер <code>{html_text(pc_name)}</code> не найден в каталоге Active Directory."],
            buttons=[button_row(callback_button("🏠 Главное меню", "home:main", "link"))],
        )
    else:
        dns_raw = details.get("dns_name", "")
        target_host = dns_raw if dns_raw and dns_raw != "—" else pc_name
        dns_arg = target_host if target_host != pc_name else ""

        is_online, active_lotus = await asyncio.gather(
            asyncio.to_thread(check_pc_online, target_host),
            asyncio.to_thread(get_active_lotus_transfers_by_pc, pc_name),
        )
        if is_online:
            session_info = await asyncio.to_thread(
                get_pc_logged_user_detailed, pc_name, dns_arg, True
            )
        else:
            saved = await asyncio.to_thread(get_pc_user_session, pc_name)
            if saved and saved.get("username"):
                session_info = {
                    "username": saved["username"],
                    "source": "cache",
                    "updated_at": saved.get("updated_at"),
                    "error_code": None,
                }
            else:
                session_info = {"username": None, "source": "none", "updated_at": None, "error_code": None}

        card = render_pc_profile(details, is_online, session_info, len(active_lotus))

    if origin and origin.get("rich_chat_id") and origin.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], card)
    elif edit:
        await edit_rich(message, card)
    else:
        await send_rich(message, card)


def _render_pc_search_page(computers: list[dict], page: int = 0) -> str:
    total = len(computers)
    total_pages = max(1, (total + PC_SEARCH_PAGE_SIZE - 1) // PC_SEARCH_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = computers[page * PC_SEARCH_PAGE_SIZE : (page + 1) * PC_SEARCH_PAGE_SIZE]

    table_rows = [(f"<code>{html_text(pc['Name'])}</code>", html_text(pc["Description"])) for pc in chunk]
    buttons = [
        button_row(callback_button(f"💻 {pc['Name']}"[:46], f"pc:show:{pc['Name']}"))
        for pc in chunk
    ]

    nav = []
    if page > 0:
        nav.append(callback_button("◀️", f"ppage:{page - 1}", "secondary"))
    if page < total_pages - 1:
        nav.append(callback_button("▶️", f"ppage:{page + 1}", "secondary"))
    if nav:
        buttons.append(button_row(*nav))

    buttons.append(button_row(callback_button("⬅️ Главное меню", "home:main", "link")))

    return render_bubble_card(
        title="💻 Компьютеры в AD",
        badge=f"Найдено: {total} · стр. {page + 1}/{total_pages}",
        body_lines=["Выберите рабочую станцию из списка для диагностики и управления:"],
        table_rows=table_rows,
        buttons=buttons,
    )


@router.message(PCSearchState.waiting_for_query)
async def process_pc_search(message: Message, state: FSMContext):
    query = (message.text or "").strip()
    origin = await state.get_data()
    await state.clear()

    computers = await asyncio.to_thread(ad_service.search_computers, query)
    if not computers:
        card = render_bubble_card(
            title="💻 Результаты поиска",
            badge="Не найдено",
            body_lines=[f"По запросу <b>{html_text(query)}</b> компьютеров не найдено."],
            buttons=[
                button_row(callback_button("🔄 Искать снова", "home:pc", "primary")),
                button_row(callback_button("🏠 Главное меню", "home:main", "link")),
            ],
        )
        if origin.get("rich_chat_id") and origin.get("rich_message_id"):
            await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], card)
        else:
            await send_rich(message, card)
        return

    if len(computers) == 1:
        await show_pc_profile(message, computers[0]["Name"], origin=origin)
        return

    await state.update_data(pc_search_results=computers)
    card = _render_pc_search_page(computers, 0)

    if origin.get("rich_chat_id") and origin.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], card)
    else:
        await send_rich(message, card)


@router.callback_query(F.data.startswith("ppage:"))
async def paginate_pc_search(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split(":", 1)[1])
    await callback.answer()
    data = await state.get_data()
    computers = data.get("pc_search_results", [])
    if not computers:
        await edit_rich(
            callback.message,
            render_bubble_card(
                title="⚠️ Список поиска устарел",
                body_lines=["Повторите поиск компьютера заново."],
                buttons=[button_row(callback_button("💻 Найти ПК", "home:pc", "primary"))],
            ),
        )
        return
    await edit_rich(callback.message, _render_pc_search_page(computers, page))


def _auth_denied_card(pc_name: str, back_cb: str) -> str:
    return render_bubble_card(
        title="🔐 Ошибка доступа WinRM",
        badge="auth_denied",
        body_lines=[
            f"Компьютер <code>{html_text(pc_name)}</code> отклонил учетные данные WinRM (<code>{html_text(config.PC_ADMIN_USER)}</code>).",
            f"Убедитесь, что <code>{html_text(config.PC_ADMIN_USER)}</code> входит в <code>RemoteAdmins</code> / локальных администраторов ПК.",
        ],
        buttons=[
            button_row(callback_button("💻 К карточке ПК", back_cb, "link")),
            button_row(callback_button("🏠 Главное меню", "home:main", "link")),
        ],
    )


@router.callback_query(F.data.startswith("pc:"))
async def handle_pc_action(callback: CallbackQuery, state: FSMContext):
    action, _, value = callback.data.removeprefix("pc:").partition(":")
    await callback.answer()

    if action == "show":
        await edit_rich(
            callback.message,
            render_bubble_card(
                title=f"💻 Загрузка карточки {html_text(value)}",
                badge="Опрос ПК",
                body_lines=["⏳ Проверка доступности и активной сессии..."],
            ),
        )
        await show_pc_profile(callback.message, value, edit=True)
        return

    if action == "diag":
        await edit_rich(
            callback.message,
            render_bubble_card(
                title=f"📊 Диагностика {html_text(value)}",
                badge="WinRM опрос",
                body_lines=["⏳ Сбор сведений о RAM, дисках, модели и S/N оборудования..."],
            ),
        )
        details = await asyncio.to_thread(ad_service.get_computer_details, value)
        dns_raw = details.get("dns_name", "") if details else ""
        dns_name = dns_raw if dns_raw and dns_raw != "—" else value
        diag = await asyncio.to_thread(get_pc_diagnostics, value, dns_name)

        if isinstance(diag, dict) and diag.get("_error") == "auth_denied":
            await edit_rich(callback.message, _auth_denied_card(value, f"pc:show:{value}"))
            return

        if not diag:
            card = render_bubble_card(
                title="⚠️ Диагностика недоступна",
                badge="Оффлайн / WinRM ошибка",
                body_lines=[
                    f"Компьютер <code>{html_text(value)}</code> недоступен по сети или служба WinRM (порт 5985) закрыта.",
                    "Убедитесь, что машина включена и находится в локальной сети Банка.",
                ],
                buttons=[
                    button_row(
                        callback_button("🔄 Повторить опрос", f"pc:diag:{value}", "primary"),
                        callback_button("💻 К карточке ПК", f"pc:show:{value}", "link"),
                    )
                ],
            )
            await edit_rich(callback.message, card)
            return

        body = [
            f"💻 <b>Компьютер:</b> <code>{html_text(value)}</code>",
            f"🏭 <b>Оборудование:</b> {html_text(diag.get('manufacturer', '—'))} {html_text(diag.get('model', '—'))}",
            f"🔢 <b>Серийный номер (S/N):</b> <code>{html_text(diag.get('serial_number', '—'))}</code>",
            f"🌐 <b>IPv4:</b> <code>{html_text(diag.get('ip_address', '—'))}</code>",
            f"⏱ <b>Время работы (Uptime):</b> {html_text(diag.get('uptime', '—'))}",
            f"🧠 <b>RAM:</b> {diag.get('ram_used_gb', 0)} ГБ / {diag.get('ram_total_gb', 0)} ГБ ({diag.get('ram_percent', 0)}%)",
            render_progress_bar(diag.get("ram_percent", 0), length=12),
        ]

        table_rows = []
        for disk in diag.get("disks", []):
            dev = disk.get("device", "Диск")
            pct = disk.get("percent", 0)
            free = disk.get("free_gb", 0)
            total = disk.get("total_gb", 0)
            bar = render_progress_bar(pct, length=8)
            table_rows.append((f"Диск {html_text(dev)}", f"{bar}<br>{free} ГБ свободно из {total} ГБ"))

        card = render_bubble_card(
            title="📊 Системная диагностика ПК",
            badge="Телеметрия WinRM",
            body_lines=body,
            table_rows=table_rows,
            footer_note="Данные получены напрямую из операционной системы.",
            buttons=[
                button_row(
                    callback_button("⚠️ События (24ч)", f"pc:events:{value}", "secondary"),
                    callback_button("🔄 Обновить", f"pc:diag:{value}", "link"),
                ),
                button_row(
                    callback_button("🔄 Перезагрузить ПК", f"pc:reboot_confirm:{value}", "danger"),
                    callback_button("💻 К карточке ПК", f"pc:show:{value}", "link"),
                ),
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "events":
        await edit_rich(
            callback.message,
            render_bubble_card(
                title=f"⚠️ Журнал System (24ч) — {html_text(value)}",
                badge="Загрузка событий",
                body_lines=["⏳ Чтение критических ошибок (Level 1, 2) из журнала событий Windows..."],
            ),
        )
        details = await asyncio.to_thread(ad_service.get_computer_details, value)
        dns_raw = details.get("dns_name", "") if details else ""
        dns_name = dns_raw if dns_raw and dns_raw != "—" else value
        events = await asyncio.to_thread(check_pc_critical_events, value, dns_name)

        if isinstance(events, dict) and events.get("_error") == "auth_denied":
            await edit_rich(callback.message, _auth_denied_card(value, f"pc:show:{value}"))
            return

        if events is None:
            card = render_bubble_card(
                title=f"⚠️ Журнал событий — {html_text(value)}",
                badge="Недоступно",
                body_lines=[f"Не удалось прочитать журнал System на <code>{html_text(value)}</code> (оффлайн или WinRM недоступен)."],
                buttons=[button_row(callback_button("💻 К карточке ПК", f"pc:show:{value}", "link"))],
            )
            await edit_rich(callback.message, card)
            return

        if not events:
            card = render_bubble_card(
                title=f"✅ Журнал System — {html_text(value)}",
                badge="Ошибок нет",
                body_lines=["За последние 24 часа критических ошибок (Level 1, 2) в журнале <b>System</b> не зафиксировано."],
                buttons=[
                    button_row(
                        callback_button("🔄 Обновить", f"pc:events:{value}", "secondary"),
                        callback_button("💻 К карточке ПК", f"pc:show:{value}", "link"),
                    )
                ],
            )
            await edit_rich(callback.message, card)
            return

        lines = [f"Последние события (макс. 8) за 24 часа на <code>{html_text(value)}</code>:\n"]
        for ev in events[:8]:
            lines.append(
                f"• <b>{html_text(ev.get('time', ''))}</b> [ID {html_text(str(ev.get('Id', '')))} · "
                f"<code>{html_text(ev.get('ProviderName', ''))}</code>]\n  <i>{html_text(ev.get('msg', ''))}</i>"
            )

        card = render_bubble_card(
            title=f"⚠️ Журнал System — {html_text(value)}",
            badge=f"Событий: {len(events)}",
            body_lines=lines,
            buttons=[
                button_row(
                    callback_button("🔄 Обновить", f"pc:events:{value}", "secondary"),
                    callback_button("💻 К карточке ПК", f"pc:show:{value}", "link"),
                )
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "reboot_confirm":
        card = render_bubble_card(
            title="🔄 Подтверждение перезагрузки",
            badge="Внимание",
            body_lines=[
                f"Вы уверены, что хотите принудительно перезагрузить компьютер <code>{html_text(value)}</code>?",
                "Все несохраненные данные пользователя на машине будут закрыты.",
            ],
            buttons=[
                button_row(callback_button("⚠️ Да, перезагрузить", f"pc:reboot_do:{value}", "danger")),
                button_row(callback_button("❌ Отмена", f"pc:show:{value}", "link")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "reboot_do":
        await edit_rich(
            callback.message,
            render_bubble_card(
                title="🔄 Перезагрузка ПК",
                badge="Выполнение",
                body_lines=[f"⏳ Отправка команды перезагрузки на <code>{html_text(value)}</code>..."],
            ),
        )
        details = await asyncio.to_thread(ad_service.get_computer_details, value)
        dns_raw = details.get("dns_name", "") if details else ""
        dns_name = dns_raw if dns_raw and dns_raw != "—" else value
        success, err_code = await asyncio.to_thread(reboot_remote_pc_detailed, value, dns_name)
        await send_audit_log(callback.bot, callback.from_user, "Удаленная перезагрузка ПК", value, status=success)

        if not success and err_code == "auth_denied":
            await edit_rich(callback.message, _auth_denied_card(value, f"pc:show:{value}"))
            return

        badge = "Выполнено" if success else "Ошибка"
        res_text = (
            f"Команда перезагрузки успешно доставлена на <code>{html_text(value)}</code>."
            if success
            else f"Не удалось перезагрузить <code>{html_text(value)}</code> (ПК оффлайн или недоступен WinRM)."
        )

        card = render_bubble_card(
            title="🔄 Результат перезагрузки",
            badge=badge,
            body_lines=[res_text],
            buttons=[
                button_row(
                    callback_button("💻 К карточке ПК", f"pc:show:{value}", "link"),
                    callback_button("🏠 Главное меню", "home:main", "link"),
                )
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "move":
        target_ou = config.TARGET_PC_MOVE_OU
        success = await asyncio.to_thread(ad_service.move_computer, value, target_ou)
        await send_audit_log(callback.bot, callback.from_user, "Перемещение ПК", f"{value} -> {target_ou}", status=success)
        badge = "Успешно" if success else "Ошибка"
        result_text = "Компьютер успешно перемещён в целевое подразделение." if success else "Не удалось переместить компьютер."

        card = render_bubble_card(
            title="📦 Перемещение завершено",
            badge=badge,
            body_lines=[
                result_text,
                f"Целевой контейнер: <code>{html_text(target_ou)}</code>",
            ],
            buttons=[
                button_row(callback_button("💻 К карточке ПК", f"pc:show:{value}", "link")),
                button_row(callback_button("🏠 Главное меню", "home:main", "link")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "delete_confirm":
        card = render_bubble_card(
            title="🗑 Подтверждение удаления",
            badge="Осторожно",
            body_lines=[
                f"Вы действительно хотите удалить учетную запись <code>{html_text(value)}</code> из Active Directory?",
                "<b>Внимание:</b> действие необратимо. Рабочая станция потеряет доверительные отношения с доменом.",
            ],
            buttons=[
                button_row(callback_button("⚠️ Да, удалить компьютер", f"pc:delete_do:{value}", "danger")),
                button_row(callback_button("❌ Отмена", f"pc:show:{value}", "link")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    if action == "delete_do":
        success = await asyncio.to_thread(ad_service.delete_computer, value)
        await send_audit_log(callback.bot, callback.from_user, "Удаление ПК из домена", value, status=success)
        badge = "Успешно" if success else "Ошибка"
        result_text = f"Компьютер <code>{html_text(value)}</code> удален из каталога AD." if success else f"Не удалось удалить <code>{html_text(value)}</code>."

        card = render_bubble_card(
            title="🗑 Результат удаления",
            badge=badge,
            body_lines=[result_text],
            buttons=[button_row(callback_button("🏠 Главное меню", "home:main", "link"))],
        )
        await edit_rich(callback.message, card)
