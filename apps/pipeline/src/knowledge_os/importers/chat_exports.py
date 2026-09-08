"""Parse ChatGPT/Gemini exports into deterministic private Markdown files.

This adapter deliberately accepts exported files only.  It never signs in,
calls a provider API, follows links, or sends the source archive anywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 128 * 1024 * 1024
MAX_MESSAGES_PER_CONVERSATION = 2_000
MAX_CONVERSATIONS = 10_000
MAX_TEXT_CHARS = 200_000


class ChatExportError(ValueError):
    """Raised when an export is unsupported or unsafe to parse."""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    text: str


@dataclass(frozen=True)
class ChatConversation:
    provider: str
    conversation_id: str
    title: str
    created_at: str
    updated_at: str
    messages: Tuple[ChatMessage, ...]


@dataclass(frozen=True)
class MaterializedConversation:
    path: Path
    provider: str
    conversation_id: str
    sha256: str
    duplicate: bool


def _clean_text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    normalized = "".join(
        char for char in normalized
        if ord(char) >= 32 or char in "\n\t"
    )
    return normalized.strip()[:limit]


def _timestamp(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    return _clean_text(value, 80)


def _identifier(value: Any, fallback: str) -> str:
    identifier = _clean_text(value, 240)
    return identifier or fallback


class _HtmlTextParser(HTMLParser):
    _BLOCKS = {"article", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "section", "tr"}
    _SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.skip_depth = 0
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        lowered = tag.casefold()
        if lowered in self._SKIP:
            self.skip_depth += 1
        if lowered == "title":
            self.in_title = True
        if lowered in self._BLOCKS and not self.skip_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "title":
            self.in_title = False
        if lowered in self._SKIP and self.skip_depth:
            self.skip_depth -= 1
        if lowered in self._BLOCKS and not self.skip_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = _clean_text(data)
        if not text:
            return
        if self.in_title:
            self.title = (self.title + " " + text).strip()
        self.parts.extend((text, " "))

    def text(self) -> str:
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"[ \t]{2,}", " ", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()[:MAX_TEXT_CHARS]


def _html_text(value: Any) -> str:
    parser = _HtmlTextParser()
    try:
        parser.feed(_clean_text(value))
        parser.close()
    except Exception:
        return _clean_text(value)
    return parser.text()


def _parts_text(value: Any) -> str:
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, Mapping):
        for key in ("text", "value"):
            text = _clean_text(value.get(key))
            if text:
                return text
        if isinstance(value.get("html"), str):
            return _html_text(value["html"])
        for key in ("parts", "content"):
            if key in value:
                text = _parts_text(value[key])
                if text:
                    return text
        return ""
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return "\n".join(text for item in value if (text := _parts_text(item)))[:MAX_TEXT_CHARS]
    return ""


def _role(value: Any, fallback: str = "assistant") -> str:
    role = _clean_text(value, 40).casefold()
    aliases = {
        "human": "user",
        "prompt": "user",
        "model": "assistant",
        "gemini": "assistant",
        "ai": "assistant",
    }
    return aliases.get(role, role) if role else fallback


def _message(role: Any, content: Any, fallback: str = "assistant") -> Optional[ChatMessage]:
    text = _parts_text(content)
    if not text:
        return None
    return ChatMessage(_role(role, fallback), text)


def _chatgpt_messages(raw: Mapping[str, Any]) -> List[ChatMessage]:
    mapping = raw.get("mapping")
    nodes = mapping if isinstance(mapping, Mapping) else {}
    ordered: List[Mapping[str, Any]] = []
    current_id = raw.get("current_node")
    seen = set()
    while isinstance(current_id, str) and current_id in nodes and current_id not in seen:
        seen.add(current_id)
        node = nodes[current_id]
        if isinstance(node, Mapping):
            ordered.append(node)
            current_id = node.get("parent")
        else:
            break
    if ordered:
        ordered.reverse()
    else:
        candidates = [node for node in nodes.values() if isinstance(node, Mapping)]
        def node_time(node: Mapping[str, Any]) -> float:
            message = node.get("message")
            if not isinstance(message, Mapping):
                return 0.0
            try:
                return float(message.get("create_time") or 0)
            except (TypeError, ValueError):
                return 0.0
        candidates.sort(key=node_time)
        ordered = candidates
    result: List[ChatMessage] = []
    for node in ordered:
        message = node.get("message")
        if not isinstance(message, Mapping):
            continue
        author = message.get("author")
        author_role = author.get("role") if isinstance(author, Mapping) else author
        item = _message(author_role, message.get("content"), "assistant")
        if item and item.role not in {"system", "developer"}:
            result.append(item)
    if not result and isinstance(raw.get("messages"), Sequence):
        for item in raw["messages"]:
            if not isinstance(item, Mapping):
                continue
            message = _message(item.get("role") or item.get("author"), item.get("content"), "assistant")
            if message:
                result.append(message)
    return result[:MAX_MESSAGES_PER_CONVERSATION]


def _chatgpt_conversation(raw: Mapping[str, Any], index: int) -> Optional[ChatConversation]:
    messages = _chatgpt_messages(raw)
    if not messages:
        return None
    fallback = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]
    conversation_id = _identifier(raw.get("id") or raw.get("conversation_id"), fallback)
    title = _clean_text(raw.get("title"), 200) or f"ChatGPT 对话 {index + 1:04d}"
    return ChatConversation(
        provider="chatgpt",
        conversation_id=conversation_id,
        title=title,
        created_at=_timestamp(raw.get("create_time")),
        updated_at=_timestamp(raw.get("update_time")),
        messages=tuple(messages),
    )


def _gemini_messages(raw: Mapping[str, Any]) -> List[ChatMessage]:
    entries = raw.get("entries") or raw.get("messages") or raw.get("contents")
    result: List[ChatMessage] = []
    if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes, bytearray)):
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                continue
            role = entry.get("role") or entry.get("author") or entry.get("speaker")
            if isinstance(role, Mapping):
                role = role.get("role") or role.get("name")
            item = _message(role, entry.get("content") or entry.get("parts") or entry.get("text"), "user" if index % 2 == 0 else "assistant")
            if item:
                result.append(item)
    if result:
        return result[:MAX_MESSAGES_PER_CONVERSATION]
    title = _clean_text(raw.get("title"), MAX_TEXT_CHARS)
    response = _parts_text(raw.get("safeHtmlItem") or raw.get("response") or raw.get("response_text"))
    if title:
        prompt = re.sub(r"^prompted\s*", "", title, flags=re.IGNORECASE).strip()
        result.append(ChatMessage("user", prompt or title))
    if response:
        result.append(ChatMessage("assistant", response))
    return result[:MAX_MESSAGES_PER_CONVERSATION]


def _gemini_conversation(raw: Mapping[str, Any], index: int) -> Optional[ChatConversation]:
    messages = _gemini_messages(raw)
    if not messages:
        return None
    fallback = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]
    conversation_id = _identifier(raw.get("id") or raw.get("conversation_id"), fallback)
    title = _clean_text(raw.get("title"), 200)
    if not title:
        title = messages[0].text.splitlines()[0][:200] or f"Gemini 对话 {index + 1:04d}"
    created = raw.get("create_time") or raw.get("created_at") or raw.get("time")
    updated = raw.get("update_time") or raw.get("updated_at") or created
    return ChatConversation(
        provider="gemini",
        conversation_id=conversation_id,
        title=title,
        created_at=_timestamp(created),
        updated_at=_timestamp(updated),
        messages=tuple(messages),
    )


def _looks_like_chatgpt(value: Any) -> bool:
    if isinstance(value, Mapping):
        messages = value.get("messages")
        return isinstance(value.get("mapping"), Mapping) or (
            isinstance(messages, Sequence) and not isinstance(messages, (str, bytes, bytearray))
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_looks_like_chatgpt(item) for item in value[:20])
    return False


def _payload_conversations(payload: Any, provider: str, name: str) -> List[ChatConversation]:
    conversations = payload.get("conversations") if isinstance(payload, Mapping) else None
    if isinstance(conversations, Sequence) and not isinstance(conversations, (str, bytes, bytearray)):
        items = conversations
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        items = payload
    elif isinstance(payload, Mapping):
        items = [payload]
    else:
        raise ChatExportError(f"无法识别导出 JSON：{name}")
    if len(items) > MAX_CONVERSATIONS:
        raise ChatExportError("导出会话数量超过安全上限")
    selected = provider
    if selected == "auto":
        selected = "chatgpt" if _looks_like_chatgpt(payload) else "gemini"
    result = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        conversation = (_chatgpt_conversation if selected == "chatgpt" else _gemini_conversation)(item, index)
        if conversation:
            result.append(conversation)
    return result


def _safe_member_name(name: str) -> None:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ChatExportError("导出压缩包包含越界路径")


def _read_candidates(path: Path, provider: str) -> List[Tuple[str, bytes]]:
    if path.is_dir():
        candidates = []
        for child in sorted(path.rglob("*")):
            if child.is_symlink():
                raise ChatExportError("导出目录不能包含符号链接")
            if child.is_file() and child.suffix.casefold() in {".json", ".html", ".htm"}:
                if child.stat().st_size > MAX_JSON_BYTES:
                    raise ChatExportError(f"导出文件过大：{child.name}")
                candidates.append((child.name, child.read_bytes()))
        return candidates
    if not path.is_file():
        raise ChatExportError(f"导出文件不存在：{path}")
    if path.is_symlink():
        raise ChatExportError("导出文件不能是符号链接")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ChatExportError("导出文件超过安全上限")
    if path.suffix.casefold() not in {".zip", ".json", ".html", ".htm"}:
        raise ChatExportError("仅支持 JSON、HTML 或 ZIP 导出文件")
    if path.suffix.casefold() != ".zip":
        return [(path.name, path.read_bytes())]
    candidates = []
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ChatExportError("导出压缩包文件数量超过安全上限")
        total = 0
        for info in infos:
            _safe_member_name(info.filename)
            if info.is_dir():
                continue
            if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                raise ChatExportError("导出压缩包内单个文件超过安全上限")
            if info.compress_size and info.file_size / info.compress_size > 200:
                raise ChatExportError("导出压缩包压缩比超过安全上限")
            total += info.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise ChatExportError("导出压缩包解压总量超过安全上限")
            suffix = Path(info.filename).suffix.casefold()
            if suffix not in {".json", ".html", ".htm"}:
                continue
            lowered = info.filename.casefold()
            selected = (
                provider == "auto"
                or provider == "chatgpt" and "conversation" in lowered
                or provider == "gemini" and ("gemini" in lowered or "activity" in lowered)
            )
            if selected:
                candidates.append((info.filename, archive.read(info)))
    if not candidates:
        raise ChatExportError("压缩包中没有可识别的会话导出文件")
    return candidates


def parse_export(path: Path, provider: str = "auto") -> List[ChatConversation]:
    """Parse one JSON/HTML/ZIP export or an extracted export directory."""

    provider = provider.casefold()
    if provider not in {"auto", "chatgpt", "gemini"}:
        raise ChatExportError("provider 必须是 auto、chatgpt 或 gemini")
    conversations: List[ChatConversation] = []
    for name, data in _read_candidates(path.expanduser().resolve(), provider):
        if len(data) > MAX_JSON_BYTES:
            raise ChatExportError(f"导出文件过大：{name}")
        suffix = Path(name).suffix.casefold()
        if suffix in {".html", ".htm"}:
            parser = _HtmlTextParser()
            parser.feed(_clean_text(data.decode("utf-8", errors="replace")))
            text = parser.text()
            if text:
                conversations.append(ChatConversation(
                    provider="gemini" if provider != "chatgpt" else provider,
                    conversation_id=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                    title=parser.title[:200] or Path(name).stem,
                    created_at="",
                    updated_at="",
                    messages=(ChatMessage("assistant", text),),
                ))
            continue
        try:
            payload = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ChatExportError(f"导出 JSON 无法读取：{name}") from exc
        conversations.extend(_payload_conversations(payload, provider, name))
    if not conversations:
        raise ChatExportError("没有从导出文件中解析出可用会话")
    unique: Dict[Tuple[str, str, str], ChatConversation] = {}
    for conversation in conversations:
        key = (conversation.provider, conversation.conversation_id, "\n".join(message.text for message in conversation.messages))
        unique.setdefault(key, conversation)
    return list(unique.values())[:MAX_CONVERSATIONS]


def _safe_slug(value: str) -> str:
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "-", value).strip(" .-")
    value = re.sub(r"\s+", "-", value)
    return (value[:80] or "conversation")


def render_conversation(conversation: ChatConversation) -> str:
    provider_label = "ChatGPT" if conversation.provider == "chatgpt" else "Gemini"
    lines = [f"# {conversation.title}", "", f"> 来源：{provider_label} 官方导出", f"> 会话 ID：{conversation.conversation_id}"]
    if conversation.created_at:
        lines.append(f"> 创建时间：{conversation.created_at}")
    if conversation.updated_at and conversation.updated_at != conversation.created_at:
        lines.append(f"> 更新时间：{conversation.updated_at}")
    lines.extend(("", "## 对话记录", ""))
    labels = {"user": "用户", "assistant": "助手", "tool": "工具", "system": "系统"}
    for index, message in enumerate(conversation.messages, 1):
        label = labels.get(message.role, message.role or "消息")
        lines.extend((f"### {label} {index}", "", message.text, ""))
    return "\n".join(lines).rstrip() + "\n"


def _write_if_missing(path: Path, content: bytes) -> bool:
    if path.exists():
        return path.read_bytes() == content
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(str(temporary), str(path))
            return False
        except FileExistsError:
            return path.read_bytes() == content
    finally:
        if temporary.exists():
            temporary.unlink()


def materialize_conversations(
    conversations: Iterable[ChatConversation], output_dir: Path, *, dry_run: bool = False
) -> List[MaterializedConversation]:
    """Write stable Markdown files; repeat imports never overwrite content."""

    results = []
    for conversation in conversations:
        content = render_conversation(conversation).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        filename = f"{conversation.provider}-{_safe_slug(conversation.title)}-{digest[:12]}.md"
        path = output_dir / filename
        duplicate = path.exists() and path.read_bytes() == content
        if not dry_run:
            duplicate = _write_if_missing(path, content)
        results.append(MaterializedConversation(
            path=path,
            provider=conversation.provider,
            conversation_id=conversation.conversation_id,
            sha256=digest,
            duplicate=duplicate,
        ))
    return results
