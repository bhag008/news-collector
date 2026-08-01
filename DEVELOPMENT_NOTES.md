# news-collector 開発引き継ぎメモ

このファイルは、Claude Codeのチャットセッションが切り替わっても開発を継続できるように、
これまでの経緯・設計判断・注意点をまとめたものです。次のセッションではまずこのファイルを
読んでから作業を再開してください。

## プロジェクト概要

- **目的**: テーマ(+任意で日付/日付範囲)を指定すると、Googleニュースから関連記事を収集し、
  AI(Claude Haiku 4.5)で要約したうえでGmail経由でメール送信するデスクトップツール
- **配布形態**: Python不要のexe単体ファイル(PyInstallerでビルド)。非エンジニアの友人へ配布する想定
- **リポジトリ**: https://github.com/bhag008/news-collector (public)
- **最新リリース**: v2.1 (このメモ作成時点)
- **ローカルパス**: `C:\Users\bunbu\OneDrive\デスクトップ\news-collector`

なお、同じ作業ディレクトリ内に別プロジェクト `calculator-app` (Tkinter電卓) もあるが、
そちらは完成済みで追加作業の予定なし。GitHub: https://github.com/bhag008/calculator-app

## UI形式

**v2.0でCLI(ターミナル入力)からGUI(Tkinter)へ移行した。** 非技術者の友人が使うツールであり、
ターミナルでの`input()`操作は分かりにくいという理由。

- ウィンドウ内に「受け取り先メールアドレス」「ニュースのテーマ」「対象日(チェックボックスで
  on/offし、有効時は`tkcalendar.DateEntry`でカレンダーから選択)」を配置し、「ニュースを収集して
  メール送信」ボタンで実行する単一画面構成
- 対象日はフリーテキスト入力(`2026/7/30`のような書式)をやめ、`tkcalendar`のカレンダーピッカーに
  変更した。非技術者には書式を覚えさせるより選ばせる方が分かりやすいため。これに伴い
  `parse_date_input`/`parse_date_range_input`は不要になり削除した
- 処理中はウィンドウ内にプログレスバー(記事取得中は不定形、母数が判明したら
  `n/total`件の実数表示に切り替え)とステータスラベルを表示する。完了・記事0件・送信失敗などは
  `messagebox`のポップアップで通知する。コンソール画面は表示しない(`--windowed`ビルド)
- **中止ボタン(送信ボタンの右隣)**: 処理中のみ有効になり、押すと`threading.Event`
  (`cancel_event`)をsetする。`fetch_news()`はRSS取得直後・記事処理ループの各イテレーション・
  ループ終了後の3箇所で`cancel_event.is_set()`をチェックし、検知したら未着手のfutureを
  `future.cancel()`したうえで独自例外`OperationCancelled`を投げる。呼び出し元(`_run_task`)は
  これを捕まえて`("cancelled", ...)`をqueueに積み、メール送信はスキップする。記事収集が
  完了しメール送信の直前でも念のため`cancel_event.is_set()`をチェックしており、送信という
  取り消せない操作の直前まで中止を反映できるようにしている
  - 実行中のリクエスト(`requests.get`等)自体を強制中断することはできない
    (Pythonではスレッドの強制終了は安全に行えないため)。`ThreadPoolExecutor`の
    `with`ブロックを抜ける際に実行中タスクの完了を待つので、中止ボタンを押してから
    実際に画面が中止完了状態になるまで数秒〜十数秒のタイムラグがあり得る。これは
    仕様として許容している(即座に強制終了する必要はないというのがユーザーの要望の範囲内)
- `fetch_news()`は`progress_callback(done, total)`引数を受け取るようになった(以前は`print`で
  直接進捗を出力していた)。GUIはこれを`queue.Queue`経由でメインスレッドに渡し、`after()`で
  ポーリングして描画を更新している(Tkinterはメインスレッド以外からのウィジェット操作が
  安全でないため、`ThreadPoolExecutor`を含む収集処理はバックグラウンドスレッドで走らせ、
  結果はqueue経由でのみやり取りする設計)
- 受け取り先メールアドレスは、以前は初回起動時の専用ウィザード(`run_setup_wizard`)で
  一度だけ聞いていたが、GUI化に伴い廃止。毎回フォームの欄に表示し(`.env`があれば前回値を
  自動入力)、送信成功時に`.env`へ上書き保存する方式にした。友人がメールアドレスを
  変更したくなった場合もフォームを書き換えるだけでよい

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `main.py` | アプリ本体(唯一のソースファイル) |
| `requirements.txt` | 依存パッケージ一覧 |
| `sender.env` | 送信元Gmail認証情報+Anthropic APIキー(**Git管理外・秘密情報**) |
| `sender.env.example` | 上記のテンプレート(Git管理対象) |
| `.env` | 実行時に生成される届け先メール設定(Git管理外、ローカルテスト用) |
| `README.md` | 利用者向けドキュメント(配布用exeの使い方・開発者向けセットアップ) |
| `build_env/` | exeビルド専用のPython仮想環境(Git管理外)。ユーザーのAnaconda環境を汚さないために分離している |
| `dist/news-collector.exe` | ビルド成果物 |

