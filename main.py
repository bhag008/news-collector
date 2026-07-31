import datetime
import json
import os
import re
import smtplib
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText
from urllib.parse import quote, urlparse

import feedparser
import requests
import trafilatura
from dotenv import load_dotenv

NEWS_COUNT = 30
REQUEST_TIMEOUT = 10
MAX_WORKERS = 5
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


def run_setup_wizard():
    print("=" * 50)
    print("初回起動のため、設定を行います。")
    print("=" * 50)
    print()

    to_email = ""
    while not to_email:
        to_email = input("ニュースを届けたいメールアドレス: ").strip()

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(f"TO_EMAIL={to_email}\n")

    print()
    print("設定を保存しました。設定をやり直したい場合は、このexeと同じ場所にある")
    print(".env ファイルを削除してから再実行してください。")
    print()


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


def extract_summary(article_url, max_sentences=3, max_chars=300):
    try:
        resp = requests.get(article_url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.exceptions.RequestException:
        return None

    text = trafilatura.extract(resp.content, url=article_url)
    if not text:
        return None

    if _looks_paywalled(text):
        return None

    text = _strip_boilerplate(text)
    if not text.strip():
        return None

    return summarize_text(text, max_sentences=max_sentences, max_chars=max_chars)


def format_date(entry):
    parsed = entry.get("published_parsed")
    if parsed:
        return f"{parsed.tm_mon}/{parsed.tm_mday}"
    return ""


def parse_date_input(text):
    text = text.strip()
    if not text:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _entry_jst_date(entry):
    parsed = entry.get("published_parsed")
    if not parsed:
        return None
    utc_dt = datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)
    return (utc_dt + datetime.timedelta(hours=9)).date()


def process_entry(entry):
    article_url = resolve_article_url(entry.link)
    summary = extract_summary(article_url)
    return {
        "title": entry.title,
        "date": format_date(entry),
        "source": entry.get("source", {}).get("title", ""),
        "summary": summary,
        "url": article_url,
    }


def fetch_news(theme, count, target_date=None):
    query_text = theme
    if target_date:
        next_day = target_date + datetime.timedelta(days=1)
        query_text = f"{theme} after:{target_date.isoformat()} before:{next_day.isoformat()}"

    query = urllib.parse.quote(query_text)
    feed_url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(feed_url)

    entries = feed.entries
    if target_date:
        entries = [e for e in entries if _entry_jst_date(e) == target_date]
    entries = entries[:count]
    total = len(entries)
    if total == 0:
        return []

    articles = [None] * total
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_index = {executor.submit(process_entry, e): i for i, e in enumerate(entries)}
        done = 0
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            articles[i] = future.result()
            done += 1
            print(f"  {done}/{total} 件処理しました")

    return articles


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


def main():
    load_dotenv(SENDER_CONFIG_PATH)
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]

    if not os.path.exists(CONFIG_PATH):
        run_setup_wizard()

    load_dotenv(CONFIG_PATH)
    to_email = os.environ["TO_EMAIL"]

    theme = input("収集したいニュースのテーマを入力してください: ").strip()
    if not theme:
        print("テーマが入力されていません。")
        return

    date_input = input("対象の日付を指定する場合は入力してください(例: 2026/7/30、空欄で指定なし): ").strip()
    target_date = None
    if date_input:
        target_date = parse_date_input(date_input)
        if target_date is None:
            print("日付の形式が正しくありません(例: 2026/7/30)。日付指定なしで続けます。")

    print(f"「{theme}」に関するニュースを収集中...(記事の要約も取得するため、少し時間がかかります)")
    articles = fetch_news(theme, NEWS_COUNT, target_date=target_date)

    if not articles:
        print("ニュースが見つかりませんでした。")
        return

    date_label = f"({target_date.strftime('%Y/%m/%d')})" if target_date else ""
    body = build_email_body(theme, articles, date_label=date_label)
    subject = f"【ニュース収集】{theme}{date_label} ({len(articles)}件)"

    try:
        send_email(gmail_address, gmail_app_password, to_email, subject, body)
    except smtplib.SMTPAuthenticationError:
        print()
        print("メール送信に失敗しました。作成者に連絡してください。")
        return

    print(f"{len(articles)}件のニュースを {to_email} に送信しました。")


if __name__ == "__main__":
    main()
    try:
        input("終了するには何かキーを押してください...")
    except EOFError:
        pass
