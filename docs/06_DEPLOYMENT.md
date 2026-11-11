# 06. Развёртывание и Эксплуатация

| Параметр | Значение |
|---|---|
| Проект | Kolins · AD Control |
| Версия документа | 1.0 |
| Дата | 2026-11-09 |
| Статус | Утверждён |
| Автор | AI Documentation Agent |

## Содержание
1. [Системные требования](#1-системные-требования)
2. [Установка и запуск в окружении Windows](#2-установка-и-запуск-в-окружении-windows)
3. [Настройка сервисной учетной записи в Active Directory](#3-настройка-сервисной-учетной-записи-в-active-directory)
4. [Автозапуск в качестве службы Windows (NSSM)](#4-автозапуск-в-качестве-службы-windows-nssm)
5. [Мониторинг и логирование](#5-мониторинг-и-логирование)
6. [История изменений документа](#6-история-изменений-документа)

---

## 1. Системные требования
- **ОС:** Windows Server 2016/2019/2022 или Windows 10/11 Pro/Enterprise.
  > [!IMPORTANT]
  > Для корректной работы функций опроса рабочих станций (`ping`, WMI через `Get-WmiObject` в PowerShell) сервер, на котором запущен бот, должен иметь сетевую видимость рабочих станций (открытые порты ICMP, RPC/WMI TCP 135, dynamic ports) и права локального администратора/WMI на целевых ПК.
- **Python:** 3.12+ (поддерживается 3.14).
- **Сетевой доступ:**
  - К контроллеру домена AD: TCP 389 (LDAP), TCP 636 (LDAPS, если используется прямой SSL).
  - В сеть Интернет: HTTPS (TCP 443) к `api.telegram.org` и `zoom.us`.

---

## 2. Установка и запуск в окружении Windows

1. **Клонирование репозитория / распаковка проекта:**
   ```powershell
   cd C:\Users\kbeletskiy\Documents\Kolins
   ```

2. **Создание и активация виртуального окружения:**
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

3. **Установка зависимостей:**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Настройка переменных окружения:**
   Создайте файл `.env` на основе шаблона из [05_CONFIGURATION.md](file:///c:/Users/kbeletskiy/Documents/Kolins/docs/05_CONFIGURATION.md) и заполните параметры:
   ```powershell
   notepad .env
   ```

5. **Ручной запуск приложения:**
   ```powershell
   python main.py
   ```

---

## 3. Настройка сервисной учетной записи в Active Directory
Для выполнения операций сброса пароля, разблокировки и перемещения объектов сервисному аккаунту `AD_USER` необходимо делегировать следующие разрешения в AD DS:
1. **Делегирование управления на OU пользователей:**
   - Сброс паролей учетных записей пользователей и принудительная смена пароля при следующем входе.
   - Чтение и запись всех сведений об учетных записях.
   - Снятие блокировки учетной записи (`lockoutTime`).
2. **Делегирование на целевые OU (для перемещения):**
   - Разрешения `Create Computer Objects`, `Delete Computer Objects`, `Write Distinguished Name` для соответствующих контейнеров.
3. **Разрешения на сброс паролей через LDAP:**
   - Требуется поддержка StartTLS или LDAPS, так как передача атрибута `unicodePwd` блокируется контроллером домена при незашифрованном соединении.

---

## 4. Автозапуск в качестве службы Windows (NSSM)
Для обеспечения непрерывной работы бота в фоновом режиме рекомендуется использовать **NSSM (Non-Sucking Service Manager)**:

```powershell
# Установка службы
nssm install KolinsBot "C:\Users\kbeletskiy\Documents\Kolins\venv\Scripts\python.exe" "C:\Users\kbeletskiy\Documents\Kolins\main.py"
nssm set KolinsBot AppDirectory "C:\Users\kbeletskiy\Documents\Kolins"
nssm set KolinsBot Start SERVICE_AUTO_START

# Настройка автоперезапуска при сбоях
nssm set KolinsBot AppExit Default Restart
nssm set KolinsBot AppRestartDelay 5000

# Запуск службы
nssm start KolinsBot
```

---

## 5. Мониторинг и логирование
- Консольный вывод бота форматируется через `logger_service.py` со стандартными уровнями `INFO`, `WARNING`, `ERROR`.
- Все успешные и ошибочные административные операции логируются непосредственно в Telegram-чат аудита `LOG_CHAT_ID`.

## 6. История изменений документа

| Версия | Дата | Автор | Описание изменений |
|---|---|---|---|
| 1.0 | 2026-11-09 | AI Documentation Agent | Регламент развертывания, требования AD DS и запуск через службу. |
