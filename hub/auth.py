"""Revocable device/browser credentials and single-team membership."""
from __future__ import annotations
import hashlib
import secrets
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from sqlalchemy import select, delete
from . import db

hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def public_user(row):
    return {k: row[k] for k in ('id', 'username', 'display_name', 'role', 'active')}

def verify_password(password, password_hash):
    if not password_hash:
        return False
    try:
        return hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False

def issue_token(conn, user_id, kind='browser'):
    raw = secrets.token_urlsafe(48)
    expiry = db.now() + (30 if kind == 'device' else 7) * 86400
    conn.execute(db.tokens.insert().values(id=db.new_id(), user_id=user_id, token_hash=digest(raw), kind=kind, expires_at=expiry))
    return raw

def lookup_token(conn, raw):
    if not raw or len(raw) > 256:
        return None
    return conn.execute(select(db.users).join(db.tokens, db.users.c.id == db.tokens.c.user_id).where(db.tokens.c.token_hash == digest(raw), db.tokens.c.expires_at > db.now(), db.users.c.active.is_(True), db.users.c.id != 'local')).mappings().first()

def revoke_user(conn, user_id):
    conn.execute(delete(db.tokens).where(db.tokens.c.user_id == user_id))

def bootstrap_admin(engine, username, display_name, password):
    if len(password) < 12 or len(password) > 1024:
        raise ValueError('管理员密码长度必须为 12–1024 个字符')
    username = username.strip().lower()
    if not username or len(username) > 100:
        raise ValueError('用户名不能为空或超过 100 个字符')
    with engine.begin() as conn:
        if conn.execute(select(db.users.c.id).where(db.users.c.role == 'admin', db.users.c.id != 'local')).first():
            raise ValueError('管理员已初始化，请使用邀请功能创建成员')
        uid = db.new_id()
        conn.execute(db.users.insert().values(id=uid, username=username, display_name=display_name or username, password_hash=hasher.hash(password), role='admin', active=True))
    return uid
