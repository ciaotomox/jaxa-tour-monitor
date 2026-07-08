# jaxa-tour-monitor

JAXA筑波宇宙センター **個人見学ツアー** の空き監視 (2026年8月の土日)。
空きが出たら Gmail に通知。`aalto-monitor` / `Sanu-Monitoring2026` と同じ GitHub Actions + Gmail SMTP パターン。

## 監視対象
- https://tksc-spacedome.revn.jp/reservations/calendar?label_id=1
- 2026/8 の土日 × 各日 11:30 / 15:00 の2枠
- 「残り0名 → 残り1名以上」の変化時のみ通知 (スパム防止)
- サイト構造変更などで3回連続失敗した場合もメールで警告

## セットアップ (5分)
```bash
gh repo create jaxa-tour-monitor --public --source=. --push   # 5分間隔のためpublic推奨(コードに秘密情報なし)
gh secret set GMAIL_ADDRESS --body "ciaotomox@gmail.com"
gh secret set GMAIL_APP_PASSWORD --body "<Gmailアプリパスワード>"
gh workflow run "JAXA Tour Monitor"   # 初回手動実行で動作確認
```
※ 既存モニターと同じアプリパスワードを流用可。

## カスタマイズ (monitor.py 冒頭)
| 変数 | 現在値 | 説明 |
|---|---|---|
| `TARGET_MONTH` | 8 | 対象月 |
| `TARGET_WEEKDAYS` | `{5, 6}` | 土日のみ。全曜日なら `{0,1,2,3,4,5,6}` |
| cron (monitor.yml) | `*/5` | GitHub Actionsの最短間隔。月約8,600分消費するため **publicリポジトリ必須**(privateの無料枠2,000分/月を大幅超過) |

※ GitHub Actionsのscheduleは混雑時に数分〜十数分遅延することがあります(仕様)。

## 動作確認 (ローカル)
```bash
DRY_RUN=1 python3 monitor.py   # メール送信せずログのみ
```

8月終了後は自動停止し、終了通知メールを1回送ります。
