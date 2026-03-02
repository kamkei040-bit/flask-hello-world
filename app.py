# app.py （統合・本番用 / 丸ごとコピペ）
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

# ======================
# In-memory state
# ======================
USER_STATE = {}
STATE_TTL_SEC = 60 * 60 * 6  # 6 hours

# ======================
# Env
# ======================
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

# ======================
# Shipping (ゆうゆうメルカリ便想定 / 本番用)
# ※改定があれば、ここだけ金額を直せばOK
# ======================
SHIPPING_TABLE_YEN = {
    "S": 230,   # ゆうパケット系（薄物）
    "M": 455,   # ゆうパケットプラス
    "L": 770,   # ゆうパック60-80（目安）
    "XL": 1070, # ゆうパック100-120（目安）
}


def shipping_price_list_text() -> str:
    return (
        "【ゆうゆうメルカリ便 送料目安一覧】\n"
        f"S（ゆうパケット系）: {SHIPPING_TABLE_YEN['S']}円\n"
        f"M（ゆうパケットプラス）: {SHIPPING_TABLE_YEN['M']}円\n"
        f"L（ゆうパック60-80）: {SHIPPING_TABLE_YEN['L']}円\n"
        f"XL（ゆうパック100-120）: {SHIPPING_TABLE_YEN['XL']}円\n"
    )


def normalize_weight_to_kg(text: str):
    """
    Accepts:
      '10kg', '0.8kg', '850g', '850' (treated as grams if >=50), '1.2'
    Returns:
      float kg or None
    """
    s = (text or "").strip().lower().replace(" ", "").replace(",", "")
    m = re.search(r"(\d+(\.\d+)?)\s*(kg|g)?", s)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(3) or ""
    if unit == "g" or (unit == "" and val >= 50):
        return val / 1000.0
    return val


def infer_size_from_name(name: str):
    """
    Fallback guess when user didn't provide size.
    """
    n = (name or "")
    if any(k in n for k in ["DVD", "ブルーレイ", "Blu-ray", "CD", "本", "カード"]):
        return "S"
    if any(k in n for k in ["コントローラー", "controller", "DualSense", "DualShock"]):
        return "M"
    if any(k in n for k in ["ブーツ", "靴", "シューズ"]):
        return "L"
    if any(k in n for k in ["本体", "ゲーム機", "PS5", "Switch", "XBOX"]):
        return "XL"
    return None


def estimate_shipping_yen(size_code: str | None, weight_kg: float | None, name: str | None) -> int:
    """
    Size/weight first. If not provided, fallback to name guess, then safe side (L).
    Adds weight safety bump for heavy items.
    """
    size_code = (size_code or "").upper().strip() or None
    if not size_code:
        size_code = infer_size_from_name(name or "") or "L"

    base = SHIPPING_TABLE_YEN.get(size_code, SHIPPING_TABLE_YEN["L"])

    # Weight safety bump (adjust freely)
    if weight_kg is not None:
        if weight_kg >= 10:
            base = max(base, SHIPPING_TABLE_YEN["XL"] + 500)
        elif weight_kg >= 5:
            base = max(base, SHIPPING_TABLE_YEN["XL"])
        elif weight_kg >= 2:
            base = max(base, SHIPPING_TABLE_YEN["L"])

    return int(base)


# ======================
# LINE helpers
# ======================
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


def fetch_line_image_bytes(message_id: str) -> bytes:
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    r = requests.get(url, headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}, timeout=30)
    r.raise_for_status()
    return r.content


# ======================
# OpenAI vision analysis
# ======================
def analyze_image_for_mercari(image_bytes: bytes) -> dict:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{b64}"

    prompt = """あなたは日本の物販（メルカリ）仕入れ判定アシスタントです。
画像の商品をできるだけ正確に特定し、仕入れ判断に必要な情報を「JSONだけ」で返してください。
（前後に文章をつけない。JSONのみ。）

必須キー:
{
  "name": "商品名推定",
  "brand": "ブランド/メーカー(不明ならnull)",
  "model": "型番(不明ならnull)",
  "jan": "JAN/EAN(不明ならnull)",
  "category": "カテゴリ",
  "condition_guess": "未使用/中古など",
  "keywords": ["メルカリ検索ワード1","2","3"],
  "shipping_yen_guess": 210,
  "price_range_yen": [1800, 3500],
  "tips": {
    "title_example": "出品タイトル例（短く）",
    "desc_points": ["説明に入れる要点1","2"]
  }
}
"""

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
    )

    text = (resp.output_text or "").strip()

    # Safety: extract first {...} json
    if "{" in text and "}" in text:
        text = text[text.find("{") : text.rfind("}") + 1]

    return json.loads(text)


# ======================
# Utilities
# ======================
def cleanup_state():
    now = time.time()
    expired = []
    for uid, st in list(USER_STATE.items()):
        if now - st.get("ts", 0) > STATE_TTL_SEC:
            expired.append(uid)
    for uid in expired:
        USER_STATE.pop(uid, None)


