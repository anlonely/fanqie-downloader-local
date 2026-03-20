from __future__ import annotations

import io
import json
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from hashlib import pbkdf2_hmac
from html import unescape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from Cryptodome.Cipher import AES


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
DATA_ROOT = APP_ROOT / "data"
DOWNLOAD_ROOT = Path.home() / "Downloads"
SESSION_FILE = DATA_ROOT / "session.json"
JOBS_FILE = DATA_ROOT / "jobs.json"
CHARSET_FILE = DATA_ROOT / "charset.json"
EXTENSION_ROOT = APP_ROOT / "chrome_extension_fanqie_cookie"
DEFAULT_PORT = 18930
REQUEST_TIMEOUT = 20
SESSION_VALIDATION_TTL = 180.0
CHROME_PROFILE_ROOT = Path.home() / "Library/Application Support/Google/Chrome"
DEBUG_CHROME_PROFILE_ROOT = Path("/tmp/fanqie-debug-profile")
BROWSER_LOGIN_REQUIRED_COOKIES = {
    "sessionid",
    "sessionid_ss",
    "uid_tt",
    "uid_tt_ss",
    "ttwid",
    "d_ticket",
    "odin_tt",
}


def now_ts() -> float:
    return time.time()


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def parse_cookie_header(raw_cookie: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for part in str(raw_cookie or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            pairs[name] = value
    return pairs


def parse_cookie_export_text(raw_text: str) -> tuple[str, dict[str, Any]]:
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("Cookie 为空或格式不正确")

    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except Exception as error:
            raise ValueError(f"Cookie 导出 JSON 无法解析: {error}") from error
        if not isinstance(payload, dict):
            raise ValueError("Cookie 导出 JSON 必须是对象")
        cookie_header = str(
            payload.get("cookieHeader")
            or payload.get("cookie")
            or payload.get("cookies")
            or ""
        ).strip()
        if not cookie_header:
            raise ValueError("Cookie 导出 JSON 缺少 cookieHeader")
        metadata = {
            "source": str(payload.get("source") or "browser-extension").strip(),
            "page_url": str(payload.get("pageUrl") or payload.get("url") or "").strip(),
            "profile_path": str(payload.get("profilePath") or "").strip(),
            "user_agent": str(payload.get("userAgent") or "").strip(),
            "exported_at": str(payload.get("exportedAt") or "").strip(),
            "cookie_names": list(payload.get("cookieNames") or []),
            "login_state": {
                "has_authentication": bool((payload.get("loginState") or {}).get("hasAuthentication")),
                "user_name": str((payload.get("loginState") or {}).get("userName") or "").strip(),
                "avatar": str((payload.get("loginState") or {}).get("avatar") or "").strip(),
            },
        }
        return cookie_header, metadata

    return text, {}


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", str(name or "").strip())


def generate_filename(book_name: str, author_name: str, extension: str) -> str:
    safe_book_name = sanitize_filename(book_name) or "未命名书籍"
    safe_author_name = sanitize_filename(author_name)
    ext = str(extension or "txt").lstrip(".") or "txt"
    if safe_author_name:
        return f"{safe_book_name} 作者：{safe_author_name}.{ext}"
    return f"{safe_book_name}.{ext}"


def extract_numeric_id(target: str) -> str:
    text = str(target or "").strip()
    if not text:
        raise ValueError("请输入番茄书籍链接、章节链接或对应 ID")
    match = re.search(r"(?<!\d)(\d{10,})(?!\d)", text)
    if match:
        return match.group(1)
    raise ValueError("未能从输入中识别番茄 ID")


def extract_error_summary(body: str, fallback: str) -> str:
    text = str(body or "").strip()
    if not text:
        return str(fallback or "请求失败")

    title_match = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = re.sub(r"\s+", " ", title_match.group(1)).strip()
        if title:
            return title[:120]

    normalized = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
    if normalized:
        return normalized[:160]
    return str(fallback or "请求失败")


def extract_json_after_marker(text: str, marker: str = "window.__INITIAL_STATE__=") -> dict[str, Any]:
    marker_index = text.find(marker)
    if marker_index < 0:
        raise ValueError("页面中未找到 window.__INITIAL_STATE__")

    start = text.find("{", marker_index + len(marker))
    if start < 0:
        raise ValueError("页面中的初始化状态缺少 JSON 起始符")

    depth = 0
    in_string = False
    escaping = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaping:
                escaping = False
            elif char == "\\":
                escaping = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blob = text[start:index + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    sanitized = re.sub(r"(?<=[:\[,])\s*undefined(?=\s*[,}\]])", " null", blob)
                    return json.loads(sanitized)

    raise ValueError("页面中的初始化状态 JSON 未闭合")


def looks_like_browser_login(cookie_names: list[str] | set[str], login_state: dict[str, Any] | None = None) -> bool:
    names = set(map(str, cookie_names or []))
    if login_state and bool(login_state.get("has_authentication")):
        return True
    return BROWSER_LOGIN_REQUIRED_COOKIES.issubset(names)


def _chrome_safe_storage_key() -> bytes:
    password = subprocess.check_output(
        ["security", "find-generic-password", "-s", "Chrome Safe Storage", "-w"],
        stderr=subprocess.DEVNULL,
    ).decode("utf-8", errors="ignore").strip()
    if not password:
        raise RuntimeError("未能从 macOS Keychain 读取 Chrome Safe Storage")
    return pbkdf2_hmac("sha1", password.encode("utf-8"), b"saltysalt", 1003, dklen=16)


def _decrypt_chrome_cookie_value(encrypted_value: bytes, key: bytes) -> str:
    if not encrypted_value:
        return ""
    if encrypted_value.startswith(b"v10"):
        payload = encrypted_value[3:]
        raw = AES.new(key, AES.MODE_CBC, b" " * 16).decrypt(payload)
        pad = raw[-1]
        if pad:
            raw = raw[:-pad]
        if len(raw) > 32:
            raw = raw[32:]
        return raw.decode("utf-8", errors="ignore")
    return encrypted_value.decode("utf-8", errors="ignore")


def _chrome_profile_candidates() -> list[Path]:
    roots = [CHROME_PROFILE_ROOT, DEBUG_CHROME_PROFILE_ROOT]
    profiles: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        direct = root / "Cookies"
        if direct.exists():
            profiles.append(root)
        default_profile = root / "Default"
        if default_profile.exists():
            profiles.append(default_profile)
        profiles.extend(sorted(path for path in root.glob("Profile *") if path.is_dir()))
    deduped: list[Path] = []
    seen: set[str] = set()
    for profile in profiles:
        token = str(profile.resolve())
        if token in seen:
            continue
        seen.add(token)
        deduped.append(profile)
    return deduped


def read_fanqie_cookie_payload_from_local_chrome() -> dict[str, Any]:
    key = _chrome_safe_storage_key()
    best_payload: dict[str, Any] | None = None
    best_score = -1

    for profile_dir in _chrome_profile_candidates():
        cookies_db = profile_dir / "Cookies"
        if not cookies_db.exists():
            continue

        temp_copy = Path(tempfile.mktemp(prefix="fanqie-cookies-", suffix=".sqlite"))
        try:
            shutil.copy2(cookies_db, temp_copy)
            with sqlite3.connect(str(temp_copy)) as connection:
                rows = connection.execute(
                    """
                    select host_key, name, value, encrypted_value
                    from cookies
                    where host_key like '%fanqienovel.com%'
                    order by name
                    """
                ).fetchall()
        except Exception:
            rows = []
        finally:
            try:
                temp_copy.unlink(missing_ok=True)
            except Exception:
                pass

        if not rows:
            continue

        cookies: dict[str, str] = {}
        for _host_key, name, value, encrypted_value in rows:
            plaintext = str(value or "").strip()
            if not plaintext and encrypted_value:
                plaintext = _decrypt_chrome_cookie_value(bytes(encrypted_value), key).strip()
            if plaintext:
                cookies[str(name)] = plaintext

        if not cookies:
            continue

        cookie_names = sorted(cookies.keys())
        score = len(set(cookie_names) & BROWSER_LOGIN_REQUIRED_COOKIES) * 100 + len(cookie_names)
        if score <= best_score:
            continue

        best_score = score
        best_payload = {
            "source": "local-chrome",
            "pageUrl": "https://fanqienovel.com/",
            "title": "",
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "userAgent": "",
            "cookieNames": cookie_names,
            "cookieHeader": "; ".join(f"{name}={value}" for name, value in cookies.items()),
            "loginState": {
                "hasAuthentication": looks_like_browser_login(cookie_names),
                "userName": "",
                "avatar": "",
            },
            "profilePath": str(profile_dir),
        }

    if not best_payload:
        raise RuntimeError("未从本机 Chrome 读取到 fanqienovel.com Cookie，请先在 Chrome 中登录番茄网页")

    return best_payload


def flatten_chapters(chapter_groups: Any) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    groups = chapter_groups if isinstance(chapter_groups, list) else []

    for group in groups:
        if isinstance(group, dict):
            chapter_list = [group]
        elif isinstance(group, list):
            chapter_list = [item for item in group if isinstance(item, dict)]
        else:
            continue

        for item in chapter_list:
            item_id = str(item.get("itemId") or "").strip()
            if not item_id:
                continue
            flattened.append(
                {
                    "item_id": item_id,
                    "title": str(item.get("title") or "").strip() or item_id,
                    "index": int(item.get("realChapterOrder") or len(flattened) + 1),
                    "volume_name": str(item.get("volume_name") or "").strip(),
                    "need_pay": bool(item.get("needPay")),
                    "locked": bool(item.get("isChapterLock") or item.get("isPaidPublication") or item.get("isPaidStory")),
                }
            )

    flattened.sort(key=lambda chapter: (chapter["index"], chapter["item_id"]))
    return flattened


def clean_chapter_html(content_html: str) -> str:
    text = str(content_html or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = text.replace("\r", "")
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    paragraphs = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n\n".join(paragraphs)


def is_pua(char: str) -> bool:
    code_point = ord(char)
    return 0xE000 <= code_point <= 0xF8FF


class DownloadPaused(RuntimeError):
    def __init__(self, runtime_state: dict[str, Any], message: str = "任务已暂停") -> None:
        super().__init__(message)
        self.runtime_state = runtime_state


class DownloadCancelled(RuntimeError):
    def __init__(self, runtime_state: dict[str, Any], message: str = "任务已取消") -> None:
        super().__init__(message)
        self.runtime_state = runtime_state


class CharacterDecoder:
    CODE_RANGES = ((58344, 58715), (58345, 58716))

    def __init__(self, charset_path: Path) -> None:
        data = json.loads(charset_path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or len(data) != 2:
            raise ValueError("charset.json 格式不正确")
        self.charset: list[list[str]] = [list(map(str, item)) for item in data]

    def decode(self, text: str, mode: int) -> str:
        start, end = self.CODE_RANGES[mode]
        table = self.charset[mode]
        result: list[str] = []
        for char in str(text or ""):
            code_point = ord(char)
            if start <= code_point <= end:
                offset = code_point - start
                if 0 <= offset < len(table) and table[offset] and table[offset] != "?":
                    result.append(table[offset])
                else:
                    result.append(char)
            else:
                result.append(char)
        return "".join(result)

    def decode_best(self, text: str) -> str:
        payload = str(text or "")
        if not any(is_pua(char) for char in payload):
            return payload
        candidate_zero = self.decode(payload, 0)
        candidate_one = self.decode(payload, 1)
        if self._score(candidate_zero) >= self._score(candidate_one):
            return candidate_zero
        return candidate_one

    @staticmethod
    def _score(text: str) -> int:
        score = 0
        for char in text[:4000]:
            code_point = ord(char)
            if is_pua(char):
                score -= 5
            elif 0x4E00 <= code_point <= 0x9FFF:
                score += 3
            elif char.isascii() and (char.isalnum() or char in " ，。！？：；、“”‘’《》（）—-…,.!?;:[]()[]/"):
                score += 1
            elif char == "?":
                score -= 1
        return score


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        data = self._read()
        if not str(data.get("novel_web_id") or "").strip():
            data["novel_web_id"] = str(random.randint(10**18, 10**19 - 1))
            self._write(data)

    def _read(self) -> dict[str, Any]:
        try:
            if self.path.exists():
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
        except Exception:
            pass
        return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def has_cookie(self) -> bool:
        data = self._read()
        return bool(str(data.get("cookie_header") or "").strip())

    def cookie_header(self) -> str:
        with self._lock:
            data = self._read()
            cookies = parse_cookie_header(str(data.get("cookie_header") or ""))
            novel_web_id = str(data.get("novel_web_id") or "").strip() or str(random.randint(10**18, 10**19 - 1))
            data["novel_web_id"] = novel_web_id
            self._write(data)
        if cookies and "novel_web_id" not in cookies:
            cookies["novel_web_id"] = novel_web_id
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    def save_cookie_header(self, raw_cookie: str) -> dict[str, Any]:
        cookie_header, metadata = parse_cookie_export_text(raw_cookie)
        cookies = parse_cookie_header(cookie_header)
        if not cookies:
            raise ValueError("Cookie 为空或格式不正确")

        with self._lock:
            data = self._read()
            if "novel_web_id" in cookies:
                data["novel_web_id"] = cookies["novel_web_id"]
            data["cookie_header"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
            data["updated_at"] = now_text()
            data["validation"] = {
                "state": "pending",
                "valid": False,
                "message": "正在校验登录态",
                "checked_at": "",
                "checked_at_ts": 0.0,
                "user_name": "",
                "avatar": "",
            }
            if metadata:
                data["last_import"] = metadata
            self._write(data)
        return self.info()

    def save_cookie_from_simple_cookie(self, cookie: SimpleCookie[str]) -> dict[str, Any]:
        parts = [f"{morsel.key}={morsel.value}" for morsel in cookie.values()]
        return self.save_cookie_header("; ".join(parts))

    def clear(self, *, reason: str = "", preserve_last_import: bool = False) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            novel_web_id = str(data.get("novel_web_id") or "").strip() or str(random.randint(10**18, 10**19 - 1))
            payload: dict[str, Any] = {"novel_web_id": novel_web_id}
            if preserve_last_import and isinstance(data.get("last_import"), dict):
                payload["last_import"] = data["last_import"]
            if reason:
                payload["validation"] = {
                    "state": "expired",
                    "valid": False,
                    "message": reason,
                    "checked_at": now_text(),
                    "checked_at_ts": now_ts(),
                    "user_name": "",
                    "avatar": "",
                }
            self._write(payload)
        return self.info()

    def update_validation(self, payload: dict[str, Any], *, auto_clear: bool = False) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["validation"] = {
                "state": str(payload.get("state") or "unknown"),
                "valid": bool(payload.get("valid")),
                "message": str(payload.get("message") or ""),
                "checked_at": str(payload.get("checked_at") or now_text()),
                "checked_at_ts": float(payload.get("checked_at_ts") or now_ts()),
                "user_name": str(payload.get("user_name") or ""),
                "avatar": str(payload.get("avatar") or ""),
            }
            if auto_clear:
                data.pop("cookie_header", None)
                data.pop("updated_at", None)
            self._write(data)
        return self.info()

    def info(self) -> dict[str, Any]:
        data = self._read()
        configured = bool(str(data.get("cookie_header") or "").strip())
        cookies = parse_cookie_header(str(data.get("cookie_header") or ""))
        if cookies and str(data.get("novel_web_id") or "").strip():
            cookies["novel_web_id"] = str(data["novel_web_id"])
        validation = data.get("validation") if isinstance(data.get("validation"), dict) else {}
        if not configured and str(validation.get("state") or "") != "expired":
            validation = {}
        return {
            "configured": configured,
            "cookie_names": sorted(cookies.keys()),
            "updated_at": str(data.get("updated_at") or "").strip(),
            "session_file": str(self.path),
            "last_import": data.get("last_import") if isinstance(data.get("last_import"), dict) else {},
            "validation": {
                "state": str(validation.get("state") or ("not_configured" if not configured else "pending")),
                "valid": bool(validation.get("valid")),
                "message": str(validation.get("message") or ("未保存登录态" if not configured else "等待校验")),
                "checked_at": str(validation.get("checked_at") or ""),
                "checked_at_ts": float(validation.get("checked_at_ts") or 0.0),
                "user_name": str(validation.get("user_name") or ""),
                "avatar": str(validation.get("avatar") or ""),
            },
        }


class FanqieClient:
    def __init__(self, decoder: CharacterDecoder, session_store: SessionStore) -> None:
        self.decoder = decoder
        self.session_store = session_store
        self.user_agents = [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.0; rv:136.0) Gecko/20100101 Firefox/136.0",
        ]

    def _headers(self, *, include_cookie: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://fanqienovel.com/",
            "User-Agent": random.choice(self.user_agents),
        }
        if include_cookie:
            cookie_header = self.session_store.cookie_header()
            if cookie_header:
                headers["Cookie"] = cookie_header
        return headers

    def _fetch_text(self, url: str, *, include_cookie: bool = True) -> str:
        request = Request(url, headers=self._headers(include_cookie=include_cookie))
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return response.read().decode("utf-8", errors="ignore")
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            message = extract_error_summary(body, str(error.reason or "请求失败"))
            failure = RuntimeError(f"请求失败: {url} ({error.code}) {message}")
            setattr(failure, "status_code", int(error.code))
            setattr(failure, "url", url)
            raise failure from error
        except URLError as error:
            raise RuntimeError(f"网络错误: {url} ({error.reason})") from error

    def validate_session(self) -> dict[str, Any]:
        if not self.session_store.has_cookie():
            return {
                "state": "not_configured",
                "valid": False,
                "message": "未保存登录态",
                "checked_at": now_text(),
                "checked_at_ts": now_ts(),
                "user_name": "",
                "avatar": "",
            }

        html = self._fetch_text("https://fanqienovel.com/", include_cookie=True)
        state = extract_json_after_marker(html)
        common = state.get("common") if isinstance(state.get("common"), dict) else {}
        authenticated = bool(common.get("hasAuthentication"))
        if not authenticated:
            return {
                "state": "unauthenticated",
                "valid": False,
                "message": "已读取到 Cookie，但番茄首页当前仍返回未登录状态。请先在番茄网页确认右上角已出现头像或昵称，再重新同步登录态。",
                "checked_at": now_text(),
                "checked_at_ts": now_ts(),
                "user_name": str(common.get("name") or ""),
                "avatar": str(common.get("avatar") or ""),
            }
        return {
            "state": "valid",
            "valid": True,
            "message": "登录态可用",
            "checked_at": now_text(),
            "checked_at_ts": now_ts(),
            "user_name": str(common.get("name") or ""),
            "avatar": str(common.get("avatar") or ""),
        }

    def _resolve_book_id_from_reader(self, item_id: str) -> str:
        html = self._fetch_text(f"https://fanqienovel.com/reader/{item_id}")
        state = extract_json_after_marker(html)
        reader = state.get("reader") if isinstance(state.get("reader"), dict) else {}
        chapter_data = reader.get("chapterData") if isinstance(reader.get("chapterData"), dict) else {}
        book_id = str(chapter_data.get("bookId") or "").strip()
        if not book_id:
            raise RuntimeError("章节页中未找到所属书籍 ID")
        return book_id

    def _normalize_book_target(self, target: str) -> str:
        text = str(target or "").strip()
        parsed = urlparse(text)
        path = parsed.path.strip()

        page_match = re.search(r"/page/(\d{10,})", path)
        if page_match:
            return page_match.group(1)

        reader_match = re.search(r"/reader/(\d{10,})", path)
        if reader_match:
            return self._resolve_book_id_from_reader(reader_match.group(1))

        numeric_id = extract_numeric_id(text)
        try:
            self._fetch_text(f"https://fanqienovel.com/page/{numeric_id}")
            return numeric_id
        except RuntimeError as error:
            if int(getattr(error, "status_code", 0) or 0) != 404:
                raise
        return self._resolve_book_id_from_reader(numeric_id)

    def get_book(self, target: str) -> dict[str, Any]:
        book_id = self._normalize_book_target(target)
        html = self._fetch_text(f"https://fanqienovel.com/page/{book_id}")
        state = extract_json_after_marker(html)
        page = state.get("page") if isinstance(state.get("page"), dict) else {}
        chapters = flatten_chapters(page.get("chapterListWithVolume"))
        if not chapters:
            raise RuntimeError("未从目录页解析到章节列表")

        return {
            "book_id": str(page.get("bookId") or book_id),
            "book_name": str(page.get("bookName") or "未知书名").strip(),
            "author": str(page.get("authorName") or page.get("author") or "未知作者").strip(),
            "abstract": str(page.get("description") or page.get("abstract") or "").strip(),
            "status": str(page.get("status") or page.get("creationStatus") or "").strip(),
            "category": str(page.get("category") or "").strip(),
            "thumb_url": str(page.get("thumbUrl") or page.get("thumbUri") or "").strip(),
            "chapter_total": int(page.get("chapterTotal") or len(chapters)),
            "chapters": chapters,
        }

    def get_chapter(self, item_id: str) -> dict[str, Any]:
        html = self._fetch_text(f"https://fanqienovel.com/reader/{item_id}")
        state = extract_json_after_marker(html)
        reader = state.get("reader") if isinstance(state.get("reader"), dict) else {}
        chapter_data = reader.get("chapterData") if isinstance(reader.get("chapterData"), dict) else {}
        raw_html = str(chapter_data.get("content") or "")
        if not raw_html:
            raise RuntimeError(f"章节 {item_id} 内容为空")

        decoded_html = self.decoder.decode_best(raw_html)
        content = clean_chapter_html(decoded_html)
        if not content:
            raise RuntimeError(f"章节 {item_id} 解码后为空")

        return {
            "item_id": str(item_id),
            "title": str(chapter_data.get("title") or item_id).strip(),
            "content": content,
        }

    def download_book(
        self,
        target: str,
        output_dir: str,
        *,
        progress_callback: Any | None = None,
        control_callback: Any | None = None,
        runtime_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        runtime = dict(runtime_state or {})
        book = runtime.get("book") if isinstance(runtime.get("book"), dict) else None
        if book is None:
            book = self.get_book(target)
        chapters = runtime.get("chapters") if isinstance(runtime.get("chapters"), list) else book["chapters"]
        results = runtime.get("results") if isinstance(runtime.get("results"), dict) else {}
        failures = runtime.get("failures") if isinstance(runtime.get("failures"), dict) else {}

        total = len(chapters)
        save_dir = Path(output_dir or DOWNLOAD_ROOT).expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)

        def build_runtime() -> dict[str, Any]:
            return {
                "book": book,
                "chapters": chapters,
                "results": results,
                "failures": failures,
            }

        def emit(current: int, title: str, message: str) -> None:
            if progress_callback:
                progress_callback(
                    {
                        "current": current,
                        "total": total,
                        "title": title,
                        "message": message,
                        "book_name": book["book_name"],
                    }
                )

        emit(len(results) + len(failures), "", "已解析书籍，准备执行队列任务")

        for chapter in chapters:
            chapter_index = int(chapter["index"])
            if chapter_index in results or chapter_index in failures:
                continue

            action = control_callback() if control_callback else ""
            if action == "pause":
                raise DownloadPaused(build_runtime(), "任务已暂停")
            if action == "cancel":
                raise DownloadCancelled(build_runtime(), "任务已取消")

            time.sleep(0.16)
            try:
                payload = self.get_chapter(chapter["item_id"])
                payload["index"] = chapter_index
                payload["requested_title"] = chapter["title"]
                payload["need_pay"] = chapter["need_pay"]
                payload["locked"] = chapter["locked"]
                results[chapter_index] = payload
                emit(len(results) + len(failures), payload["title"], f"已完成 {payload['title']}")
            except Exception as error:
                failures[chapter_index] = {
                    "index": chapter_index,
                    "item_id": chapter["item_id"],
                    "title": chapter["title"],
                    "error": str(error),
                }
                emit(len(results) + len(failures), chapter["title"], f"失败: {chapter['title']}")

        ordered_lines: list[str] = [
            book["book_name"],
            f"作者：{book['author']}",
            f"书籍 ID：{book['book_id']}",
            f"章节总数：{book['chapter_total']}",
        ]
        if book["abstract"]:
            ordered_lines.extend(["", "简介：", book["abstract"]])

        for chapter in chapters:
            chapter_index = int(chapter["index"])
            ordered_lines.extend(["", "", chapter["title"], ""])
            result = results.get(chapter_index)
            if result:
                ordered_lines.append(str(result["content"]))
            else:
                ordered_lines.append(f"[下载失败] {chapter['title']}")

        filename = generate_filename(book["book_name"], book["author"], "txt")
        output_path = save_dir / filename
        output_path.write_text("\n".join(ordered_lines).strip() + "\n", encoding="utf-8")

        return {
            "book": book,
            "output_path": str(output_path),
            "downloaded_chapters": len(results),
            "failed_chapters": [failures[index] for index in sorted(failures)],
            "total_chapters": total,
        }


@dataclass
class DownloadJob:
    job_id: str
    target: str
    output_dir: str
    status: str
    created_at: str
    updated_at: str
    progress_current: int
    progress_total: int
    message: str
    book_name: str = ""
    title: str = ""
    result_path: str = ""
    error: str = ""
    failures: list[dict[str, Any]] = field(default_factory=list)
    pinned: bool = False
    queue_rank: int = 0
    pause_requested: bool = False
    cancel_requested: bool = False
    delete_requested: bool = False

    def to_record(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "target": self.target,
            "output_dir": self.output_dir,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "message": self.message,
            "book_name": self.book_name,
            "title": self.title,
            "result_path": self.result_path,
            "error": self.error,
            "failures": list(self.failures),
            "pinned": self.pinned,
            "queue_rank": self.queue_rank,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.to_record(),
            "can_pause": self.status in {"queued", "running"},
            "can_resume": self.status == "paused",
            "can_pin": self.status in {"queued", "paused", "failed", "completed", "canceled"},
            "can_delete": self.status not in {"canceling"},
            "can_open_file": bool(self.result_path and self.status == "completed"),
            "can_open_folder": bool(self.result_path and self.status == "completed"),
        }

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> "DownloadJob":
        status = str(payload.get("status") or "queued")
        if status in {"running", "canceling"}:
            status = "paused"
            message = "应用重启后任务已暂停，请手动继续"
        else:
            message = str(payload.get("message") or "")
        return cls(
            job_id=str(payload.get("job_id") or uuid.uuid4().hex[:8]),
            target=str(payload.get("target") or ""),
            output_dir=str(payload.get("output_dir") or str(DOWNLOAD_ROOT)),
            status=status,
            created_at=str(payload.get("created_at") or now_text()),
            updated_at=str(payload.get("updated_at") or now_text()),
            progress_current=int(payload.get("progress_current") or 0),
            progress_total=int(payload.get("progress_total") or 0),
            message=message or "等待执行",
            book_name=str(payload.get("book_name") or ""),
            title=str(payload.get("title") or ""),
            result_path=str(payload.get("result_path") or ""),
            error=str(payload.get("error") or ""),
            failures=list(payload.get("failures") or []),
            pinned=bool(payload.get("pinned")),
            queue_rank=int(payload.get("queue_rank") or 0),
        )


class AppState:
    def __init__(self) -> None:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.decoder = CharacterDecoder(CHARSET_FILE)
        self.session_store = SessionStore(SESSION_FILE)
        self.client = FanqieClient(self.decoder, self.session_store)
        self._jobs: dict[str, DownloadJob] = {}
        self._runtime: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._next_rank = 1
        self._load_jobs()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def _load_jobs(self) -> None:
        try:
            if JOBS_FILE.exists():
                payload = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict):
                            job = DownloadJob.from_record(item)
                            self._jobs[job.job_id] = job
        except Exception:
            self._jobs = {}
        if self._jobs:
            self._next_rank = max(job.queue_rank for job in self._jobs.values()) + 1

    def _save_jobs_locked(self) -> None:
        payload = [job.to_record() for job in sorted(self._jobs.values(), key=self._list_sort_key)]
        JOBS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _list_sort_key(self, job: DownloadJob) -> tuple[int, int, int, str]:
        status_priority = {
            "running": 0,
            "canceling": 1,
            "queued": 2,
            "paused": 3,
            "failed": 4,
            "completed": 5,
            "canceled": 6,
        }
        pin_group = 0 if job.pinned else 1
        return (status_priority.get(job.status, 9), pin_group, job.queue_rank, job.created_at)

    def _next_job_locked(self) -> DownloadJob | None:
        candidates = [job for job in self._jobs.values() if job.status == "queued"]
        if not candidates:
            return None
        candidates.sort(key=lambda job: (0 if job.pinned else 1, job.queue_rank, job.created_at))
        return candidates[0]

    def _control_action_locked(self, job: DownloadJob) -> str:
        if job.delete_requested or job.cancel_requested:
            return "cancel"
        if job.pause_requested:
            return "pause"
        return ""

    def _worker_loop(self) -> None:
        while True:
            job: DownloadJob | None = None
            with self._lock:
                job = self._next_job_locked()
                if job is not None:
                    job.status = "running"
                    job.updated_at = now_text()
                    job.message = "准备下载"
                    job.pause_requested = False
                    job.cancel_requested = False
                    self._save_jobs_locked()

            if job is None:
                time.sleep(0.3)
                continue

            try:
                summary = self.client.download_book(
                    job.target,
                    job.output_dir,
                    progress_callback=lambda payload, job_id=job.job_id: self._update_job_progress(job_id, payload),
                    control_callback=lambda job_id=job.job_id: self._job_control_action(job_id),
                    runtime_state=self._runtime.get(job.job_id),
                )
                with self._lock:
                    current = self._jobs.get(job.job_id)
                    if current is None:
                        continue
                    current.status = "completed"
                    current.updated_at = now_text()
                    current.progress_current = int(summary["downloaded_chapters"] + len(summary["failed_chapters"]))
                    current.progress_total = int(summary["total_chapters"])
                    current.book_name = str(summary["book"]["book_name"])
                    current.title = "完成"
                    current.message = f"已保存到 {summary['output_path']}"
                    current.result_path = str(summary["output_path"])
                    current.failures = list(summary["failed_chapters"])
                    current.pause_requested = False
                    current.cancel_requested = False
                    current.delete_requested = False
                    self._runtime.pop(job.job_id, None)
                    self._save_jobs_locked()
            except DownloadPaused as pause:
                with self._lock:
                    current = self._jobs.get(job.job_id)
                    if current is None:
                        continue
                    self._runtime[job.job_id] = pause.runtime_state
                    current.status = "paused"
                    current.updated_at = now_text()
                    current.message = str(pause)
                    current.pause_requested = False
                    current.cancel_requested = False
                    self._save_jobs_locked()
            except DownloadCancelled as cancel:
                with self._lock:
                    current = self._jobs.get(job.job_id)
                    self._runtime.pop(job.job_id, None)
                    if current is None:
                        continue
                    if current.delete_requested:
                        self._jobs.pop(job.job_id, None)
                    else:
                        current.status = "canceled"
                        current.updated_at = now_text()
                        current.message = str(cancel)
                        current.pause_requested = False
                        current.cancel_requested = False
                        current.delete_requested = False
                    self._save_jobs_locked()
            except Exception as error:
                with self._lock:
                    current = self._jobs.get(job.job_id)
                    self._runtime.pop(job.job_id, None)
                    if current is None:
                        continue
                    current.status = "failed"
                    current.updated_at = now_text()
                    current.error = str(error)
                    current.message = f"下载失败: {error}"
                    current.pause_requested = False
                    current.cancel_requested = False
                    current.delete_requested = False
                    self._save_jobs_locked()

    def _update_job_progress(self, job_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                return
            current.status = "running"
            current.updated_at = now_text()
            current.progress_current = int(payload.get("current") or 0)
            current.progress_total = int(payload.get("total") or 0)
            current.title = str(payload.get("title") or "")
            current.message = str(payload.get("message") or "下载中")
            if str(payload.get("book_name") or "").strip():
                current.book_name = str(payload["book_name"])
            self._save_jobs_locked()

    def _job_control_action(self, job_id: str) -> str:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                return "cancel"
            return self._control_action_locked(current)

    def refresh_session(self, *, force: bool = False) -> dict[str, Any]:
        info = self.session_store.info()
        validation = info["validation"]
        if not info["configured"]:
            return info
        if not force and float(validation.get("checked_at_ts") or 0.0) > 0 and now_ts() - float(validation["checked_at_ts"]) < SESSION_VALIDATION_TTL:
            return info

        payload = self.client.validate_session()
        login_state = info.get("last_import", {}).get("login_state") if isinstance(info.get("last_import"), dict) else {}
        if not bool(payload.get("valid")) and looks_like_browser_login(info.get("cookie_names", []), login_state):
            payload = {
                "state": "valid",
                "valid": True,
                "message": "已同步浏览器登录态。番茄首页的服务端重放校验未通过，但本机浏览器 Cookie 组合完整，可直接用于本地下载。",
                "checked_at": now_text(),
                "checked_at_ts": now_ts(),
                "user_name": str(login_state.get("user_name") or ""),
                "avatar": str(login_state.get("avatar") or ""),
            }
        self.session_store.update_validation(payload, auto_clear=False)
        return self.session_store.info()

    def sync_session_from_local_chrome(self) -> dict[str, Any]:
        payload = read_fanqie_cookie_payload_from_local_chrome()
        self.session_store.save_cookie_header(json.dumps(payload, ensure_ascii=False))
        info = self.session_store.info()
        login_state = info.get("last_import", {}).get("login_state") if isinstance(info.get("last_import"), dict) else {}
        if looks_like_browser_login(info.get("cookie_names", []), login_state):
            self.session_store.update_validation(
                {
                    "state": "valid",
                    "valid": True,
                    "message": "已从本机 Chrome 同步登录态，可直接用于本地下载。",
                    "checked_at": now_text(),
                    "checked_at_ts": now_ts(),
                    "user_name": str(login_state.get("user_name") or ""),
                    "avatar": str(login_state.get("avatar") or ""),
                },
                auto_clear=False,
            )
            return self.session_store.info()
        return self.refresh_session(force=True)

    def config_payload(self) -> dict[str, Any]:
        return {
            "default_output_dir": str(DOWNLOAD_ROOT),
            "session": self.refresh_session(force=False),
            "jobs": [job.to_dict() for job in self.list_jobs()],
        }

    def list_jobs(self) -> list[DownloadJob]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=self._list_sort_key)
        return jobs

    def get_job(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def create_job(self, target: str, output_dir: str) -> DownloadJob:
        with self._lock:
            job = DownloadJob(
                job_id=uuid.uuid4().hex[:8],
                target=target,
                output_dir=output_dir,
                status="queued",
                created_at=now_text(),
                updated_at=now_text(),
                progress_current=0,
                progress_total=0,
                message="已加入队列，等待执行",
                queue_rank=self._next_rank,
            )
            self._next_rank += 1
            self._jobs[job.job_id] = job
            self._save_jobs_locked()
            return job

    def pause_job(self, job_id: str) -> DownloadJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("任务不存在")
            if job.status == "queued":
                job.status = "paused"
                job.updated_at = now_text()
                job.message = "任务已暂停，等待继续"
            elif job.status == "running":
                job.pause_requested = True
                job.updated_at = now_text()
                job.message = "当前章节完成后暂停"
            else:
                raise ValueError("当前任务不能暂停")
            self._save_jobs_locked()
            return job

    def resume_job(self, job_id: str) -> DownloadJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("任务不存在")
            if job.status != "paused":
                raise ValueError("当前任务不在暂停状态")
            job.status = "queued"
            job.updated_at = now_text()
            job.message = "任务已恢复，等待执行"
            job.pause_requested = False
            self._save_jobs_locked()
            return job

    def toggle_pin(self, job_id: str) -> DownloadJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("任务不存在")
            job.pinned = not job.pinned
            if job.pinned:
                min_rank = min((candidate.queue_rank for candidate in self._jobs.values()), default=job.queue_rank)
                job.queue_rank = min_rank - 1
            else:
                job.queue_rank = self._next_rank
                self._next_rank += 1
            job.updated_at = now_text()
            job.message = "任务已置顶" if job.pinned else "已取消置顶"
            self._save_jobs_locked()
            return job

    def delete_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("任务不存在")
            if job.status == "running":
                job.delete_requested = True
                job.cancel_requested = True
                job.status = "canceling"
                job.updated_at = now_text()
                job.message = "当前章节完成后删除任务"
                self._save_jobs_locked()
                return {"deleted": False, "job": job.to_dict()}

            self._jobs.pop(job_id, None)
            self._runtime.pop(job_id, None)
            self._save_jobs_locked()
            return {"deleted": True}

    def _result_path_for_job(self, job_id: str) -> Path:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("任务不存在")
            result_path = str(job.result_path or "").strip()
        if not result_path:
            raise ValueError("当前任务没有可打开的结果文件")
        path = Path(result_path).expanduser()
        if not path.exists():
            raise ValueError("结果文件不存在，可能已被移动或删除")
        return path

    def open_job_file(self, job_id: str) -> dict[str, Any]:
        path = self._result_path_for_job(job_id)
        subprocess.Popen(["open", str(path)])
        return {"opened": True, "mode": "file", "path": str(path)}

    def open_job_folder(self, job_id: str) -> dict[str, Any]:
        path = self._result_path_for_job(job_id)
        subprocess.Popen(["open", "-R", str(path)])
        return {"opened": True, "mode": "folder", "path": str(path)}

    def build_extension_zip(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(EXTENSION_ROOT.rglob("*")):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(EXTENSION_ROOT))
        return buffer.getvalue()


APP_STATE = AppState()


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "LocalFanqiePersonal/2.0"

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as error:
            raise ValueError(f"JSON 请求体无效: {error}") from error
        if not isinstance(payload, dict):
            raise ValueError("JSON 请求体必须是对象")
        return payload

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, content_type: str, payload: bytes, *, filename: str = "", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(payload)

    def _serve_static(self, relative_path: str) -> None:
        relative = relative_path.lstrip("/") or "index.html"
        file_path = STATIC_ROOT / relative
        if not file_path.exists() or not file_path.is_file():
            self._send_json({"ok": False, "error": "资源不存在"}, status=404)
            return

        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }.get(file_path.suffix, "application/octet-stream")
        self._send_bytes(content_type, file_path.read_bytes())

    def _handle_error(self, error: Exception, status: int = 400) -> None:
        self._send_json({"ok": False, "error": str(error)}, status=status)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._serve_static("index.html")
                return
            if path.startswith("/static/"):
                self._serve_static(path.removeprefix("/static/"))
                return
            if path == "/api/config":
                self._send_json({"ok": True, "data": APP_STATE.config_payload()})
                return
            if path == "/api/jobs":
                self._send_json({"ok": True, "data": [job.to_dict() for job in APP_STATE.list_jobs()]})
                return
            if path.startswith("/api/jobs/"):
                job_id = path.rsplit("/", 1)[-1]
                job = APP_STATE.get_job(job_id)
                if job is None:
                    self._send_json({"ok": False, "error": "任务不存在"}, status=404)
                    return
                self._send_json({"ok": True, "data": job.to_dict()})
                return
            if path == "/api/session/status":
                self._send_json({"ok": True, "data": APP_STATE.refresh_session(force=True)})
                return
            if path == "/api/session/sync-chrome":
                self._send_json({"ok": True, "data": APP_STATE.sync_session_from_local_chrome()})
                return
            if path == "/api/extension-download":
                payload = APP_STATE.build_extension_zip()
                self._send_bytes("application/zip", payload, filename="fanqie-cookie-exporter.zip")
                return
            self._send_json({"ok": False, "error": "接口不存在"}, status=404)
        except Exception as error:
            self._handle_error(error, status=500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/book":
                payload = self._read_json_body()
                data = APP_STATE.client.get_book(str(payload.get("target") or ""))
                self._send_json({"ok": True, "data": data})
                return

            if path == "/api/download":
                payload = self._read_json_body()
                target = str(payload.get("target") or "").strip()
                output_dir = str(payload.get("output_dir") or DOWNLOAD_ROOT).strip()
                if not target:
                    raise ValueError("请输入书籍链接、章节链接或对应 ID")
                job = APP_STATE.create_job(target, output_dir)
                self._send_json({"ok": True, "data": job.to_dict()})
                return

            if path == "/api/session/save-cookie":
                payload = self._read_json_body()
                cookie_text = str(payload.get("cookie") or "").strip()
                info = APP_STATE.session_store.save_cookie_header(cookie_text)
                login_state = info.get("last_import", {}).get("login_state") if isinstance(info.get("last_import"), dict) else {}
                if looks_like_browser_login(info.get("cookie_names", []), login_state):
                    APP_STATE.session_store.update_validation(
                        {
                            "state": "valid",
                            "valid": True,
                            "message": "已从浏览器同步登录态，可直接用于本地下载。",
                            "checked_at": now_text(),
                            "checked_at_ts": now_ts(),
                            "user_name": str(login_state.get("user_name") or ""),
                            "avatar": str(login_state.get("avatar") or ""),
                        },
                        auto_clear=False,
                    )
                    data = APP_STATE.session_store.info()
                else:
                    data = APP_STATE.refresh_session(force=True)
                self._send_json({"ok": True, "data": data})
                return

            if path == "/api/session/clear":
                data = APP_STATE.session_store.clear()
                self._send_json({"ok": True, "data": data})
                return

            if path == "/api/session/open-login":
                subprocess.Popen(["open", "https://fanqienovel.com/"])
                self._send_json(
                    {
                        "ok": True,
                        "data": {
                            "message": "已打开番茄登录页。完成登录后，可直接用旁边的 Cookie 插件导出 JSON 并粘贴回来。"
                        },
                    }
                )
                return

            if path.startswith("/api/jobs/") and path.endswith("/pause"):
                job_id = path.split("/")[3]
                job = APP_STATE.pause_job(job_id)
                self._send_json({"ok": True, "data": job.to_dict()})
                return

            if path.startswith("/api/jobs/") and path.endswith("/resume"):
                job_id = path.split("/")[3]
                job = APP_STATE.resume_job(job_id)
                self._send_json({"ok": True, "data": job.to_dict()})
                return

            if path.startswith("/api/jobs/") and path.endswith("/pin"):
                job_id = path.split("/")[3]
                job = APP_STATE.toggle_pin(job_id)
                self._send_json({"ok": True, "data": job.to_dict()})
                return

            if path.startswith("/api/jobs/") and path.endswith("/delete"):
                job_id = path.split("/")[3]
                data = APP_STATE.delete_job(job_id)
                self._send_json({"ok": True, "data": data})
                return

            if path.startswith("/api/jobs/") and path.endswith("/open-file"):
                job_id = path.split("/")[3]
                data = APP_STATE.open_job_file(job_id)
                self._send_json({"ok": True, "data": data})
                return

            if path.startswith("/api/jobs/") and path.endswith("/open-folder"):
                job_id = path.split("/")[3]
                data = APP_STATE.open_job_folder(job_id)
                self._send_json({"ok": True, "data": data})
                return

            self._send_json({"ok": False, "error": "接口不存在"}, status=404)
        except ValueError as error:
            self._handle_error(error, status=400)
        except Exception as error:
            self._handle_error(error, status=500)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{now_text()}] {self.address_string()} - {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT), RequestHandler)
    print(f"Local Fanqie Personal running at http://127.0.0.1:{DEFAULT_PORT}")
    print(f"Session file: {SESSION_FILE}")
    print(f"Jobs file: {JOBS_FILE}")
    server.serve_forever()


if __name__ == "__main__":
    main()
