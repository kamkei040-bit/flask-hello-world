# app.py
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

# ======================
# Helpers
# ======================

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
    requests.post(LINE_REPLY_URL, headers=line_headers(), json=payload, timeout=20)

def push_message(to: str, text: str):
    payload = {
        "to": to,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    requests.post(LINE_PUSH_URL, headers=line_headers(), json=payload, timeout=20)

def fetch_line_image_bytes(message_id: str) -> bytes:
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    r = requests.get(url, headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}, timeout=30)
    r.raise_for_status()
    return r.content

def mercari_search_url(keyword: str) -> str:
    q = urllib.parse.quote(keyword or "")
    return f"https://jp.mercari.com/search?keyword={q}"

# ======================
# OpenAI解析
# ======================

def analyze_image_for_mercari(image_bytes: bytes) -> dict:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{b64}"

    prompt = """画像の商品を特定し、JSONのみで返してください。
{
  "name": "",
  "condition_guess": "",
  "keywords": [],
  "price_range_yen": [0,0],
  "tips": {
    "desc_points": []
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
    if "{" in text and "}" in text:
        text = text[text.find("{"):text.rfind("}") + 1]
    return json.loads(text)

# ======================
# テンプレ生成
# ======================

def build_template(st: dict) -> str:
    r = st.get("last_result") or {}
    title = r.get("name") or "商品名"

    condition = r.get("condition_guess") or "未使用品"
    tips = (r.get("tips") or {}).get("desc_points") or []
    point = tips[0] if tips else "商品の魅力をぜひご確認ください。"

    template = (
        f"{title}\n"
        " ――――――――――\n"
        "  【商品内容】\n"
        f"   ① {title}\n"
        "\n"
        " ―――――――――― \n"
        " \n"
        f"◆状態：{condition}です。\n"
        "※写真をご確認ください。\n"
        "\n"
        "◆おすすめポイント\n"
        f"✔ {point}\n"
        "\n"
        "\n"
        "推し活用・コレクション用・保存用におすすめです。\n"
        "早い者勝ちとなります。\n"
        "即購入OK。\n"
        "少しでも気になった方、この機会をお見逃しなく！\n"
        "値下げ交渉は複数購入まとめ取引の場合のみで検討します。"
    )

    return template

# ======================
# Webhook
# ======================

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    events = data.get("events", [])

    for event in events:
        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        user_id = (event.get("source") or {}).get("userId")
        message = event.get("message", {})
        msg_type = message.get("type")

        try:
            if msg_type == "image":
                reply_message(reply_token, "画像を受け取りました。解析中です…")

                img_bytes = fetch_line_image_bytes(message.get("id"))
                result = analyze_image_for_mercari(img_bytes)

                if user_id:
                    USER_STATE[user_id] = {
                        "ts": time.time(),
                        "last_result": result,
                    }

                name = result.get("name") or "不明"
                link = mercari_search_url(name)

                msg = (
                    f"【商品推定】{name}\n\n"
                    f"▼メルカリ検索\n{link}\n\n"
                    "テンプレを作成する場合は「テンプレ」と送ってください。"
                )

                if user_id:
                    push_message(user_id, msg)
                continue

            if msg_type == "text":
                text = (message.get("text") or "").strip()

                if "テンプレ" in text:
                    st = USER_STATE.get(user_id)
                    if not st:
                        reply_message(reply_token, "直前の画像がありません。")
                        continue

                    template = build_template(st)
                    reply_message(reply_token, template)
                    continue

                reply_message(reply_token, "画像を送ってください。")
                continue

        except Exception as e:
            reply_message(reply_token, f"エラー：{type(e).__name__}")

    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running", 200
