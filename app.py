from flask import Flask, request
import os
import requests
import openai

app = Flask(__name__)

# 環境変数から取得
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

openai.api_key = OPENAI_API_KEY


@app.route("/")
def home():
    return "LINE Bot is running"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("Received:", data)

    try:
        events = data["events"]

        for event in events:

            if event["type"] == "message":

                reply_token = event["replyToken"]

                if event["message"]["type"] == "text":

                    user_message = event["message"]["text"]

                    # OpenAIに問い合わせ
                    response = openai.ChatCompletion.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": "あなたは物販の利益判定アシスタントです"},
                            {"role": "user", "content": user_message}
                        ]
                    )

                    ai_reply = response["choices"][0]["message"]["content"]

                    # LINEに返信
                    headers = {
                        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                        "Content-Type": "application/json"
                    }

                    body = {
                        "replyToken": reply_token,
                        "messages": [
                            {
                                "type": "text",
                                "text": ai_reply
                            }
                        ]
                    }

                    requests.post(
                        "https://api.line.me/v2/bot/message/reply",
                        headers=headers,
                        json=body
                    )

    except Exception as e:
        print("Error:", e)

    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
