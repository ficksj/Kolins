import datetime
import hashlib
import ssl
import struct
import threading
import ldap3
from ldap3.utils.conv import escape_filter_chars

import config
from services.logger_service import get_logger

TZ_LOCAL = datetime.timezone(datetime.timedelta(hours=5))


# --- ЧИСТАЯ РЕАЛИЗАЦИЯ MD4 ДЛЯ PYTHON 3.14+ ---
class PureMD4:
    def __init__(self, data=b''):
        self.buf = bytearray()
        if data:
            self.update(data)

    def update(self, data):
        self.buf.extend(data)

    def digest(self):
        msg = self.buf
        orig_len_in_bits = (len(msg) * 8) & 0xffffffffffffffff
        msg.append(0x80)
        while len(msg) % 64 != 56:
            msg.append(0)
        msg += struct.pack('<Q', orig_len_in_bits)

        A, B, C, D = 0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476

        def F(x, y, z): return (x & y) | (~x & z)
        def G(x, y, z): return (x & y) | (x & z) | (y & z)
        def H(x, y, z): return x ^ y ^ z

        def lrot(val, n): return ((val << n) & 0xffffffff) | (val >> (32 - n))

        for i in range(0, len(msg), 64):
            X = list(struct.unpack('<16I', msg[i:i+64]))
            a, b, c, d = A, B, C, D

            # Round 1
            for s, k in zip([3, 7, 11, 19]*4, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]):
                a = lrot((a + F(b, c, d) + X[k]) & 0xffffffff, s)
            for s, k in zip([3, 7, 11, 19]*4, [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15]):
                d = lrot((d + F(a, b, c) + X[k]) & 0xffffffff, s)
            for s, k in zip([3, 7, 11, 19]*4, [0, 8, 1, 9, 2, 10, 3, 11, 4, 12, 5, 13, 6, 14, 7, 15]):
                c = lrot((c + F(d, a, b) + X[k]) & 0xffffffff, s)
            for s, k in zip([3, 7, 11, 19]*4, [0, 12, 1, 13, 2, 14, 3, 15, 4, 8, 5, 9, 6, 10, 7, 11]):
                b = lrot((b + F(c, d, a) + X[k]) & 0xffffffff, s)

            # Round 2
            for s, k in zip([3, 5, 9, 13]*4, [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15]):
                a = lrot((a + G(b, c, d) + X[k] + 0x5a827999) & 0xffffffff, s)
            for s, k in zip([3, 5, 9, 13]*4, [0, 8, 1, 9, 2, 10, 3, 11, 4, 12, 5, 13, 6, 14, 7, 15]):
                d = lrot((d + G(a, b, c) + X[k] + 0x5a827999) & 0xffffffff, s)
            for s, k in zip([3, 5, 9, 13]*4, [0, 12, 1, 13, 2, 14, 3, 15, 4, 8, 5, 9, 6, 10, 7, 11]):
                c = lrot((c + G(d, a, b) + X[k] + 0x5a827999) & 0xffffffff, s)
            for s, k in zip([3, 5, 9, 13]*4, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]):
                b = lrot((b + G(c, d, a) + X[k] + 0x5a827999) & 0xffffffff, s)

            # Round 3
            for s, k in zip([3, 9, 11, 15]*4, [0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15]):
                a = lrot((a + H(b, c, d) + X[k] + 0x6ed9eba1) & 0xffffffff, s)
            for s, k in zip([3, 9, 11, 15]*4, [0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15]):
                d = lrot((d + H(a, b, c) + X[k] + 0x6ed9eba1) & 0xffffffff, s)
            for s, k in zip([3, 9, 11, 15]*4, [0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15]):
                c = lrot((c + H(d, a, b) + X[k] + 0x6ed9eba1) & 0xffffffff, s)
            for s, k in zip([3, 9, 11, 15]*4, [0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15]):
                b = lrot((b + H(c, d, a) + X[k] + 0x6ed9eba1) & 0xffffffff, s)

            A = (A + a) & 0xffffffff
            B = (B + b) & 0xffffffff
            C = (C + c) & 0xffffffff
            D = (D + d) & 0xffffffff

        return struct.pack('<4I', A, B, C, D)

    def hexdigest(self):
        return self.digest().hex()


_original_hash_new = hashlib.new