def parse_yen_from_text(s: str):
    s = (s or "").replace(",", "")
    m = re.search(r"([0-9]{1,7})", s)
    return int(m.group(1)) if m else None


def compute_profit(sell_yen: int, cost_yen: int, ship_yen: int, fee_rate: float = 0.10) -> int:
    fee = int(round(sell_yen * fee_rate))
    return sell_yen - fee - ship_yen - cost_yen


def mercari_search_url(keyword: str) -> str:
    q = urllib.parse.quote(keyword or "")
    return f"https://jp.mercari.com/search?keyword={q}"


# ======================
# Template generator (指定フォーマット)
# ======================
def build_listing_template(st: dict) -> str:
    r = st.get("last_result") or {}
    title = r.get("name") or st.get("name") or "商品名"
    condition = r.get("condition_guess") or "未使用"
    tips = (r.get("tips") or {}).get("desc_points") or []
    point = tips[0].strip() if tips and isinstance(tips[0], str) and tips[0].strip() else "写真をご確認ください。"

    # ユーザー入力のサイズ/重さがあればテンプレにも反映（任意）
    size_code = st.get("ship_size")
    weight_kg = st.get("ship_weight_kg")

    # 状態の文を固定形式に寄せる（「◯◯です。」に収める）
    cond_line = f"{condition}です。"

    extra = []
    if size_code:
        extra.append(f"（サイズ目安：{size_code}）")
    if isinstance(weight_kg, float):
        extra.append(f"（重さ目安：{weight_kg:.2f}kg）")

    extra_text = " " + " ".join(extra) if extra else ""

    template = (
        f"{title}\n"
        " ――――――――――\n"
        "  【商品内容】\n"
        f"   ① {title}\n"
        "\n"
        " ―――――――――― \n"
        " \n"
        f"◆状態：{cond_line}{extra_text}\n"
        "※写真をご確認ください。\n"
        "\n"
        "◆おすすめポイント\n"
        f"✔ {point}\n"
        "\n"
        "\n"
        "推し活用・コレクション用・保存用におすすめです。\n"
        "早い者勝ちとなります。\n"
        "即購入OK。\n"
        "少しでも気になった方、この機会をお見逃しなく！\n"
        "値下げ交渉は複数購入まとめ取引の場合のみで検討します。"
    )
    return template


