# 05. Конфигурация и Переменные Окружения

| Параметр | Значение |
|---|---|
| Проект | Kolins · AD Control |
| Версия документа | 1.0 |
| Дата | 2026-11-09 |
| Статус | Утверждён |
| Автор | AI Documentation Agent |

## Содержание
1. [Файл конфигурации (.env)](#1-файл-конфигурации-env)
2. [Таблица параметров окружения](#2-таблица-параметров-окружения)
3. [Пример безопасного шаблона (.env.example)](#3-пример-безопасного-шаблона-envexample)
4. [История изменений документа](#4-история-изменений-документа)

---

## 1. Файл конфигурации (.env)

Загрузка переменных производится в файле [config.py](file:///c:/Users/kbeletskiy/Documents/Kolins/config.py) через библиотеку `python-dotenv`.

> [!CAUTION]
> Файл `.env` содержит чувствительные корпоративные данные (доменный пароль сервисной учетной записи AD, токен Telegram-бота).
> Файл `.env` **не должен** попадать в систему контроля версий Git. Рекомендуется добавить `.env` в `.gitignore`.

---

## 2. Таблица параметров окружения

| Переменная | Обязательная | Значение по умолчанию | Назначение |
|---|---|---|---|
| `BOT_TOKEN` | Да | — | API-токен Telegram-бота, полученный у `@BotFather`. |
| `AD_SERVER` | Да | `192.168.201.15` | IP-адрес или DNS-имя контроллера домена Active Directory. |
| `AD_DOMAIN` | Да | `kdb.kz` | FQDN обслуживаемого домена Windows. |
| `AD_USER` | Да | — | Имя сервисной учетной записи с правами делегирования в AD (UPN или `DOMAIN\user`). |
| `AD_PASSWORD` | Да | — | Пароль сервисной учетной записи в Active Directory. |
| `AD_SEARCH_BASE`| Нет | `DC=kdb,DC=kz` | Базовый DN для поиска объектов в каталоге LDAP. |
| `LOG_CHAT_ID` | Нет | `None` | Числовой ID чата/канала Telegram для отправки аудит-логов действий. |
| `ADMIN_IDS` | Нет | `""` | Список Telegram User ID администраторов через запятую (например: `12345678,98765432`). |
| `ZOOM_ACCOUNT_ID` | Опционально | `""` | Account ID для Zoom Server-to-Server OAuth приложения. |
| `ZOOM_CLIENT_ID` | Опционально | `""` | Client ID Zoom приложения. |
| `ZOOM_CLIENT_SECRET` | Опционально | `""` | Client Secret Zoom приложения. |

---

## 3. Пример безопасного шаблона (.env.example)

```env
# Telegram Bot
BOT_TOKEN=0000000000:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789,987654321
LOG_CHAT_ID=-1001234567890

# Active Directory
AD_SERVER=192.168.201.15
AD_DOMAIN=kdb.kz
AD_USER=svc_kolins@kdb.kz
AD_PASSWORD=***
AD_SEARCH_BASE=DC=kdb,DC=kz

# Zoom API (опционально)
ZOOM_ACCOUNT_ID=
ZOOM_CLIENT_ID=
ZOOM_CLIENT_SECRET=
```

## 4. История изменений документа

| Версия | Дата | Автор | Описание изменений |
|---|---|---|---|
| 1.0 | 2026-11-09 | AI Documentation Agent | Полное описание всех конфигурационных параметров и шаблона окружения. |
