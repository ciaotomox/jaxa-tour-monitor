#!/usr/bin/env python3
"""
JAXA筑波宇宙センター 個人見学ツアー 空き監視
https://tksc-spacedome.revn.jp/reservations/calendar?label_id=1

対象: 2026年8月の土日
空き(残り1名以上)が出たら Gmail に通知する。
状態は state.json に保存し、0名→1名以上 の変化時のみ通知(スパム防止)。
"""

import datetime as dt
import json
import os
import re
import smtplib
import sys
import time
import urllib.parse
from email.mime.text import MIMEText
from email.header import Header

import requests

# ===================== 設定 =====================
BASE = "https://tksc-spacedome.revn.jp"
LABEL_ID = "1"                 # 個人見学ツアー
TARGET_YEAR = 2026
TARGET_MONTH = 8
TARGET_WEEKDAYS = {5, 6}       # 5=土, 6=日 (平日も見たい場合は {0,1,2,3,4,5,6})
STATE_FILE = "state.json"
REQUEST_INTERVAL = 1.0         # ポップアップ取得間の待機秒(サーバー負荷配慮)

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
NOTIFY_TO = os.environ.get("NOTIFY_TO", "ciaotomox@gmail.com")
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


# ===================== 状態管理 =====================
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"slots": {}, "consecutive_failures": 0, "failure_notified": False,
            "ended_notified": False}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


# ===================== メール通知 =====================
def send_mail(subject, body):
    if DRY_RUN:
        print("=== DRY_RUN: メール送信スキップ ===")
        print("Subject:", subject)
        print(body)
        return
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_ADDRESS / GMAIL_APP_PASSWORD が未設定です", file=sys.stderr)
        sys.exit(1)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = NOTIFY_TO
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)
    print(f"メール送信完了: {subject}")


