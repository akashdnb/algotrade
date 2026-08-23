"""Telegram Bot API client.

Deliberately small: send a message, verify the bot and chat. No polling, no
webhooks, no command handling -- the system talks, it does not listen. Inbound
actions go through `workflow_dispatch`, which already has authentication and an
audit trail that a chat bot would have to reinvent badly.

MarkdownV2 is the send format. It requires escaping a long list of characters,
and an unescaped one is a 400 rather than a mangled message -- so escaping is
done here rather than trusted to each call site.
"""

from __future__ import annotations

import time
from typing import Any, Final

import requests

API: Final[str] = "https://api.telegram.org/bot{token}/{method}"

#: Reserved in MarkdownV2. Every one must be backslash-escaped in body text.
_RESERVED: Final[str] = r"_*[]()~`>#+-=|{}.!"

#: Telegram rejects anything longer. Messages are split rather than truncated.
MAX_LENGTH: Final[int] = 4096

DEFAULT_TIMEOUT: Final[int] = 20
DEFAULT_RETRIES: Final[int] = 3


class TelegramError(RuntimeError):
    """The Bot API refused, or was unreachable after retries."""


def _describe(response) -> str:  # noqa: ANN001
    """Telegram's own explanation, which is far more useful than the status."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:200]}"
    description = body.get("description")
    return (f"HTTP {response.status_code}: {description}" if description
            else f"HTTP {response.status_code}: {response.text[:200]}")


def escape(text: str) -> str:
    """Escape MarkdownV2 reserved characters in body text.

    Not applied to text you intend as markup -- build the markup, then escape
    the interpolated values.
    """
    out = []
    for char in text:
        if char in _RESERVED:
            out.append("\\")
        out.append(char)
    return "".join(out)


def split_message(text: str, *, limit: int = MAX_LENGTH) -> list[str]:
    """Split on line boundaries so a table or list is never cut mid-row."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        # A single line longer than the limit is hard-split; nothing else can
        # be done, and dropping it silently would be worse.
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit])
            continue
        if size + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        session: requests.Session | None = None,
    ) -> None:
        if not token or not chat_id:
            raise TelegramError("token and chat_id are both required")
        self._token = token
        self._chat_id = chat_id
        self._timeout = timeout
        self._retries = retries
        self._session = session or requests.Session()

    def _call(self, method: str, **payload: Any) -> dict[str, Any]:
        url = API.format(token=self._token, method=method)
        last = ""
        for attempt in range(1, self._retries + 1):
            try:
                response = self._session.post(url, json=payload, timeout=self._timeout)
            except requests.RequestException as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if response.ok:
                    body = response.json()
                    if body.get("ok"):
                        return body.get("result", {})
                    last = f"API error: {body.get('description')}"
                elif response.status_code == 400:
                    # OUR bug -- bad markup, bad chat id -- and it will not fix
                    # itself, so retrying only delays the real error by three
                    # backoffs. Telegram returns 400 with ok=false, which means
                    # `response.ok` is False: this branch has to live out here,
                    # not inside the ok path where it is unreachable.
                    last = _describe(response)
                    break
                elif response.status_code == 429:
                    # Honour Telegram's own backoff rather than guessing.
                    wait = int(
                        response.json().get("parameters", {}).get("retry_after", 5)
                    )
                    time.sleep(min(wait, 30))
                    last = f"rate limited, waited {wait}s"
                    continue
                else:
                    last = f"HTTP {response.status_code}: {response.text[:200]}"

            if attempt < self._retries:
                time.sleep(2 ** (attempt - 1))

        raise TelegramError(f"{method} failed after {self._retries} attempts: {last}")

    # ------------------------------------------------------------- methods --

    def get_me(self) -> dict[str, Any]:
        return self._call("getMe")

    def get_chat(self) -> dict[str, Any]:
        """Verify the chat is reachable WITHOUT posting to it."""
        return self._call("getChat", chat_id=self._chat_id)

    def send(
        self, text: str, *, markdown: bool = True, silent: bool = False
    ) -> list[int]:
        """Send, splitting if necessary. Returns the message ids sent."""
        ids: list[int] = []
        for chunk in split_message(text):
            payload: dict[str, Any] = {
                "chat_id": self._chat_id,
                "text": chunk,
                "disable_notification": silent,
                "link_preview_options": {"is_disabled": True},
            }
            if markdown:
                payload["parse_mode"] = "MarkdownV2"
            result = self._call("sendMessage", **payload)
            ids.append(result.get("message_id", 0))
        return ids