def _patched_hash_new(name, string=b'', *args, **kwargs):
    if name.lower() == 'md4':
        return PureMD4(string)
    return _original_hash_new(name, string, *args, **kwargs)


hashlib.new = _patched_hash_new
# ----------------------------------------------------

logger = get_logger("ADService")


def _parse_ad_dt(val) -> datetime.datetime | None:
    """Преобразует дату-время из атрибута ldap3 в aware datetime (UTC)."""
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        if val.year <= 1601:
            return None
        return val if val.tzinfo else val.replace(tzinfo=datetime.timezone.utc)
    s = str(val).strip()
    if not s or s.startswith("1601") or s == "0":
        return None
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.year <= 1601:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def _fmt_local_dt(val, date_only: bool = False) -> str:
    """Форматирует AD datetime в локальный часовой пояс (UTC+5)."""
    dt = _parse_ad_dt(val)
    if not dt:
        return "—"
    local_dt = dt.astimezone(TZ_LOCAL)
    return local_dt.strftime("%d.%m.%Y" if date_only else "%d.%m.%Y %H:%M")


def _extract_cn(dn: str) -> str:
    for part in str(dn).split(","):
        if part.upper().startswith("CN="):
            return part[3:]
    return str(dn)


def _attr_val(attrs: dict, key: str, default=None):
    val = attrs.get(key, default)
    if isinstance(val, list):
        return val[0] if val else default
    return val if val is not None else default


