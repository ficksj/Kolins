# 02. Модули и компоненты системы Kolins

| Параметр | Значение |
|---|---|
| Проект | Kolins · AD Control |
| Версия документа | 1.0 |
| Дата | 2026-11-09 |
| Статус | Утверждён |
| Автор | AI Documentation Agent |

## Содержание
1. [Модуль инициализации (main.py, start.py, config.py)](#1-модуль-инициализации)
2. [Слой промежуточного ПО (Middlewares)](#2-слой-промежуточного-по-middlewares)
3. [Слой обработчиков (Handlers)](#3-слой-обработчиков-handlers)
4. [Служебные сервисы (Services)](#4-служебные-сервисы-services)
5. [Неиспользуемые и архивные компоненты](#5-неиспользуемые-и-архивные-компоненты)
6. [История изменений документа](#6-история-изменений-документа)

---

## 1. Модуль инициализации

### 1.1. `main.py`
- **Зона ответственности:** Корневая точка входа. Настраивает экземпляр `Bot` с `parse_mode=ParseMode.HTML`, подключает миддлвары, регистрирует роутеры и запускает `dp.start_polling(bot)`.
- **Ключевые функции:** `main()` ([main.py](file:///c:/Users/kbeletskiy/Documents/Kolins/main.py#L14-L45)).
- **Зависимости:** `aiogram`, `config`, `handlers.routers`, `middlewares.auth_middleware.AdminAuthMiddleware`, `middlewares.delete_message_middleware.DeleteUserMessageMiddleware`.

### 1.2. `config.py`
- **Зона ответственности:** Загрузка и первичная валидация параметров среды из `.env`.
- **Переменные:**
  - `BOT_TOKEN`: токен Telegram-бота.
  - `AD_SERVER`, `AD_DOMAIN`, `AD_USER`, `AD_PASSWORD`, `AD_SEARCH_BASE`: параметры доступа к контроллеру домена.
  - `LOG_CHAT_ID`: идентификатор группы/канала аудита (числовой).
  - `ADMIN_IDS`: список Telegram User ID администраторов, которым разрешён доступ к боту.

### 1.3. `start.py`
- **Зона ответственности:** Вспомогательный скрипт генерации структуры пустых папок и файлов проекта.
- **Статус:** Вспомогательный утилитарный скрипт для начальной инициализации директорий.

---

## 2. Слой промежуточного ПО (Middlewares)

### 2.1. `middlewares/auth_middleware.py` (`AdminAuthMiddleware`)
- **Назначение:** Ограничение доступа к функционалу бота. Проверяет `event.from_user.id` по списку `config.ADMIN_IDS`.
- **Логика:**
  - Если пользователь отсутствует в `ADMIN_IDS`, генерируется `logger.warning`.
  - Для `Message` отправляется ответ: `⛔ У вас нет прав для работы с этим ботом.`
  - Для `CallbackQuery` возвращается алерт: `⛔ Доступ запрещен.`
  - При отсутствии ограничений (список пуст) или совпадении ID запрос передаётся дальше по цепочке.

### 2.2. `middlewares/delete_message_middleware.py` (`DeleteUserMessageMiddleware`)
- **Назначение:** Автоматическое удаление пользовательских текстовых сообщений (например, поисковых запросов) после их обработки.
- **Логика:** Вызывает следующий хэндлер `await handler(event, data)`, после чего производит `await event.delete()`. В случае отсутствия прав у бота ошибка отлавливается и пишется в `logger.debug`.

---

## 3. Слой обработчиков (Handlers)

### 3.1. `handlers/menu_handlers.py`
- **Назначение:** Главное навигационное меню, статистика домена, создание конференций Zoom.
- **Обрабатываемые события:**
  - Команда `/start` -> функция `cmd_start` (отрисовка стартового меню [render_home](file:///c:/Users/kbeletskiy/Documents/Kolins/handlers/menu_handlers.py#L28-L39)).
  - Callback-запросы `home:*` (`home:user`, `home:pc`, `home:zoom`, `home:stats`, `home:main`).
  - Состояние `ZoomState.waiting_for_topic` -> вызов `zoom_service.create_meeting` и отправка аудита в `send_audit_log`.

### 3.2. `handlers/user_handlers.py`
- **Назначение:** Поиск сотрудников в Active Directory, вывод карточки сотрудника, сброс паролей, разблокировка и перемещение по OU.
- **Обрабатываемые события:**
  - FSM-поиск `UserSearchState.waiting_for_query` -> `ad_service.search_users(query)`. При одном совпадении сразу выводит карточку, при нескольких — компактную таблицу с кнопками выбора.
  - Callback-запросы `user:*`:
    - `user:show:<username>`: отображение карточки профиля.
    - `user:password:<username>`: сброс пароля на фиксированный временный (`ASDfg123+`) с отправкой события в аудит.
    - `user:unlock:<username>`: разблокировка аккаунта.
    - `user:cert:<username>`: перемещение сотрудника в OU подготовки сертификатов (`OU=notebooklenovo,OU=Bank Divisions,OU=KDB Users,DC=kdb,DC=kz`) с сохранением исходного OU в словаре `USER_ORIGINAL_OU`.
    - `user:revert:<username>`: возврат сотрудника в исходное подразделение из кэша `USER_ORIGINAL_OU`.

### 3.3. `handlers/pc_handlers.py`
- **Назначение:** Поиск компьютеров, вывод карточки ПК с диагностикой (пинг, залогиненный пользователь), перемещение и удаление.
- **Обрабатываемые события:**
  - FSM-поиск `PCSearchState.waiting_for_query` -> `ad_service.search_computers(query)`.
  - Callback-запросы `pc:*`:
    - `pc:show:<name>`: параллельный запуск проверки доступности (`check_pc_online`) и текущей сессии (`get_pc_logged_user`) через `asyncio.gather(asyncio.to_thread(...))`.
    - `pc:move:<name>`: перемещение рабочей станции в `OU=KDB notebooklenovo,OU=KDB Nout,DC=kdb,DC=kz`.
    - `pc:delete_confirm:<name>`: вывод диалога подтверждения безвозвратного удаления ПК.
    - `pc:delete_do:<name>`: вызов `ad_service.delete_computer` и аудит действия.

### 3.4. `handlers/states.py`
- Содержит группы состояний `aiogram.fsm`: `UserSearchState` и `PCSearchState`.

---

## 4. Служебные сервисы (Services)

### 4.1. `services/ad_service.py` (`ActiveDirectoryService`)
- Полнофункциональный клиент взаимодействия с AD через библиотеку `ldap3`.
- **Встроенный PureMD4:** Обеспечивает обратную совместимость алгоритма NTLM-аутентификации с Python 3.12/3.14.
- **Ключевые методы:**
  - `connect()`: подключение через NTLM или StartTLS+SIMPLE.
  - `search_users(query)`: поиск через ANR `(&(objectClass=user)(anr=...))` с фолбэком по частям имени `name=*...*`.
  - `get_user_details(username)`: выборка расширенных атрибутов (`userAccountControl`, `mail`, `department`, `physicalDeliveryOfficeName`, `scriptPath`, `logonWorkstation`).
  - `reset_password(username, new_password)`: смена `unicodePwd` через UTF-16LE в StartTLS-сессии.
  - `unlock_user(username)`: установка `lockoutTime = 0`.
  - `move_user(user_dn, target_ou_dn)` / `move_computer(pc_name, target_ou_dn)`: перемещение объектов по дереву каталога с сохранением RDN.
  - `delete_computer(pc_name)`: удаление объекта компьютера.
  - `get_domain_stats()`: подсчет общего, активного, отключенного числа пользователей и компьютеров.

### 4.2. `services/pc_monitor_service.py`
- Запуск диагностических команд операционной системы:
  - `check_pc_online(dns_name)`: вызов утилиты `ping -n 1 -w 500`.
  - `get_pc_logged_user(pc_name)`: вызов скрипта PowerShell с запросом WMI-класса `Win32_ComputerSystem.UserName`.
  - `check_pc_critical_events(pc_name)`: опрос журнала событий `System` на наличие критических записей (уровни 1, 2) за последний час.

### 4.3. `services/zoom_service.py` (`ZoomService`)
- Интеграция с Zoom REST API:
  - `_get_access_token()`: получение bearer-токена по OAuth grant `account_credentials`.
  - `create_meeting(topic)`: создание мгновенной конференции (`type: 1`) с возвратом ссылки `join_url` и пароля. Предусмотрен фолбэк для изолированных сетей.

### 4.4. `services/audit_service.py`
- Формирование Rich-таблицы с метаданными операции: ФИО и username администратора, тип действия, целевой объект (`target`), статус выполнения (✅ / ❌). Отправка сообщения в `LOG_CHAT_ID`.

### 4.5. `services/rich_message_service.py`
- Модуль форматирования под Telegram Rich Message: экранирование спецсимволов HTML (`html_text`), генерация тегов `<tg-button>`, `<tg-button-row>`, отправка и редактирование сообщений.

### 4.6. `services/logger_service.py`
- Стандартизированный `logging.StreamHandler` с форматированием времени, уровня и имени модуля.

---

## 5. Неиспользуемые и архивные компоненты
- **`services/db_service.py`:** Содержит скелет функции `init_db()` для SQLite (`database.db`), но нигде в хэндлерах или сервисах не используется.
- **`services/print_service.py`:** Заглушка генерации печатной формы (`generate_print_form`), логика отсутствует.

## 6. История изменений документа

| Версия | Дата | Автор | Описание изменений |
|---|---|---|---|
| 1.0 | 2026-11-09 | AI Documentation Agent | Подробное описание всех модулей, классов и зон ответственности. |
