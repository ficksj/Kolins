import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.states import PCSearchState
from services.ad_service import ad_service
from services.audit_service import send_audit_log
from services.logger_service import get_logger
from services.pc_monitor_service import check_pc_online, get_pc_logged_user, get_pc_diagnostics, reboot_remote_pc
from services.rich_message_service import render_progress_bar
from services.rich_message_service import (
    button_row,
    callback_button,
    edit_rich,
    edit_rich_by_ids,
    html_text,
    render_bubble_card,
    send_rich,
)

logger = get_logger("PCHandlers")
router = Router()

TARGET_PC_OU = "OU=KDB notebooklenovo,OU=KDB Nout,DC=kdb,DC=kz"


def render_pc_profile(details: dict, is_online: bool, logged_user: str | None) -> str:
    name = details["name"]
    status_badge = "🟢 В сети" if is_online else "🔴 Оффлайн"
    user_info = f"<code>{html_text(logged_user)}</code>" if logged_user else "—"

    body = [
        f"<b>{html_text(name)}</b> · <code>{html_text(details['dns_name'])}</code>",
        f"Сетевая доступность: <b>{status_badge}</b>",
        f"👤 <b>Активный пользователь:</b> {user_info}",
    ]

    table_rows = [
        ("Операционная система", f"{html_text(details['os'])} {html_text(details['os_version'])}"),
        ("Описание", html_text(details['description'])),
        ("Дата регистрации", html_text(details['created'])),
    ]

    details_content = f"<b>Distinguished Name:</b><br><code>{html_text(details['dn'])}</code>"

    buttons = [
        button_row(
            callback_button('📊 Диагностика ПК', f'pc:diag:{name}', 'primary'),
            callback_button('📦 В notebooklenovo', f'pc:move:{name}', 'primary'),
        ),
        button_row(
            callback_button('🔄 Перезагрузить', f'pc:reboot_confirm:{name}', 'danger'),
            callback_button('🗑 Удалить из AD', f'pc:delete_confirm:{name}', 'danger'),
        ),
        button_row(
            callback_button('🔄 Обновить статус', f'pc:show:{name}', 'link'),
            callback_button('⬅️ Главное меню', 'home:main', 'link'),
        ),
    ]

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
    details = ad_service.get_computer_details(pc_name)
    if not details:
        card = render_bubble_card(
            title="❌ Компьютер не найден",
            badge="Ошибка",
            body_lines=[f"Компьютер <code>{html_text(pc_name)}</code> не найден в каталоге Active Directory."],
            buttons=[button_row(callback_button('🏠 Главное меню', 'home:main', 'link'))]
        )
    else:
        dns_name = details.get("dns_name", pc_name)
        if dns_name != "—":
            is_online, logged_user = await asyncio.gather(
                asyncio.to_thread(check_pc_online, dns_name),
                asyncio.to_thread(get_pc_logged_user, pc_name, dns_name),
            )
        else:
            is_online, logged_user = False, None
        card = render_pc_profile(details, is_online, logged_user)

    if origin and origin.get("rich_chat_id") and origin.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], card)
    elif edit:
        await edit_rich(message, card)
    else:
        await send_rich(message, card)


@router.message(F.text == "💻 Поиск ПК")
async def start_pc_search(message: Message, state: FSMContext):
    prompt_card = render_bubble_card(
        title="💻 Поиск компьютера",
        badge="Ввод данных",
        body_lines=["Отправьте отдельным сообщением <b>сетевое имя</b> или <b>описание ПК</b>."],
        buttons=[button_row(callback_button('❌ Отмена', 'home:main', 'link'))]
    )
    prompt = await send_rich(message, prompt_card)
    await state.update_data(rich_chat_id=prompt.chat.id, rich_message_id=prompt.message_id)
    await state.set_state(PCSearchState.waiting_for_query)