class ActiveDirectoryService:
    def __init__(self):
        self.server_uri = getattr(config, "AD_SERVER", "192.168.201.15") or "192.168.201.15"
        self.domain = getattr(config, "AD_DOMAIN", "kdb.kz") or "kdb.kz"
        self.user = getattr(config, "AD_USER", "") or ""
        self.password = getattr(config, "AD_PASSWORD", "") or ""
        self.base_dn = getattr(config, "AD_SEARCH_BASE", "DC=kdb,DC=kz")
        self.conn = None
        self._lock = threading.RLock()
        self._max_pwd_age_days: int | None = None
        self.connect()

    def connect(self):
        """Подключение к Active Directory (NTLM -> StartTLS + SIMPLE)."""
        with self._lock:
            try:
                if self.conn:
                    try:
                        self.conn.unbind()
                    except Exception:
                        pass

                server_host = self.server_uri.replace("ldap://", "").replace("ldaps://", "")
                tls_req = ldap3.Tls(validate=ssl.CERT_NONE)
                server = ldap3.Server(server_host, port=389, use_ssl=False, tls=tls_req, get_info=ldap3.ALL)

                user_ntlm = self.user
                if "@" in user_ntlm:
                    user_ntlm = f"{self.domain.split('.')[0].upper()}\\{user_ntlm.split('@')[0]}"
                elif "\\" not in user_ntlm:
                    user_ntlm = f"{self.domain.split('.')[0].upper()}\\{user_ntlm}"

                # 1. Сначала пробуем NTLM
                self.conn = ldap3.Connection(
                    server,
                    user=user_ntlm,
                    password=self.password,
                    authentication=ldap3.NTLM,
                    auto_bind=False,
                )

                if not self.conn.bind():
                    logger.warning(f"NTLM bind не прошел ({self.conn.result.get('description')}). Пробуем StartTLS + SIMPLE...")
                    username = self.user.split("\\")[-1] if "\\" in self.user else self.user
                    bind_user = f"{username}@{self.domain}" if "@" not in username else username

                    self.conn = ldap3.Connection(
                        server,
                        user=bind_user,
                        password=self.password,
                        authentication=ldap3.SIMPLE,
                        auto_bind=False,
                    )

                    try:
                        self.conn.start_tls()
                    except Exception as tls_err:
                        logger.error(f"Ошибка активации StartTLS: {tls_err}")

                    if not self.conn.bind():
                        logger.error(f"StartTLS + SIMPLE bind также отклонен: {self.conn.result}")
                        self.conn = None
                        return

                logger.info("Успешное подключение и авторизация в Active Directory")
            except Exception as e:
                logger.error(f"Ошибка подключения к AD: {e}")
                self.conn = None

    def ensure_connection(self):
        """Автоматическое переподключение при отвалившемся сокете."""
        with self._lock:
            if not self.conn or not self.conn.bound or self.conn.closed:
                self.connect()

    def is_connected(self) -> bool:
        """Безопасная проверка активности LDAP-соединения."""
        with self._lock:
            return bool(self.conn and self.conn.bound and not self.conn.closed)

    def _safe_search(self, search_base, search_filter, attributes):
        """Потокобезопасный поиск с реконектом при разорванном сокете."""
        with self._lock:
            self.ensure_connection()
            if not self.conn:
                return False
            try:
                return self.conn.search(
                    search_base=search_base,
                    search_filter=search_filter,
                    attributes=attributes,
                )
            except (ldap3.core.exceptions.LDAPSocketSendError, ldap3.core.exceptions.LDAPCommunicationError):
                logger.warning("Сокет AD был разорван. Выполняем переподключение...")
                self.connect()
                if self.conn:
                    return self.conn.search(
                        search_base=search_base,
                        search_filter=search_filter,
                        attributes=attributes,
                    )
            return False

    def _safe_paged_search(self, search_base: str, search_filter: str, attributes: list[str], paged_size: int = 500) -> list[dict]:
        """Постраничный потокобезопасный поиск для обхода лимита AD в 1000 объектов."""
        with self._lock:
            self.ensure_connection()
            if not self.conn:
                return []
            for attempt in range(2):
                try:
                    results = []
                    generator = self.conn.extend.standard.paged_search(
                        search_base=search_base,
                        search_filter=search_filter,
                        search_scope=ldap3.SUBTREE,
                        attributes=attributes,
                        paged_size=paged_size,
                        generator=True,
                    )
                    for item in generator:
                        if item.get("type") == "searchResEntry":
                            results.append(item)
                    return results
                except (ldap3.core.exceptions.LDAPSocketSendError, ldap3.core.exceptions.LDAPCommunicationError):
                    if attempt == 0:
                        logger.warning("Сокет AD разорван в paged_search, переподключение...")
                        self.connect()
                        if not self.conn:
                            return []
                    else:
                        return []
                except Exception as e:
                    logger.error(f"Ошибка paged_search: {e}")
                    return []
        return []

    def get_max_pwd_age_days(self) -> int:
        """Получает политику домена maxPwdAge (в днях)."""
        if self._max_pwd_age_days is not None:
            return self._max_pwd_age_days
        try:
            with self._lock:
                if self._safe_search(self.base_dn, "(objectClass=domainDNS)", ["maxPwdAge"]) and self.conn.entries:
                    raw_age = self.conn.entries[0].maxPwdAge.value
                    if isinstance(raw_age, datetime.timedelta):
                        days = abs(int(raw_age.total_seconds() // 86400))
                        if days > 0:
                            self._max_pwd_age_days = days
                            return days
                    elif isinstance(raw_age, int) and raw_age != 0:
                        days = abs(int(raw_age)) // (10_000_000 * 86400)
                        if days > 0:
                            self._max_pwd_age_days = days
                            return days
        except Exception as e:
            logger.debug(f"Не удалось получить maxPwdAge: {e}")
        self._max_pwd_age_days = 90
        return 90

    def search_users(self, query: str) -> list[dict]:
        """Поиск пользователей в AD через ANR со статусами УЗ."""
        safe_query = escape_filter_chars(query)
        filter_str = f"(&(objectCategory=person)(objectClass=user)(anr={safe_query}))"
        logger.info(f"Выполняется AD поиск ANR: {filter_str}")

        attrs = ['sAMAccountName', 'displayName', 'name', 'department', 'userAccountControl', 'lockoutTime']
        with self._lock:
            if not self._safe_search(self.base_dn, filter_str, attrs):
                return []

            users = []
            seen = set()
            for entry in self.conn.entries:
                if not hasattr(entry, 'sAMAccountName') or not entry.sAMAccountName:
                    continue
                username = str(entry.sAMAccountName)
                if username.lower() in seen:
                    continue
                seen.add(username.lower())

                if hasattr(entry, 'displayName') and entry.displayName:
                    display_name = str(entry.displayName)
                elif hasattr(entry, 'name') and entry.name:
                    display_name = str(entry.name)
                else:
                    display_name = username

                uac = int(entry.userAccountControl.value) if hasattr(entry, 'userAccountControl') and entry.userAccountControl else 0
                is_disabled = bool(uac & 2)
                lt_dt = _parse_ad_dt(entry.lockoutTime.value) if hasattr(entry, 'lockoutTime') and entry.lockoutTime else None
                is_locked = lt_dt is not None

                users.append({
                    "username": username,
                    "display_name": display_name,
                    "department": str(entry.department) if hasattr(entry, 'department') and entry.department else "—",
                    "is_disabled": is_disabled,
                    "is_locked": is_locked,
                })

            logger.info(f"Найдено пользователей: {len(users)}")
            return users

    def get_user_details(self, username: str) -> dict | None:
        """Получение расширенной информации о пользователе."""
        safe_username = escape_filter_chars(username)
        filter_str = f"(&(objectCategory=person)(objectClass=user)(sAMAccountName={safe_username}))"
        attrs = [
            'sAMAccountName', 'displayName', 'mail', 'department', 'title',
            'telephoneNumber', 'mobile', 'manager', 'company', 'whenCreated',
            'pwdLastSet', 'lastLogonTimestamp', 'badPwdCount', 'lockoutTime',
            'userAccountControl', 'distinguishedName', 'physicalDeliveryOfficeName',
            'scriptPath', 'logonWorkstation', 'memberOf'
        ]

        with self._lock:
            if not self._safe_search(self.base_dn, filter_str, attrs) or not self.conn.entries:
                return None

            entry = self.conn.entries[0]
            uac = int(entry.userAccountControl.value) if entry.userAccountControl else 0
            is_disabled = bool(uac & 2)
            pwd_never_expires = bool(uac & 0x10000)

            manager_raw = str(entry.manager) if hasattr(entry, 'manager') and entry.manager else ""
            manager_name = _extract_cn(manager_raw) if manager_raw else ""

            groups = []
            group_dns = []
            if hasattr(entry, 'memberOf') and entry.memberOf:
                raw_groups = entry.memberOf.values if hasattr(entry.memberOf, 'values') else entry.memberOf
                for g in raw_groups:
                    g_dn = str(g)
                    g_cn = _extract_cn(g_dn)
                    groups.append(g_cn)
                    group_dns.append({"name": g_cn, "dn": g_dn})
            groups.sort(key=str.lower)
            group_dns.sort(key=lambda x: x["name"].lower())

            lt_dt = _parse_ad_dt(entry.lockoutTime.value) if hasattr(entry, 'lockoutTime') and entry.lockoutTime else None
            is_locked = lt_dt is not None

            pwd_dt = _parse_ad_dt(entry.pwdLastSet.value) if hasattr(entry, 'pwdLastSet') and entry.pwdLastSet else None
            pwd_set = _fmt_local_dt(pwd_dt) if pwd_dt else "Требуется смена при входе"

            pwd_expiry_str = "Бессрочный"
            if pwd_never_expires:
                pwd_expiry_str = "Бессрочный (Never Expires)"
            elif pwd_dt:
                max_days = self.get_max_pwd_age_days()
                exp_dt = pwd_dt + datetime.timedelta(days=max_days)
                days_left = (exp_dt - datetime.datetime.now(datetime.timezone.utc)).days
                exp_local = exp_dt.astimezone(TZ_LOCAL).strftime("%d.%m.%Y")
                if days_left < 0:
                    pwd_expiry_str = f"Истек ({exp_local})"
                else:
                    pwd_expiry_str = f"{exp_local} (осталось {days_left} дн.)"

            last_logon = _fmt_local_dt(entry.lastLogonTimestamp.value) if hasattr(entry, 'lastLogonTimestamp') and entry.lastLogonTimestamp else "—"
            created_at = _fmt_local_dt(entry.whenCreated.value, date_only=True) if hasattr(entry, 'whenCreated') and entry.whenCreated else "—"

            return {
                "username": str(entry.sAMAccountName),
                "display_name": str(entry.displayName) if entry.displayName else str(entry.sAMAccountName),
                "mail": str(entry.mail) if entry.mail else "—",
                "department": str(entry.department) if entry.department else "—",
                "title": str(entry.title) if hasattr(entry, 'title') and entry.title else "—",
                "manager_name": manager_name or "—",
                "manager_dn": manager_raw,
                "company": str(entry.company) if hasattr(entry, 'company') and entry.company else 'АО "KDB Bank"',
                "telephone_number": str(entry.telephoneNumber) if entry.telephoneNumber else "—",
                "mobile": str(entry.mobile) if hasattr(entry, 'mobile') and entry.mobile else "—",
                "physical_delivery_office_name": str(entry.physicalDeliveryOfficeName) if hasattr(entry, 'physicalDeliveryOfficeName') and entry.physicalDeliveryOfficeName else "—",
                "script_path": str(entry.scriptPath) if hasattr(entry, 'scriptPath') and entry.scriptPath else "—",
                "logon_workstation": str(entry.logonWorkstation) if hasattr(entry, 'logonWorkstation') and entry.logonWorkstation else "—",
                "pwd_last_set": pwd_set,
                "pwd_expiry": pwd_expiry_str,
                "last_logon": last_logon,
                "bad_pwd_count": int(entry.badPwdCount.value) if hasattr(entry, 'badPwdCount') and entry.badPwdCount else 0,
                "created_at": created_at,
                "groups": groups,
                "group_dns": group_dns,
                "uac": uac,
                "is_disabled": is_disabled,
                "is_locked": is_locked,
                "dn": str(entry.entry_dn),
            }

    def reset_password(self, username: str, new_password: str, must_change: bool = True) -> bool:
        """Сброс пароля пользователя в AD с опцией смены при следующем входе (pwdLastSet=0)."""
        with self._lock:
            details = self.get_user_details(username)
            if not details:
                logger.error(f"Пользователь {username} не найден для сброса пароля.")
                return False

            user_dn = details["dn"]
            password_quoted = f'"{new_password}"'
            unicode_password = password_quoted.encode('utf-16-le')

            try:
                if not self.conn.tls_started:
                    try:
                        self.conn.start_tls()
                    except Exception:
                        pass

                status = self.conn.modify(user_dn, {'unicodePwd': [(ldap3.MODIFY_REPLACE, [unicode_password])]})
                if not status:
                    logger.error(f"Ошибка AD при сбросе пароля {username}: {self.conn.result['description']} - {self.conn.result.get('message')}")
                    return False

                mods = {}
                if must_change:
                    mods['pwdLastSet'] = [(ldap3.MODIFY_REPLACE, [0])]
                if details.get("is_locked"):
                    mods['lockoutTime'] = [(ldap3.MODIFY_REPLACE, [0])]
                if mods:
                    self.conn.modify(user_dn, mods)
                return True
            except Exception as e:
                logger.error(f"Исключение при сбросе пароля: {e}")
                return False

    def unlock_user(self, username: str) -> bool:
        """Разблокировка учетной записи."""
        with self._lock:
            details = self.get_user_details(username)
            if not details:
                return False
            try:
                res = self.conn.modify(details["dn"], {'lockoutTime': [(ldap3.MODIFY_REPLACE, [0])]})
                return bool(res and self.conn.result['result'] == 0)
            except Exception as e:
                logger.error(f"Ошибка разблокировки: {e}")
                return False

    def set_user_enabled(self, username: str, enabled: bool) -> bool:
        """Включает или отключает учетную запись пользователя через userAccountControl."""
        with self._lock:
            details = self.get_user_details(username)
            if not details:
                return False
            uac = int(details.get("uac", 512))
            new_uac = (uac & ~2) if enabled else (uac | 2)
            try:
                res = self.conn.modify(details["dn"], {'userAccountControl': [(ldap3.MODIFY_REPLACE, [new_uac])]})
                if not res:
                    logger.error(f"Ошибка изменения статуса УЗ {username}: {self.conn.result}")
                return bool(res and self.conn.result['result'] == 0)
            except Exception as e:
                logger.error(f"Исключение set_user_enabled для {username}: {e}")
                return False

    def search_groups(self, query: str, limit: int = 20) -> list[dict]:
        """Поиск групп безопасности AD по имени или CN."""
        safe_q = escape_filter_chars(query.strip())
        filter_str = f"(&(objectClass=group)(|(cn=*{safe_q}*)(name=*{safe_q}*)))"
        with self._lock:
            if not self._safe_search(self.base_dn, filter_str, ['cn', 'name', 'description']):
                return []
            groups = []
            for entry in self.conn.entries:
                name = str(entry.cn) if hasattr(entry, 'cn') and entry.cn else str(entry.name)
                if name.lower() in config.PROTECTED_AD_GROUPS:
                    continue
                groups.append({
                    "name": name,
                    "dn": str(entry.entry_dn),
                    "description": str(entry.description) if hasattr(entry, 'description') and entry.description else "—",
                })
                if len(groups) >= limit:
                    break
            return groups

    def add_user_to_group(self, username: str, group_dn: str) -> tuple[bool, str]:
        """Добавляет пользователя в группу безопасности AD."""
        group_cn = _extract_cn(group_dn).lower()
        if group_cn in config.PROTECTED_AD_GROUPS:
            return False, "Изменение привилегированной группы запрещено политикой безопасности бота."
        with self._lock:
            details = self.get_user_details(username)
            if not details:
                return False, "Пользователь не найден в AD."
            try:
                res = self.conn.modify(group_dn, {'member': [(ldap3.MODIFY_ADD, [details["dn"]])]})
                if res:
                    return True, "ok"
                desc = self.conn.result.get("description", "error")
                if desc == "entryAlreadyExists" or desc == "attributeOrValueExists":
                    return False, "Пользователь уже состоит в этой группе."
                return False, f"Ошибка AD: {desc}"
            except Exception as e:
                return False, str(e)

    def remove_user_from_group(self, username: str, group_dn: str) -> tuple[bool, str]:
        """Удаляет пользователя из группы безопасности AD."""
        group_cn = _extract_cn(group_dn).lower()
        if group_cn in config.PROTECTED_AD_GROUPS:
            return False, "Изменение привилегированной группы запрещено политикой безопасности бота."
        with self._lock:
            details = self.get_user_details(username)
            if not details:
                return False, "Пользователь не найден в AD."
            try:
                res = self.conn.modify(group_dn, {'member': [(ldap3.MODIFY_DELETE, [details["dn"]])]})
                if res:
                    return True, "ok"
                return False, f"Ошибка AD: {self.conn.result.get('description', 'error')}"
            except Exception as e:
                return False, str(e)

    def move_user(self, user_dn: str, target_ou_dn: str) -> bool:
        """Перемещение пользователя в другое подразделение (OU)."""
        with self._lock:
            self.ensure_connection()
            if not self.conn:
                return False
            try:
                rdn = user_dn.split(',')[0]
                res = self.conn.modify_dn(
                    dn=user_dn,
                    relative_dn=rdn,
                    delete_old_dn=True,
                    new_superior=target_ou_dn
                )
                if not res:
                    logger.error(f"Ошибка AD при перемещении: {self.conn.result}")
                    return False
                return True
            except Exception as e:
                logger.error(f"Исключение при перемещении пользователя: {e}")
                return False

    def find_ou_dn_by_name(self, ou_name: str):
        """Поиск DN организационного юнита (OU) по имени."""
        safe_ou_name = escape_filter_chars(ou_name)
        filter_ou = f"(&(objectClass=organizationalUnit)(ou={safe_ou_name}))"
        with self._lock:
            if not self._safe_search(self.base_dn, filter_ou, ['distinguishedName']) or not self.conn.entries:
                filter_name = f"(&(objectClass=organizationalUnit)(name={safe_ou_name}))"
                if not self._safe_search(self.base_dn, filter_name, ['distinguishedName']) or not self.conn.entries:
                    logger.error(f"OU '{ou_name}' не найдена в base_dn: {self.base_dn}")
                    return None
            return str(self.conn.entries[0].entry_dn)

    def search_computers(self, query: str) -> list[dict]:
        """Поиск компьютеров в AD."""
        safe_query = escape_filter_chars(query)
        filter_str = f"(&(objectClass=computer)(|(name=*{safe_query}*)(description=*{safe_query}*)))"
        with self._lock:
            if not self._safe_search(self.base_dn, filter_str, ['name', 'description', 'dNSHostName']):
                return []

            computers = []
            for entry in self.conn.entries:
                computers.append({
                    "Name": str(entry.name),
                    "Description": str(entry.description) if entry.description else "Без описания",
                    "DnsName": str(entry.dNSHostName) if hasattr(entry, 'dNSHostName') and entry.dNSHostName else "",
                })
            return computers

    def get_computer_details(self, pc_name: str) -> dict | None:
        """Получение детальной информации о компьютере."""
        safe_pc_name = escape_filter_chars(pc_name)
        filter_str = f"(&(objectClass=computer)(name={safe_pc_name}))"
        attrs = [
            'name', 'dNSHostName', 'operatingSystem', 'operatingSystemVersion',
            'description', 'whenCreated', 'lastLogonTimestamp', 'distinguishedName'
        ]
        with self._lock:
            if not self._safe_search(self.base_dn, filter_str, attrs) or not self.conn.entries:
                return None

            entry = self.conn.entries[0]
            return {
                "name": str(entry.name),
                "dns_name": str(entry.dNSHostName) if entry.dNSHostName else "—",
                "os": str(entry.operatingSystem) if entry.operatingSystem else "—",
                "os_version": str(entry.operatingSystemVersion) if entry.operatingSystemVersion else "",
                "description": str(entry.description) if entry.description else "—",
                "created": _fmt_local_dt(entry.whenCreated.value, date_only=True) if entry.whenCreated else "—",
                "last_logon": _fmt_local_dt(entry.lastLogonTimestamp.value) if hasattr(entry, 'lastLogonTimestamp') and entry.lastLogonTimestamp else "—",
                "dn": str(entry.entry_dn),
            }

    def move_computer(self, pc_name: str, target_ou_dn: str) -> bool:
        """Перемещение компьютера в другое OU."""
        with self._lock:
            details = self.get_computer_details(pc_name)
            if not details:
                return False
            try:
                rdn = f"CN={pc_name}"
                return bool(self.conn.modify_dn(dn=details["dn"], relative_dn=rdn, delete_old_dn=True, new_superior=target_ou_dn))
            except Exception as e:
                logger.error(f"Ошибка перемещения ПК: {e}")
                return False

    def delete_computer(self, pc_name: str) -> bool:
        """Удаление учётной записи компьютера из домена."""
        with self._lock:
            details = self.get_computer_details(pc_name)
            if not details:
                return False
            try:
                result = self.conn.delete(details["dn"])
                if not result:
                    logger.error(f"Ошибка AD при удалении ПК {pc_name}: {self.conn.result}")
                return bool(result)
            except Exception as e:
                logger.error(f"Исключение при удалении ПК {pc_name}: {e}")
                return False

    def get_domain_stats(self) -> dict | None:
        """Точная постраничная статистика по пользователям (без компьютеров) и ПК домена."""
        try:
            user_entries = self._safe_paged_search(
                self.base_dn,
                "(&(objectCategory=person)(objectClass=user))",
                ['sAMAccountName', 'userAccountControl', 'lockoutTime'],
            )
            total_users = len(user_entries)
            disabled_users = 0
            locked_users = 0
            for item in user_entries:
                attrs = item.get("attributes", {})
                uac = int(_attr_val(attrs, "userAccountControl", 0) or 0)
                is_dis = bool(uac & 2)
                if is_dis:
                    disabled_users += 1
                else:
                    lt = _parse_ad_dt(_attr_val(attrs, "lockoutTime"))
                    if lt is not None:
                        locked_users += 1
            active_users = total_users - disabled_users

            pc_entries = self._safe_paged_search(
                self.base_dn,
                "(objectClass=computer)",
                ['name', 'lastLogonTimestamp'],
            )
            total_computers = len(pc_entries)
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=90)
            stale_computers = 0
            for item in pc_entries:
                attrs = item.get("attributes", {})
                dt = _parse_ad_dt(_attr_val(attrs, "lastLogonTimestamp"))
                if dt is None or dt < cutoff:
                    stale_computers += 1

            return {
                "total_users": total_users,
                "active_users": active_users,
                "disabled_users": disabled_users,
                "locked_users": locked_users,
                "total_computers": total_computers,
                "stale_computers": stale_computers,
            }
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return None

    def get_target_computers(self) -> list[str]:
        """Динамический сбор компьютеров из целевых контейнеров конфига."""
        computers = []
        for ou_dn in config.TARGET_PC_OUS:
            entries = self._safe_paged_search(ou_dn, "(&(objectClass=computer))", ['dNSHostName', 'name'])
            for item in entries:
                attrs = item.get("attributes", {})
                dns = _attr_val(attrs, "dNSHostName")
                name = _attr_val(attrs, "name")
                if dns:
                    computers.append(str(dns))
                elif name:
                    computers.append(str(name))
        return list(set(computers))

    def get_user_photo(self, username: str) -> bytes | None:
        """Получение фото пользователя из AD (thumbnailPhoto)."""
        safe_username = escape_filter_chars(username)
        filter_str = f"(&(objectCategory=person)(objectClass=user)(sAMAccountName={safe_username}))"
        with self._lock:
            if not self._safe_search(self.base_dn, filter_str, ['thumbnailPhoto']):
                return None
            if not self.conn.entries or not hasattr(self.conn.entries[0], 'thumbnailPhoto'):
                return None
            photo_data = self.conn.entries[0].thumbnailPhoto.value
            return photo_data if photo_data else None

    def get_locked_users(self) -> list[dict]:
        """Поиск реально заблокированных АКТИВНЫХ учетных записей (исключая disabled)."""
        filter_str = "(&(objectCategory=person)(objectClass=user)(lockoutTime>=1)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))"
        attrs = ['sAMAccountName', 'displayName', 'department', 'title', 'lockoutTime', 'badPwdCount']
        entries = self._safe_paged_search(self.base_dn, filter_str, attrs)
        users = []
        for item in entries:
            a = item.get("attributes", {})
            lt_dt = _parse_ad_dt(_attr_val(a, "lockoutTime"))
            if not lt_dt:
                continue
            uname = str(_attr_val(a, "sAMAccountName", "") or "")
            if not uname:
                continue
            users.append({
                "username": uname,
                "display_name": str(_attr_val(a, "displayName") or uname),
                "department": str(_attr_val(a, "department") or "—"),
                "title": str(_attr_val(a, "title") or "—"),
                "bad_pwd_count": int(_attr_val(a, "badPwdCount", 0) or 0),
                "lockout_time": _fmt_local_dt(lt_dt),
                "lockout_ts": lt_dt.timestamp(),
            })
        users.sort(key=lambda x: x["lockout_ts"], reverse=True)
        return users

    def get_expiring_passwords(self, days: int = 7) -> list[dict]:
        """Активные сотрудники, чей пароль истекает в течение N дней (или уже истек)."""
        max_days = self.get_max_pwd_age_days()
        filter_str = (
            "(&(objectCategory=person)(objectClass=user)"
            "(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
            "(!(userAccountControl:1.2.840.113556.1.4.803:=65536)))"
        )
        attrs = ['sAMAccountName', 'displayName', 'department', 'pwdLastSet']
        entries = self._safe_paged_search(self.base_dn, filter_str, attrs)
        now = datetime.datetime.now(datetime.timezone.utc)
        expiring = []
        for item in entries:
            a = item.get("attributes", {})
            uname = str(_attr_val(a, "sAMAccountName", "") or "")
            if not uname or uname.lower().startswith(("krbtgt", "guest", "default", "iusr", "tsinternet")):
                continue
            pwd_dt = _parse_ad_dt(_attr_val(a, "pwdLastSet"))
            if not pwd_dt:
                continue
            exp_dt = pwd_dt + datetime.timedelta(days=max_days)
            days_left = (exp_dt - now).days
            if -30 <= days_left <= days:
                expiring.append({
                    "username": uname,
                    "display_name": str(_attr_val(a, "displayName") or uname),
                    "department": str(_attr_val(a, "department") or "—"),
                    "days_left": days_left,
                    "expires_at": exp_dt.astimezone(TZ_LOCAL).strftime("%d.%m.%Y"),
                })
        expiring.sort(key=lambda x: x["days_left"])
        return expiring

    def get_stale_computers(self, days: int = 90) -> list[dict]:
        """Компьютеры, не выходившие на связь с доменом более N дней."""
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        stale = []
        seen = set()
        for ou_dn in config.TARGET_PC_OUS:
            entries = self._safe_paged_search(
                ou_dn,
                "(objectClass=computer)",
                ['name', 'operatingSystem', 'lastLogonTimestamp', 'distinguishedName'],
            )
            for item in entries:
                a = item.get("attributes", {})
                name = str(_attr_val(a, "name", "") or "")
                if not name or name.upper() in seen:
                    continue
                seen.add(name.upper())
                dt = _parse_ad_dt(_attr_val(a, "lastLogonTimestamp"))
                if dt is None or dt < cutoff:
                    days_ago = (datetime.datetime.now(datetime.timezone.utc) - dt).days if dt else 9999
                    stale.append({
                        "name": name,
                        "os": str(_attr_val(a, "operatingSystem") or "—"),
                        "last_logon": _fmt_local_dt(dt, date_only=True) if dt else "Никогда",
                        "days_ago": days_ago,
                        "dn": str(item.get("dn") or ""),
                    })
        stale.sort(key=lambda x: x["days_ago"], reverse=True)
        return stale


ad_service = ActiveDirectoryService()
