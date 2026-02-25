from flask import Flask, request, abort
import os
import json
import hmac
import hashlib
import base64
import requests

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")

def verify_line_signature(body: bytes, signature: str) -> bool:
    if not LINE_CHANNEL_SECRET:
        return False
    hash_ = hmac.new(LINE_CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    computed = base64.b64encode(hash_).decode("utf-8")
    return hmac.compare_digest(computed, signature)

def reply_message(reply_token: str, messages: list[dict]):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {"replyToken": reply_token, "messages": messages}
    r = requests.post(url, headers=headers, data=json.dumps(payload))
    print("LINE reply status:", r.status_code, r.text)
    return r

@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running"

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_data()  # bytes
    signature = request.headers.get("X-Line-Signature", "")

    if not verify_line_signature(body, signature):
        print("Invalid signature")
        abort(400)

    data = request.json
    print("Received from LINE:", json.dumps(data, ensure_ascii=False))

    events = data.get("events", [])
    for event in events:
        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        message = event.get("message", {})
        msg_type = message.get("type")

        # テキスト
        if msg_type == "text":
            text = message.get("text", "")
            reply_message(reply_token, [
                {"type": "text", "text": f"受け取りました：{text}\n\n（次：写真を送ると、メルカリで利益が出そうか判定します）\n写真を送ってみてください。"}
            ])

        # 画像
        elif msg_type == "image":
            # いまは「受け取った」まで確実に返す（ここがまず大事）
            reply_message(reply_token, [
                {"type": "text", "text": "画像を受け取りました。\nいまから商品を判定します。\n\n（次のステップで、画像内容をAI解析→メルカリ相場→利益見込みに進めます）"}
            ])

        else:
            reply_message(reply_token, [
                {"type": "text", "text": f"{msg_type} を受け取りました（対応準備中）"}
            ])

    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
