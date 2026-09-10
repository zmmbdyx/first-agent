"""本地数据加密存储：sessions/ uploads/ 落盘即加密（Fernet）。
密钥来源：环境变量 DATA_KEY > data/.key 文件（自动生成，已被 .gitignore 排除）。
兼容旧明文文件：读取时无加密前缀则按原文处理；加密不可用时静默降级为明文。"""
import base64
import hashlib
import os
from pathlib import Path

from config import ROOT

ENC_PREFIX = b"ENC1:"
_key = None
_available = None

try:
    from cryptography.fernet import Fernet
    _available = True
except ImportError:
    _available = False


def enabled() -> bool:
    return bool(_available)


def _get_key() -> bytes:
    global _key
    if _key:
        return _key
    env_key = os.getenv("DATA_KEY")
    if env_key:
        # 任意口令 → 派生合法 Fernet key
        _key = base64.urlsafe_b64encode(hashlib.sha256(env_key.encode()).digest())
        return _key
    key_file = ROOT / "data" / ".key"
    if key_file.exists():
        _key = key_file.read_bytes().strip()
    else:
        _key = Fernet.generate_key()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(_key)
    return _key


def encrypt(data: bytes) -> bytes:
    if not _available:
        return data
    return ENC_PREFIX + Fernet(_get_key()).encrypt(data)


def decrypt(data: bytes) -> bytes:
    if not data.startswith(ENC_PREFIX):
        return data  # 旧明文兼容
    if not _available:
        raise RuntimeError("文件已加密但缺少 cryptography 库，请 pip install cryptography")
    return Fernet(_get_key()).decrypt(data[len(ENC_PREFIX):])


def read_bytes(path) -> bytes:
    """读取可能加密的文件并解密（工具层统一入口）。"""
    return decrypt(Path(path).read_bytes())


def write_bytes(path, data: bytes):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(encrypt(data))
