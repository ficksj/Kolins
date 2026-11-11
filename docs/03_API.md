# 03. API, Команды и Контракты Взаимодействия

| Параметр | Значение |
|---|---|
| Проект | Kolins · AD Control |
| Версия документа | 1.0 |
| Дата | 2026-11-09 |
| Статус | Утверждён |
| Автор | AI Documentation Agent |

## Содержание
1. [Пользовательский интерфейс Telegram](#1-пользовательский-интерфейс-telegram)
2. [Callback-протокол бота](#2-callback-протокол-бота)
3. [Интеграции с внешними API](#3-интеграции-с-внешними-api)
4. [История изменений документа](#4-история-изменений-документа)

---

## 1. Пользовательский интерфейс Telegram

### 1.1. Команды бота
- `/start` — Инициализация взаимодействия с ботом, проверка прав доступа и отображение главного меню с актуальным статусом подключения к контроллеру домена Active Directory.

### 1.2. Текстовые сообщения
- При переходе в режим поиска (`UserSearchState.waiting_for_query`) бот ожидает текст с ФИО или логином сотрудника (например: `Иванов`, `ivanov_i`, `Дина`).
- При переходе в режим поиска ПК (`PCSearchState.waiting_for_query`) бот ожидает имя компьютера или фрагмент описания (например: `WS-012`, `Бухгалтерия`).
- При создании Zoom-конференции (`ZoomState.waiting_for_topic`) бот ожидает ввод темы или имени сотрудника для названия комнаты.

---

## 2. Callback-протокол бота

Бот использует структурированный префиксный роутинг в `callback_data` (ограничение до 64 байт UTF-8).

| Префикс / Шаблон | Обработчик | Описание действия |
|---|---|---|
| `home:main` | `menu_handlers.py` | Возврат в главное меню |
| `home:user` | `menu_handlers.py` | Переход в режим поиска пользователя (активация FSM) |
| `home:pc` | `menu_handlers.py` | Переход в режим поиска компьютера (активация FSM) |
| `home:stats` | `menu_handlers.py` | Отображение/обновление карточки статистики домена |
| `home:zoom` | `menu_handlers.py` | Запуск диалога создания конференции Zoom |
| `user:show:<username>` | `user_handlers.py` | Отображение подробной карточки сотрудника |
| `user:password:<username>` | `user_handlers.py` | Сброс пароля на `ASDfg123+` |
| `user:unlock:<username>` | `user_handlers.py` | Разблокировка учетной записи (`lockoutTime = 0`) |
| `user:cert:<username>` | `user_handlers.py` | Перемещение в OU `notebooklenovo` для выпуска сертификата |
| `user:revert:<username>` | `user_handlers.py` | Возврат пользователя в сохранённый исходный OU |
| `pc:show:<name>` | `pc_handlers.py` | Карточка ПК с диагностикой (пинг, залогиненный юзер) |
| `pc:move:<name>` | `pc_handlers.py` | Перемещение ПК в подразделение `TARGET_PC_OU` |
| `pc:delete_confirm:<name>` | `pc_handlers.py` | Экран подтверждения удаления учетной записи ПК |
| `pc:delete_do:<name>` | `pc_handlers.py` | Безвозвратное удаление учетной записи ПК из AD |

---

## 3. Интеграции с внешними API

### 3.1. Active Directory (LDAP over TLS / StartTLS)
- **Протокол:** LDAPv3 (порт 389 с переходом в StartTLS или прямой LDAPS 636).
- **Используемые фильтры:**
  - Поиск пользователя: `(&(objectClass=user)(anr={query}))`
  - Поиск ПК: `(&(objectClass=computer)(|(name=*{query}*)(description=*{query}*)))`
  - Статистика пользователей: `(objectClass=user)` с чтением бита `userAccountControl & 2`
- **Операции модификации:**
  - `modify(user_dn, {'unicodePwd': [(MODIFY_REPLACE, [quoted_password])]})`
  - `modify(user_dn, {'lockoutTime': [(MODIFY_REPLACE, [0])]})`
  - `modify_dn(dn, relative_dn, delete_old_dn=True, new_superior=target_ou_dn)`
  - `delete(dn)`

### 3.2. Zoom Server-to-Server OAuth API
- **Endpoint авторизации:** `POST https://zoom.us/oauth/token?grant_type=account_credentials&account_id={ZOOM_ACCOUNT_ID}`
  - Заголовки: `Authorization: Basic Base64(client_id:client_secret)`
  - Ответ: `{ "access_token": "...", "token_type": "bearer", "expires_in": 3600 }`
- **Endpoint создания встречи:** `POST https://api.zoom.us/v2/users/me/meetings`
  - Заголовки: `Authorization: Bearer {access_token}`, `Content-Type: application/json`
  - Тело запроса:
    ```json
    {
      "topic": "Техподдержка AD: <тема>",
      "type": 1,
      "settings": {
        "host_video": true,
        "participant_video": true,
        "join_before_host": true
      }
    }
    ```
  - Ответ: `{ "join_url": "https://zoom.us/j/...", "password": "..." }`

## 4. История изменений документа

| Версия | Дата | Автор | Описание изменений |
|---|---|---|---|
| 1.0 | 2026-11-09 | AI Documentation Agent | Документирование команд, callback-схемы и контрактов внешних API. |
