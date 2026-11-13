import asyncio
import re
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

import config
from services.ad_service import ad_service
from services.pc_monitor_service import find_pc_by_username_scan, kill_remote_processes
from services.db_service import (
    get_user_pc_session,
    get_pc_user_session,
    get_active_lotus_transfers_by_pc,
    get_latest_lotus_transfer_by_pc,
    list_pending_lotus_transfers,
    get_lotus_transfer_by_id,
)
from services import lotus_service
from services.audit_service import send_audit_log
from services.rich_message_service import (
    render_bubble_card,
    button_row,
    callback_button,
    html_text,
    edit_rich,
)

router = Router()


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "lotus_user.id"


def _lotus_error_hint(error_code: str | None, error_text: str | None) -> str:
    base = html_text(error_text or "Неизвестная ошибка SMB.")
    if error_code == "access_denied":
        return (
            f"{base}\n💡 <i>Подсказка:</i> Учетная запись <code>{html_text(config.PC_ADMIN_USER)}</code> "
            f"должна входить в группу <code>RemoteAdmins</code> / локальных администраторов ПК."
        )
    if error_code == "offline":
        return f"{base}\n💡 Компьютер должен быть включен и доступен по порту SMB (445)."
    return base


async def _render_lotus_pc_panel(
    callback: CallbackQuery,
    pc_name: str,
    state: FSMContext,
    back_cb: str | None = None,
    ad_username: str | None = None,
):
    loading = render_bubble_card(
        title=f"🪪 Lotus Notes ID — {html_text(pc_name)}",
        badge="Сканирование SMB",
        body_lines=[f"⏳ Подключение к <code>\\\\{html_text(pc_name)}\\C$\\Users\\*\\AppData\\Local\\IBM\\Notes\\Data</code>..."],
    )
    await edit_rich(callback.message, loading)

    pc_details, active_transfers, latest_transfer, session_info = await asyncio.gather(
        asyncio.to_thread(ad_service.get_computer_details, pc_name),
        asyncio.to_thread(get_active_lotus_transfers_by_pc, pc_name),
        asyncio.to_thread(get_latest_lotus_transfer_by_pc, pc_name),
        asyncio.to_thread(get_pc_user_session, pc_name),
    )

    dns_name = pc_details["dns_name"] if (pc_details and pc_details.get("dns_name") not in (None, "", "—")) else pc_name
    resolved_user = ad_username or (session_info.get("username") if session_info else None)

    scan_res = await asyncio.to_thread(lotus_service.scan_pc_lotus_ids, dns_name)
    files = scan_res.get("files", [])

    await state.update_data(
        lotus_scan_files=files,
        lotus_pc_ctx={
            "pc_name": pc_name.upper(),
            "dns_name": dns_name,
            "ad_username": resolved_user,
        },
    )

    lines = [
        f"💻 Компьютер: <code>{html_text(pc_name.upper())}</code> ({html_text(dns_name)})",
        f"📂 Хранилище: <code>{html_text(config.LOTUS_STORAGE_UNC)}</code>",
    ]
    if resolved_user:
        lines.append(f"👤 Сотрудник: <code>{html_text(resolved_user)}</code>")

    buttons = []

    if active_transfers:
        lines.append("\n<b>📦 Ожидают возврата на этот ПК:</b>")
        for t in active_transfers:
            lines.append(
                f"• <b>{html_text(t['original_filename'])}</b> (профиль <code>{html_text(t['win_profile'])}</code>)\n"
                f"  В хранилище: <code>{html_text(t['storage_filename'])}</code> • {html_text(t['extracted_at'])}"
            )
            buttons.append(
                button_row(
                    callback_button(f"🔄 Вернуть {t['original_filename']}", f"lotus:ret:{t['id']}", "success"),
                    callback_button("🗑", f"lotus:del:{t['id']}", "danger"),
                )
            )
    elif latest_transfer:
        lines.append(
            f"\n🧠 <i>Память последнего файла:</i> <code>{html_text(latest_transfer['original_filename'])}</code> "
            f"(профиль <code>{html_text(latest_transfer['win_profile'])}</code>, "
            f"статус: <b>{html_text(latest_transfer['status'])}</b>)"
        )

    lines.append("\n<b>🔎 Файлы *.id на рабочей станции:</b>")
    if scan_res.get("ok"):
        if not files:
            lines.append("— <i>В профилях пользователей файлы *.id не обнаружены.</i>")
        else:
            for idx, f in enumerate(files[:10]):
                lines.append(
                    f"<b>{idx + 1}.</b> <code>{html_text(f['win_profile'])}</code> → "
                    f"<b>{html_text(f['filename'])}</b> ({f['size_kb']} КБ, {html_text(f['modified_str'])})"
                )
                buttons.append(
                    button_row(
                        callback_button(
                            f"📤 Выгрузить [{f['win_profile']}] {f['filename']}",
                            f"lotus:pick:{idx}",
                            "primary",
                        )
                    )
                )
    else:
        lines.append(_lotus_error_hint(scan_res.get("error_code"), scan_res.get("error")))

    buttons.append(
        button_row(
            callback_button("🔄 Пересканировать", f"lotus:pc:{pc_name}", "secondary"),
            callback_button("📋 Все ожидающие ID", "lotus:list", "secondary"),
        )
    )
    if back_cb:
        buttons.append(button_row(callback_button("⬅️ Назад", back_cb, "secondary")))
    else:
        buttons.append(button_row(callback_button("⬅️ Карточка ПК", f"pc:show:{pc_name}", "secondary")))

    card = render_bubble_card(
        title=f"🪪 Lotus Notes ID — {html_text(pc_name.upper())}",
        badge=f"Найдено: {len(files)}" if scan_res.get("ok") else "Ошибка SMB",
        body_lines=lines,
        buttons=buttons,
    )
    await edit_rich(callback.message, card)