@router.message(PCSearchState.waiting_for_query)
async def process_pc_search(message: Message, state: FSMContext):
    query = (message.text or "").strip()
    origin = await state.get_data()
    await state.clear()
    computers = ad_service.search_computers(query)
    if not computers:
        card = render_bubble_card(
            title="💻 Результаты поиска",
            badge="Не найдено",
            body_lines=[f"По запросу <b>{html_text(query)}</b> компьютеров не найдено."],
            buttons=[
                button_row(callback_button('🔄 Искать снова', 'home:pc', 'primary')),
                button_row(callback_button('🏠 Главное меню', 'home:main', 'link'))
            ]
        )
        if origin.get("rich_chat_id") and origin.get("rich_message_id"):
            await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], card)
        else:
            await send_rich(message, card)
        return

    table_rows = [(f"<code>{html_text(pc['Name'])}</code>", html_text(pc['Description'])) for pc in computers[:10]]
    buttons = [
        button_row(callback_button(f"💻 {pc['Name']}"[:46], f"pc:show:{pc['Name']}"))
        for pc in computers[:8]
    ]
    buttons.append(button_row(callback_button('⬅️ Главное меню', 'home:main', 'link')))

    card = render_bubble_card(
        title="💻 Компьютеры в AD",
        badge=f"Найдено: {len(computers)}",
        body_lines=["Выберите рабочую станцию из списка для диагностики и управления:"],
        table_rows=table_rows,
        buttons=buttons
    )

    if origin.get("rich_chat_id") and origin.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], card)
    else:
        await send_rich(message, card)


