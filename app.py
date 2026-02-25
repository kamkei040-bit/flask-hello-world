from flask import Flask, request, abort
import os
import json
import hmac
import hashlib
import base64
import requests
from openai import OpenAI

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


def verify_line_signature(body: str, signature: str) -> bool:
    if not LINE_CHANNEL_SECRET or not signature:
        return False
    hash_bytes = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256
    ).digest()
    expected = base64.b64encode(hash_bytes).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def line_reply(reply_token: str, messages: list[dict]):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"replyToken": reply_token, "messages": messages}
    r = requests.post(url, headers=headers, data=json.dumps(payload))
    return r.status_code, r.text


def fetch_line_image_bytes(message_id: str) -> bytes:
    url = f"https://api.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.content


@app.route("/")
def home():
    return "LINE Bot is running"


@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature", "")

    # 署名検証（ちゃんとしたBot運用に必須）
    if not verify_line_signature(body, signature):
        abort(400)

    data = json.loads(body)

    events = data.get("events", [])
    for event in events:
        if event.get("type") != "message":
            continue

        reply_token = event.get("replyToken")
        message = event.get("message", {})
        msg_type = message.get("type")

        # テキスト
        if msg_type == "text":
            user_text = message.get("text", "")
            # いまは確認用：オウム返し
            line_reply(reply_token, [{"type": "text", "text": f"受け取りました：{user_text}"}])
            continue

        # 画像
        if msg_type == "image":
            message_id = message.get("id")

            # まずは「受け取った」返信（ここが大事）
            line_reply(reply_token, [{"type": "text", "text": "画像を受け取りました！分析しますね。"}])

            # 画像の取得（次ステップでAI解析に使う）
            try:
                img_bytes = fetch_line_image_bytes(message_id)
                print(f"Image bytes received: {len(img_bytes)} bytes")
            except Exception as e:
                print("Failed to fetch image:", e)
            continue

        # その他（スタンプ等）
        line_reply(reply_token, [{"type": "text", "text": f"{msg_type} を受け取りました（画像/文字が一番得意です）"}])

    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