async def _render_pending_lotus_list(callback: CallbackQuery):
    items = await asyncio.to_thread(list_pending_lotus_transfers)
    if not items:
        card = render_bubble_card(
            title="🪪 IBM Lotus Notes ID",
            badge="Очередь пуста",
            body_lines=[
                "✅ Нет ID-файлов, ожидающих возврата на компьютеры.",
                f"📂 Папка хранилища: <code>{html_text(config.LOTUS_STORAGE_UNC)}</code>",
                "\nЧтобы забрать ID-файл с ПК, откройте карточку компьютера или сотрудника и нажмите «🪪 Lotus ID».",
            ],
            buttons=[
                button_row(callback_button("💻 Найти ПК", "home:pc", "primary")),
                button_row(callback_button("⬅️ Главная", "home:main", "secondary")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    lines = [f"Файлов на хранении (ожидают возврата после сброса пароля): <b>{len(items)}</b>\n"]
    buttons = []
    for t in items[:10]:
        user_part = f" • {t['ad_username']}" if t.get("ad_username") else ""
        lines.append(
            f"• <code>{html_text(t['pc_name'])}</code>{html_text(user_part)}: "
            f"<b>{html_text(t['original_filename'])}</b> (профиль <code>{html_text(t['win_profile'])}</code>)\n"
            f"  Файл в storage: <code>{html_text(t['storage_filename'])}</code>"
        )
        buttons.append(
            button_row(
                callback_button(f"🔄 Вернуть {t['pc_name']} ({t['original_filename']})", f"lotus:ret:{t['id']}", "success"),
                callback_button("📂", f"lotus:pc:{t['pc_name']}", "primary"),
            )
        )

    buttons.append(
        button_row(
            callback_button("🔄 Обновить", "lotus:list", "secondary"),
            callback_button("⬅️ Главная", "home:main", "secondary"),
        )
    )
    card = render_bubble_card(
        title="🪪 Lotus ID — Ожидают возврата",
        badge=f"Активных: {len(items)}",
        body_lines=lines,
        buttons=buttons,
    )
    await edit_rich(callback.message, card)


@router.callback_query(F.data == "lotus:list")
async def show_pending_lotus_list(callback: CallbackQuery):
    await callback.answer()
    await _render_pending_lotus_list(callback)


@router.callback_query(F.data.startswith("lotus:pc:"))
async def open_lotus_for_pc(callback: CallbackQuery, state: FSMContext):
    pc_name = callback.data.split(":", 2)[2]
    await callback.answer()
    await _render_lotus_pc_panel(callback, pc_name, state)


@router.callback_query(F.data.startswith("lotus:user:"))
async def open_lotus_for_user(callback: CallbackQuery, state: FSMContext):
    username = callback.data.split(":", 2)[2]
    await callback.answer()

    session = await asyncio.to_thread(get_user_pc_session, username)
    if session and session.get("pc_name"):
        await _render_lotus_pc_panel(
            callback,
            session["pc_name"],
            state,
            back_cb=f"user:show:{username}",
            ad_username=username,
        )
        return

    loading = render_bubble_card(
        title=f"🪪 Lotus ID — {html_text(username)}",
        badge="Поиск ПК",
        body_lines=[f"⏳ В кэше нет привязанного ПК. Выполняется быстрый поиск компьютера сотрудника <code>{html_text(username)}</code>..."],
    )
    await edit_rich(callback.message, loading)

    target_computers = await asyncio.to_thread(ad_service.get_target_computers)
    found_pc = await find_pc_by_username_scan(username, target_computers) if target_computers else None
    if found_pc:
        pc_short = found_pc.split(".")[0]
        await _render_lotus_pc_panel(
            callback,
            pc_short,
            state,
            back_cb=f"user:show:{username}",
            ad_username=username,
        )
        return

    card = render_bubble_card(
        title=f"🪪 Lotus ID — {html_text(username)}",
        badge="ПК не найден",
        body_lines=[
            f"❌ Не удалось автоматически определить активный ПК сотрудника <code>{html_text(username)}</code>.",
            "Вы можете привязать ПК вручную в карточке сотрудника или найти компьютер через поиск ПК.",
        ],
        buttons=[
            button_row(callback_button("💻 Поиск ПК", "home:pc", "primary")),
            button_row(callback_button("⬅️ К сотруднику", f"user:show:{username}", "secondary")),
        ],
    )
    await edit_rich(callback.message, card)


async def _perform_extract(callback: CallbackQuery, idx: int, storage_filename: str, state: FSMContext):
    data = await state.get_data()
    files = data.get("lotus_scan_files", [])
    ctx = data.get("lotus_pc_ctx", {})
    if idx < 0 or idx >= len(files) or not ctx:
        await edit_rich(
            callback.message,
            render_bubble_card(
                title="⚠️ Сессия устарела",
                badge="Lotus ID",
                body_lines=["Список файлов устарел. Повторите поиск компьютера."],
                buttons=[button_row(callback_button("⬅️ Главная", "home:main", "secondary"))],
            ),
        )
        return

    f = files[idx]
    pc_name = ctx["pc_name"]
    dns_name = ctx["dns_name"]
    ad_username = ctx.get("ad_username")

    loading = render_bubble_card(
        title="📤 Копирование Lotus ID",
        badge="Выгрузка в storage",
        body_lines=[
            f"⏳ Копирование <code>{html_text(f['filename'])}</code> с <code>{html_text(pc_name)}</code> "
            f"в <code>{html_text(config.LOTUS_STORAGE_UNC)}\\{html_text(storage_filename)}</code>..."
        ],
    )
    await edit_rich(callback.message, loading)

    res = await asyncio.to_thread(
        lotus_service.extract_id_to_storage,
        dns_name,
        pc_name,
        f["win_profile"],
        f["unc_dir"],
        f["filename"],
        storage_filename,
        ad_username,
        callback.from_user.id,
    )

    await send_audit_log(
        callback.bot,
        callback.from_user,
        f"Выгружен Lotus ID ({f['filename']} -> {storage_filename})",
        pc_name,
        status=res.get("ok", False),
    )

    if res.get("ok"):
        tid = res["transfer_id"]
        card = render_bubble_card(
            title="✅ Lotus ID выгружен в хранилище",
            badge="Готово к сбросу пароля",
            body_lines=[
                f"💻 ПК: <code>{html_text(pc_name)}</code> | Профиль: <code>{html_text(f['win_profile'])}</code>",
                f"📄 Запомненное имя файла: <b>{html_text(f['filename'])}</b>",
                f"📦 Сохранено в: <code>{html_text(res['storage_path'])}</code>",
                f"📏 Размер: <b>{res['bytes_copied']} байт</b>",
                "\nТеперь можно сбросить пароль Lotus ID, а затем нажать кнопку возврата ниже:",
            ],
            buttons=[
                button_row(callback_button(f"🔄 Вернуть {f['filename']} на ПК", f"lotus:ret:{tid}", "success")),
                button_row(callback_button(f"⬅️ Lotus панель {pc_name}", f"lotus:pc:{pc_name}", "secondary")),
            ],
        )
        await edit_rich(callback.message, card)
    else:
        card = render_bubble_card(
            title="❌ Ошибка выгрузки Lotus ID",
            badge="Ошибка SMB",
            body_lines=[_lotus_error_hint(res.get("error_code"), res.get("error"))],
            buttons=[button_row(callback_button(f"⬅️ К Lotus ПК {pc_name}", f"lotus:pc:{pc_name}", "secondary"))],
        )
        await edit_rich(callback.message, card)


@router.callback_query(F.data.startswith("lotus:pick:"))
async def pick_lotus_file(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":", 2)[2])
    data = await state.get_data()
    files = data.get("lotus_scan_files", [])
    ctx = data.get("lotus_pc_ctx", {})
    if idx < 0 or idx >= len(files) or not ctx:
        await callback.answer("⚠️ Список файлов устарел, обновите сканирование", show_alert=True)
        return

    await callback.answer()
    f = files[idx]
    pc_name = ctx["pc_name"]

    col = await asyncio.to_thread(lotus_service.check_storage_collision, f["filename"])
    if not col.get("ok"):
        card = render_bubble_card(
            title="❌ Хранилище недоступно",
            badge="Ошибка SMB",
            body_lines=[_lotus_error_hint(col.get("error_code"), col.get("error"))],
            buttons=[button_row(callback_button("⬅️ Назад", f"lotus:pc:{pc_name}", "secondary"))],
        )
        await edit_rich(callback.message, card)
        return

    if col.get("exists"):
        safe_name = _sanitize_filename(f"{pc_name}_{f['win_profile']}_{f['filename']}")
        card = render_bubble_card(
            title="⚠️ Конфликт имен в хранилище",
            badge="Файл уже существует",
            body_lines=[
                f"В папке <code>{html_text(config.LOTUS_STORAGE_UNC)}</code> уже есть файл <b>{html_text(f['filename'])}</b>!",
                f"📏 Текущий размер в storage: <b>{col['size_kb']} КБ</b> • изменён <i>{html_text(col['modified_str'])}</i>",
                f"\n🧠 <i>Даже при сохранении под безопасным именем</i> <code>{html_text(safe_name)}</code> "
                f"<i>бот запомнит оригинальное имя</i> <b>{html_text(f['filename'])}</b> <i>и при возврате запишет его на ПК под исходным именем!</i>",
            ],
            buttons=[
                button_row(callback_button(f"📝 Безопасно: {safe_name}", f"lotus:pull_ren:{idx}", "primary")),
                button_row(callback_button(f"♻️ Перезаписать {f['filename']}", f"lotus:pull_ovw:{idx}", "danger")),
                button_row(callback_button("❌ Отмена", f"lotus:pc:{pc_name}", "secondary")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    await _perform_extract(callback, idx, f["filename"], state)


@router.callback_query(F.data.startswith("lotus:pull_ren:"))
async def pull_lotus_renamed(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":", 2)[2])
    data = await state.get_data()
    files = data.get("lotus_scan_files", [])
    ctx = data.get("lotus_pc_ctx", {})
    if idx < 0 or idx >= len(files) or not ctx:
        await callback.answer("⚠️ Список устарел", show_alert=True)
        return
    await callback.answer()
    f = files[idx]
    safe_name = _sanitize_filename(f"{ctx['pc_name']}_{f['win_profile']}_{f['filename']}")
    await _perform_extract(callback, idx, safe_name, state)


@router.callback_query(F.data.startswith("lotus:pull_ovw:"))
async def pull_lotus_overwrite(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":", 2)[2])
    data = await state.get_data()
    files = data.get("lotus_scan_files", [])
    if idx < 0 or idx >= len(files):
        await callback.answer("⚠️ Список устарел", show_alert=True)
        return
    await callback.answer()
    await _perform_extract(callback, idx, files[idx]["filename"], state)


async def _do_restore_lotus(callback: CallbackQuery, transfer_id: int):
    t = await asyncio.to_thread(get_lotus_transfer_by_id, transfer_id)
    if not t:
        return

    pc_name = t["pc_name"]
    loading = render_bubble_card(
        title="🔄 Возврат Lotus ID на ПК",
        badge="Запись по SMB",
        body_lines=[
            f"⏳ Возврат <code>{html_text(t['storage_filename'])}</code> → "
            f"<code>{html_text(t['original_unc_dir'])}\\{html_text(t['original_filename'])}</code>..."
        ],
    )
    await edit_rich(callback.message, loading)

    res = await asyncio.to_thread(lotus_service.restore_id_from_storage, transfer_id, False)
    await send_audit_log(
        callback.bot,
        callback.from_user,
        f"Возврат Lotus ID ({t['original_filename']})",
        pc_name,
        status=res.get("ok", False),
    )

    if res.get("ok"):
        card = render_bubble_card(
            title="✅ Lotus ID возвращен на компьютер!",
            badge="Восстановлено",
            body_lines=[
                f"💻 ПК: <code>{html_text(pc_name)}</code> | Профиль: <code>{html_text(t['win_profile'])}</code>",
                f"📄 Восстановлен файл: <b>{html_text(t['original_filename'])}</b>",
                f"📍 Путь: <code>{html_text(res['target_path'])}</code>",
                f"📏 Скопировано: <b>{res['bytes_copied']} байт</b>",
                "\nКопия в хранилище пока сохранена в качестве резервной. Удалить её из <code>\\storage\\public\\id</code>?",
            ],
            buttons=[
                button_row(callback_button("🧹 Удалить копию из storage", f"lotus:del:{transfer_id}", "danger")),
                button_row(callback_button(f"⬅️ Lotus ПК {pc_name}", f"lotus:pc:{pc_name}", "secondary")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    if res.get("error_code") == "in_use":
        card = render_bubble_card(
            title="⚠️ ID-файл занят IBM Notes!",
            badge="STATUS_SHARING_VIOLATION",
            body_lines=[
                f"Файл <b>{html_text(t['original_filename'])}</b> на <code>{html_text(pc_name)}</code> заблокирован запущенным процессом Notes.",
                "Вы можете принудительно закрыть <code>notes.exe / nlnotes.exe</code> через WinRM и повторить возврат автоматически.",
            ],
            buttons=[
                button_row(callback_button("⚡ Закрыть Notes и вернуть ID", f"lotus:killnotes:{transfer_id}", "danger")),
                button_row(callback_button("🔄 Повторить возврат", f"lotus:ret:{transfer_id}", "primary")),
                button_row(callback_button(f"⬅️ Lotus ПК {pc_name}", f"lotus:pc:{pc_name}", "secondary")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    card = render_bubble_card(
        title="❌ Не удалось вернуть Lotus ID",
        badge="Ошибка SMB",
        body_lines=[_lotus_error_hint(res.get("error_code"), res.get("error"))],
        buttons=[
            button_row(callback_button("🔄 Повторить возврат", f"lotus:ret:{transfer_id}", "primary")),
            button_row(callback_button(f"⬅️ Lotus ПК {pc_name}", f"lotus:pc:{pc_name}", "secondary")),
        ],
    )
    await edit_rich(callback.message, card)


@router.callback_query(F.data.startswith("lotus:ret:"))
async def restore_lotus_id(callback: CallbackQuery):
    transfer_id = int(callback.data.split(":", 2)[2])
    t = await asyncio.to_thread(get_lotus_transfer_by_id, transfer_id)
    if not t:
        await callback.answer("❌ Запись переноса не найдена", show_alert=True)
        return
    await callback.answer()
    await _do_restore_lotus(callback, transfer_id)


@router.callback_query(F.data.startswith("lotus:killnotes:"))
async def kill_notes_and_restore(callback: CallbackQuery):
    transfer_id = int(callback.data.split(":", 2)[2])
    t = await asyncio.to_thread(get_lotus_transfer_by_id, transfer_id)
    if not t:
        await callback.answer("❌ Запись переноса не найдена", show_alert=True)
        return
    await callback.answer()

    pc_name = t["pc_name"]
    dns_name = t.get("dns_name") or pc_name

    loading = render_bubble_card(
        title="⚡ Завершение IBM Notes и возврат ID",
        badge="WinRM + SMB",
        body_lines=[f"⏳ Остановка процессов <code>notes/nlnotes</code> на <code>{html_text(pc_name)}</code>..."],
    )
    await edit_rich(callback.message, loading)

    killed, err_code = await asyncio.to_thread(kill_remote_processes, pc_name, dns_name)
    if not killed:
        msg = "❌ Ошибка авторизации WinRM при остановке Notes." if err_code == "auth_denied" else "❌ Не удалось выполнить WinRM-команду на ПК."
        card = render_bubble_card(
            title="❌ Не удалось остановить IBM Notes",
            badge="WinRM ошибка",
            body_lines=[msg, "Попросите пользователя вручную закрыть IBM Notes и нажмите «🔄 Повторить возврат»."],
            buttons=[
                button_row(callback_button("🔄 Повторить возврат", f"lotus:ret:{transfer_id}", "primary")),
                button_row(callback_button(f"⬅️ Lotus ПК {pc_name}", f"lotus:pc:{pc_name}", "secondary")),
            ],
        )
        await edit_rich(callback.message, card)
        return

    await asyncio.sleep(1.0)
    await _do_restore_lotus(callback, transfer_id)


@router.callback_query(F.data.startswith("lotus:del:"))
async def delete_lotus_storage_copy(callback: CallbackQuery, state: FSMContext):
    transfer_id = int(callback.data.split(":", 2)[2])
    t = await asyncio.to_thread(get_lotus_transfer_by_id, transfer_id)
    res = await asyncio.to_thread(lotus_service.delete_storage_copy, transfer_id)
    if res.get("ok"):
        await callback.answer("🧹 Копия удалена из storage", show_alert=False)
        if t:
            await send_audit_log(callback.bot, callback.from_user, f"Удалена копия Lotus ID ({t['storage_filename']})", t["pc_name"])
    else:
        await callback.answer(f"❌ {res.get('error', 'Ошибка')}", show_alert=True)

    if t:
        await _render_lotus_pc_panel(callback, t["pc_name"], state)
    else:
        await _render_pending_lotus_list(callback)
