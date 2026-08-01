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

import anthropic
import feedparser
import requests
import trafilatura
from dotenv import load_dotenv

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


def parse_date_input(text):
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, "%Y/%m/%d").date()
    except ValueError:
        return None


def parse_date_range_input(text):
    text = text.strip()
    if not text:
        return None, None

    for sep in ("~", "〜", "-"):
        if sep in text:
            start_str, _, end_str = text.partition(sep)
            start = parse_date_input(start_str)
            end = parse_date_input(end_str)
            if start and end:
                if start > end:
                    start, end = end, start
                return start, end
            return None, None

    single = parse_date_input(text)
    if single:
        return single, single
    return None, None


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


def fetch_news(theme, count, start_date=None, end_date=None, anthropic_client=None):
    query_text = build_theme_query(theme)
    if start_date and end_date:
        next_day = end_date + datetime.timedelta(days=1)
        query_text = f"{query_text} after:{start_date.isoformat()} before:{next_day.isoformat()}"

    query = urllib.parse.quote(query_text)
    feed_url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(feed_url)

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
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            results[i] = future.result()
            done += 1
            print(f"  {done}/{total} 件処理しました")

    articles = [a for a in results if a["summary"]]
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


def main():
    load_dotenv(SENDER_CONFIG_PATH)
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key) if anthropic_api_key else None

    if not os.path.exists(CONFIG_PATH):
        run_setup_wizard()

    load_dotenv(CONFIG_PATH)
    to_email = os.environ["TO_EMAIL"]

    theme = input(
        "収集したいニュースのテーマを入力してください"
        "(複数のキーワードをカンマ区切りで入力すると、いずれかを含む記事が対象になります。例: 飲酒運転,酒酔い,アルコール): "
    ).strip()
    if not theme:
        print("テーマが入力されていません。")
        return

    date_input = input(
        "対象の日付を指定する場合は入力してください"
        "(例: 2026/7/30、範囲指定は 2026/7/28-2026/7/30、空欄で指定なし): "
    ).strip()
    start_date = end_date = None
    if date_input:
        start_date, end_date = parse_date_range_input(date_input)
        if start_date is None:
            print("日付の形式が正しくありません(例: 2026/7/30 または 2026/7/28-2026/7/30)。日付指定なしで続けます。")

    print(f"「{theme}」に関するニュースを収集中...(記事の要約も取得するため、少し時間がかかります)")
    articles = fetch_news(
        theme, NEWS_COUNT, start_date=start_date, end_date=end_date, anthropic_client=anthropic_client
    )

    if not articles:
        print("ニュースが見つかりませんでした。")
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
