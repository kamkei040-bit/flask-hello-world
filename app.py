from flask import Flask, request
import os
import requests

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

@app.route("/")
def home():
    return "LINE Bot is running"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("Received:", data)

    events = data.get("events", [])
    if not events:
        return "OK"

    event = events[0]
    reply_token = event["replyToken"]

    user_text = event["message"]["text"]
    reply_text = f"あなたが送った内容：{user_text}"

    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    body = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": reply_text
            }
        ]
    }

    r = requests.post(url, headers=headers, json=body)

    print("Reply result:", r.status_code, r.text)

    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
