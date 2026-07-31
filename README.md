# ニュース収集ツール

テーマを入力すると、Googleニュース(RSS)から関連ニュースのURL一覧を取得し、メールで送信します。

## 他の人のPCで使う場合(exe版・Python不要)

1. [Releases](../../releases) から `news-collector.exe` をダウンロードする
2. Gmailの「アプリパスワード」を発行する(下記参照)
3. `news-collector.exe` をダブルクリックで実行する
4. 初回のみ、画面の指示に従って以下を入力する
   - 自分のGmailアドレス
   - Gmailのアプリパスワード
   - ニュースを届けたいメールアドレス(空欄で自分宛て)
   - 収集件数(空欄で30件)
5. 続けてニュースのテーマ(例: 為替、生成AI)を入力するとメールが届く
6. 2回目以降はテーマの入力だけで使える(設定は`.exe`と同じ場所の`.env`に保存される)

設定をやり直したい場合は、`.exe`と同じフォルダにある`.env`ファイルを削除してから再実行してください。

### Gmailアプリパスワードの発行方法

1. https://myaccount.google.com/security で2段階認証を有効にする
2. https://myaccount.google.com/apppasswords にアクセスし、名前を入力して発行する
3. 表示された16桁のパスワードをコピーしておく

## 開発者向け(ソースから実行する場合)

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

```bash
python main.py
```

### exeを再ビルドする場合

```bash
python -m venv build_env
./build_env/Scripts/pip install -r requirements.txt pyinstaller
./build_env/Scripts/pyinstaller --onefile --name news-collector --console main.py
```

`dist/news-collector.exe` が生成されます。
