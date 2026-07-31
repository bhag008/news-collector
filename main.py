import os
import smtplib
import urllib.parse
from email.mime.text import MIMEText

import feedparser
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
TO_EMAIL = os.environ["TO_EMAIL"]
NEWS_COUNT = int(os.environ.get("NEWS_COUNT", "30"))


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


def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = TO_EMAIL

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)


def main():
    theme = input("収集したいニュースのテーマを入力してください: ").strip()
    if not theme:
        print("テーマが入力されていません。")
        return

    print(f"「{theme}」に関するニュースを収集中...")
    articles = fetch_news(theme, NEWS_COUNT)

    if not articles:
        print("ニュースが見つかりませんでした。")
        return

    body = build_email_body(theme, articles)
    subject = f"【ニュース収集】{theme} ({len(articles)}件)"

    send_email(subject, body)
    print(f"{len(articles)}件のニュースを {TO_EMAIL} に送信しました。")


if __name__ == "__main__":
    main()
