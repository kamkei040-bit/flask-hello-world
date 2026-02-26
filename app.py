import os
import json
import base64
import requests
from flask import Flask, request
from openai import OpenAI
import time
import re

app = Flask(__name__)

# メモリ保存
USER_STATE = {}
STATE_TTL_SEC = 60 * 60 * 6

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# -----------------------
# LINE返信
# -----------------------
def reply_message(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:4900]}]
    }

    r = requests.post(url, headers=headers, json=payload)
    print("Reply:", r.status_code, r.text)


# -----------------------
# LINE Push送信（遅い処理用）
# -----------------------
def push_message(user_id, text):

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text[:4900]}]
    }

    r = requests.post(url, headers=headers, json=payload)
    print("Push:", r.status_code, r.text)


# -----------------------
# LINE画像取得
# -----------------------
def fetch_line_image_bytes(message_id):

    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    r = requests.get(url, headers=headers)

    return r.content


# -----------------------
# AI解析
# -----------------------
def analyze_image(image_bytes):

    b64 = base64.b64encode(image_bytes).decode()

    prompt = """
画像の商品を特定してJSONのみ返してください

{
"name":"商品名",
"keywords":["検索語1","検索語2"],
"shipping_yen_guess":210,
"price_range_yen":[1000,3000]
}
"""

    resp = client.responses.create(

        model="gpt-4.1-mini",

        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image",
                 "image_url": f"data:image/jpeg;base64,{b64}"}
            ]
        }]
    )

    text = resp.output_text.strip()

    text = text[text.find("{"):text.rfind("}")+1]

    return json.loads(text)


# -----------------------
# 利益計算
# -----------------------
def profit_calc(sell, cost, ship):

    fee = int(sell * 0.1)

    return sell - cost - ship - fee


# -----------------------
# Webhook（重要）
# -----------------------
@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json(silent=True)

    print("Received:", data)

    if not data:
        return "OK"

    events = data.get("events", [])

    for event in events:

        reply_token = event.get("replyToken")

        user_id = event.get("source", {}).get("userId")

        msg = event.get("message", {})

        msg_type = msg.get("type")

        try:

            # テキスト
            if msg_type == "text":

                text = msg.get("text")

                reply_message(reply_token, f"受信：{text}")

            # 画像
            if msg_type == "image":

                reply_message(reply_token, "画像受信。解析中...")

                image_id = msg.get("id")

                img = fetch_line_image_bytes(image_id)

                result = analyze_image(img)

                USER_STATE[user_id] = result

                name = result.get("name")

                price = result.get("price_range_yen")

                ship = result.get("shipping_yen_guess")

                push_message(user_id,
                    f"商品:{name}\n"
                    f"価格:{price}\n"
                    f"送料:{ship}"
                )

        except Exception as e:

            print("Error:", e)

    return "OK"


# -----------------------
# 動作確認用
# -----------------------
@app.route("/", methods=["GET"])
def home():
    return "LINE BOT OK"
