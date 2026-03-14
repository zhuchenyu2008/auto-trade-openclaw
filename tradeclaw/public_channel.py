from __future__ import annotations

import hashlib
import html as htmllib
import re
import urllib.request
from typing import Iterable

from .models import ChannelEvent, PublicMessage


_RE_WRAP = re.compile(r'<div class="[^"]*tgme_widget_message_wrap[^"]*".*?</div>\s*</div>', re.S)
_RE_POST = re.compile(r'data-post="([^"]+)"')
_RE_DT = re.compile(r'<time[^>]+datetime="([^"]+)"')
_RE_TEXT = re.compile(r'<div class="[^"]*tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_RE_BR = re.compile(r'<br\s*/?>', re.I)
_RE_TAG = re.compile(r'<[^>]+>')


class PublicChannelWatcher:
    def __init__(self, state: dict):
        self.state = state
        self.state.setdefault("channels", {})

    def fetch(self, channel: str, timeout: int = 30) -> str:
        url = f"https://t.me/s/{channel}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")

    def parse_messages(self, channel: str, html: str) -> list[PublicMessage]:
        out: list[PublicMessage] = []
        for block in _RE_WRAP.findall(html):
            mpost = _RE_POST.search(block)
            if not mpost:
                continue
            post = mpost.group(1)
            if not post.startswith(channel + "/"):
                continue
            try:
                post_id = int(post.split("/", 1)[1])
            except Exception:
                continue
            mdt = _RE_DT.search(block)
            dt = mdt.group(1) if mdt else None
            mtext = _RE_TEXT.search(block)
            text_html = mtext.group(1) if mtext else ""
            text = self._html_to_text(text_html)
            permalink = f"https://t.me/{channel}/{post_id}"
            out.append(
                PublicMessage(
                    post_id=post_id,
                    permalink=permalink,
                    dt=dt,
                    text=text,
                    fingerprint=self._fingerprint(post_id, dt, text),
                )
            )
        return out

    def detect_events(
        self,
        channel: str,
        messages: list[PublicMessage],
        max_recent_messages: int = 8,
        bootstrap_if_empty: bool = True,
    ) -> list[ChannelEvent]:
        chan_state = self.state["channels"].setdefault(channel, {"seen": {}})
        seen: dict[str, dict] = chan_state.setdefault("seen", {})
        events: list[ChannelEvent] = []
        ordered = sorted(messages, key=lambda m: m.post_id)
        if bootstrap_if_empty and not seen and ordered:
            for msg in ordered[-500:]:
                seen[str(msg.post_id)] = {
                    "fingerprint": msg.fingerprint,
                    "text": msg.text,
                    "dt": msg.dt,
                    "permalink": msg.permalink,
                }
            return []
        for msg in ordered:
            key = str(msg.post_id)
            prev = seen.get(key)
            recent = [m for m in ordered if m.post_id <= msg.post_id][-max_recent_messages:]
            if prev is None:
                events.append(ChannelEvent(kind="new", channel=channel, message=msg, recent_messages=recent))
            elif prev.get("fingerprint") != msg.fingerprint:
                events.append(
                    ChannelEvent(
                        kind="edited",
                        channel=channel,
                        message=msg,
                        previous_text=prev.get("text") or "",
                        recent_messages=recent,
                    )
                )
            seen[key] = {
                "fingerprint": msg.fingerprint,
                "text": msg.text,
                "dt": msg.dt,
                "permalink": msg.permalink,
            }
        # prune to latest 500 ids
        latest_ids = [str(m.post_id) for m in ordered[-500:]]
        chan_state["seen"] = {k: seen[k] for k in latest_ids if k in seen}
        return events

    @staticmethod
    def _html_to_text(s: str) -> str:
        s = _RE_BR.sub("\n", s)
        s = _RE_TAG.sub("", s)
        s = htmllib.unescape(s)
        s = s.replace("\u00a0", " ")
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()

    @staticmethod
    def _fingerprint(post_id: int, dt: str | None, text: str) -> str:
        h = hashlib.sha256()
        h.update(str(post_id).encode())
        h.update(b"\n")
        if dt:
            h.update(dt.encode())
        h.update(b"\n")
        h.update(text.encode("utf-8", errors="ignore"))
        return h.hexdigest()