## 重要な設計判断とその理由

1. **`sender.env`にGmailアプリパスワードとAnthropic APIキーを保存し、exeビルド時に`--add-data`で埋め込んでいる。**
   利用者(友人)は届け先メールアドレスの入力だけで使える。この設計は「exeを持っている人なら
   誰でも中身を取り出してこれらの認証情報を使える」というセキュリティトレードオフをユーザー
   (bhag008)が承知の上で選択したもの。Anthropic APIキーには月$10の利用上限を設定済み。

2. **AI要約はClaude Haiku 4.5を使用。** コスト最優先(単純な要約タスクのため)。
   `ANTHROPIC_API_KEY`が未設定の場合は無料の冒頭抽出方式(`summarize_text`)にフォールバックする。

3. **Googleニュースのリンクは中継URL(JavaScriptで実記事へリダイレクト)のため、
   単純なHTTPリクエストでは実URLが取得できない。** そのため`SSujitX/google-news-url-decoder`
   と同等のロジックを自前で実装している(`_get_base64_str` / `_get_decoding_params` /
   `_decode_url` / `resolve_article_url`)。既存パッケージ`googlenewsdecoder`はPython 3.9+
   必須でこの環境(Python 3.8.5)では使えないため移植した。

4. **記事本文取得は`trafilatura`ライブラリを使用し、`resp.content`(バイト列)を渡す。**
   `resp.text`を使うと一部サイトで文字コード誤判定による文字化けが発生することが判明したため。

5. **著作権への配慮**: 記事全文は一切保存・送信せず、AIによる150〜250文字程度の要約のみを
   メールに含める。この方針はユーザーと議論した上で決定(詳細は過去チャット参照)。

6. **重複記事の除外(v1.8で実装)**: AIに要約と同時に「出来事キーワード」も生成させ
   (プロンプト内で`###`区切りで出力)、それをキーに文字bigramのJaccard類似度で重複判定している。
   - `TITLE_SIMILARITY_THRESHOLD = 0.2` (実データで較正済み: 同一事件ペアは概ね0.23〜0.55、
     別事件ペアは0.02〜0.10だったため、その間に設定)
   - 単純な見出しの文字列比較では「同じ出来事を別媒体が全く違う文言で報じている」ケースを
     検出できなかったため、AIによる正規化キー方式に切り替えた経緯がある

7. **候補記事は指定件数の3倍(`count * 3`)まで取得し、要約に成功して重複でないものだけを
   指定件数まで採用する。** 報道量が少ないテーマ・期間では指定件数に満たないことがあるが、
   これはユーザーが明示的に許容した仕様(候補をさらに増やす選択肢もあったが見送った)。

8. **複数キーワードのOR検索(v1.8)**: テーマ入力でカンマ区切り(`,`または`、`)にすると
   `(A OR B OR C)`形式のクエリになる。`build_theme_query()`参照。

9. **日付指定**: 単一日 `2026/7/30` または範囲 `2026/7/28-2026/7/30`(`-`/`~`/`〜`で区切り)。
   Googleの`after:`/`before:`演算子で絞り込んだ上、`_entry_jst_date()`でJST基準の日付を
   再検証している(GMT/JSTのズレ対策)。

## ビルド手順(exe再生成)

```bash
cd "/c/Users/bunbu/OneDrive/デスクトップ/news-collector"
# 実行中のexeがあれば先に終了させる(OneDriveの同期でファイルがロックされることがある)
taskkill //F //IM news-collector.exe 2>&1
taskkill //F //IM "news-collector (1).exe" 2>&1  # ユーザーが手動でコピー/リネームしている場合がある

rm -rf build news-collector.spec
rm -f dist/news-collector.exe   # dist/ フォルダごとの削除はOneDriveロックで失敗しやすいので個別に削除

./build_env/Scripts/pyinstaller.exe --onefile --name news-collector --windowed \
  --add-data "sender.env;." \
  --add-data "build_env/lib/site-packages/trafilatura/settings.cfg;trafilatura" \
  --add-data "build_env/lib/site-packages/justext/stoplists;justext/stoplists" \
  main.py
```

`--add-data`の3つは必須(欠けるとtrafilatura/justextの内部データファイル不足で実行時エラーになる)。
`tkcalendar`は純Pythonパッケージで追加データファイル不要のため`--add-data`は不要。
v2.0でGUI化したため`--console`から`--windowed`(コンソール非表示)に変更した。

### リリース手順

