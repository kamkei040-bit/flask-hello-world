import os
import json
import base64
import requests
from flask import Flask, request
from openai import OpenAI
import time
import re
import urllib.parse

app = Flask(__name__)

USER_STATE = {}
STATE_TTL_SEC = 60 * 60 * 6

LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def line_headers():
    return {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def reply_message(reply_token: str, text: str):
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    r = requests.post(LINE_REPLY_URL, headers=line_headers(), json=payload, timeout=20)
    print("Reply status:", r.status_code, r.text)
    return r


def push_message(to: str, text: str):
    payload = {
        "to": to,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    r = requests.post(LINE_PUSH_URL, headers=line_headers(), json=payload, timeout=20)
    print("Push status:", r.status_code, r.text)
    return r


def fetch_line_image_bytes(message_id: str) -> bytes:
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    r = requests.get(url, headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}, timeout=30)
    r.raise_for_status()
    return r.content


def analyze_image_for_mercari(image_bytes: bytes) -> dict:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{b64}"

    prompt = """あなたは日本の物販（メルカリ）仕入れ判定アシスタントです。
画像の商品をできるだけ正確に特定し、仕入れ判断に必要な情報を「JSONだけ」で返してください。
（前後に文章をつけない。JSONのみ。）

必須キー:
{
  "name": "商品名推定",
  "brand": "ブランド/メーカー(不明ならnull)",
  "model": "型番(不明ならnull)",
  "jan": "JAN/EAN(不明ならnull)",
  "category": "カテゴリ",
  "condition_guess": "未使用/中古など",
  "keywords": ["メルカリ検索ワード1","2","3"],
  "shipping_yen_guess": 210,
  "price_range_yen": [1800, 3500],
  "tips": {
    "title_example": "出品タイトル例（短く）",
    "desc_points": ["説明に入れる要点1","2"]
  }
}
"""

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url},
            ],
        }],
    )

    text = (resp.output_text or "").strip()

    # 保険：最初の { から最後の } を抜く
    if "{" in text and "}" in text:
        text = text[text.find("{"):text.rfind("}") + 1]

    return json.loads(text)


def cleanup_state():
    now = time.time()
    expired = []
    for uid, st in USER_STATE.items():
        if now - st.get("ts", 0) > STATE_TTL_SEC:
            expired.append(uid)
    for uid in expired:
        USER_STATE.pop(uid, None)


def parse_yen_from_text(s: str):
    s = s.replace(",", "")
    m = re.search(r"([0-9]{1,7})", s)
    return int(m.group(1)) if m else None


def compute_profit(sell_yen: int, cost_yen: int, ship_yen: int, fee_rate: float = 0.10) -> int:
    fee = int(round(sell_yen * fee_rate))
    return sell_yen - fee - ship_yen - cost_yen


def mercari_search_url(keyword: str) -> str:
    q = urllib.parse.quote(keyword)
    return f"https://jp.mercari.com/search?keyword={q}"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}

    print("Received:", json.dumps(data, ensure_ascii=False))

    events = data.get("events", [])
    for event in events:
        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        src = event.get("source") or {}
        user_id = src.get("userId")  # Push先（1:1トークなら入る）
        message = event.get("message", {})
        msg_type = message.get("type")

        cleanup_state()

        try:
            # ===== 画像 =====
            if msg_type == "image":
                message_id = message.get("id")
                if not message_id:
                    reply_message(reply_token, "画像IDが取れませんでした。もう一度送ってください。")
                    continue

                # まず即返信（これが重要）
                reply_message(reply_token, "画像を受け取りました。解析中です…（数秒〜20秒）")

                # 解析（時間かかってもOK）
                img_bytes = fetch_line_image_bytes(message_id)
                result = analyze_image_for_mercari(img_bytes)

                ship = result.get("shipping_yen_guess")
                pr = result.get("price_range_yen") or None
                low, high = (pr[0], pr[1]) if (isinstance(pr, list) and len(pr) == 2) else (None, None)

                keywords = result.get("keywords") or []
                name = result.get("name") or "不明"

                if user_id:
                    USER_STATE[user_id] = {
                        "ts": time.time(),
                        "shipping_yen": ship if isinstance(ship, int) else None,
                        "price_low": low if isinstance(low, int) else None,
                        "price_high": high if isinstance(high, int) else None,
                        "name": name,
                        "keywords": keywords,
                    }

                kw_text = " / ".join(keywords[:3]) if keywords else "（不明）"
                ship_text = f"{ship}円" if isinstance(ship, int) else "不明"
                pr_text = f"{low}〜{high}円" if (isinstance(low, int) and isinstance(high, int)) else "不明"
                link = mercari_search_url(keywords[0] if keywords else name)

msg = (
    f"【商品推定】{name}\n"
    f"【検索】{kw_text}\n"
    f"【送料目安】{ship_text}\n"
    f"【売価目安(推定)】{pr_text}\n\n"
    f"▼メルカリ検索（売れた相場の確認用）\n{link}\n"
    f"※アプリで開かない場合：リンク長押し →「ブラウザで開く」（外部ブラウザ）\n\n"
    f"次に価格を送ってください：\n"
    f"・仕入れ 980\n"
    f"・売れた 2800（実相場）\n"
    f"・売値 2800（希望売値）"
)

                # Pushで送る
                if user_id:
                    push_message(user_id, msg)
                else:
                    print("No userId. Push cannot be sent.")
                continue

            # ===== テキスト =====
            if msg_type == "text":
                user_text = (message.get("text") or "").strip()

                cost = parse_yen_from_text(user_text) if "仕入" in user_text else None
                sell = None
                if "売れた" in user_text:
                    sell = parse_yen_from_text(user_text)
                elif "売" in user_text:
                    sell = parse_yen_from_text(user_text)

                st = USER_STATE.get(user_id) if user_id else None

                if cost is not None:
                    if not st:
                        reply_message(reply_token, "直前の画像が見つかりません。先に商品画像を送ってください。")
                        continue

                    ship = st.get("shipping_yen") or 0

                    if sell is None:
                        low = st.get("price_low")
                        high = st.get("price_high")
                        if isinstance(low, int) and isinstance(high, int):
                            sell = int(round((low + high) / 2))
                        else:
                            reply_message(reply_token, "仕入れOK。次に「売れた 2800」または「売値 2800」を送ってください。")
                            continue

                    profit = compute_profit(sell, cost, ship, fee_rate=0.10)
                    reply_message(reply_token, f"利益目安：{profit}円（売値{sell}円・送料{ship}円・手数料10%）")
                    continue

                if sell is not None and cost is None:
                    reply_message(reply_token, "OK！次に「仕入れ 980」も送ってください（利益を確定します）。")
                    continue

                reply_message(reply_token, "画像を送ってください。価格は「仕入れ 980」「売れた 2800」「売値 2800」でOKです。")
                continue

            reply_message(reply_token, f"{msg_type} を受け取りました（画像かテキストでお願いします）")

        except Exception as e:
            print("Error:", e)
            # ここまで来れば最低限の返信は返す
            reply_message(reply_token, f"エラー：{type(e).__name__}")

    return "OK"


@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running"
