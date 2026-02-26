from flask import Flask, request
import json

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "OK ROOT", 200

@app.route("/webhook", methods=["GET", "POST"])
def webhook():

    if request.method == "GET":
        return "OK WEBHOOK GET", 200

    data = request.get_json(silent=True) or {}
    print("Received:", json.dumps(data, ensure_ascii=False))

    return "OK", 200
