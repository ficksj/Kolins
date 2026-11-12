import asyncio
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from services.ad_service import ad_service
from services.audit_service import send_audit_log
from services.print_service import generate_equipment_act
from aiogram.types import BufferedInputFile
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
    get_pc_logged_user,
)
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
from handlers.states import UserSearchState

logger = get_logger("UserHandlers")
router = Router()


def render_user_profile(details: dict) -> str:
    username = details["username"]
    
    # Бейдж статуса
    if details.get("is_disabled"):
        status_badge = "🔴 Отключена"
    elif details.get("is_locked"):
        status_badge = "🔒 Заблокирована"
    else:
        status_badge = "🟢 Активна"

    # Основной бабл профиля
    body = [
        f"<b>{html_text(details['display_name'])}</b> · <code>{html_text(username)}</code>",
        f"💼 <b>{html_text(details.get('title', '—'))}</b>",
        f"🏢 <i>{html_text(details.get('department', '—'))}</i>",
        f"📊 Статус учетной записи: <b>{status_badge}</b>",
    ]

    # Таблица основных атрибутов
    table_rows = [
        ("Внутр. телефон", f"<code>{html_text(details.get('telephone_number', '—'))}</code>"),
        ("Мобильный", f"<code>{html_text(details.get('mobile', '—'))}</code>"),
        ("Email", f"<code>{html_text(details.get('mail', '—'))}</code>"),
        ("Кабинет", html_text(details.get('physical_delivery_office_name', '—'))),
        ("Руководитель", html_text(details.get('manager_name', '—'))),
    ]

    # Формирование блока групп безопасности (до 10 групп)
    groups = details.get("groups", [])
    if groups:
        groups_html = "<br>".join(f"• <code>{html_text(g)}</code>" for g in groups[:10])
        if len(groups) > 10:
            groups_html += f"<br><i>... и еще {len(groups) - 10} групп</i>"
    else:
        groups_html = "<i>Не состоит в группах</i>"

    # Детальная техническая сводка для системных администраторов
    details_content = (
        f"<b>Дата создания в AD:</b> {html_text(details.get('created_at', '—'))}<br>"
        f"<b>Смена пароля:</b> {html_text(details.get('pwd_last_set', '—'))}<br>"
        f"<b>Последний вход:</b> {html_text(details.get('last_logon', '—'))}<br>"
        f"<b>Ошибок пароля:</b> <code>{details.get('bad_pwd_count', 0)}</code><br>"
        f"<b>Logon Workstation:</b> <code>{html_text(details.get('logon_workstation', '—'))}</code><br>"
        f"<b>Скрипт входа:</b> <code>{html_text(details.get('script_path', '—'))}</code><br>"
        f"<b>Distinguished Name:</b><br><code>{html_text(details.get('dn', '—'))}</code><br><br>"
        f"<b>👥 Группы безопасности ({len(groups)}):</b><br>{groups_html}"
    )

    buttons = [
        button_row(callback_button('💻 ПК сотрудника', f'user:pc:{username}', 'primary')),
        button_row(
            callback_button('🔑 Сбросить пароль', f'user:password:{username}', 'danger'),
            callback_button('🔓 Разблокировать', f'user:unlock:{username}', 'success'),
        ),
        button_row(
            callback_button('📜 Выдать сертификат', f'user:cert:{username}', 'link'),
            callback_button('📄 Акт передачи техники', f'user:act:{username}', 'primary'),
        ),
        button_row(
            callback_button('🔄 Обновить профиль', f'user:show:{username}', 'link'),
        ),
        button_row(callback_button('⬅️ Главное меню', 'home:main', 'link')),
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
    details = ad_service.get_user_details(username)
    if not details:
        error_card = render_bubble_card(
            title="❌ Сотрудник не найден",
            badge="Ошибка",
            body_lines=[f"Пользователь <code>{html_text(username)}</code> не найден в Active Directory."],
            buttons=[button_row(callback_button('⬅️ Главное меню', 'home:main', 'link'))]
        )
        if origin and origin.get("rich_chat_id") and origin.get("rich_message_id"):
            await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], error_card)
        elif edit:
            await edit_rich(message, error_card)
        else:
            await send_rich(message, error_card)
        return

    rich = render_user_profile(details)
    if origin and origin.get("rich_chat_id") and origin.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], rich)
    elif edit:
        await edit_rich(message, rich)
    else:
        await send_rich(message, rich)


