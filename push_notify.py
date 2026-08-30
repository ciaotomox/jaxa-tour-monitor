"""push_notify.py — 監視通知を ntfy.sh 経由で iPhone / Apple Watch にプッシュする。

※ このリポジトリは public のためトピック名はコードに埋め込まず、
   GitHub Secrets の環境変数 NTFY_TOPIC からのみ読む（未設定なら何もしない）。

使い方:
    from push_notify import push_ntfy
    push_ntfy(subject, body)          # メール送信の直後に呼ぶ

* 失敗しても例外は投げず警告ログを出すだけ（監視本体は止めない）
* 標準ライブラリのみ使用
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger(__name__)

DEFAULT_TOPIC = ""  # public リポジトリのため埋め込まない（NTFY_TOPIC Secret を使用）
NTFY_URL = "https://ntfy.sh/"


def push_ntfy(title: str, message: str = "", click: str | None = None, priority: int = 4) -> bool:
    """ntfy.sh にプッシュ通知を送る。priority 4 = high（Watch も振動）。"""
    topic = os.environ.get("NTFY_TOPIC", DEFAULT_TOPIC).strip()
    if not topic:
        return False
    payload = {
        "topic": topic,
        "title": (title or "監視通知")[:200],
        "message": (message or title or "")[:3500],
        "priority": priority,
        "tags": ["bell"],
    }
    if click:
        payload["click"] = click
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            res.read()
        log.info("push_notify: ntfy送信完了")
        return True
    except Exception as e:  # noqa: BLE001 — 監視本体を止めない
        log.warning("push_notify: ntfy送信失敗: %s", e)
    return False
