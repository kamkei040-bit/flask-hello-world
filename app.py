@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_data()
    signature = request.headers.get("X-Line-Signature", "")

    # （署名検証を入れている場合はここでverify）
    # verify_line_signature(body, signature) など

    data = request.json
    print("Received from LINE:", data)

    events = data.get("events", [])
    for event in events:
        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        message = event.get("message", {})
        msg_type = message.get("type")

        if msg_type == "text":
            user_text = message.get("text", "")
            # ここで返信（あなたのreply関数を呼ぶ）
            reply_message(reply_token, f"受け取りました：{user_text}")

        elif msg_type == "image":
            reply_message(reply_token, "画像を受け取りました。判定します！")

        else:
            reply_message(reply_token, f"{msg_type} を受け取りました（対応準備中）")

    return "OK"
