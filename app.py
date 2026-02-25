from flask import Flask, request, abort
import os
import json
import hashlib
import hmac
import base64
import requests
import openai

app = Flask(__name__)

# Renderの環境変数から読み込み
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

openai.api_key = OPENAI_API_KEY


@app.route("/")
def home():
    return "LINE Bot is running"


def validate_signature(body: str, signature: str) -> bool:
    if not LINE_CHANNEL_SECRET:
        return False
    hash_ = hmac.new(LINE_CHANNEL_SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    expected = base64.b64encode(hash_).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def reply_message(reply_token: str, text: str):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }
    r = requests.post(url, headers=headers, data=json.dumps(payload))
    return r.status_code, r.text


def ask_openai(user_text: str) -> str:
    # まずは動作確認用に短め
    res = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "あなたは親切なアシスタントです。短く分かりやすく答えてください。"},
            {"role": "user", "content": user_text},
        ],
        temperature=0.4,
        max_tokens=200,
    )
    return res["choices"][0]["message"]["content"].strip()


@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature", "")

    # 署名検証（これが通らないとLINEが弾かれる）
    if not validate_signature(body, signature):
        abort(400)

    data = json.loads(body)

    # LINEのイベントを処理
    events = data.get("events", [])
    for event in events:
        if event.get("type") == "message":
            message = event.get("message", {})
            if message.get("type") == "text":
                user_text = message.get("text", "")
                reply_token = event.get("replyToken")

                try:
                    ai_text = ask_openai(user_text)
                except Exception as e:
                    ai_text = f"OpenAIエラー: {e}"

                reply_message(reply_token, ai_text)

    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
