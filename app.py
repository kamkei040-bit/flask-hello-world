import os
import json
import base64
import requests
from flask import Flask, request
from openai import OpenAI
import time
import re


app = Flask(__name__)

# ユーザーごとに直近の解析結果を保存（メモリ上：Render再起動で消えます）
USER_STATE = {}  # { userId: {"ts": epoch, "shipping_yen": int|None, "price_low": int|None, "price_high": int|None, "name": str, "keywords": [..]} }
STATE_TTL_SEC = 60 * 60 * 6  # 6時間


LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

def reply_message(reply_token: str, text: str):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    print("Reply status:", r.status_code, r.text)
    return r

def fetch_line_image_bytes(message_id: str) -> bytes:
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=30)
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
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
    )

    text = (resp.output_text or "").strip()

    # JSONだけを期待するが、万一混ざった時の保険：最初の { から最後の } を抜く
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
    if m:
        return int(m.group(1))
    return None

def compute_profit(sell_yen: int, cost_yen: int, ship_yen: int, fee_rate: float = 0.10) -> int:
    fee = int(round(sell_yen * fee_rate))
    profit = sell_yen - fee - ship_yen - cost_yen
    return profit

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("Received:", json.dumps(data, ensure_ascii=False))

    events = data.get("events", [])
    for event in events:
        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        # ユーザーID（グループだと無いこともあるので保険）
        user_id = (event.get("source") or {}).get("userId") or "unknown"

        message = event.get("message", {})
        msg_type = message.get("type")

        # 古い保存データ掃除
        cleanup_state()

        try:
            # ===== 画像が来たら：解析して保存 → 短い返信 =====
            if msg_type == "image":
                message_id = message.get("id")
                if not message_id:
                    reply_message(reply_token, "画像IDが取れませんでした。もう一度送ってください。")
                    continue

                img_bytes = fetch_line_image_bytes(message_id)
                data = analyze_image_for_mercari(img_bytes)  # dict が返る

                ship = data.get("shipping_yen_guess")
                pr = data.get("price_range_yen") or None
                low, high = (pr[0], pr[1]) if (isinstance(pr, list) and len(pr) == 2) else (None, None)

                USER_STATE[user_id] = {
                    "ts": time.time(),
                    "shipping_yen": ship if isinstance(ship, int) else None,
                    "price_low": low if isinstance(low, int) else None,
                    "price_high": high if isinstance(high, int) else None,
                    "name": data.get("name") or "不明",
                    "keywords": data.get("keywords") or [],
                    "title_example": (data.get("tips") or {}).get("title_example"),
                }

                kw = USER_STATE[user_id]["keywords"]
                kw_text = " / ".join(kw[:3]) if kw else "（不明）"
                ship_text = f"{USER_STATE[user_id]['shipping_yen']}円" if USER_STATE[user_id]["shipping_yen"] else "不明"
                pr_text = f"{low}〜{high}円" if (low and high) else "不明"

                reply = (
                    f"【商品推定】{USER_STATE[user_id]['name']}\n"
                    f"【検索】{kw_text}\n"
                    f"【送料目安】{ship_text}\n"
                    f"【売価目安】{pr_text}\n\n"
                    f"次に「仕入れ 980」のように仕入れ価格を送ってください。\n"
                    f"売値を指定したい場合は「売値 2800」も一緒に送ると利益を確定できます。"
                )
                reply_message(reply_token, reply)
                continue

            # ===== テキストが来たら：仕入れ/売値を拾って利益返信 =====
            if msg_type == "text":
                user_text = (message.get("text") or "").strip()

                cost = None
                sell = None

                if "仕入" in user_text:
                    cost = parse_yen_from_text(user_text)
                if "売" in user_text:
                    sell = parse_yen_from_text(user_text)

                st = USER_STATE.get(user_id)

                # 仕入れ価格が来た
                if cost is not None:
                    if not st:
                        reply_message(reply_token, "直前の画像が見つかりません。先に商品画像を送ってください。")
                        continue

                    ship = st.get("shipping_yen") or 0  # 不明なら0で仮

                    # 売値が未指定ならレンジ中央値で仮計算、レンジも無ければ売値要求
                    if sell is None:
                        low = st.get("price_low")
                        high = st.get("price_high")
                        if low and high:
                            sell = int(round((low + high) / 2))
                        else:
                            reply_message(reply_token, "仕入れ価格を受け取りました。売値も送ってください（例：売値 2800）。")
                            continue

                    profit = compute_profit(sell, cost, ship, fee_rate=0.10)
                    reply_message(reply_token, f"利益目安：{profit}円（売値{sell}円・送料{ship}円・手数料10%）")
                    continue

                # 仕入れが無い普通のテキストは案内
                reply_message(reply_token, "画像を送ってください。仕入れ価格は「仕入れ 980」、売値は「売値 2800」のように送れます。")
                continue

            # それ以外（スタンプ等）
            reply_message(reply_token, f"{msg_type} を受け取りました（画像かテキストでお願いします）")

        except Exception as e:
            print("Error:", e)
            reply_message(reply_token, f"エラー：{type(e).__name__}")

    return "OK"

@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running"
