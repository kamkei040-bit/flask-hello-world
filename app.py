# app.py
import os
import json
import base64
import requests
from flask import Flask, request
from openai import OpenAI
import time
import re
import urllib.parse

app = Flask(__name__)

USER_STATE = {}
STATE_TTL_SEC = 60 * 60 * 6

LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

# ======================
# ゆうゆうメルカリ便 送料テーブル
# ======================
SHIPPING_TABLE_YEN = {
    "S": 230,   # ゆうパケット系
    "M": 455,   # ゆうパケットプラス
    "L": 770,   # ゆうパック60-80
    "XL": 1070, # ゆうパック100-120
}

def estimate_shipping_yen(size_code):
    if not size_code:
        return SHIPPING_TABLE_YEN["L"]
    return SHIPPING_TABLE_YEN.get(size_code.upper(), SHIPPING_TABLE_YEN["L"])

def mercari_search_url(keyword: str) -> str:
    q = urllib.parse.quote(keyword or "")
    return f"https://jp.mercari.com/search?keyword={q}"

def line_headers():
    return {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

def reply_message(reply_token: str, text: str):
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    r = requests.post(LINE_REPLY_URL, headers=line_headers(), json=payload, timeout=20)
    print("Reply status:", r.status_code, r.text)
    return r

def push_message(to: str, text: str):
    payload = {
        "to": to,
        "messages": [{"type": "text", "text": text[:4900]}],
    }
    r = requests.post(LINE_PUSH_URL, headers=line_headers(), json=payload, timeout=20)
    print("Push status:", r.status_code, r.text)
    return r

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    print("Received:", json.dumps(data, ensure_ascii=False))

    events = data.get("events", [])
    for event in events:
        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        user_id = (event.get("source") or {}).get("userId")
        message = event.get("message", {})
        msg_type = message.get("type")

        try:
            if msg_type == "image":
                reply_message(reply_token, "画像を受け取りました。解析中です…")

                # 仮の商品情報（例）
                name = "商品名サンプル"
                keyword = name
                link = mercari_search_url(keyword)

                size_code = USER_STATE.get(user_id, {}).get("ship_size")
                ship = estimate_shipping_yen(size_code)

                msg = (
                    f"【商品推定】{name}\n"
                    f"【送料目安】{ship}円\n\n"
                    f"▼メルカリ検索\n{link}\n\n"
                    f"送料を正確にするなら S/M/L/XL を送ってください。\n\n"
                    f"【ゆうゆうメルカリ便 送料目安一覧】\n"
                    f"S（ゆうパケット系）: 230円\n"
                    f"M（ゆうパケットプラス）: 455円\n"
                    f"L（ゆうパック60-80）: 770円\n"
                    f"XL（ゆうパック100-120）: 1070円\n"
                )

                if user_id:
                    push_message(user_id, msg)
                continue

            if msg_type == "text":
                text = (message.get("text") or "").strip().upper()
                if text in ["S", "M", "L", "XL"] and user_id:
                    USER_STATE.setdefault(user_id, {})["ship_size"] = text
                    reply_message(reply_token, f"OK！サイズ{text}で送料計算します。")
                    continue

                reply_message(reply_token, "画像を送ってください。")
                continue

        except Exception as e:
            print("Error:", e)
            reply_message(reply_token, f"エラー：{type(e).__name__}")

    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running", 200
