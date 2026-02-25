import os
import json
import base64
import requests
from flask import Flask, request
from openai import OpenAI

app = Flask(__name__)

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

def analyze_image_for_mercari(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        "あなたはメルカリ物販の査定アシスタントです。画像から商品を推定し、"
        "商品名(推定)、売れやすさ(高/中/低)、価格目安レンジ、利益を出すコツ、"
        "追加で確認すべき点を日本語で簡潔に出してください。"
        "外部検索はできない前提で推定してください。"
    )

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
            ],
        }],
    )
    return resp.output_text.strip()

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("Received:", json.dumps(data, ensure_ascii=False))

    events = data.get("events", [])
    for event in events:
        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        message = event.get("message", {})
        msg_type = message.get("type")

        try:
            if msg_type == "text":
                user_text = message.get("text", "")
                reply_message(reply_token, f"受け取りました：{user_text}")
                continue

            if msg_type == "image":
                message_id = message.get("id")
                if not message_id:
                    reply_message(reply_token, "画像IDが取れませんでした。もう一度送ってください。")
                    continue

                img_bytes = fetch_line_image_bytes(message_id)
                result = analyze_image_for_mercari(img_bytes)

                # 画像は返信1回で返す（replyTokenは1回だけ）
                reply_message(reply_token, "画像を判定しました。\n\n" + result)
                continue

            reply_message(reply_token, f"{msg_type} を受け取りました（対応準備中）")

        except Exception as e:
            print("Error:", e)
            reply_message(reply_token, f"エラー：{e}")

    return "OK"

@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running"
