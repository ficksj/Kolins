# 01. Архитектура системы Kolins

| Параметр | Значение |
|---|---|
| Проект | Kolins · AD Control |
| Версия документа | 1.0 |
| Дата | 2026-11-09 |
| Статус | Утверждён |
| Автор | AI Documentation Agent |

## Содержание
1. [Общая архитектурная схема](#1-общая-архитектурная-схема)
2. [Компоненты системы](#2-компоненты-системы)
3. [Потоки данных и обработка запросов](#3-потоки-данных-и-обработка-запросов)
4. [Механизм аутентификации в Active Directory](#4-механизм-аутентификации-в-active-directory)
5. [История изменений документа](#5-история-изменений-документа)

---

## 1. Общая архитектурная схема

```mermaid
flowchart TD
    User([Telegram Admin]) -->|Команды / Кнопки / Текст| TG[Telegram Bot API]
    TG -->|Long Polling| BotApp[Aiogram 3 Dispatcher]
    
    subgraph Middlewares[Слой Middlewares]
        BotApp --> AuthMW[AdminAuthMiddleware\nПроверка ADMIN_IDS]
        AuthMW --> DelMW[DeleteUserMessageMiddleware\nОчистка пользовательского ввода]
    end
    
    subgraph Handlers[Слой маршрутизации]
        DelMW --> MenuH[menu_handlers: /start, home:*, stats, zoom]
        DelMW --> UserH[user_handlers: user:*, UserSearchState]
        DelMW --> PcH[pc_handlers: pc:*, PCSearchState]
    end
    
    subgraph Services[Слой бизнес-логики и интеграций]
        UserH & MenuH & PcH --> ADServ[ad_service.py\nActiveDirectoryService]
        PcH --> PCMon[pc_monitor_service.py\nPing & WMI PowerShell]
        MenuH --> ZoomServ[zoom_service.py\nOAuth Zoom API]
        UserH & PcH & MenuH --> AuditServ[audit_service.py\nRich Telegram Audit]
        UserH & PcH & MenuH & AuditServ --> RichServ[rich_message_service.py\nФорматирование HTML / Кнопки]
        ADServ & PCMon & AuditServ --> LogServ[logger_service.py\nКонсольный логгер]
    end
    
    subgraph External[Внешние системы и инфраструктура]
        ADServ -->|LDAP 389 / StartTLS / NTLM / SIMPLE| DC[(Active Directory Domain Controller)]
        PCMon -->|ICMP Ping / WMI Win32_ComputerSystem| Workstations[(Рабочие станции Windows)]
        ZoomServ -->|HTTPS OAuth & API v2| ZoomCloud[(Zoom Cloud API)]
        AuditServ -->|send_rich_message| LogChat[Telegram Чат Аудита LOG_CHAT_ID]
    end
```

## 2. Компоненты системы

| Модуль / Слой | Назначение | Ключевые файлы |
|---|---|---|
| **Точка входа** | Инициализация бота, регистрация миддлварей и роутеров, запуск `start_polling` | [main.py](file:///c:/Users/kbeletskiy/Documents/Kolins/main.py#L1-L50) |
| **Конфигурация** | Чтение `.env`, парсинг списков ID, настройка базового DN | [config.py](file:///c:/Users/kbeletskiy/Documents/Kolins/config.py#L1-L14) |
| **Middlewares** | Фильтрация неавторизованных пользователей и очистка входящих сообщений | [middlewares/auth_middleware.py](file:///c:/Users/kbeletskiy/Documents/Kolins/middlewares/auth_middleware.py#L1-L28)<br>[middlewares/delete_message_middleware.py](file:///c:/Users/kbeletskiy/Documents/Kolins/middlewares/delete_message_middleware.py#L1-L26) |
| **Обработчики (Handlers)** | Обработка текстовых команд, callback-кнопок, управление состояниями FSM | [handlers/menu_handlers.py](file:///c:/Users/kbeletskiy/Documents/Kolins/handlers/menu_handlers.py#L1-L148)<br>[handlers/user_handlers.py](file:///c:/Users/kbeletskiy/Documents/Kolins/handlers/user_handlers.py#L1-L198)<br>[handlers/pc_handlers.py](file:///c:/Users/kbeletskiy/Documents/Kolins/handlers/pc_handlers.py#L1-L166)<br>[handlers/states.py](file:///c:/Users/kbeletskiy/Documents/Kolins/handlers/states.py#L1-L9) |
| **Active Directory Сервис** | Работа с каталогом LDAP (поиск, смена пароля, перемещение, статус) | [services/ad_service.py](file:///c:/Users/kbeletskiy/Documents/Kolins/services/ad_service.py#L1-L439) |
| **PC Мониторинг** | Асинхронный запуск системных утилит (Ping, WMI PowerShell) | [services/pc_monitor_service.py](file:///c:/Users/kbeletskiy/Documents/Kolins/services/pc_monitor_service.py#L1-L70) |
| **Zoom Сервис** | Взаимодействие с Zoom REST API по протоколу Server-to-Server OAuth | [services/zoom_service.py](file:///c:/Users/kbeletskiy/Documents/Kolins/services/zoom_service.py#L1-L72) |
| **Сервис аудита** | Отправка форматированных отчетов об административных действиях | [services/audit_service.py](file:///c:/Users/kbeletskiy/Documents/Kolins/services/audit_service.py#L1-L34) |
| **Rich Message Сервис** | Формирование Telegram Rich HTML (кнопки, таблицы, спойлеры) | [services/rich_message_service.py](file:///c:/Users/kbeletskiy/Documents/Kolins/services/rich_message_service.py#L1-L85) |

## 3. Потоки данных и обработка запросов

### 3.1. Сценарий: Поиск сотрудника и сброс пароля

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Администратор
    participant TG as Telegram Bot
    participant MW as Middlewares
    participant Handler as user_handlers
    participant AD as ad_service (LDAP)
    participant Audit as audit_service
    participant LogChat as Чат Аудита

    Admin->>TG: Нажатие кнопки "Пользователь"
    TG->>MW: Callback 'home:user'
    MW->>Handler: Проверка ADMIN_IDS (OK)
    Handler->>Admin: Перевод в UserSearchState, запрос ФИО/логина
    Admin->>TG: Отправка "Иванов"
    TG->>MW: Message "Иванов"
    MW->>Handler: handle UserSearchState.waiting_for_query
    MW-->>TG: DeleteUserMessageMiddleware удаляет сообщение "Иванов"
    Handler->>AD: search_users("Иванов") via ANR
    AD-->>Handler: Список совпадений
    Handler->>Admin: Отображение карточки сотрудника с кнопками
    Admin->>TG: Нажатие кнопки "🔑 Сбросить пароль"
    TG->>Handler: Callback 'user:password:ivanov_i'
    Handler->>AD: reset_password("ivanov_i", "ASDfg123+")
    AD->>AD: StartTLS + ldap3.MODIFY_REPLACE unicodePwd
    AD-->>Handler: True (успех)
    Handler->>Audit: send_audit_log("Сброс пароля", "ivanov_i", True)
    Audit->>LogChat: Отправка Rich HTML карточки аудита
    Handler->>Admin: Обновление карточки в чате с временным паролем
```

## 4. Механизм аутентификации в Active Directory
В связи с тем, что в современных версиях Python (3.12+ и особенно 3.14) алгоритм хеширования MD4 объявлен устаревшим и может отсутствовать в OpenSSL, в [services/ad_service.py](file:///c:/Users/kbeletskiy/Documents/Kolins/services/ad_service.py#L9-L86) встроена чистая реализация `PureMD4`, патчащая `hashlib.new("md4")`.

Схема подключения к контроллеру домена реализует 2-ступенчатый отказоустойчивый алгоритм:
1. **Первичная попытка:** подключение через `ldap3.NTLM` на порт 389.
2. **Вторичная попытка (Fallback):** если NTLM не проходит, устанавливается соединение `ldap3.SIMPLE` в связке с принудительным `start_tls()`.
3. **Keep-Alive & Reconnect:** перед каждым поиском и модификацией вызывается `_safe_search` и `ensure_connection`, повторно поднимающие сокет при разрыве сетевой сессии.

## 5. История изменений документа

| Версия | Дата | Автор | Описание изменений |
|---|---|---|---|
| 1.0 | 2026-11-09 | AI Documentation Agent | Создание архитектурного описания, диаграмм и потоков данных. |
