# ニュース収集ツール

テーマを入力すると、Googleニュース(RSS)から関連ニュースを収集し、見出し・日付・配信元・要約(記事冒頭の抜粋)・元記事へのリンクをメールで送信します。

## 他の人のPCで使う場合(exe版・Python不要)

1. [Releases](../../releases) から `news-collector.exe` をダウンロードする
2. `news-collector.exe` をダブルクリックで実行する
3. 初回のみ、画面の指示に従って**ニュースを届けたいメールアドレス**を入力する
4. 続けてニュースのテーマ(例: 為替、生成AI)を入力するとメールが届く(記事の要約も取得するため、30件で1分弱かかります)
5. 2回目以降はテーマの入力だけで使える(設定は`.exe`と同じ場所の`.env`に保存される)

設定をやり直したい場合は、`.exe`と同じフォルダにある`.env`ファイルを削除してから再実行してください。

送信元のGmailアプリパスワードはあらかじめexeに埋め込まれているため、利用者側での発行作業は不要です。

## 開発者向け(ソースから実行する場合)

```bash
pip install -r requirements.txt
```

送信元の認証情報用に `sender.env.example` を `sender.env` にコピーし、内容を自分のGmail情報に書き換えてください(このファイルはGit管理対象外です)。

```bash
cp sender.env.example sender.env
```

| 変数名 | 説明 |
| --- | --- |
| `GMAIL_ADDRESS` | 送信元のGmailアドレス |
| `GMAIL_APP_PASSWORD` | Gmailのアプリパスワード(16桁) |

届け先の設定(`.env`)は初回実行時に対話形式で作成されます。手動で作る場合は `.env.example` を参考にしてください。

```bash
python main.py
```

### 記事本文の要約について

記事本文は、Googleニュースのリンクを実際の記事URLに変換したうえで`trafilatura`ライブラリを使って取得し、冒頭2〜3文を要約として抜粋しています。著作権に配慮し、本文全体は取得・送信していません。Googleニュース側のリンク形式が変わると、この処理が動かなくなる可能性があります。

### exeを再ビルドする場合

```bash
python -m venv build_env
./build_env/Scripts/pip install -r requirements.txt pyinstaller

./build_env/Scripts/pyinstaller --onefile --name news-collector --console \
  --add-data "sender.env;." \
  --add-data "build_env/lib/site-packages/trafilatura/settings.cfg;trafilatura" \
  --add-data "build_env/lib/site-packages/justext/stoplists;justext/stoplists" \
  main.py
```

`dist/news-collector.exe` が生成されます。
