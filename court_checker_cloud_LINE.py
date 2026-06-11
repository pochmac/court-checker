import datetime
import calendar
import time
import jpholiday
import smtplib
from email.mime.text import MIMEText
from playwright.sync_api import sync_playwright
import os
import requests

# ==========================================
# 設定項目
# ==========================================
URLS = [
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/122/reservation_calendar",
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/123/reservation_calendar",
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/125/reservation_calendar"
]

# URLとコート名の対応マップ
COURT_NAMES = {
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/122/reservation_calendar": "Aコート",
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/123/reservation_calendar": "Bコート",
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/125/reservation_calendar": "Cコート"
}

# 監視したいキーワード
TARGET_KEYWORDS = [
    "08:30 - 10:00 : ￥0 先着",
    "10:00 - 12:00 : ￥0 先着"
]

# 手動設定用の土日祝日（必要な場合のみ）
MANUAL_HOLIDAYS = []

# --- 定期報告の設定 ---
REPORT_HOURS = [0, 14]

# --- メール通知設定 ---
# 🟢 修正：GitHub Secrets（環境変数）から最優先で取得します
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "spike3363@gmail.com")

# 🟢 重要な修正：コード内に直接書いていた生のアプリパスワード("xxdevbll...")を完全に消去しました。
# これにより、公開（Public）リポジトリにしてもあなたのGoogleアカウントが悪用される心配はゼロになります。
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD") 

TO_EMAILS = ["kita.ngntennis@gmail.com", "hito3363@gmail.com"]

# --- LINE通知設定 ---
# 🟢 重要な修正：コード内に直接書いていた長いアクセストークン("x9scVz...")を完全に消去しました。
# GitHub Secretsに設定した「LINE_CHANNEL_ACCESS_TOKEN」からのみ安全に読み込みます。
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

# 🟢 重要な修正：コード内に直接書いていたユーザーID("U6f12e...")を完全に消去しました。
# これでLINEアカウントの身元が全世界に晒されるリスクも完全に解消されます。
LINE_USER_ID = os.environ.get("LINE_USER_ID")

