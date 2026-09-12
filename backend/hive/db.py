from contextlib import contextmanager
from pathlib import Path
import re
import pymysql
from pymysql.cursors import DictCursor
from .domain import encode, uid, now


class Database:
    def __init__(self, settings):
        self.settings = settings

    def connect(self):
        s = self.settings
        return pymysql.connect(host=s.mysql_host, port=s.mysql_port, user=s.mysql_user,
                               password=s.mysql_password, database=s.mysql_database,
                               charset="utf8mb4", cursorclass=DictCursor, autocommit=False,
                               connect_timeout=5, read_timeout=30, write_timeout=30,
                               init_command="SET time_zone = '+00:00'")

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def all(self, sql, args=()):
        with self.transaction() as cursor:
            cursor.execute(sql, args)
            return cursor.fetchall()

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def migrate(self):
        with self.transaction() as c:
            c.execute("CREATE TABLE IF NOT EXISTS schema_versions (version VARCHAR(80) PRIMARY KEY)")
        for file in sorted((Path(__file__).parent / "sql").glob("*.sql")):
            if self.one("SELECT version FROM schema_versions WHERE version=%s", (file.name,)):
                continue
            # Migration files contain only plain DDL, no procedural SQL.
            with self.transaction() as c:
                for statement in file.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        added=re.fullmatch(r"\s*ALTER TABLE (\w+) ADD COLUMN (\w+) .+",statement,re.S|re.I)
                        if added:
                            c.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                                      (self.settings.mysql_database,added[1],added[2]))
                            if c.fetchone():
                                continue  # DDL may have committed before a crashed migration checkpoint.
                        c.execute(statement)
                c.execute("INSERT INTO schema_versions VALUES (%s)", (file.name,))

    @staticmethod
    def audit(cursor, actor, action, object_id, detail=None):
        cursor.execute(
            "INSERT INTO audit_events (id,actor_id,username,action,object_id,detail,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (uid(), actor.id, actor.username, action, object_id, encode(detail or {}), now()))