# ======================
# Webhook
# ======================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}

    print("Received:", json.dumps(data, ensure_ascii=False))

    events = data.get("events", [])
    for event in events:
        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        src = event.get("source") or {}
        user_id = src.get("userId")  # Push destination (1:1 chat)
        message = event.get("message", {})
        msg_type = message.get("type")

        cleanup_state()

        try:
            # ======================
            # IMAGE
            # ======================
            if msg_type == "image":
                message_id = message.get("id")
                if not message_id:
                    reply_message(reply_token, "画像IDが取れませんでした。もう一度送ってください。")
                    continue

                # Quick reply first
                reply_message(reply_token, "画像を受け取りました。解析中です…（数秒〜20秒）")

                img_bytes = fetch_line_image_bytes(message_id)
                result = analyze_image_for_mercari(img_bytes)

                pr = result.get("price_range_yen") or None
                low, high = (pr[0], pr[1]) if (isinstance(pr, list) and len(pr) == 2) else (None, None)

                keywords = result.get("keywords") or []
                name = result.get("name") or "不明"
                category = result.get("category") or None

                # Use user-provided size/weight for shipping
                st0 = USER_STATE.get(user_id, {}) if user_id else {}
                size_code = st0.get("ship_size") if user_id else None
                weight_kg = st0.get("ship_weight_kg") if user_id else None
                ship = estimate_shipping_yen(size_code, weight_kg, name)

                if user_id:
                    USER_STATE[user_id] = {
                        "ts": time.time(),
                        "shipping_yen": ship,
                        "price_low": low if isinstance(low, int) else None,
                        "price_high": high if isinstance(high, int) else None,
                        "name": name,
                        "keywords": keywords,
                        "category": category,
                        "ship_size": size_code,
                        "ship_weight_kg": weight_kg,
                        "sell_yen": st0.get("sell_yen"),  # keep
                        "last_result": result,            # ★テンプレ用
                    }

                kw_text = " / ".join(keywords[:3]) if keywords else "（不明）"
                ship_text = f"{ship}円"
                pr_text = f"{low}〜{high}円" if (isinstance(low, int) and isinstance(high, int)) else "不明"
                link = mercari_search_url(keywords[0] if keywords else name)

                msg = (
                    f"【商品推定】{name}\n"
                    f"【検索】{kw_text}\n"
                    f"【送料目安（ゆうゆう想定）】{ship_text}\n"
                    f"【売価目安(推定)】{pr_text}\n\n"
                    f"▼メルカリ検索（相場確認）\n{link}\n"
                    f"※iPhone：リンク→右下︙→Safariで開く\n\n"
                    f"送料を正確にする場合（任意）：\n"
                    f"・サイズ S/M/L/XL\n"
                    f"・重さ 850g または 1.2kg\n\n"
                    f"次に入力：\n"
                    f"・売値 2800（希望売値）\n"
                    f"・仕入れ 980\n"
                    f"・テンプレ（出品テンプレ生成）\n\n"
                    f"{shipping_price_list_text()}"
                )

                if user_id:
                    push_message(user_id, msg)
                else:
                    print("No userId. Push cannot be sent.")

                continue

            # ======================
            # TEXT
            # ======================
            if msg_type == "text":
                user_text = (message.get("text") or "").strip()
                t_upper = user_text.upper()
                st = USER_STATE.get(user_id) if user_id else None

                # 0) テンプレ生成
                if "テンプレ" in user_text or "出品" in user_text and "テンプレ" in user_text:
                    if not st or not st.get("last_result"):
                        reply_message(reply_token, "直前の画像解析結果がありません。先に商品画像を送ってください。")
                        continue
                    template = build_listing_template(st)
                    reply_message(reply_token, template)
                    continue

                # 1) Shipping inputs first (size / weight)
                if user_id:
                    if t_upper in ["S", "M", "L", "XL"]:
                        USER_STATE.setdefault(user_id, {})["ts"] = time.time()
                        USER_STATE[user_id]["ship_size"] = t_upper
                        reply_message(
                            reply_token,
                            f"OK！サイズ{t_upper}で送料計算します。\n"
                            f"{shipping_price_list_text()}\n"
                            "次に重さ（例: 850g / 1.2kg）か「テンプレ」または価格を送ってください。",
                        )
                        continue

                    wkg = normalize_weight_to_kg(user_text)
                    if wkg is not None and (("KG" in t_upper) or ("G" in t_upper) or user_text.isdigit()):
                        USER_STATE.setdefault(user_id, {})["ts"] = time.time()
                        USER_STATE[user_id]["ship_weight_kg"] = wkg
                        reply_message(
                            reply_token,
                            f"OK！重さ{wkg:.2f}kgで送料計算します。\n"
                            "次にサイズ（S/M/L/XL）か「テンプレ」または価格を送ってください。",
                        )
                        continue

                # 2) 売値（希望）保存
                if "売値" in user_text:
                    sy = parse_yen_from_text(user_text)
                    if user_id and sy is not None:
                        USER_STATE.setdefault(user_id, {})["ts"] = time.time()
                        USER_STATE[user_id]["sell_yen"] = sy
                        reply_message(reply_token, f"OK！売値{sy}円で覚えました。必要なら「テンプレ」と送ってください。")
                        continue

                # 3) 利益計算
                cost = parse_yen_from_text(user_text) if "仕入" in user_text else None

                sold = None
                if "売れた" in user_text:
                    sold = parse_yen_from_text(user_text)

                if cost is not None:
                    if not st:
                        reply_message(reply_token, "直前の画像が見つかりません。先に商品画像を送ってください。")
                        continue

                    size_code = st.get("ship_size")
                    weight_kg = st.get("ship_weight_kg")
                    nm = st.get("name")
                    ship = estimate_shipping_yen(size_code, weight_kg, nm)

                    # 利益計算に使う売値：売れた > 売値(希望) > 推定中央値
                    sell_for_profit = sold or st.get("sell_yen")
                    if sell_for_profit is None:
                        low = st.get("price_low")
                        high = st.get("price_high")
                        if isinstance(low, int) and isinstance(high, int):
                            sell_for_profit = int(round((low + high) / 2))

                    if sell_for_profit is None:
                        reply_message(reply_token, "仕入れOK。次に「売れた 2800」または「売値 2800」を送ってください。")
                        continue

                    profit = compute_profit(sell_for_profit, cost, ship, fee_rate=0.10)
                    reply_message(
                        reply_token,
                        f"利益目安：{profit}円（売値{sell_for_profit}円・送料{ship}円・手数料10%）\n"
                        "必要なら「テンプレ」と送ってください。",
                    )
                    continue

                # default guidance
                reply_message(
                    reply_token,
                    "画像を送ってください。\n"
                    "送料は「S/M/L/XL」や「850g / 1.2kg」で精度UP。\n"
                    "価格は「売値 2800」「仕入れ 980」「売れた 2800」。\n"
                    "出品文は「テンプレ」と送れば作成します。\n\n"
                    f"{shipping_price_list_text()}",
                )
                continue

            # Other message types
            reply_message(reply_token, f"{msg_type} を受け取りました（画像かテキストでお願いします）")

        except Exception as e:
            print("Error:", e)
            reply_message(reply_token, f"エラー：{type(e).__name__}")

    return "OK", 200


@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running", 200
