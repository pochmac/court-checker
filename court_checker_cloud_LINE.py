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
# 1. システム設定項目
# ==========================================

# 監視対象の予約カレンダーURL
URLS = [
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/122/reservation_calendar",
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/123/reservation_calendar",
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/125/reservation_calendar"
]

# 通知用メッセージに変換するためのURLとコート名のマッピング
COURT_NAMES = {
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/122/reservation_calendar": "Aコート",
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/123/reservation_calendar": "Bコート",
    "https://city.nagano.nagano.machikagi-remote.jp/rooms/125/reservation_calendar": "Cコート"
}

# 取得した画面テキストから「空き」と判定するためのターゲット文字列
TARGET_KEYWORDS = [
    "08:30 - 10:00 : ￥0 先着",
    "10:00 - 12:00 : ￥0 先着"
]

# 祝日判定ライブラリ（jpholiday）でカバーできない臨時の休館日・祝日を手動設定するリスト（フォーマット: 'YYYY-MM-DD'）
MANUAL_HOLIDAYS = []

# --- 定期生存報告の実行時間設定 ---
# ⚠️ 注意: サーバー環境（GitHub Actions等）の標準時である「UTC（世界標準時）」で指定します。
# [設定値の解説]
#  - 0 : UTC 0時  ＝ 日本時間(JST)  9時
#  - 14: UTC 14時 ＝ 日本時間(JST) 23時
# 空き枠が「なかった」場合でも、この時間帯に合致すればシステムが正常稼働している旨の定期報告メール/LINEが飛びます。
REPORT_HOURS = [0, 14]

# --- メール通知設定（Gmail SMTP経由） ---
# 環境変数（GitHub Secrets等）から認証情報を取得。未設定時のフォールバックとしてデフォルトアドレスを指定
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "spike3363@gmail.com")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD") 

# 通知先メールアドレス（複数指定可能）
TO_EMAILS = ["kita.ngntennis@gmail.com", "hito3363@gmail.com"]

# --- LINE公式アカウント通知設定（Messaging API経由） ---
# 環境変数からLINE送信に必要なアクセストークンとユーザーIDを取得
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")


# ==========================================
# 2. 通知処理モジュール（メール ＆ LINE）
# ==========================================

def send_line_message(text_content):
    """
    LINE公式アカウントの登録者（友だち）全員にメッセージをブロードキャスト送信する。
    LINEの文字数制限（最大5000文字）を考慮し、4500文字で安全に丸める処理を含む。
    """
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
    """
    Gmail SMTPサーバー（587番ポート / STARTTLS）を利用して、指定アドレス宛に一斉メールを送信する。
    """
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
    """
    メール送信とLINE送信を同時に実行する包括的な通知ラッパー関数。
    """
    send_email(subject, body)
    line_text = f"【{subject}】\n\n{body}"
    send_line_message(line_text)


# ==========================================
# 3. 日付判定・抽出ロジック
# ==========================================

def get_target_dates_by_month():
    """
    現在日を起点として、「当月」と「翌月」の残り日数の中から、
    監視対象とする【土曜日・日曜日・祝日・手動設定休日】の日付オブジェクトを抽出して返す。
    """
    today = datetime.date.today()
    current_month_dates = []
    next_month_dates = []
    
    for i in range(2):
        # i=0: 当月 / i=1: 翌月の年・月を算出（年跨ぎに対応）
        year = today.year + (today.month + i - 1) // 12
        month = (today.month + i - 1) % 12 + 1
        _, num_days = calendar.monthrange(year, month)
        
        for day in range(1, num_days + 1):
            date_obj = datetime.date(year, month, day)
            
            # 過去の日付はスキップ
            if date_obj < today:
                continue
            date_str = date_obj.strftime("%Y-%m-%d")
            
            # 土日（weekday 5,6）、祝日判定、または手動休日リストに合致するかチェック
            if date_obj.weekday() >= 5 or jpholiday.is_holiday(date_obj) or date_str in MANUAL_HOLIDAYS:
                if i == 0:
                    current_month_dates.append(date_obj)
                else:
                    next_month_dates.append(date_obj)
                    
    return current_month_dates, next_month_dates