@router.callback_query(F.data.startswith("pc:"))
async def handle_pc_action(callback: CallbackQuery, state: FSMContext):
    action, _, value = callback.data.removeprefix("pc:").partition(":")
    await callback.answer()

    if action == "show":
        await show_pc_profile(callback.message, value, edit=True)
        return


    if action == "diag":
        await callback.answer("Опрос WMI/системных параметров ПК...")
        details = ad_service.get_computer_details(value)
        dns_name = details.get("dns_name", value) if details else value
        diag = await asyncio.to_thread(get_pc_diagnostics, value, dns_name)

        if not diag:
            card = render_bubble_card(
                title="⚠️ Диагностика недоступна",
                badge="Оффлайн / WMI ошибка",
                body_lines=[
                    f"Компьютер <code>{html_text(value)}</code> недоступен по сети или удаленный WMI заблокирован брандмауэром Windows.",
                    "Убедитесь, что машина включена и находится в локальной сети Банка."
                ],
                buttons=[
                    button_row(
                        callback_button('🔄 Повторить опрос', f'pc:diag:{value}', 'primary'),
                        callback_button('💻 К карточке ПК', f'pc:show:{value}', 'link'),
                    )
                ]
            )
            await edit_rich(callback.message, card)
            return

        body = [
            f"💻 <b>Компьютер:</b> <code>{html_text(value)}</code>",
            f"⏱ <b>Время непрерывной работы (Uptime):</b> {html_text(diag.get('uptime', '—'))}",
            f"🧠 <b>Оперативная память (RAM):</b> {diag.get('ram_used_gb', 0)} ГБ / {diag.get('ram_total_gb', 0)} ГБ ({diag.get('ram_percent', 0)}%)",
            render_progress_bar(diag.get('ram_percent', 0), length=12),
        ]

        table_rows = []
        for disk in diag.get("disks", []):
            dev = disk.get("device", "Диск")
            pct = disk.get("percent", 0)
            free = disk.get("free_gb", 0)
            total = disk.get("total_gb", 0)
            bar = render_progress_bar(pct, length=8)
            table_rows.append((f"Диск {dev}", f"{bar}<br>{free} ГБ свободно из {total} ГБ"))

        card = render_bubble_card(
            title="📊 Системная диагностика ПК",
            badge="Телеметрия WMI",
            body_lines=body,
            table_rows=table_rows,
            footer_note="Данные получены напрямую из операционной системы.",
            buttons=[
                button_row(
                    callback_button('🔄 Перезагрузить ПК', f'pc:reboot_confirm:{value}', 'danger'),
                    callback_button('🔄 Обновить', f'pc:diag:{value}', 'link'),
                ),
                button_row(callback_button('💻 К карточке ПК', f'pc:show:{value}', 'link')),
            ]
        )
        await edit_rich(callback.message, card)
        return

    if action == "reboot_confirm":
        card = render_bubble_card(
            title="🔄 Подтверждение перезагрузки",
            badge="Внимание",
            body_lines=[
                f"Вы уверены, что хотите принудительно перезагрузить компьютер <code>{html_text(value)}</code>?",
                "Все несохраненные данные пользователя на машине будут закрыты."
            ],
            buttons=[
                button_row(callback_button('⚠️ Да, перезагрузить', f'pc:reboot_do:{value}', 'danger')),
                button_row(callback_button('❌ Отмена', f'pc:show:{value}', 'link'))
            ]
        )
        await edit_rich(callback.message, card)
        return

    if action == "reboot_do":
        await callback.answer("Отправка команды на перезагрузку...")
        details = ad_service.get_computer_details(value)
        dns_name = details.get("dns_name", value) if details else value
        success = await asyncio.to_thread(reboot_remote_pc, value, dns_name)
        await send_audit_log(
            callback.bot, callback.from_user,
            "Удаленная перезагрузка ПК", value, status=success
        )
        badge = "Выполнено" if success else "Ошибка"
        res_text = f"Команда перезагрузки успешно доставлена на <code>{html_text(value)}</code>." if success else f"Не удалось перезагрузить <code>{html_text(value)}</code> (ПК оффлайн или запрещен удаленный RPC)."
        
        card = render_bubble_card(
            title="🔄 Результат перезагрузки",
            badge=badge,
            body_lines=[res_text],
            buttons=[
                button_row(
                    callback_button('💻 К карточке ПК', f'pc:show:{value}', 'link'),
                    callback_button('🏠 Главное меню', 'home:main', 'link')
                )
            ]
        )
        await edit_rich(callback.message, card)
        return

    if action == "move":
        success = ad_service.move_computer(value, TARGET_PC_OU)
        await send_audit_log(
            callback.bot, callback.from_user,
            "Перемещение ПК", f"{value} -> KDB notebooklenovo", status=success
        )
        badge = "Успешно" if success else "Ошибка"
        result_text = "Компьютер успешно перемещён в целевое подразделение." if success else "Не удалось переместить компьютер."

        card = render_bubble_card(
            title="📦 Перемещение завершено",
            badge=badge,
            body_lines=[
                result_text,
                f"Целевой контейнер: <code>{html_text(TARGET_PC_OU)}</code>"
            ],
            buttons=[
                button_row(callback_button('💻 К карточке ПК', f'pc:show:{value}', 'link')),
                button_row(callback_button('🏠 Главное меню', 'home:main', 'link'))
            ]
        )
        await edit_rich(callback.message, card)
        return

    if action == "delete_confirm":
        card = render_bubble_card(
            title="🗑 Подтверждение удаления",
            badge="Осторожно",
            body_lines=[
                f"Вы действительно хотите удалить учетную запись <code>{html_text(value)}</code> из Active Directory?",
                "<b>Внимание:</b> действие необратимо. Рабочая станция потеряет доверительные отношения с доменом."
            ],
            buttons=[
                button_row(callback_button('⚠️ Да, удалить компьютер', f'pc:delete_do:{value}', 'danger')),
                button_row(callback_button('❌ Отмена', f'pc:show:{value}', 'link'))
            ]
        )
        await edit_rich(callback.message, card)
        return

    if action == "delete_do":
        success = ad_service.delete_computer(value)
        await send_audit_log(
            callback.bot, callback.from_user,
            "Удаление ПК из домена", value, status=success
        )
        badge = "Успешно" if success else "Ошибка"
        result_text = f"Компьютер <code>{html_text(value)}</code> удален из каталога AD." if success else f"Не удалось удалить <code>{html_text(value)}</code>."

        card = render_bubble_card(
            title="🗑 Результат удаления",
            badge=badge,
            body_lines=[result_text],
            buttons=[button_row(callback_button('🏠 Главное меню', 'home:main', 'link'))]
        )
        await edit_rich(callback.message, card)
        return
