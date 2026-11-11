# 07. Руководство Разработчика (Development Guide)

| Параметр | Значение |
|---|---|
| Проект | Kolins · AD Control |
| Версия документа | 1.0 |
| Дата | 2026-11-09 |
| Статус | Утверждён |
| Автор | AI Documentation Agent |

## Содержание
1. [Структура репозитория](#1-структура-репозитория)
2. [Стандарты кодирования и стиль](#2-стандарты-кодирования-и-стиль)
3. [Работа с Telegram Rich Message](#3-работа-с-telegram-rich-message)
4. [Добавление нового действия / хэндлера](#4-добавление-нового-действия--хэндлера)
5. [Тестирование и локальная отладка](#5-тестирование-и-локальная-отладка)
6. [История изменений документа](#6-история-изменений-документа)

---

## 1. Структура репозитория

```
Kolins/
├── .env                       # Переменные окружения (локально, не коммитить)
├── config.py                  # Парсинг переменных окружения
├── main.py                    # Инициализация и запуск polling
├── requirements.txt           # Зависимости проекта
├── start.py                   # Вспомогательный скрипт структуры папок
├── handlers/                  # Роутеры aiogram
│   ├── __init__.py            # Экспорт списка роутеров: routers
│   ├── states.py              # Группы FSM состояний
│   ├── menu_handlers.py       # Главное меню, статистика, Zoom
│   ├── user_handlers.py       # Действия над пользователями AD
│   └── pc_handlers.py         # Действия над рабочими станциями
├── middlewares/               # Промежуточное ПО aiogram
│   ├── __init__.py
│   ├── auth_middleware.py     # Проверка прав администратора
│   └── delete_message_middleware.py # Очистка сообщений пользователя
├── services/                  # Бизнес-логика и клиенты API
│   ├── __init__.py
│   ├── ad_service.py          # Active Directory (LDAP3 + NTLM/PureMD4)
│   ├── audit_service.py       # Отправка структурированного аудита
│   ├── db_service.py          # Локальная БД (резерв)
│   ├── logger_service.py      # Настройка логирования
│   ├── pc_monitor_service.py  # Диагностика (ping, PowerShell WMI)
│   ├── print_service.py       # Печатные формы (резерв)
│   ├── rich_message_service.py# Telegram Rich UI утилиты
│   └── zoom_service.py        # Zoom REST API клиент
└── docs/                      # Корпоративная техническая документация
```

---

## 2. Стандарты кодирования и стиль
- **Python 3.12+:** Использование современных аннотаций типов (`str | None`, `dict[str, Any]`), `async/await`.
- **Чистота и безопасность ввода:** Все динамические строки, вставляемые в разметку Rich Message, **обязательно** экранируются вызовом `html_text(val)` во избежание XSS/инъекций и сбоев парсера Telegram.
- **Безопасность LDAP-запросов:** Все пользовательские параметры в фильтрах AD должны экранироваться через `ldap3.utils.conv.escape_filter_chars(query)`.
- **Экранирование для PowerShell:** Имена компьютеров при передаче в PowerShell скрипты экранируют одинарные кавычки (`.replace("'", "''")`).

---

## 3. Работа с Telegram Rich Message

Для отображения интерфейса используются утилиты из `services/rich_message_service.py`:
- `send_rich(message, html)` — отправка новой карточки.
- `edit_rich(message, html)` — редактирование существующей карточки на месте.
- `edit_rich_by_ids(bot, chat_id, message_id, html)` — редактирование по сохраненным координатам (удобно после FSM-ввода текстового сообщения).
- `button_row(*buttons)` и `callback_button(label, data, style)` — создание интерактивных кнопок. Доступные стили: `primary`, `danger`, `success`, `link`.
- Ограничение длины `data` кнопки: строго от 1 до 64 байт.

---

## 4. Добавление нового действия / хэндлера

Для добавления новой операции (например, сброс сессии пользователя):
1. **В `services/ad_service.py`** добавьте метод взаимодействия с LDAP / PowerShell.
2. **В `handlers/user_handlers.py`**:
   - Добавьте кнопку в карточку профиля: `callback_button('Текст', f'user:new_action:{username}')`.
   - В `handle_user_action` обработайте условие:
     ```python
     if action == "new_action":
         result = ad_service.some_method(username)
         await send_audit_log(callback.bot, callback.from_user, "Новое действие", username, status=result)
         await edit_rich(callback.message, f"<h3>Результат</h3>...")
         return
     ```

---

## 5. Тестирование и локальная отладка
- Перед коммитом проверьте синтаксис: `python -m py_compile main.py handlers/*.py services/*.py`.
- Тестовый запуск рекомендуется производить с личным тестовым Telegram-ботом и учетной записью с минимальными привилегиями в тестовом домене AD.

## 6. История изменений документа

| Версия | Дата | Автор | Описание изменений |
|---|---|---|---|
| 1.0 | 2026-11-09 | AI Documentation Agent | Создание руководства разработчика и рекомендаций по расширению. |
