# ニュース収集ツール

テーマを入力すると、Googleニュース(RSS)から関連ニュースのURL一覧を取得し、メールで送信します。

## セットアップ

```bash
pip install -r requirements.txt
```

`.env.example` を `.env` にコピーし、内容を自分の情報に書き換えてください。

```bash
cp .env.example .env
```

| 変数名 | 説明 |
| --- | --- |
| `GMAIL_ADDRESS` | 送信元のGmailアドレス |
| `GMAIL_APP_PASSWORD` | Gmailのアプリパスワード(16桁) |
| `TO_EMAIL` | 送信先メールアドレス |
| `NEWS_COUNT` | 収集する件数(デフォルト30) |

## 実行方法

```bash
python main.py
```

テーマを聞かれるので入力すると、ニュースを収集してメールを送信します。
