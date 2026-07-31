import os
import smtplib
import sys
import urllib.parse
from email.mime.text import MIMEText

import feedparser
from dotenv import load_dotenv


def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(get_base_dir(), ".env")


def run_setup_wizard():
    print("=" * 50)
    print("初回起動のため、設定を行います。")
    print("Gmailの「アプリパスワード」が必要です。")
    print("お持ちでない場合は下記URLから発行してください。")
    print("https://myaccount.google.com/apppasswords")
    print("=" * 50)
    print()

    gmail_address = input("あなたのGmailアドレス: ").strip()
    gmail_app_password = input("Gmailのアプリパスワード(16桁): ").strip()
    to_email = input("ニュースの送信先メールアドレス(空欄なら自分宛て): ").strip() or gmail_address
    news_count = input("収集する件数(空欄なら30件): ").strip() or "30"

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(f"GMAIL_ADDRESS={gmail_address}\n")
        f.write(f"GMAIL_APP_PASSWORD={gmail_app_password}\n")
        f.write(f"TO_EMAIL={to_email}\n")
        f.write(f"NEWS_COUNT={news_count}\n")

    print()
    print("設定を保存しました。設定をやり直したい場合は、このexeと同じ場所にある")
    print(".env ファイルを削除してから再実行してください。")
    print()


def fetch_news(theme, count):
    query = urllib.parse.quote(theme)
    feed_url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(feed_url)
    return [(entry.title, entry.link) for entry in feed.entries[:count]]


def build_email_body(theme, articles):
    lines = [f"テーマ「{theme}」に関するニュース ({len(articles)}件)", ""]
    for i, (title, link) in enumerate(articles, start=1):
        lines.append(f"{i}. {title}\n   {link}")
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
    if not os.path.exists(CONFIG_PATH):
        run_setup_wizard()

    load_dotenv(CONFIG_PATH)
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    to_email = os.environ["TO_EMAIL"]
    news_count = int(os.environ.get("NEWS_COUNT", "30"))

    theme = input("収集したいニュースのテーマを入力してください: ").strip()
    if not theme:
        print("テーマが入力されていません。")
        return

    print(f"「{theme}」に関するニュースを収集中...")
    articles = fetch_news(theme, news_count)

    if not articles:
        print("ニュースが見つかりませんでした。")
        return

    body = build_email_body(theme, articles)
    subject = f"【ニュース収集】{theme} ({len(articles)}件)"

    try:
        send_email(gmail_address, gmail_app_password, to_email, subject, body)
    except smtplib.SMTPAuthenticationError:
        print()
        print("メール送信に失敗しました。Gmailアドレスかアプリパスワードが間違っている可能性があります。")
        print(f"設定をやり直す場合は {CONFIG_PATH} を削除してから再実行してください。")
        return

    print(f"{len(articles)}件のニュースを {to_email} に送信しました。")


if __name__ == "__main__":
    main()
    try:
        input("終了するには何かキーを押してください...")
    except EOFError:
        pass