```bash
git add main.py  # 変更したファイル
git commit -m "..."
git push

"/c/Program Files/GitHub CLI/gh.exe" release create v2.1 "dist/news-collector.exe" \
  --title "v2.1" --notes "変更内容の説明"
```

`gh`コマンドはこのBash/PowerShellセッションのPATHに乗っていない(セッション開始後に
wingetでインストールしたため)。フルパス`/c/Program Files/GitHub CLI/gh.exe`で呼び出すこと。

## テスト時の既知の落とし穴

- **Git BashやPowerShellから日本語テーマをパイプ入力すると`UnicodeEncodeError`
  (サロゲート文字破損)が発生する。これはテストツール側のエンコーディングの問題であり、
  実際にユーザーがキーボードで手入力する場合は発生しない。** 何度も実機検証済み。
  - ASCII文字のテーマ(`AI`など)なら `printf "line1\nline2\n" | ./news-collector.exe` で
    問題なくテストできる
  - 日本語テーマの検証が必要な場合は、Pythonの関数を直接呼び出す(`main.fetch_news(...)`)か、
    実際にユーザー本人に手入力してもらって確認する
- ビルド前に古いexeプロセスを`taskkill`しておかないと、PyInstallerがファイルを
  上書きできずビルド失敗することがある
- **Claude Codeのこの実行環境からは、ネイティブWindowウィンドウ(Tkinterアプリ含む)への
  マウスクリックの自動シミュレーションが届かない。** `SetCursorPos`/`mouse_event`で座標を
  指定してもクリックが別のウィンドウ(Claude Codeのデスクトップアプリ自身など)に吸われる。
  さらにスクリーンショット(`CopyFromScreen`)も不安定で、初回は正しく撮れても、同じ
  ウィンドウを`ShowWindow`/`SetForegroundWindow`後に撮り直すとChat画面側が写ってしまう
  ことがあり、`PrintWindow` APIに切り替えてもウィンドウハンドルの取得自体が不安定だった。
  そのため、GUIの対話フロー(チェックボックス切り替え、日付選択、送信ボタン・中止ボタン等)は
  自動検証をあてにせず、ユーザー本人による実機での動作確認が必要という前提で進めること。
  ロジック部分(`fetch_news`など)はGUIを介さず直接Pythonから呼び出してテスト可能

## バージョン履歴の概要

- v1.0〜v1.2: 基本機能(Googleニュース収集、Gmail送信、exe化、初回セットアップウィザード)
- v1.3: 要約をTextRank方式に(後にv1.4で冒頭抽出方式に戻す)
- v1.4: 対象日付の指定機能、文字化けバグ修正
- v1.5: 日付の範囲指定に対応
- v1.6: Claude Haiku 4.5によるAI要約に対応、要約失敗記事の除外+件数補充
- v1.7: 「NO_SUMMARY」が本文に混入するバグ修正、文体を「だ・である調」に統一
- v1.8: AIによる重複記事の除外(媒体をまたいだ同一事件の統合)、カンマ区切りOR検索
- v2.0: CLIからTkinter GUIへ移行(カレンダーからの日付選択、プログレスバー、ポップアップ通知)
- v2.1: 収集中に中止できる「中止」ボタンを追加(`cancel_event`/`OperationCancelled`によるキャンセル伝播)

## 未対応・今後の検討事項(明示的な依頼はまだないが会話中に触れたもの)

- PR TIMESなど一部のプレスリリース系サイトで、フォームやCTA文言が要約に混ざることがある
  (無料アルゴリズム時代からの既知の軽微な問題。AI要約導入後は目立たなくなったが完全解消はしていない)
- スケジュール実行(定期実行)には未対応。手動実行のみ

## 直前の会話内容

CLI(ターミナル入力)からTkinter GUIへの移行(v2.0)を実施した。`main.py`のCLIフロー
(`run_setup_wizard`、`input()`によるテーマ・日付入力、`print`による進捗表示)を撤去し、
`App(tk.Tk)`クラスによるフォーム画面に置き換えた。`fetch_news()`に`progress_callback`引数を
追加し、業務ロジック関数(`fetch_news`/`send_email`/`build_email_body`等)自体は変更していない。
ユーザー(bhag008)本人が実機の`dist/news-collector.exe`をクリックして動作確認し、
「問題ありません」と確認。大規模アップデートとして v2.0 でリリース済み(コミット`eef6134`)。

その後、「実行中に中止するための機能が欲しい」との依頼で中止ボタンを追加した(上記UI形式
セクション参照)。`OperationCancelled`例外・`cancel_event`によるキャンセル伝播のロジックは
Pythonから直接`fetch_news`を呼び出して動作確認済み(進捗2件時点でキャンセルし、正しく
`OperationCancelled`が送出されることを確認)。ユーザー本人が実機で中止ボタンの動作を確認し
「問題ありません」と確認。v2.1としてリリース済み。
