from datetime import timedelta
import hashlib
import secrets
import unicodedata
import bcrypt
from .domain import Actor, DomainError, now, uid


class Identity:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def login(self, username, password=''):
        username = username.strip()
        if not 1 <= len(username) <= 64 or any(unicodedata.category(c).startswith("C") for c in username):
            raise DomainError("用户名须为 1–64 个可见字符", 422)
        admin = username in self.settings.admin_users
        if admin:
            if not self.settings.admin_password_hash:
                raise DomainError('管理员密码尚未配置，请设置 HIVE_ADMIN_PASSWORD_HASH', 503)
            try:
                valid = bcrypt.checkpw(password.encode(), self.settings.admin_password_hash.encode())
            except ValueError:
                valid = False
            if not valid:
                raise DomainError('管理员密码错误', 401)
        token = secrets.token_urlsafe(32)
        stamp = now()
        with self.db.transaction() as c:
            c.execute("INSERT INTO users (id,username,created_at,last_login_at) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE last_login_at=%s",
                      (uid(), username, stamp, stamp, stamp))
            c.execute("SELECT * FROM users WHERE username=%s FOR UPDATE", (username,))
            row = c.fetchone()
            if row['deleted_at'] is not None:
                raise DomainError('成员已被删除，请联系管理员', 403)
            actor = self.actor(row)
            c.execute("INSERT INTO sessions (token_hash,user_id,created_at,expires_at,admin_authenticated) VALUES (%s,%s,%s,%s,%s)",
                      (self.digest(token), actor.id, stamp, stamp + timedelta(hours=24), admin))
            self.db.audit(c, actor, "login", actor.id)
        return token, actor

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def current(self, token):
        if not token:
            raise DomainError("请先输入用户名登录", 401)
        row = self.db.one("SELECT u.*,s.admin_authenticated FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=%s AND s.expires_at>%s AND u.deleted_at IS NULL",
                          (self.digest(token), now()))
        if not row:
            raise DomainError("登录已过期，请重新登录", 401)
        if row['username'] in self.settings.admin_users and not row['admin_authenticated']:
            raise DomainError('请使用管理员密码重新登录', 401)
        return self.actor(row)

    def actor(self, row):
        admin = row['username'] in self.settings.admin_users
        return Actor(row['id'], row['username'], admin,
                     admin or bool(row['can_request']), admin or bool(row['can_view_credentials']))

    @staticmethod
    def require_admin(actor):
        if not actor.admin:
            raise DomainError('此操作需要管理员身份', 403)

    def members(self, actor):
        self.require_admin(actor)
        rows = self.db.all('SELECT id,username,created_at,last_login_at,can_request,can_view_credentials FROM users WHERE deleted_at IS NULL ORDER BY created_at,id')
        for row in rows:
            row['admin'] = row['username'] in self.settings.admin_users
            row['can_request'] = row['admin'] or bool(row['can_request'])
            row['can_view_credentials'] = row['admin'] or bool(row['can_view_credentials'])
        return rows

    def _member(self, cursor, member_id):
        cursor.execute('SELECT id,username FROM users WHERE id=%s AND deleted_at IS NULL FOR UPDATE', (member_id,))
        row = cursor.fetchone()
        if not row:
            raise DomainError('成员不存在', 404)
        if row['username'] in self.settings.admin_users:
            raise DomainError('不能删除管理员或修改管理员权限', 403)
        return row

    def permissions(self, actor, member_id, payload):
        self.require_admin(actor)
        with self.db.transaction() as c:
            self._member(c, member_id)
            c.execute('UPDATE users SET can_request=%s,can_view_credentials=%s WHERE id=%s',
                      (payload['can_request'], payload['can_view_credentials'], member_id))
            self.db.audit(c, actor, 'member.permissions', member_id, payload)

    def remove(self, actor, member_id):
        self.require_admin(actor)
        with self.db.transaction() as c:
            self._member(c, member_id)
            c.execute("SELECT id FROM resource_requests WHERE owner_user_id=%s AND status NOT IN ('RELEASED','CANCELLED','FAILED') LIMIT 1", (member_id,))
            if c.fetchone():
                raise DomainError('成员仍有未结束的申请，请先取消或归还资源')
            c.execute("SELECT id FROM executions WHERE owner_user_id=%s AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED') LIMIT 1", (member_id,))
            if c.fetchone():
                raise DomainError('成员仍有运行中的任务，请先结束任务')
            c.execute('UPDATE users SET deleted_at=%s,can_request=FALSE,can_view_credentials=FALSE WHERE id=%s', (now(), member_id))
            c.execute('DELETE FROM sessions WHERE user_id=%s', (member_id,))
            self.db.audit(c, actor, 'member.remove', member_id)

    def logout(self, token, actor):
        with self.db.transaction() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=%s", (self.digest(token),))
            self.db.audit(c, actor, "logout", actor.id)
