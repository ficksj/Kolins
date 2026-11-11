from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
from services.ad_service import ad_service
from services.audit_service import send_audit_log
from services.rich_message_service import (
    button_row,
    callback_button,
    copy_button,
    edit_rich,
    edit_rich_by_ids,
    html_text,
    render_bubble_card,
    send_rich,
    webapp_button,
)
from services.zoom_service import zoom_service
from handlers.states import PCSearchState, UserSearchState

router = Router()


class ZoomState(StatesGroup):
    waiting_for_topic = State()


def render_home(user_name: str, ad_connected: bool) -> str:
    ad_status = "🟢 Подключено (StartTLS)" if ad_connected else "🔴 Нет соединения"
    server = getattr(config, "AD_SERVER", "192.168.201.15")
    domain = getattr(config, "AD_DOMAIN", "kdb.kz")

    body = [
        f"🌐 <b>Домен:</b> <code>{html_text(domain)}</code> (сервер: <code>{html_text(server)}</code>)",
        f"📡 <b>Каталог AD:</b> {ad_status}",
        f"👤 <b>Администратор:</b> <b>{html_text(user_name)}</b>",
    ]

    buttons = [
        button_row(
            callback_button('👤 Найти сотрудника', 'home:user', 'primary'),
            callback_button('💻 Найти ПК', 'home:pc', 'primary'),
        ),
        button_row(
            callback_button('📊 Сводка домена', 'home:stats', 'link'),
            callback_button('🎥 Создать Zoom', 'home:zoom', 'success'),
        ),
    ]

    return render_bubble_card(
        title="Kolins · Active Directory Hub",
        badge="v2.0",
        body_lines=body,
        footer_note="Выберите действие в панели управления ниже:",
        buttons=buttons,
    )


def render_stats(stats: dict) -> str:
    total_u = stats.get('total_users', 0)
    active_u = stats.get('active_users', 0)
    disabled_u = stats.get('disabled_users', 0)
    total_pc = stats.get('total_computers', 0)

    body = [
        f"👥 <b>Всего учетных записей:</b> <b>{total_u}</b>",
        f"🟢 <b>Активные пользователи:</b> {active_u}",
        f"🔴 <b>Отключенные (Disabled):</b> {disabled_u}",
        f"💻 <b>Компьютеры в домене:</b> {total_pc}",
    ]

    table_rows = [
        ("Пользователи", f"🟢 {active_u} / 🔴 {disabled_u}"),
        ("Рабочие станции", f"💻 {total_pc}"),
        ("Служба каталогов", "Active Directory DS"),
    ]

    buttons = [
        button_row(callback_button('🔄 Обновить метрики', 'home:stats', 'primary')),
        button_row(callback_button('⬅️ Главное меню', 'home:main', 'link')),
    ]

    return render_bubble_card(
        title="📊 Статистика домена",
        badge="Active Directory",
        body_lines=body,
        table_rows=table_rows,
        buttons=buttons,
    )


@router.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    connected = bool(ad_service.conn and ad_service.conn.bound)
    await send_rich(message, render_home(user.full_name or "Администратор", connected))


