from flask import Flask, request
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "LINE Bot is running"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("Received from LINE:", data)
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
