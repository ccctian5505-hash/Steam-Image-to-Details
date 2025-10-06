import os
import requests
import easyocr
import pytz
import unicodedata
from datetime import datetime
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

# ✅ Read environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    raise ValueError("❌ Missing BOT_TOKEN or CHAT_ID environment variables!")

# 🕒 PH timezone
ph_tz = pytz.timezone("Asia/Manila")

# Initialize EasyOCR
reader = easyocr.Reader(["en"], gpu=False)

# Clean up item names
def clean_item_name(name):
    name = name.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    name = unicodedata.normalize("NFKC", name)
    return name.strip()

# Get Steam price (PHP)
def get_price(item_name, retries=3):
    url = "https://steamcommunity.com/market/priceoverview/"
    params = {
        "country": "PH",
        "currency": 18,  # PHP
        "appid": 570,    # Dota 2
        "market_hash_name": item_name,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for _ in range(retries):
        try:
            res = requests.get(url, params=params, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get("success"):
                    return data.get("lowest_price") or data.get("median_price") or "No price listed"
        except Exception:
            pass
    return "Error fetching price"

# 🧠 OCR text extractor
def extract_item_names(image_path):
    results = reader.readtext(image_path, detail=0, paragraph=True)
    items = [clean_item_name(line) for line in results if len(line.strip()) > 2]
    return items

# 🧾 Telegram handler — when user sends an image
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ph_time = datetime.now(ph_tz).strftime("%Y-%m-%d_%H-%M")
    output_file = f"Steam_Item_Check_{ph_time}.txt"

    await update.message.reply_text("🕵️ Processing your image... Please wait.")

    photo = await update.message.photo[-1].get_file()
    image_path = f"temp_{user.id}.jpg"
    await photo.download_to_drive(image_path)

    # OCR extract
    items = extract_item_names(image_path)
    if not items:
        await update.message.reply_text("❌ No text detected in image.")
        return

    results = []
    success_count = 0
    fail_count = 0
    total_value = 0.0

    for item in items:
        price = get_price(item)
        results.append(f"{item}\t{price}")

        if "₱" in price:
            success_count += 1
            try:
                value = float(price.replace("₱", "").replace(",", "").strip())
                total_value += value
            except ValueError:
                pass
        else:
            fail_count += 1

    # Save to file
    with open(output_file, "w", encoding="utf-8") as f:
        for r in results:
            f.write(r + "\n")
        f.write("\n===== SUMMARY =====\n")
        f.write(f"Total Successful: {success_count}\n")
        f.write(f"Total Failed: {fail_count}\n")
        f.write(f"Total PHP Value: ₱{total_value:,.2f}\n")

    # Send summary
    summary_msg = (
        f"✅ **Scan Complete!**\n\n"
        f"🧾 Total Items: {len(items)}\n"
        f"✅ Success: {success_count}\n"
        f"⚠️ Failed: {fail_count}\n"
        f"💰 Total Value: ₱{total_value:,.2f}\n\n"
        f"📎 Sending file result..."
    )

    await update.message.reply_text(summary_msg)
    await context.bot.send_document(chat_id=update.effective_chat.id, document=InputFile(output_file))

    # Cleanup
    os.remove(image_path)
    os.remove(output_file)

# 🚀 Main app
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

print("🤖 Steam Image to Details Bot is running...")
app.run_polling()