@router.callback_query(F.data.startswith("home:"))
async def handle_home_action(callback: CallbackQuery, state: FSMContext):
    action = callback.data.removeprefix("home:")
    await callback.answer()

    if action == "user":
        await state.update_data(rich_chat_id=callback.message.chat.id, rich_message_id=callback.message.message_id)
        await state.set_state(UserSearchState.waiting_for_query)
        html = render_bubble_card(
            title="🔎 Поиск сотрудника",
            badge="Режим ввода",
            body_lines=[
                "Отправьте сообщением <b>логин</b> или <b>ФИО</b> сотрудника.",
                "<i>Поддерживается частичный ввод (например: <code>Жанабаева</code> или <code>dzhanab</code>).</i>"
            ],
            buttons=[button_row(callback_button('❌ Отмена', 'home:main', 'link'))]
        )
        await edit_rich(callback.message, html)
        return

    if action == "pc":
        await state.update_data(rich_chat_id=callback.message.chat.id, rich_message_id=callback.message.message_id)
        await state.set_state(PCSearchState.waiting_for_query)
        html = render_bubble_card(
            title="💻 Поиск компьютера",
            badge="Режим ввода",
            body_lines=[
                "Отправьте сообщением <b>сетевое имя</b> или <b>описание ПК</b>.",
                "<i>Пример: <code>7538</code> или <code>N10054</code>.</i>"
            ],
            buttons=[button_row(callback_button('❌ Отмена', 'home:main', 'link'))]
        )
        await edit_rich(callback.message, html)
        return

    if action == "zoom":
        await state.update_data(rich_chat_id=callback.message.chat.id, rich_message_id=callback.message.message_id)
        await state.set_state(ZoomState.waiting_for_topic)
        html = render_bubble_card(
            title="🎥 Новая Zoom-встреча",
            badge="Техподдержка",
            body_lines=[
                "Отправьте сообщением <b>тему конференции</b> или <b>имя сотрудника</b> для подключения."
            ],
            buttons=[button_row(callback_button('❌ Отмена', 'home:main', 'link'))]
        )
        await edit_rich(callback.message, html)
        return

    if action == "stats":
        stats = ad_service.get_domain_stats()
        if not stats:
            await edit_rich(
                callback.message,
                render_bubble_card(
                    title="❌ Ошибка сбора статистики",
                    body_lines=["Не удалось получить актуальные данные из Active Directory."],
                    buttons=[button_row(callback_button('⬅️ Главное меню', 'home:main', 'link'))]
                )
            )
            return
        await edit_rich(callback.message, render_stats(stats))
        return

    if action == "main":
        user = callback.from_user
        connected = bool(ad_service.conn and ad_service.conn.bound)
        await edit_rich(callback.message, render_home(user.full_name or "Администратор", connected))


@router.message(F.text == "📊 Статистика домена")
async def legacy_domain_stats(message: Message):
    stats = ad_service.get_domain_stats()
    if not stats:
        await send_rich(message, "<h3>❌ Не удалось получить статистику из Active Directory.</h3>")
        return
    await send_rich(message, render_stats(stats))


@router.message(F.text == "🎥 Создать Zoom")
async def start_zoom_creation(message: Message, state: FSMContext):
    prompt_html = render_bubble_card(
        title="🎥 Новая Zoom-встреча",
        badge="Создание",
        body_lines=["Отправьте отдельным сообщением <b>тему встречи</b> или <b>ФИО сотрудника</b>."],
        buttons=[button_row(callback_button('❌ Отмена', 'home:main', 'link'))]
    )
    prompt = await send_rich(message, prompt_html)
    await state.update_data(rich_chat_id=prompt.chat.id, rich_message_id=prompt.message_id)
    await state.set_state(ZoomState.waiting_for_topic)


@router.message(ZoomState.waiting_for_topic)
async def process_zoom_creation(message: Message, state: FSMContext):
    topic = (message.text or "").strip()
    origin = await state.get_data()
    await state.clear()
    meeting = await zoom_service.create_meeting(topic=topic)
    if not meeting:
        await send_audit_log(message.bot, message.from_user, "Создание Zoom-встречи", topic, status=False)
        failure = render_bubble_card(
            title="❌ Ошибка создания Zoom",
            body_lines=["Не удалось инициализировать конференцию через Zoom API."],
            buttons=[button_row(callback_button('🏠 Главное меню', 'home:main', 'link'))]
        )
        if origin.get("rich_chat_id") and origin.get("rich_message_id"):
            await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], failure)
        else:
            await send_rich(message, failure)
        return

    await send_audit_log(message.bot, message.from_user, "Создание Zoom-встречи", topic, status=True)

    body = [
        f"🎯 <b>Тема:</b> {html_text(topic)}",
        f"🔑 <b>Пароль:</b> <code>{html_text(meeting['password'])}</code>",
        f"🔗 <b>Прямая ссылка:</b> <a href=\"{html_text(meeting['join_url'])}\">Подключиться к комнате</a>"
    ]

    buttons = [
        button_row(copy_button('📋 Скопировать ссылку', meeting['join_url'])),
        button_row(callback_button('🏠 Главное меню', 'home:main', 'link'))
    ]

    rich = render_bubble_card(
        title="🎥 Zoom-конференция готова",
        badge="Успешно",
        body_lines=body,
        buttons=buttons
    )

    if origin.get("rich_chat_id") and origin.get("rich_message_id"):
        await edit_rich_by_ids(message.bot, origin["rich_chat_id"], origin["rich_message_id"], rich)
    else:
        await send_rich(message, rich)
