import datetime
import json
import os
import queue
import re
import smtplib
import sys
import threading
import tkinter as tk
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText
from tkinter import messagebox, ttk
from urllib.parse import quote, urlparse

import anthropic
import feedparser
import requests
import trafilatura
from dotenv import load_dotenv
from tkcalendar import DateEntry

class OperationCancelled(Exception):
    pass


NEWS_COUNT = 30
REQUEST_TIMEOUT = 10
MAX_WORKERS = 5
ANTHROPIC_MODEL = "claude-haiku-4-5"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


def get_bundle_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_exe_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


SENDER_CONFIG_PATH = os.path.join(get_bundle_dir(), "sender.env")
CONFIG_PATH = os.path.join(get_exe_dir(), ".env")


def _get_base64_str(google_link):
    url = urlparse(google_link)
    path = url.path.split("/")
    if url.hostname == "news.google.com" and len(path) > 1 and path[-2] in ("articles", "read"):
        return path[-1]
    return None


def _get_decoding_params(base64_str):
    for url in (
        f"https://news.google.com/articles/{base64_str}",
        f"https://news.google.com/rss/articles/{base64_str}",
    ):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            continue
        sig_match = re.search(r'data-n-a-sg="([^"]+)"', resp.text)
        ts_match = re.search(r'data-n-a-ts="([^"]+)"', resp.text)
        if sig_match and ts_match:
            return sig_match.group(1), ts_match.group(1)
    return None, None


