# ニュース収集ツール

テーマを入力すると、Googleニュース(RSS)から関連ニュースを収集し、見出し・日付・配信元・要約(AIによる全文要約)・元記事へのリンクをメールで送信します。

## 他の人のPCで使う場合(exe版・Python不要)

1. [Releases](../../releases) から `news-collector.exe` をダウンロードする
2. `news-collector.exe` をダブルクリックで実行する
3. 初回のみ、画面の指示に従って**ニュースを届けたいメールアドレス**を入力する
4. 続けてニュースのテーマ(例: 為替、生成AI)を入力するとメールが届く(AIによる要約取得のため、30件で1〜2分程度かかります)
   - 複数キーワードをカンマ区切りで入力すると、いずれかを含む記事が対象になります(例: `飲酒運転,酒酔い,アルコール`)
5. 2回目以降はテーマの入力だけで使える(設定は`.exe`と同じ場所の`.env`に保存される)
6. 対象の日付を指定したい場合は、テーマ入力後に日付(例: `2026/7/30`)や範囲(例: `2026/7/28-2026/7/30`)を入力する(空欄で最新のニュースが対象)

要約が取得できなかった記事は結果から除外され、代わりに他の候補記事で件数が補われます。同じ出来事を複数の配信元が報じている場合も、AIが判定して1件にまとめます(候補記事の絶対数が少ないテーマ・期間では、指定件数に満たないことがあります)。

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
| `ANTHROPIC_API_KEY` | Claude API(要約生成用)のAPIキー。[console.anthropic.com](https://console.anthropic.com/)で発行 |

届け先の設定(`.env`)は初回実行時に対話形式で作成されます。手動で作る場合は `.env.example` を参考にしてください。

```bash
python main.py
```

### 記事本文の要約について

記事本文は、Googleニュースのリンクを実際の記事URLに変換したうえで`trafilatura`ライブラリを使って取得し、Claude Haiku 4.5(Anthropic API)で150〜250文字程度に要約しています。著作権に配慮し、本文全体はメールに含めていません。Googleニュース側のリンク形式が変わると、この処理が動かなくなる可能性があります。

要約に失敗した記事(有料会員限定・広告ブロック検知ページなど、本文が取得できないもの)は結果から自動的に除外されます。指定件数に満たない場合は、追加の候補記事を取得して補います。

`ANTHROPIC_API_KEY`が未設定の場合は、AI要約の代わりに無料の冒頭抽出方式にフォールバックします(この場合、要約に失敗した記事もタイトル+リンクのみで結果に含まれます)。

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
