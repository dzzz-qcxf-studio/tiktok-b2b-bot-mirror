"""认证模块 — JWT + API Key 双模式"""

import hashlib
import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel

from tiktok_bot_core.storage.database import get_db
from tiktok_bot_core.storage.sqlite_store import SqliteStore

_SECRET = os.getenv("JWT_SECRET", hashlib.sha256(os.urandom(64)).hexdigest()[:32])
ALGORITHM = "HS256"
EXPIRE_DAYS = 7
security = HTTPBearer(auto_error=False)

store = SqliteStore()


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    return salt + ":" + hashlib.sha256((salt + password).encode()).hexdigest()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":", 1)
        return h == hashlib.sha256((salt + password).encode()).hexdigest()
    except (ValueError, AttributeError):
        return False

# ===== Models =====


class LoginRequest(BaseModel):
    username: str
    password: str
    method: str = "password"  # "password" | "apikey"


class RegisterRequest(BaseModel):
    username: str
    password: str
    invite_code: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


# ===== Token =====


def create_token(username: str) -> str:
    return jwt.encode(
        {"sub": username, "exp": datetime.utcnow() + timedelta(days=EXPIRE_DAYS)},
        _SECRET, algorithm=ALGORITHM,
    )


def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


# ===== Login Logic =====


def authenticate(username: str, password: str) -> bool:
    db = get_db()
    with db.session() as s:
        user = store.get_account(s, username)  # 查 accounts 表
        if not user:
            return False
        return verify_password(password, user.password_hash or "")


def authenticate_apikey(apikey: str) -> Optional[str]:
    db = get_db()
    with db.session() as s:
        users = store.get_api_users(s)
        for u in users:
            stored = u.get("api_key_hash", "")
            if stored and verify_password(apikey, stored):
                return u["username"]
    return None


# ===== Dependency =====


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """从 JWT 或 API Key header 中提取当前用户，未登录则返回 'guest'"""
    # 1. JWT token
    if credentials:
        username = decode_token(credentials.credentials)
        if username:
            return username

    # 2. API Key (header: X-API-Key)
    apikey = request.headers.get("X-API-Key")
    if apikey:
        username = authenticate_apikey(apikey)
        if username:
            return username

    # 3. Query param ?token=xxx
    token = request.query_params.get("token")
    if token:
        username = decode_token(token)
        if username:
            return username

    return "guest"


async def require_user(current_user: str = Depends(get_current_user)):
    if current_user == "guest":
        raise HTTPException(status_code=401, detail="请先登录")
    return current_user