# ===================== スクレイピング =====================
def fetch_availability():
    """対象月の土日について {date: {"event_id": int, "slots": {time: remaining}}} を返す"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    referer = f"{BASE}/reservations/calendar?label_id={LABEL_ID}"

    # 1) ページ取得 → セッションCookie + CSRFトークン
    r = s.get(f"{BASE}/reservations/calendar",
              params={"label_id": LABEL_ID}, timeout=30)
    r.raise_for_status()
    m = re.search(r'name="_csrfToken"[^>]*value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("CSRFトークンが取得できません(サイト構造変更の可能性)")
    csrf = m.group(1)

    # 2) 月カレンダー取得 → 日付ごとの event id
    r = s.post(f"{BASE}/ajax/reservations/calendar",
               params={"label_id": LABEL_ID},
               data={"_csrfToken": csrf,
                     "date": f"{TARGET_YEAR}-{TARGET_MONTH:02d}-01", "page": 1},
               headers={"X-Requested-With": "XMLHttpRequest", "Referer": referer},
               timeout=30)
    r.raise_for_status()
    month_html = r.json()["html"]
    day_events = {}
    for mm in re.finditer(r'data-search-data="([^"]+)"', month_html):
        d = json.loads(mm.group(1).replace("&quot;", '"'))
        day_events[d["date"].replace("\\/", "/")] = d["id"]
    if not day_events:
        raise RuntimeError("月カレンダーからイベントが取得できません(サイト構造変更の可能性)")

    # 3) 対象曜日の各日についてポップアップ取得 → 時間帯別残数
    result = {}
    days_in_month = (dt.date(TARGET_YEAR + (TARGET_MONTH == 12),
                             TARGET_MONTH % 12 + 1, 1) - dt.timedelta(days=1)).day
    for day in range(1, days_in_month + 1):
        date = dt.date(TARGET_YEAR, TARGET_MONTH, day)
        if date.weekday() not in TARGET_WEEKDAYS:
            continue
        ds = date.strftime("%Y/%m/%d")
        eid = day_events.get(ds)
        if eid is None:
            continue
        r = s.post(f"{BASE}/ajax/reservations/calendar-popup",
                   params={"label_id": LABEL_ID, "popup_type": 2},
                   data={"_csrfToken": csrf, "id": eid, "date": ds},
                   headers={"X-Requested-With": "XMLHttpRequest",
                            "Referer": referer},
                   timeout=30)
        r.raise_for_status()
        html = r.json()["html"]
        slots = {}
        for chunk in html.split("list_body_line_wrap")[1:]:
            t = re.search(r'(\d{1,2}:\d{2})\s*~', chunk)
            rem = re.search(r'残り\s*(\d+)\s*名', chunk)
            if t:
                slots[t.group(1)] = int(rem.group(1)) if rem else -1
        result[ds] = {"event_id": eid, "slots": slots}
        time.sleep(REQUEST_INTERVAL)
    return result


# ===================== メイン =====================
def reservation_url(event_id, date_str, time_str):
    ts = urllib.parse.quote_plus(f"{date_str} {time_str}")
    return f"{BASE}/reservations/add?event_id={event_id}&usage_timestamp_from={ts}"


def main():
    state = load_state()
    today = dt.date.today()

    # 監視期間終了チェック
    end_date = dt.date(TARGET_YEAR, TARGET_MONTH,
                       (dt.date(TARGET_YEAR + (TARGET_MONTH == 12),
                                TARGET_MONTH % 12 + 1, 1) - dt.timedelta(days=1)).day)
    if today > end_date:
        if not state.get("ended_notified"):
            send_mail("【JAXA見学監視】監視期間が終了しました",
                      f"{TARGET_YEAR}年{TARGET_MONTH}月の監視期間が終了したため、"
                      "以降のチェックは行いません。\n"
                      "GitHubリポジトリのActionsを無効化するか、"
                      "monitor.pyのTARGET_MONTHを変更してください。")
            state["ended_notified"] = True
            save_state(state)
        print("監視期間終了。処理をスキップします。")
        return

    # 取得
    try:
        current = fetch_availability()
    except Exception as e:
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        print(f"取得失敗 ({state['consecutive_failures']}回連続): {e}", file=sys.stderr)
        if state["consecutive_failures"] >= 3 and not state.get("failure_notified"):
            send_mail("【JAXA見学監視】エラー: 3回連続で取得に失敗",
                      f"サイト構造の変更またはアクセスブロックの可能性があります。\n\n"
                      f"エラー内容: {e}\n\n確認URL: {BASE}/reservations/calendar?label_id={LABEL_ID}")
            state["failure_notified"] = True
        save_state(state)
        sys.exit(1)

    # 成功したので失敗カウンタをリセット
    if state.get("consecutive_failures", 0) > 0 or state.get("failure_notified"):
        state["consecutive_failures"] = 0
        state["failure_notified"] = False

    # 差分検出: 残0以下 → 残1以上 になったスロットを通知対象に
    prev_slots = state.get("slots", {})
    newly_available = []
    log_lines = []
    for ds in sorted(current.keys()):
        info = current[ds]
        wd = WEEKDAY_JA[dt.datetime.strptime(ds, "%Y/%m/%d").weekday()]
        for t in sorted(info["slots"].keys()):
            rem = info["slots"][t]
            key = f"{ds} {t}"
            prev = prev_slots.get(key, 0)
            log_lines.append(f"  {ds}({wd}) {t}~ 残り{rem}名")
            if rem > 0 and prev <= 0:
                newly_available.append({
                    "date": ds, "weekday": wd, "time": t, "remaining": rem,
                    "url": reservation_url(info["event_id"], ds, t),
                })
            prev_slots[key] = rem
    state["slots"] = prev_slots

    print(f"チェック完了 {dt.datetime.now().isoformat(timespec='seconds')}")
    print("\n".join(log_lines))

    # 通知
    if newly_available:
        lines = [f"JAXA筑波宇宙センター 個人見学ツアー({TARGET_MONTH}月の土日)に空きが出ました！\n"]
        for a in newly_available:
            lines.append(f"■ {a['date']}({a['weekday']}) {a['time']}~ : 残り{a['remaining']}名")
            lines.append(f"  予約: {a['url']}\n")
        lines.append(f"カレンダー: {BASE}/reservations/calendar?label_id={LABEL_ID}")
        lines.append("\n※キャンセル待ちの枠はすぐ埋まる可能性があります。お早めに。")
        first = newly_available[0]
        subject = (f"【JAXA見学】空きが出ました！ {first['date'][5:]}({first['weekday']}) "
                   f"{first['time']}~ 残り{first['remaining']}名"
                   + (f" 他{len(newly_available)-1}枠" if len(newly_available) > 1 else ""))
        send_mail(subject, "\n".join(lines))
    else:
        print("新規の空きなし。通知は送信しません。")

    save_state(state)


if __name__ == "__main__":
    main()
