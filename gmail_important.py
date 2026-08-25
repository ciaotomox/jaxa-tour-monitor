"""gmail_important.py — SMTP で送った自己宛通知メールに Gmail の「重要」マークを付ける。

Gmail のフィルタ「常に重要マークを付ける」は、自分から自分へ SMTP 送信した
メールには新着時に効かない（スター・ラベルは効く）。そこで送信直後に IMAP
（Gmail 拡張 X-GM-LABELS）で \\Important ラベルを付与する。

使い方:
    from gmail_important import stamp_message_id, mark_important

    mid = stamp_message_id(msg)                 # 送信前: Message-ID を付与
    ... smtplib で送信 ...
    mark_important(addr, app_password, mid, subject=subject)   # 送信後

* 認証は SMTP と同じ Gmail アドレス＋アプリパスワード（追加の Secret 不要）
* 失敗しても例外は投げず警告ログを出すだけ（監視本体は止めない）
* 標準ライブラリのみ使用
"""

from __future__ import annotations

import imaplib
import logging
import re
import time
from email.utils import make_msgid

log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def stamp_message_id(msg, domain: str = "gmail.com") -> str:
    """送信前に Message-ID ヘッダを付与し、その値を返す（既にあればそれを返す）。"""
    if not msg.get("Message-ID"):
        msg["Message-ID"] = make_msgid(domain=domain)
    return msg["Message-ID"]


def _all_mail_folder(imap: imaplib.IMAP4) -> str:
    """LIST の特殊用途属性 \\All から「すべてのメール」フォルダ名を求める（言語非依存）。"""
    typ, lines = imap.list()
    for line in lines or []:
        if isinstance(line, bytes):
            line = line.decode("ascii", "replace")
        m = re.match(r'\((?P<flags>[^)]*)\) "(?P<delim>[^"]*)" (?P<name>.+)$', line)
        if m and "\\All" in m.group("flags"):
            return m.group("name")  # 引用符付きのまま返す（UTF-7 名にも対応）
    return '"[Gmail]/All Mail"'


def _search(imap: imaplib.IMAP4, raw_query: str) -> list[bytes]:
    typ, data = imap.uid("SEARCH", None, "X-GM-RAW", f'"{raw_query}"')
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def mark_important(
    user: str,
    password: str,
    message_id: str | None,
    subject: str | None = None,
    retries: int = 6,
    wait_sec: float = 5.0,
    timeout: float = 30.0,
) -> bool:
    """送信済みメールを Message-ID（無ければ件名）で探し、重要マークを付ける。"""
    if not user or not password:
        log.warning("gmail_important: 認証情報が無いためスキップ")
        return False
    if not message_id and not subject:
        log.warning("gmail_important: Message-ID も件名も無いためスキップ")
        return False

    queries: list[str] = []
    if message_id:
        queries.append(f"rfc822msgid:{message_id.strip().strip('<>')}")
    if subject and subject.isascii():  # imaplib は ASCII しか送れないため日本語件名は対象外
        safe_subject = subject.replace('"', " ").strip()
        queries.append(f'from:me subject:"{safe_subject}" newer_than:1d')
    if not queries:
        return False

    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=timeout) as imap:
            imap.login(user, password)
            folder = _all_mail_folder(imap)
            typ, _ = imap.select(folder)
            if typ != "OK":
                log.warning("gmail_important: フォルダ選択失敗 %s", folder)
                return False

            for attempt in range(1, retries + 1):
                for q in queries:
                    uids = _search(imap, q)
                    if uids:
                        # 件名検索の場合は最新の1通だけ
                        targets = uids if q.startswith("rfc822msgid:") else uids[-1:]
                        for uid in targets:
                            typ, _ = imap.uid("STORE", uid, "+X-GM-LABELS", "(\\Important)")
                            if typ != "OK":
                                log.warning("gmail_important: STORE 失敗 uid=%s", uid)
                                return False
                        log.info("gmail_important: 重要マーク付与 (%s, try=%d)", q.split(":")[0], attempt)
                        return True
                if attempt < retries:
                    time.sleep(wait_sec)
            log.warning("gmail_important: 送信メールが見つからず付与できませんでした")
    except Exception as e:  # noqa: BLE001 — 監視本体を止めない
        log.warning("gmail_important: 重要マーク付与に失敗: %s", e)
    return False