@router.message(F.text == "🔍 Поиск пользователя")
async def start_user_search(message: Message, state: FSMContext):
    prompt_html = render_bubble_card(
        title="🔎 Поиск сотрудника",
        badge="Ввод данных",
        body_lines=["Отправьте отдельным сообщением <b>логин</b> или <b>ФИО</b> сотрудника."],
        buttons=[button_row(callback_button('❌ Отмена', 'home:main', 'link'))]
    )
    prompt = await send_rich(message, prompt_html)
    await state.update_data(rich_chat_id=prompt.chat.id, rich_message_id=prompt.message_id)
    await state.set_state(UserSearchState.waiting_for_query)


@router.message(UserSearchState.waiting_for_query)
async def process_user_search(message: Message, state: FSMContext):
    query = (message.text or "").strip()
    origin = await state.get_data()
    await state.clear()
    users = ad_service.search_users(query)
    if not users:
        result = render_bubble_card(
            title="🔎 Результаты поиска",
            badge="Не найдено",
            body_lines=[f"По запросу <b>{html_text(query)}</b> совпадений не обнаружено."],
            buttons=[
                button_row(callback_button('🔄 Искать снова', 'home:user', 'primary')),
                button_row(callback_button('🏠 Главное меню', 'home:main', 'link'))
            ]
        )
        if origin.get("rich_chat_id") and origin.get("rich_message_id"):
            await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], result)
        else:
            await send_rich(message, result)
        return

    if len(users) == 1:
        await show_user_profile(message, users[0]["username"], origin=origin)
        return

    rows = [(user['display_name'], f"<code>{html_text(user['username'])}</code>") for user in users[:10]]
    buttons = [
        button_row(callback_button(f"👤 {user['display_name']}"[:46], f"user:show:{user['username']}"))
        for user in users[:8]
    ]
    buttons.append(button_row(callback_button('⬅️ Главное меню', 'home:main', 'link')))

    result = render_bubble_card(
        title="🔎 Найдено несколько совпадений",
        badge=f"Всего: {len(users)}",
        body_lines=["Выберите нужного сотрудника из списка ниже для открытия профиля:"],
        table_rows=rows,
        buttons=buttons,
    )

    if origin.get("rich_chat_id") and origin.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], result)
    else:
        await send_rich(message, result)


@router.callback_query(F.data.startswith("user_select_"))
async def select_user_from_list(callback: CallbackQuery):
    username = callback.data.removeprefix("user_select_")
    await callback.answer()
    await show_user_profile(callback.message, username, edit=True)