def _decode_url(signature, timestamp, base64_str):
    url = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
    payload = [
        "Fbv4je",
        f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
        f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{base64_str}",{timestamp},"{signature}"]',
    ]
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "User-Agent": USER_AGENT,
    }
    resp = requests.post(
        url,
        headers=headers,
        data=f"f.req={quote(json.dumps([[payload]]))}",
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    parsed = json.loads(resp.text.split("\n\n")[1])[:-2]
    return json.loads(parsed[0][2])[1]


def resolve_article_url(google_link):
    try:
        base64_str = _get_base64_str(google_link)
        if not base64_str:
            return google_link
        signature, timestamp = _get_decoding_params(base64_str)
        if not signature or not timestamp:
            return google_link
        return _decode_url(signature, timestamp, base64_str) or google_link
    except Exception:
        return google_link


def summarize_text(text, max_sentences=3, max_chars=300):
    sentences = [s.strip() for s in re.split(r"(?<=[。！?])", text.strip()) if s.strip()]
    if not sentences:
        return None

    summary = "".join(sentences[:max_sentences])
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "…"
    return summary or None


def summarize_with_ai(anthropic_client, text, max_chars=300):
    prompt = (
        "以下は、ニュースサイトのページから自動的に抽出したテキストです。"
        "まずこれが実際のニュース記事の本文かどうかを判断してください。\n"
        "記事本文であれば、次の2つを出力してください。\n"
        "1. 記事全体の要点を150〜250文字程度の日本語で要約する。"
        "文体は「だ・である調」で統一し、「です・ます調」は使わない。\n"
        "2. 区切り記号「###」に続けて、この記事が伝えている具体的な出来事を表す短いキーワード"
        "(いつ・どこで・誰が・何をしたか、を含めて20〜40文字程度)を出力する。"
        "同じ出来事を別の配信元が報じた記事であれば、同じような表現になるようにする。\n"
        "出力は要約文と、それに続く「###キーワード」の2つだけとし、それ以外の文章"
        "(例:「これは記事の本文です」「以下が要約です」といった前置きや説明、見出し)は一切含めないでください。\n"
        "記事本文ではなく、会員登録案内・エラーメッセージ・広告・ナビゲーションメニューなど"
        "記事以外の内容だった場合は、他には何も書かず「NO_SUMMARY」とだけ出力してください。\n\n"
        + text[:4000]
    )
    response = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(block.text for block in response.content if block.type == "text").strip()
    if not raw or "NO_SUMMARY" in raw:
        return None, None

    if "###" in raw:
        summary, _, event_key = raw.partition("###")
        summary = summary.strip()
        event_key = event_key.strip() or None
    else:
        summary = raw
        event_key = None

    if "\n\n" in summary:
        head, _, rest = summary.partition("\n\n")
        if len(head) < 60 and any(k in head for k in ("要約", "本文です", "以下")):
            summary = rest.strip()

    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "…"
    return summary, event_key


BOILERPLATE_MARKERS = [
    "RECOMMEND",
    "あなたにおすすめ",
    "PICK UP",
    "おすすめ記事",
    "おすすめコンテンツ",
    "関連記事",
    "この記事もおすすめ",
    "こちらの記事も",
    "SNSでシェア",
    "あわせて読みたい",
    "資料請求",
    "商談予約",
    "ログイン・新規登録",
    "掲載企業ログイン",
]

PAYWALL_MARKERS = [
    "有料会員限定",
    "有料登録",
    "会員限定記事",
    "この記事は会員限定",
    "この記事は有料",
    "続きを読むには",
    "ログインが必要です",
    "この記事は会員向け",
    "コンテンツブロックが有効",
    "広告ブロック機能",
    "アドブロックが有効",
]


def _strip_boilerplate(text, max_chars=3000):
    text = text[:max_chars]
    cut_positions = [pos for pos in (text.find(m) for m in BOILERPLATE_MARKERS) if pos != -1]
    if cut_positions:
        text = text[:min(cut_positions)]
    return text


def _looks_paywalled(text, search_chars=1000):
    head = text[:search_chars]
    return any(marker in head for marker in PAYWALL_MARKERS)


def extract_summary(article_url, anthropic_client=None, max_sentences=3, max_chars=300):
    try:
        resp = requests.get(article_url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.exceptions.RequestException:
        return None, None

    text = trafilatura.extract(resp.content, url=article_url)
    if not text:
        return None, None

    if _looks_paywalled(text):
        return None, None

    text = _strip_boilerplate(text)
    if not text.strip():
        return None, None

    if anthropic_client:
        try:
            return summarize_with_ai(anthropic_client, text, max_chars=max_chars)
        except Exception:
            return None, None  # AI summarization failed -> treat as "content not understood"

    return summarize_text(text, max_sentences=max_sentences, max_chars=max_chars), None


def format_date(entry):
    parsed = entry.get("published_parsed")
    if parsed:
        return f"{parsed.tm_mon}/{parsed.tm_mday}"
    return ""


def _entry_jst_date(entry):
    parsed = entry.get("published_parsed")
    if not parsed:
        return None
    utc_dt = datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)
    return (utc_dt + datetime.timedelta(hours=9)).date()


def _normalize_title_for_dedup(title):
    for sep in (" - ", " | "):
        idx = title.rfind(sep)
        if idx != -1:
            title = title[:idx]
    return title.strip()


def _normalize_url_for_dedup(url):
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    for suffix in ("/images/000", "/images"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return f"{parsed.netloc}{path}"


def _char_bigrams(text):
    text = re.sub(r"\s+", "", text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _title_similarity(a, b):
    set_a, set_b = _char_bigrams(a), _char_bigrams(b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


TITLE_SIMILARITY_THRESHOLD = 0.2


def dedupe_articles(articles):
    seen_urls = set()
    seen_titles = set()
    kept_compare_keys = []
    deduped = []
    for article in articles:
        url_key = _normalize_url_for_dedup(article["url"])
        title_key = _normalize_title_for_dedup(article["title"])
        compare_key = article.get("event_key") or title_key

        if url_key in seen_urls or title_key in seen_titles:
            continue
        if any(_title_similarity(compare_key, kept) >= TITLE_SIMILARITY_THRESHOLD for kept in kept_compare_keys):
            continue

        seen_urls.add(url_key)
        seen_titles.add(title_key)
        kept_compare_keys.append(compare_key)
        deduped.append(article)
    return deduped


def process_entry(entry, anthropic_client=None):
    article_url = resolve_article_url(entry.link)
    summary, event_key = extract_summary(article_url, anthropic_client=anthropic_client)
    return {
        "title": entry.title,
        "date": format_date(entry),
        "source": entry.get("source", {}).get("title", ""),
        "summary": summary,
        "event_key": event_key,
        "url": article_url,
    }


def build_theme_query(theme):
    keywords = [k.strip() for k in re.split(r"[,、]", theme) if k.strip()]
    if len(keywords) <= 1:
        return theme
    return "(" + " OR ".join(keywords) + ")"


def fetch_news(
    theme, count, start_date=None, end_date=None, anthropic_client=None, progress_callback=None, cancel_event=None
):
    query_text = build_theme_query(theme)
    if start_date and end_date:
        next_day = end_date + datetime.timedelta(days=1)
        query_text = f"{query_text} after:{start_date.isoformat()} before:{next_day.isoformat()}"

    query = urllib.parse.quote(query_text)
    feed_url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(feed_url)

    if cancel_event and cancel_event.is_set():
        raise OperationCancelled

    entries = feed.entries
    if start_date and end_date:
        filtered = []
        for e in entries:
            entry_date = _entry_jst_date(e)
            if entry_date and start_date <= entry_date <= end_date:
                filtered.append(e)
        entries = filtered

    candidate_entries = entries[: count * 3]
    total = len(candidate_entries)
    if total == 0:
        return []

    results = [None] * total
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_index = {
            executor.submit(process_entry, e, anthropic_client): i
            for i, e in enumerate(candidate_entries)
        }
        done = 0
        cancelled = False
        for future in as_completed(future_to_index):
            if cancel_event and cancel_event.is_set():
                for f in future_to_index:
                    f.cancel()
                cancelled = True
                break
            i = future_to_index[future]
            results[i] = future.result()
            done += 1
            if progress_callback:
                progress_callback(done, total)

    if cancelled:
        raise OperationCancelled

    articles = [a for a in results if a and a["summary"]]
    articles = dedupe_articles(articles)
    return articles[:count]


def build_email_body(theme, articles, date_label=""):
    lines = [f"テーマ「{theme}」{date_label}に関するニュース ({len(articles)}件)", ""]
    for article in articles:
        lines.append(f"●{article['title']}")
        meta = "　".join(x for x in [article["date"], article["source"]] if x)
        if meta:
            lines.append(meta)
        if article["summary"]:
            lines.append(article["summary"])
        lines.append(f"続きを読む: {article['url']}")
        lines.append("")
    return "\n".join(lines)


def send_email(gmail_address, gmail_app_password, to_email, subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = to_email

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_address, gmail_app_password)
        server.send_message(msg)


class App(tk.Tk):
    def __init__(self, gmail_address, gmail_app_password, anthropic_client):
        super().__init__()
        self.gmail_address = gmail_address
        self.gmail_app_password = gmail_app_password
        self.anthropic_client = anthropic_client
        self.task_queue = queue.Queue()
        self.cancel_event = None

        self.title("ニュース収集ツール")
        self.resizable(False, False)
        self._build_widgets()
        self._load_saved_email()

    def _build_widgets(self):
        pad = {"padx": 10, "pady": 6}
        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0)

        ttk.Label(
            frame,
            text="テーマを入力してニュースを収集し、指定したメールアドレスへお届けします。",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 10))

        ttk.Label(frame, text="受け取り先メールアドレス").grid(row=1, column=0, sticky="w", **pad)
        self.email_entry = ttk.Entry(frame, width=36)
        self.email_entry.grid(row=1, column=1, columnspan=2, sticky="we", **pad)

        ttk.Label(frame, text="ニュースのテーマ").grid(row=2, column=0, sticky="w", **pad)
        self.theme_entry = ttk.Entry(frame, width=36)
        self.theme_entry.grid(row=2, column=1, columnspan=2, sticky="we", **pad)
        ttk.Label(
            frame,
            text="複数のキーワードをカンマ区切りで入力すると、いずれかを含む記事が対象になります\n"
            "(例: 飲酒運転,酒酔い,アルコール)",
            foreground="gray",
            justify="left",
        ).grid(row=3, column=1, columnspan=2, sticky="w", padx=10)

        self.date_filter_var = tk.BooleanVar(value=False)
        self.date_filter_check = ttk.Checkbutton(
            frame,
            text="対象日を指定する",
            variable=self.date_filter_var,
            command=self._on_date_filter_toggle,
        )
        self.date_filter_check.grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 0))

        today = datetime.date.today()
        ttk.Label(frame, text="開始日").grid(row=5, column=0, sticky="w", **pad)
        self.start_date_entry = DateEntry(
            frame, width=16, date_pattern="yyyy/mm/dd", locale="ja_JP", state="disabled", year=today.year,
            month=today.month, day=today.day,
        )
        self.start_date_entry.grid(row=5, column=1, sticky="w", **pad)

        ttk.Label(frame, text="終了日").grid(row=6, column=0, sticky="w", **pad)
        self.end_date_entry = DateEntry(
            frame, width=16, date_pattern="yyyy/mm/dd", locale="ja_JP", state="disabled", year=today.year,
            month=today.month, day=today.day,
        )
        self.end_date_entry.grid(row=6, column=1, sticky="w", **pad)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=7, column=0, columnspan=3, pady=(16, 4))
        self.send_button = ttk.Button(button_frame, text="ニュースを収集してメール送信", command=self._on_submit)
        self.send_button.pack(side="left", padx=(0, 8))
        self.cancel_button = ttk.Button(button_frame, text="中止", command=self._on_cancel, state="disabled")
        self.cancel_button.pack(side="left")

        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=320)
        self.progress.grid(row=8, column=0, columnspan=3, pady=(4, 4))

        self.status_label = ttk.Label(frame, text="", foreground="gray")
        self.status_label.grid(row=9, column=0, columnspan=3)

    def _on_date_filter_toggle(self):
        state = "readonly" if self.date_filter_var.get() else "disabled"
        self.start_date_entry.configure(state=state)
        self.end_date_entry.configure(state=state)

    def _load_saved_email(self):
        if os.path.exists(CONFIG_PATH):
            load_dotenv(CONFIG_PATH)
            self.email_entry.insert(0, os.environ.get("TO_EMAIL", ""))

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.email_entry.configure(state=state)
        self.theme_entry.configure(state=state)
        self.date_filter_check.configure(state=state)
        self.send_button.configure(state=state)
        self.cancel_button.configure(state=("normal" if busy else "disabled"))
        if busy:
            self.start_date_entry.configure(state="disabled")
            self.end_date_entry.configure(state="disabled")
        else:
            self._on_date_filter_toggle()

    def _on_cancel(self):
        if self.cancel_event:
            self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status_label.configure(text="中止しています…")

    def _on_submit(self):
        to_email = self.email_entry.get().strip()
        theme = self.theme_entry.get().strip()

        if not to_email or "@" not in to_email:
            messagebox.showerror("入力エラー", "メールアドレスを正しく入力してください。")
            return
        if not theme:
            messagebox.showerror("入力エラー", "ニュースのテーマを入力してください。")
            return

        start_date = end_date = None
        if self.date_filter_var.get():
            start_date = self.start_date_entry.get_date()
            end_date = self.end_date_entry.get_date()
            if start_date > end_date:
                start_date, end_date = end_date, start_date

        self.cancel_event = threading.Event()
        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_label.configure(text="「%s」に関するニュースを検索しています…" % theme)

        threading.Thread(
            target=self._run_task, args=(to_email, theme, start_date, end_date, self.cancel_event), daemon=True
        ).start()
        self.after(100, self._poll_queue)

    def _run_task(self, to_email, theme, start_date, end_date, cancel_event):
        def progress_callback(done, total):
            self.task_queue.put(("progress", done, total))

        try:
            articles = fetch_news(
                theme,
                NEWS_COUNT,
                start_date=start_date,
                end_date=end_date,
                anthropic_client=self.anthropic_client,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )
        except OperationCancelled:
            self.task_queue.put(("cancelled", "処理を中止しました。"))
            return
        except Exception:
            self.task_queue.put(("error", "処理中にエラーが発生しました。時間をおいて再度お試しください。"))
            return

        if not articles:
            self.task_queue.put(("warning", "ニュースが見つかりませんでした。テーマや対象日を変えて試してください。"))
            return

        if cancel_event.is_set():
            self.task_queue.put(("cancelled", "処理を中止しました。"))
            return

        if start_date and end_date:
            if start_date == end_date:
                date_label = f"({start_date.strftime('%Y/%m/%d')})"
            else:
                date_label = f"({start_date.strftime('%Y/%m/%d')}〜{end_date.strftime('%Y/%m/%d')})"
        else:
            date_label = ""
        body = build_email_body(theme, articles, date_label=date_label)
        subject = f"【ニュース収集】{theme}{date_label} ({len(articles)}件)"

        try:
            send_email(self.gmail_address, self.gmail_app_password, to_email, subject, body)
        except smtplib.SMTPAuthenticationError:
            self.task_queue.put(("error", "メール送信に失敗しました。作成者に連絡してください。"))
            return
        except Exception:
            self.task_queue.put(("error", "メール送信中にエラーが発生しました。時間をおいて再度お試しください。"))
            return

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(f"TO_EMAIL={to_email}\n")

        self.task_queue.put(("done", f"{len(articles)}件のニュースを {to_email} に送信しました。"))

    def _poll_queue(self):
        try:
            while True:
                message = self.task_queue.get_nowait()
                kind = message[0]
                if kind == "progress":
                    _, done, total = message
                    if str(self.progress["mode"]) != "determinate":
                        self.progress.stop()
                        self.progress.configure(mode="determinate", maximum=total, value=0)
                    self.progress.configure(value=done)
                    self.status_label.configure(text=f"記事を確認しています…({done}/{total}件)")
                else:
                    self._finish(kind, message[1])
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _finish(self, kind, text):
        self.progress.stop()
        self.progress.configure(mode="indeterminate", value=0)
        self.status_label.configure(text="")
        self._set_busy(False)
        if kind == "done":
            messagebox.showinfo("送信完了", text)
        elif kind == "warning":
            messagebox.showwarning("ニュースなし", text)
        elif kind == "cancelled":
            messagebox.showinfo("中止しました", text)
        else:
            messagebox.showerror("エラー", text)


def launch_gui():
    load_dotenv(SENDER_CONFIG_PATH)
    try:
        gmail_address = os.environ["GMAIL_ADDRESS"]
        gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    except KeyError:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("設定エラー", "送信用の設定が見つかりません。作成者に連絡してください。")
        return

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key) if anthropic_api_key else None

    App(gmail_address, gmail_app_password, anthropic_client).mainloop()


if __name__ == "__main__":
    launch_gui()