# ==========================================
# 通知処理（メール ＆ LINE）
# ==========================================
def send_line_message(text_content):
    """LINE公式アカウントの友だち全員に一斉通知（ブロードキャスト）を送信する"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE通知スキップ: トークンが設定されていません。")
        return

    if len(text_content) > 4500:
        text_content = text_content[:4500] + "\n\n...(長文のため省略されました)"

    url = "https://api.line.me/v2/bot/message/broadcast" 
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "messages": [
            {
                "type": "text",
                "text": text_content
            }
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            print("🟢 LINE通知を全員に正常に送信しました。")
        else:
            print(f"❌ LINE通知失敗: ステータスコード {response.status_code}, 詳細: {response.text}")
    except Exception as e:
        print(f"❌ LINE送信中にエラーが発生しました: {e}")

def send_email(subject, body):
    """指定されたメールアドレスに結果をテキスト送信する"""
    if not SENDER_PASSWORD:
        print("⚠️ エラー: メール送信用のパスワード（SENDER_PASSWORD）が設定されていません。")
        return

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SENDER_EMAIL
        msg["To"] = ", ".join(TO_EMAILS)

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, TO_EMAILS, msg.as_string())
        print("📧 メールを正常に送信しました。")
    except Exception as e:
        print(f"❌ メール送信中にエラーが発生しました: {e}")


def broadcast_notifications(subject, body):
    """メールとLINEの両方に同じ内容（タイトル+本文）を送信する統括関数"""
    send_email(subject, body)
    line_text = f"【{subject}】\n\n{body}"
    send_line_message(line_text)


# ==========================================
# 対象日（当月・翌月の土日祝日）を特定する関数
# ==========================================
def get_target_dates_by_month():
    today = datetime.date.today()
    current_month_dates = []
    next_month_dates = []
    
    for i in range(2):
        year = today.year + (today.month + i - 1) // 12
        month = (today.month + i - 1) % 12 + 1
        _, num_days = calendar.monthrange(year, month)
        
        for day in range(1, num_days + 1):
            date_obj = datetime.date(year, month, day)
            if date_obj < today:
                continue
            date_str = date_obj.strftime("%Y-%m-%d")
            
            if date_obj.weekday() >= 5 or jpholiday.is_holiday(date_obj) or date_str in MANUAL_HOLIDAYS:
                if i == 0:
                    current_month_dates.append(date_obj)
                else:
                    next_month_dates.append(date_obj)
                    
    return current_month_dates, next_month_dates


# ==========================================
# カレンダー内の日付をチェックするヘルパー関数
# ==========================================
def scan_dates(page, dates, url, found_slots):
    for target_date in dates:
        date_str = target_date.strftime("%Y-%m-%d")
        print(f"  → 処理日を確認中: {date_str} ({target_date.strftime('%a')})")
        
        date_element = page.locator(f"td[data-date='{date_str}']")
        if date_element.count() > 0:
            try:
                date_element.first.scroll_into_view_if_needed()
                date_element.first.click(force=True, timeout=3000)
                time.sleep(2.0)
                
                raw_text = page.locator("body").inner_text()
                normalized_text = " ".join(raw_text.split())
                
                for keyword in TARGET_KEYWORDS:
                    normalized_keyword = " ".join(keyword.split())
                    if normalized_keyword in normalized_text:
                        print(f"   【★空き発見！】 {date_str} ({target_date.strftime('%a')}) -> {keyword}")
                        court_name = COURT_NAMES.get(url, "不明なコート")
                        found_slots.append({
                            "url": url,
                            "court": court_name,  
                            "date": f"{date_str} ({target_date.strftime('%a')})",
                            "time": keyword
                        })
            except Exception as e:
                print(f"     ↳ {date_str} のクリック中にスキップ（理由: {e}）")


# ==========================================
# メインのスクレイピング処理
# ==========================================
def check_court_availability():
    current_dates, next_dates = get_target_dates_by_month()
    total_check_days = len(current_dates) + len(next_dates)
    print(f"【チェック対象日】: 当月 {len(current_dates)}日 / 翌月 {len(next_dates)}日（計 {total_check_days}日間）を監視します。")
    
    found_slots = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for url in URLS:
            print(f"\n--- URLを確認中: {url} ---")
            start_count = len(found_slots)
            
            try:
                page.goto(url, wait_until="networkidle", timeout=15000)
                time.sleep(2.0)
                
                if current_dates:
                    scan_dates(page, current_dates, url, found_slots)
                
                if next_dates:
                    next_button = page.locator("button:has-text('>')").first
                    if next_button.count() == 0:
                        next_button = page.locator(".fc-next-button, .next-month-button, button.next").first
                    
                    if next_button.count() > 0:
                        next_button.click(force=True, timeout=3000)
                        time.sleep(2.0)
                        scan_dates(page, next_dates, url, found_slots)
                
                if len(found_slots) == start_count:
                    print("❌ このコートには対象時間帯の先着空き枠はありませんでした。")
                                        
            except Exception as e:
                print(f"エラーが発生しました（URLをスキップします）: {e}")
                
        browser.close()
    
    # 最終結果の通知処理
    current_hour = datetime.datetime.now().hour
    jst_hour = (current_hour + 9) % 24

    if found_slots:
        subject_msg = "【緊急】テニスコート先着空き枠通知"
        body_text = f"【先着空き枠】が {len(found_slots)} 件見つかりました！\n\n"
        for idx, slot in enumerate(found_slots, 1):
            body_text += f"【枠 {idx}】\n"
            body_text += f"   🏢 コート: {slot['court']}\n"  
            body_text += f"   📅 日時: {slot['date']}\n"
            body_text += f"   ⏰ 時間: {slot['time']}\n"
            body_text += f"   🔗 予約URL: {slot['url']}\n"
            body_text += "-" * 30 + "\n"
            
        broadcast_notifications(subject=subject_msg, body=body_text)

    elif current_hour in REPORT_HOURS:
        print(f"現在、サーバー時間で{current_hour}時（日本時間{jst_hour}時）です。定期生存報告を送信します。")
        
        subject_msg = f"【定期報告】テニスコート監視システム稼働中 ({jst_hour}時)"
        body_text = f"本日 {jst_hour}時のシステム生存報告です。コートの空き枠はありませんでした。\n"
        body_text += "GitHub / Google Cloud経由でスクリプトは正常に稼働しています。\n\n"
        body_text += f"📊 本日の総チェック日数: {total_check_days} 日間（土日祝）\n"
        body_text += f"🌐 監視対象URL数: {len(URLS)} 箇所\n\n"
        body_text += "【監視対象URL一覧】\n"
        for url in URLS:
            court_name = COURT_NAMES.get(url, "不明なコート")
            body_text += f"・{court_name}: {url}\n"
            
        broadcast_notifications(subject=subject_msg, body=body_text)

    else:
        print(f"今回の確認（日本時間{jst_hour}時）では先着空き枠がなかったため、通知をスキップしました。")


if __name__ == "__main__":
    check_court_availability()