@router.callback_query(F.data.startswith("user:"))
async def handle_user_action(callback: CallbackQuery):
    action, _, username = callback.data.removeprefix("user:").partition(":")
    if not username:
        await callback.answer("Не удалось определить пользователя", show_alert=True)
        return

    await callback.answer()
    if action == "show":
        await show_user_profile(callback.message, username, edit=True)
        return

    if action == "pc":
        # Экран ожидания (Bubble Card)
        loading_card = render_bubble_card(
            title="💻 Поиск рабочего места...",
            badge="Диагностика",
            body_lines=[
                f"Проверяем сессию сотрудника <code>{html_text(username)}</code>...",
                "<i>Опрос кэша и пула компьютеров домена в реальном времени.</i>"
            ]
        )
        await edit_rich(callback.message, loading_card)

        found_pc = None
        dns_target = ""

        # Шаг 1: Проверяем сохраненную сессию в локальной БД
        session = get_user_pc_session(username)
        if session:
            cached_pc = session["pc_name"]
            cached_dns = session.get("dns_name") or cached_pc
            is_online = await asyncio.to_thread(check_pc_online, cached_dns)
            if is_online:
                active_user = await asyncio.to_thread(get_pc_logged_user, cached_pc, cached_dns)
                if active_user and active_user.lower() == username.lower():
                    found_pc = cached_pc
                    dns_target = cached_dns

        # Шаг 2: Если в кэше нет или пользователь пересел — опрашиваем онлайн-машины пула
        if not found_pc:
            target_computers = await asyncio.to_thread(ad_service.get_target_computers)
            if target_computers:
                found_pc = await find_pc_by_username_scan(username, target_computers)
                if found_pc:
                    dns_target = f"{found_pc}.{ad_service.domain}"

        if found_pc:
            body = [
                f"👤 <b>Сотрудник:</b> <code>{html_text(username)}</code>",
                f"💻 <b>Компьютер:</b> <b>{html_text(found_pc)}</b>",
                "🟢 <b>Статус:</b> Активная сессия подтверждена"
            ]
            table_rows = [
                ("Имя ПК", f"<code>{html_text(found_pc)}</code>"),
                ("FQDN", f"<code>{html_text(dns_target or found_pc)}</code>"),
                ("Сетевой статус", "🟢 Online (Ping OK)")
            ]
            buttons = [
                button_row(callback_button('💻 Открыть карточку ПК', f'pc:show:{found_pc}', 'primary')),
                button_row(
                    callback_button('👤 К сотруднику', f'user:show:{username}', 'link'),
                    callback_button('🏠 Главное меню', 'home:main', 'link')
                )
            ]
            result_card = render_bubble_card(
                title="💻 Рабочее место обнаружено",
                badge="Сессия активна",
                body_lines=body,
                table_rows=table_rows,
                buttons=buttons
            )
        else:
            result_card = render_bubble_card(
                title="💻 Компьютер не найден",
                badge="Оффлайн",
                body_lines=[
                    f"Сотрудник <code>{html_text(username)}</code> в данный момент не залогинен ни на одной активной машине домена или компьютер выключен."
                ],
                buttons=[
                    button_row(callback_button('🔄 Повторить поиск', f'user:pc:{username}', 'primary')),
                    button_row(
                        callback_button('👤 К сотруднику', f'user:show:{username}', 'link'),
                        callback_button('🏠 Главное меню', 'home:main', 'link')
                    )
                ]
            )
        await edit_rich(callback.message, result_card)
        return

    if action == "password":
        fixed_password = "ASDfg123+"
        success = ad_service.reset_password(username, fixed_password)
        await send_audit_log(callback.bot, callback.from_user, "Сброс пароля", username, status=success)
        
        if success:
            body = [
                f"Пароль пользователя <code>{html_text(username)}</code> успешно сброшен.",
                f"Временный пароль: <code>{html_text(fixed_password)}</code>"
            ]
            badge = "Успешно"
        else:
            body = [f"Не удалось сбросить пароль пользователя <code>{html_text(username)}</code> в Active Directory."]
            badge = "Ошибка"

        buttons = [
            button_row(copy_button('📋 Скопировать пароль', fixed_password)) if success else "",
            button_row(
                callback_button('👤 К карточке', f'user:show:{username}', 'link'),
                callback_button('🏠 Главное меню', 'home:main', 'link')
            )
        ]
        buttons = [b for b in buttons if b]

        card = render_bubble_card(
            title="🔑 Сброс пароля",
            badge=badge,
            body_lines=body,
            footer_note="Рекомендуется сменить пароль при первом входе.",
            buttons=buttons
        )
        await edit_rich(callback.message, card)
        return

    if action == "unlock":
        success = ad_service.unlock_user(username)
        await send_audit_log(callback.bot, callback.from_user, "Разблокировка учётной записи", username, status=success)
        
        result_str = "Учётная запись успешно разблокирована." if success else "Не удалось разблокировать учётную запись."
        badge = "Успешно" if success else "Ошибка"

        card = render_bubble_card(
            title="🔓 Разблокировка аккаунта",
            badge=badge,
            body_lines=[
                f"Пользователь: <code>{html_text(username)}</code>",
                result_str
            ],
            buttons=[
                button_row(
                    callback_button('👤 К карточке', f'user:show:{username}', 'link'),
                    callback_button('🏠 Главное меню', 'home:main', 'link')
                )
            ]
        )
        await edit_rich(callback.message, card)
        return

    if action == "cert":
        details = ad_service.get_user_details(username)
        if not details:
            await edit_rich(callback.message, render_bubble_card(title="❌ Ошибка", body_lines=[f"Пользователь <code>{html_text(username)}</code> не найден."]))
            return
        original_ou = ",".join(details["dn"].split(",")[1:])
        target_ou = "OU=notebooklenovo,OU=Bank Divisions,OU=KDB Users,DC=kdb,DC=kz"
        success = ad_service.move_user(details["dn"], target_ou)
        if success:
            save_original_ou(username, original_ou)
        await send_audit_log(callback.bot, callback.from_user, "Перемещение для выдачи сертификата", username, status=success)
        
        if success:
            body = [
                f"<b>{html_text(details['display_name'])}</b> перемещён в <code>notebooklenovo</code>.",
                "После генерации сертификата нажмите кнопку возврата."
            ]
            buttons = [
                button_row(callback_button('↩️ Вернуть в исходный OU', f'user:revert:{username}', 'success')),
                button_row(callback_button('🏠 Главное меню', 'home:main', 'link'))
            ]
            badge = "Готово к сертификации"
        else:
            body = ["Не удалось переместить пользователя в целевое подразделение AD."]
            buttons = [button_row(callback_button('👤 К карточке', f'user:show:{username}', 'link'))]
            badge = "Ошибка"

        card = render_bubble_card(
            title="📜 Подготовка сертификата",
            badge=badge,
            body_lines=body,
            buttons=buttons
        )
        await edit_rich(callback.message, card)
        return


    if action == "act":
        await callback.answer("Генерация официального PDF-акта...")
        user_det = ad_service.get_user_details(username)
        if not user_det:
            await callback.answer("Ошибка: сотрудник не найден", show_alert=True)
            return

        session = get_user_pc_session(username)
        pc_det = None
        if session and session.get("pc_name"):
            pc_det = ad_service.get_computer_details(session["pc_name"])

        # Генерация PDF
        pdf_buf = await asyncio.to_thread(generate_equipment_act, user_det, pc_det)
        pdf_file = BufferedInputFile(pdf_buf.read(), filename=f"Акт_приема_передачи_{username}.pdf")

        fio = user_det.get("display_name", username)
        pc_caption = f"💻 Компьютер: <code>{pc_det['name']}</code>" if pc_det else "💻 Оборудование: Ноутбук / ПК (по умолчанию)"

        await callback.message.answer_document(
            document=pdf_file,
            caption=f"📄 <b>Официальный Акт приема-передачи оборудования</b>\n\n👤 Сотрудник: <b>{html_text(fio)}</b>\n{pc_caption}\n📅 Дата: {html_text(user_det.get('created_at', ''))[:10]}\n\n<i>Документ готов к печати и подписанию.</i>",
            parse_mode="HTML"
        )
        await send_audit_log(callback.bot, callback.from_user, "Генерация Акта приема-передачи техники", username, status=True)
        return

    if action == "revert":
        original_ou = get_original_ou(username)
        if not original_ou:
            card = render_bubble_card(
                title="⚠️ Возврат отменён",
                badge="Предупреждение",
                body_lines=["Исходный OU не найден в базе данных бота."],
                buttons=[button_row(callback_button('👤 К карточке', f'user:show:{username}', 'link'))]
            )
            await edit_rich(callback.message, card)
            return

        details = ad_service.get_user_details(username)
        success = bool(details and ad_service.move_user(details["dn"], original_ou))
        await send_audit_log(callback.bot, callback.from_user, "Возврат сотрудника в исходный OU", username, status=success)
        if success:
            remove_original_ou(username)

        result_str = "Пользователь возвращён в исходный OU." if success else "Не удалось выполнить возврат."
        badge = "Успешно" if success else "Ошибка"

        card = render_bubble_card(
            title="↩️ Возврат в исходный OU",
            badge=badge,
            body_lines=[
                f"Сотрудник: <code>{html_text(username)}</code>",
                result_str,
                f"Целевой OU: <code>{html_text(original_ou)}</code>"
            ],
            buttons=[
                button_row(
                    callback_button('👤 К карточке', f'user:show:{username}', 'link'),
                    callback_button('🏠 Главное меню', 'home:main', 'link')
                )
            ]
        )
        await edit_rich(callback.message, card)
