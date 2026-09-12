from datetime import timedelta
import hashlib
import secrets
import unicodedata
from .domain import Actor, DomainError, now, uid


class Identity:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def login(self, username):
        username = username.strip()
        if not 1 <= len(username) <= 64 or any(unicodedata.category(c).startswith("C") for c in username):
            raise DomainError("用户名须为 1–64 个可见字符", 422)
        token = secrets.token_urlsafe(32)
        stamp = now()
        with self.db.transaction() as c:
            c.execute("INSERT INTO users VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE last_login_at=%s",
                      (uid(), username, stamp, stamp, stamp))
            c.execute("SELECT id,username FROM users WHERE username=%s", (username,))
            row = c.fetchone()
            actor = Actor(row["id"], row["username"], username in self.settings.admin_users)
            c.execute("INSERT INTO sessions VALUES (%s,%s,%s,%s)",
                      (self.digest(token), actor.id, stamp, stamp + timedelta(hours=24)))
            self.db.audit(c, actor, "login", actor.id)
        return token, actor

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def current(self, token):
        if not token:
            raise DomainError("请先输入用户名登录", 401)
        row = self.db.one("SELECT u.id,u.username FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=%s AND s.expires_at>%s",
                          (self.digest(token), now()))
        if not row:
            raise DomainError("登录已过期，请重新登录", 401)
        return Actor(row["id"], row["username"], row["username"] in self.settings.admin_users)

    def logout(self, token, actor):
        with self.db.transaction() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=%s", (self.digest(token),))
            self.db.audit(c, actor, "logout", actor.id)