# ==========================================
# 4. Webスクレイピングコアロジック
# ==========================================

def scan_dates(page, dates, url, found_slots):
    """
    Playwrightのブラウザインスタンスを用いて、カレンダー内の指定された日付セルを順次クリックし、
    表示されるコマの中から対象キーワード（空枠）が存在するかを走査・検出する。
    """
    for target_date in dates:
        date_str = target_date.strftime("%Y-%m-%d")
        print(f"  → 処理日を確認中: {date_str} ({target_date.strftime('%a')})")
        
        # カレンダー上の該当する日付セル（td[data-date='YYYY-MM-DD']）を取得
        date_element = page.locator(f"td[data-date='{date_str}']")
        if date_element.count() > 0:
            try:
                # 画面外にある場合はスクロールして強制クリック、描画待ちとして2秒スリープ
                date_element.first.scroll_into_view_if_needed()
                date_element.first.click(force=True, timeout=3000)
                time.sleep(2.0)
                
                # ページ全体のテキストを取得し、改行や余分な空白を正規化
                raw_text = page.locator("body").inner_text()
                normalized_text = " ".join(raw_text.split())
                
                # 指定したキーワード（空きコマ情報）が含まれているか走査
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
                # 特定の日付でクリックエラーが発生しても全体の処理を止めずに次の日に遷移
                print(f"     ↳ {date_str} のクリック中にスキップ（理由: {e}）")


# ==========================================
# 5. メイン制御フロー
# ==========================================

def check_court_availability():
    """
    システム全体のメイン処理。
    対象日の抽出 ➔ ヘッドレスブラウザ（Playwright）の起動 ➔ 各URLのスクレイピング ➔ 結果の判定・通知
    """
    current_dates, next_dates = get_target_dates_by_month()
    total_check_days = len(current_dates) + len(next_dates)
    print(f"【チェック対象日】: 当月 {len(current_dates)}日 / 翌月 {len(next_dates)}日（計 {total_check_days}日間）を監視します。")
    
    found_slots = []
    
    # Playwrightによるブラウザ制御（Chromiumをヘッドレスモードで起動）
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for url in URLS:
            print(f"\n--- URLを確認中: {url} ---")
            start_count = len(found_slots)
            
            try:
                # カレンダーページを開き、ネットワーク通信が落ち着くまで最大15秒待機
                page.goto(url, wait_until="networkidle", timeout=15000)
                time.sleep(2.0)
                
                # 1. 当月分のカレンダーを走査
                if current_dates:
                    scan_dates(page, current_dates, url, found_slots)
                
                # 2. 翌月分のカレンダーを走査（カレンダーの「次月(＞)」ボタンを押して遷移）
                if next_dates:
                    next_button = page.locator("button:has-text('>')").first
                    if next_button.count() == 0:
                        # サイトのUI仕様変更に備えたフォールバック用のセレクター指定
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
    
    # --- 最終判定と通知のロジック ---
    # サーバー時間のタイムゾーン（通常UTC）に基づいて現在の時間・分を取得
    current_hour = datetime.datetime.now().hour
    current_minute = datetime.datetime.now().minute
    jst_hour = (current_hour + 9) % 24

    # パターンA：空き枠が見つかった場合（時間に関係なく、即座に「緊急通知」）
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

    # パターンB：空き枠はなく、指定時間（REPORT_HOURS）かつ「毎時5分未満（0分〜4分）」の初回起動時のみ実行
    elif current_hour in REPORT_HOURS and current_minute < 5:
        print(f"現在、サーバー時間で{current_hour}時{current_minute}分（日本時間{jst_hour}時台）です。定期生存報告を送信します。")
        
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

    # パターンC：空き枠がなく、定期報告の時間でもない（または同時間帯の2回目以降の起動の）場合
    else:
        print(f"今回の確認（日本時間{jst_hour}時{current_minute}分）では、通知条件を満たさなかったためスキップしました。")


if __name__ == "__main__":
    check_court_availability()
