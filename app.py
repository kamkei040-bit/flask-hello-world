import os
import json
import base64
import requests
from flask import Flask, request

from openai import OpenAI

app = Flask(__name__)

# ===== 環境変数 =====
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================
# LINE返信関数
# =========================
def reply_message(reply_token: str, text: str):

    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text[:4900]
            }
        ]
    }

    r = requests.post(url, headers=headers, json=payload, timeout=20)

    print("Reply status:", r.status_code)
    print(r.text)


# =========================
# LINEから画像取得
# =========================
def fetch_line_image_bytes(message_id: str) -> bytes:

    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    r = requests.get(url, headers=headers, timeout=30)

    r.raise_for_status()

    return r.content


# =========================
# OpenAIで画像判定
# =========================
def analyze_image_for_mercari(image_bytes: bytes) -> str:

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """
あなたはメルカリ物販の査定アシスタントです。

以下を日本語で出してください：

・商品名（推定）
・売れやすさ（高 / 中 / 低）
・メルカリ販売価格目安
・利益を出すコツ
・確認すべきポイント
"""

    resp = client.responses.create(

        model="gpt-4.1-mini",

        input=[

            {
                "role": "user",

                "content": [

                    {"type": "input_text", "text": prompt},

                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64}"
                    },

                ],
            }

        ],

    )

    return resp.output_text


# =========================
# Webhook
# =========================
@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.json

    print("Received:", json.dumps(data))

    events = data.get("events", [])

    for event in events:

        reply_token = event.get("replyToken")

        if not reply_token:
            continue

        message = event.get("message", {})

        msg_type = message.get("type")

        try:

            # ===== テキスト =====
            if msg_type == "text":

                user_text = message.get("text", "")

                reply_message(
                    reply_token,
                    f"受け取りました：{user_text}"
                )


            # ===== 画像 =====
            elif msg_type == "image":

                message_id = message.get("id")

                if not message_id:

                    reply_message(
                        reply_token,
                        "画像取得に失敗しました"
                    )

                    continue


                reply_message(
                    reply_token,
                    "画像を受け取りました。判定中です..."
                )


                image_bytes = fetch_line_image_bytes(message_id)

                result = analyze_image_for_mercari(image_bytes)


                reply_message(
                    reply_token,
                    result
                )


            # ===== その他 =====
            else:

                reply_message(
                    reply_token,
                    f"{msg_type} を受け取りました"
                )


        except Exception as e:

            print("Error:", e)

            reply_message(
                reply_token,
                f"エラー：{str(e)}"
            )

    return "OK"


# =========================
# 起動
# =========================
if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(host="0.0.0.0", port=port)
