import ssl
import struct
import hashlib
import ldap3
from ldap3.utils.conv import escape_filter_chars
import config
from services.logger_service import get_logger

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

class ActiveDirectoryService:
    def __init__(self):
        self.server_uri = getattr(config, "AD_SERVER", "192.168.201.15")
        self.domain = getattr(config, "AD_DOMAIN", "kdb.kz")
        self.user = getattr(config, "AD_USER", "")
        self.password = getattr(config, "AD_PASSWORD", "")
        self.base_dn = getattr(config, "AD_SEARCH_BASE", "DC=kdb,DC=kz")
        self.conn = None
        self.connect()

    def connect(self):
        """Подключение к Active Directory (NTLM -> StartTLS + SIMPLE)"""
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

            # 1. Сначала пробуем обычный NTLM
            self.conn = ldap3.Connection(server, user=user_ntlm, password=self.password, authentication=ldap3.NTLM, auto_bind=False)

            if not self.conn.bind():
                logger.warning(f"NTLM bind не прошел ({self.conn.result.get('description')}). Пробуем StartTLS + SIMPLE...")

                # 2. Переходим на StartTLS и меняем тип аутентификации на SIMPLE (UPN формат)
                username = self.user.split("\\")[-1] if "\\" in self.user else self.user
                bind_user = f"{username}@{self.domain}" if "@" not in username else username

                self.conn = ldap3.Connection(server, user=bind_user, password=self.password, authentication=ldap3.SIMPLE, auto_bind=False)

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
        """Автоматическое переподключение при отвалившемся сокете"""
        if not self.conn or not self.conn.bound or self.conn.closed:
            self.connect()

    def _safe_search(self, search_base, search_filter, attributes):
        """Безопасный поиск с реконектом при разорванном сокете"""
        self.ensure_connection()
        if not self.conn:
            return False
        try:
            return self.conn.search(search_base=search_base, search_filter=search_filter, attributes=attributes)
        except (ldap3.core.exceptions.LDAPSocketSendError, ldap3.core.exceptions.LDAPCommunicationError):
            logger.warning("Сокет AD был разорван. Выполняем переподключение...")
            self.connect()
            if self.conn:
                return self.conn.search(search_base=search_base, search_filter=search_filter, attributes=attributes)
        return False

    def search_users(self, query: str):
        """Поиск через ANR (Ambiguous Name Resolution) - родной механизм AD"""
        query_clean = query.strip()
        if not query_clean:
            return []

        # Используем мощь ANR: он сам найдет "Дина Жанабаева" по запросу "Дина жана"
        safe_query = escape_filter_chars(query_clean)
        filter_str = f"(&(objectClass=user)(anr={safe_query}))"

        logger.info(f"Выполняем поиск AD через ANR: {filter_str}")

        if not self._safe_search(self.base_dn, filter_str, ['sAMAccountName', 'displayName', 'name']):
            # Фолбэк: если ANR не нашел, ищем вхождение каждого слова в атрибут name
            parts = query_clean.split()
            name_conditions = "".join(
                f"(name=*{escape_filter_chars(part)}*)" for part in parts
            )
            fallback_filter = f"(&(objectClass=user)(&{name_conditions}))"

            logger.info(f"ANR не дал результатов. Пробуем fallback: {fallback_filter}")
            if not self._safe_search(self.base_dn, fallback_filter, ['sAMAccountName', 'displayName', 'name']):
                return []

        users = []
        for entry in self.conn.entries:
            if not hasattr(entry, 'sAMAccountName') or not entry.sAMAccountName:
                continue

            username = str(entry.sAMAccountName)
            if username.endswith("$"):  # Исключаем учетки компьютеров
                continue

            # AD может хранить имя в displayName или в name. Берем то, что заполнено.
            if hasattr(entry, 'displayName') and entry.displayName:
                display_name = str(entry.displayName)
            elif hasattr(entry, 'name') and entry.name:
                display_name = str(entry.name)
            else:
                display_name = username

            # Убираем дубликаты, если один пользователь выгрузился дважды
            user_dict = {"username": username, "display_name": display_name}
            if user_dict not in users:
                users.append(user_dict)

        logger.info(f"Найдено пользователей: {len(users)}")
        return users

    def get_user_details(self, username: str):
        """Получение расширенной информации о пользователе"""
        safe_username = escape_filter_chars(username)
        filter_str = f"(&(objectClass=user)(sAMAccountName={safe_username}))"
        attrs = [
            'sAMAccountName', 'displayName', 'mail', 'department', 'title',
            'telephoneNumber', 'mobile', 'manager', 'company', 'whenCreated',
            'pwdLastSet', 'lastLogonTimestamp', 'badPwdCount', 'lockoutTime',
            'userAccountControl', 'distinguishedName', 'physicalDeliveryOfficeName',
            'scriptPath', 'logonWorkstation', 'memberOf'
        ]
        if not self._safe_search(self.base_dn, filter_str, attrs) or not self.conn.entries:
            return None

        entry = self.conn.entries[0]
        uac = int(entry.userAccountControl.value) if entry.userAccountControl else 0
        is_disabled = bool(uac & 2)

        manager_raw = str(entry.manager) if hasattr(entry, 'manager') and entry.manager else ""
        manager_name = ""
        if manager_raw:
            for p in manager_raw.split(","):
                if p.startswith("CN="):
                    manager_name = p[3:]
                    break

        groups = []
        if hasattr(entry, 'memberOf') and entry.memberOf:
            raw_groups = entry.memberOf.values if hasattr(entry.memberOf, 'values') else entry.memberOf
            for g in raw_groups:
                for part in str(g).split(","):
                    if part.startswith("CN="):
                        groups.append(part[3:])
                        break

        is_locked = False
        if hasattr(entry, 'lockoutTime') and entry.lockoutTime:
            lt_val = str(entry.lockoutTime)
            if lt_val and not lt_val.startswith("1601"):
                is_locked = True

        pwd_set = str(entry.pwdLastSet.value)[:16] if hasattr(entry, 'pwdLastSet') and entry.pwdLastSet and entry.pwdLastSet.value else "—"
        last_logon = str(entry.lastLogonTimestamp.value)[:16] if hasattr(entry, 'lastLogonTimestamp') and entry.lastLogonTimestamp and entry.lastLogonTimestamp.value else "—"
        created_at = str(entry.whenCreated.value)[:10] if hasattr(entry, 'whenCreated') and entry.whenCreated and entry.whenCreated.value else "—"

        return {
            "username": str(entry.sAMAccountName),
            "display_name": str(entry.displayName) if entry.displayName else str(entry.sAMAccountName),
            "mail": str(entry.mail) if entry.mail else "—",
            "department": str(entry.department) if entry.department else "—",
            "title": str(entry.title) if hasattr(entry, 'title') and entry.title else "—",
            "manager_name": manager_name or "—",
            "manager_dn": manager_raw,
            "company": str(entry.company) if hasattr(entry, 'company') and entry.company else 'АО "Банк Развития Казахстана"',
            "telephone_number": str(entry.telephoneNumber) if entry.telephoneNumber else "—",
            "mobile": str(entry.mobile) if hasattr(entry, 'mobile') and entry.mobile else "—",
            "physical_delivery_office_name": str(entry.physicalDeliveryOfficeName) if hasattr(entry, 'physicalDeliveryOfficeName') and entry.physicalDeliveryOfficeName else "—",
            "script_path": str(entry.scriptPath) if hasattr(entry, 'scriptPath') and entry.scriptPath else "—",
            "logon_workstation": str(entry.logonWorkstation) if hasattr(entry, 'logonWorkstation') and entry.logonWorkstation else "—",
            "pwd_last_set": pwd_set,
            "last_logon": last_logon,
            "bad_pwd_count": int(entry.badPwdCount.value) if hasattr(entry, 'badPwdCount') and entry.badPwdCount else 0,
            "created_at": created_at,
            "groups": groups,
            "is_disabled": is_disabled,
            "is_locked": is_locked,
            "dn": str(entry.entry_dn)
        }

    def reset_password(self, username: str, new_password: str):
        """Сброс пароля пользователя в AD"""
        details = self.get_user_details(username)
        if not details:
            logger.error(f"Пользователь {username} не найден для сброса пароля.")
            return False

        user_dn = details["dn"]
        password_quoted = f'"{new_password}"'
        unicode_password = password_quoted.encode('utf-16-le')

        try:
            # Обязательно переходим в StartTLS перед сбросом пароля, если ещё не в нём
            if not self.conn.tls_started:
                try:
                    self.conn.start_tls()
                except Exception:
                    pass

            status = self.conn.modify(user_dn, {'unicodePwd': [(ldap3.MODIFY_REPLACE, [unicode_password])]})
            if not status:
                logger.error(f"Ошибка AD при сбросе пароля {username}: {self.conn.result['description']} - {self.conn.result.get('message')}")
                return False
            return True
        except Exception as e:
            logger.error(f"Исключение при сбросе пароля: {e}")
            return False

    def unlock_user(self, username: str):
        """Разблокировка учетной записи"""
        details = self.get_user_details(username)
        if not details:
            return False

        try:
            self.conn.modify(details["dn"], {'lockoutTime': [(ldap3.MODIFY_REPLACE, [0])]})
            return self.conn.result['result'] == 0
        except Exception as e:
            logger.error(f"Ошибка разблокировки: {e}")
            return False

    def move_user(self, user_dn: str, target_ou_dn: str):
        """Перемещение пользователя в другое подразделение (OU)"""
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
        """Поиск DN организационного юнита (OU) по имени"""
        safe_ou_name = escape_filter_chars(ou_name)
        filter_ou = f"(&(objectClass=organizationalUnit)(ou={safe_ou_name}))"
        if not self._safe_search(self.base_dn, filter_ou, ['distinguishedName']) or not self.conn.entries:
            filter_name = f"(&(objectClass=organizationalUnit)(name={safe_ou_name}))"
            if not self._safe_search(self.base_dn, filter_name, ['distinguishedName']) or not self.conn.entries:
                logger.error(f"OU '{ou_name}' не найдена в base_dn: {self.base_dn}")
                return None

        return str(self.conn.entries[0].entry_dn)

    def search_computers(self, query: str):
        """Поиск компьютеров в AD"""
        safe_query = escape_filter_chars(query)
        filter_str = f"(&(objectClass=computer)(|(name=*{safe_query}*)(description=*{safe_query}*)))"
        if not self._safe_search(self.base_dn, filter_str, ['name', 'description']):
            return []

        computers = []
        for entry in self.conn.entries:
            computers.append({
                "Name": str(entry.name),
                "Description": str(entry.description) if entry.description else "Без описания"
            })
        return computers

    def get_computer_details(self, pc_name: str):
        """Получение детальной информации о компьютере"""
        safe_pc_name = escape_filter_chars(pc_name)
        filter_str = f"(&(objectClass=computer)(name={safe_pc_name}))"
        attrs = ['name', 'dNSHostName', 'operatingSystem', 'operatingSystemVersion', 'description', 'whenCreated', 'distinguishedName']
        if not self._safe_search(self.base_dn, filter_str, attrs) or not self.conn.entries:
            return None

        entry = self.conn.entries[0]
        return {
            "name": str(entry.name),
            "dns_name": str(entry.dNSHostName) if entry.dNSHostName else "—",
            "os": str(entry.operatingSystem) if entry.operatingSystem else "—",
            "os_version": str(entry.operatingSystemVersion) if entry.operatingSystemVersion else "",
            "description": str(entry.description) if entry.description else "—",
            "created": str(entry.whenCreated) if entry.whenCreated else "—",
            "dn": str(entry.entry_dn)
        }

    def move_computer(self, pc_name: str, target_ou_dn: str):
        """Перемещение компьютера в другое OU"""
        details = self.get_computer_details(pc_name)
        if not details:
            return False

        try:
            rdn = f"CN={pc_name}"
            return self.conn.modify_dn(dn=details["dn"], relative_dn=rdn, delete_old_dn=True, new_superior=target_ou_dn)
        except Exception as e:
            logger.error(f"Ошибка перемещения ПК: {e}")
            return False

    def delete_computer(self, pc_name: str):
        """Удаление учётной записи компьютера из домена"""
        details = self.get_computer_details(pc_name)
        if not details:
            return False
        try:
            result = self.conn.delete(details["dn"])
            if not result:
                logger.error(f"Ошибка AD при удалении ПК {pc_name}: {self.conn.result}")
            return result
        except Exception as e:
            logger.error(f"Исключение при удалении ПК {pc_name}: {e}")
            return False

    def get_domain_stats(self):
        """Получение общей статистики по домену"""
        try:
            if not self._safe_search(self.base_dn, "(objectClass=user)", ['sAMAccountName', 'userAccountControl']):
                return None
            total_users = len(self.conn.entries)
            disabled_users = 0
            for entry in self.conn.entries:
                if entry.userAccountControl and (int(entry.userAccountControl.value) & 2):
                    disabled_users += 1
            active_users = total_users - disabled_users

            if not self._safe_search(self.base_dn, "(objectClass=computer)", ['name']):
                return None
            total_computers = len(self.conn.entries)

            return {
                "total_users": total_users,
                "active_users": active_users,
                "disabled_users": disabled_users,
                "total_computers": total_computers
            }
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return None

    def get_target_computers(self):
        """Динамический сбор компьютеров из папок KDB Computers, KDB Nout и Servers"""
        target_ous = [
            "OU=KDB Computers,DC=kdb,DC=kz",
            "OU=KDB Nout,DC=kdb,DC=kz",
            "CN=Servers,DC=kdb,DC=kz" # или OU=Servers, если это контейнер OU
        ]

        computers = []
        for ou_dn in target_ous:
            filter_str = "(&(objectClass=computer))"
            # Передаем dn подразделения как search_base (или первый аргумент твоего _safe_search)
            if self._safe_search(ou_dn, filter_str, ['dNSHostName', 'name']):
                for entry in self.conn.entries:
                    if hasattr(entry, 'dNSHostName') and entry.dNSHostName:
                        computers.append(str(entry.dNSHostName))
                    elif hasattr(entry, 'name') and entry.name:
                        computers.append(str(entry.name))

        # Убираем дубликаты и возвращаем список
        return list(set(computers))

    def get_user_photo(self, username: str):
        """Получение фото пользователя из AD (thumbnailPhoto)"""
        safe_username = escape_filter_chars(username)
        filter_str = f"(&(objectClass=user)(sAMAccountName={safe_username}))"
        if not self._safe_search(self.base_dn, filter_str, ['thumbnailPhoto']):
            return None

        if not self.conn.entries or not hasattr(self.conn.entries[0], 'thumbnailPhoto'):
            return None

        photo_data = self.conn.entries[0].thumbnailPhoto.value
        return photo_data if photo_data else None


    def get_locked_users(self) -> list[dict]:
        """Поиск заблокированных учетных записей (lockoutTime > 0)"""
        filter_str = "(&(objectCategory=person)(objectClass=user)(lockoutTime>=1))"
        attrs = ['sAMAccountName', 'displayName', 'department', 'title', 'lockoutTime', 'badPwdCount']
        if not self._safe_search(self.base_dn, filter_str, attrs):
            return []
        users = []
        for e in self.conn.entries:
            users.append({
                "username": str(e.sAMAccountName.value) if e.sAMAccountName else "",
                "display_name": str(e.displayName.value) if e.displayName else str(e.sAMAccountName.value),
                "department": str(e.department.value) if hasattr(e, 'department') and e.department else "—",
                "title": str(e.title.value) if hasattr(e, 'title') and e.title else "—",
                "bad_pwd_count": int(e.badPwdCount.value) if hasattr(e, 'badPwdCount') and e.badPwdCount else 0,
            })
        return users

ad_service = ActiveDirectoryService()
