#!/usr/bin/env python3
"""
Dota2 Image -> Steam PHP Price Scraper via Telegram

Usage:
- Set environment variables BOT_TOKEN and CHAT_ID (or use Railway/Heroku secret envs).
- Run: python main.py
- Send an image to your bot. Bot will OCR, look up Steam market prices (PH, currency=18), save a timestamped .txt,
  and send a summary + the file back to your chat.

Tune cooldowns & retries in the SETTINGS block.
"""

import os
import re
import time
import requests
import unicodedata
from datetime import datetime
import pytz

# OCR
try:
    import easyocr
except Exception as e:
    easyocr = None

# Telegram
from telegram import Update, Bot
from telegram.ext import Updater, MessageHandler, Filters, CallbackContext

# ---------------------------
# SETTINGS (tweak these)
# ---------------------------
COUNTRY = "PH"
CURRENCY = 18        # 18 => PHP
APPID = 570          # Dota 2
OCR_LANGS = ["en"]   # languages for easyocr

# Request timing
SUCCESS_DELAY = 2.5      # seconds between successful steam queries
ERROR_DELAY = 6.0        # delay when error occurs / retry
MAX_RETRIES = 3          # retries for steam API call
COOLDOWN_EVERY = 20      # every N items, do a longer cooldown
COOLDOWN_TIME = 12       # seconds for longer cooldown

# Telegram bot uses environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    raise ValueError("❌ Missing BOT_TOKEN or CHAT_ID environment variables!")

# Where to store temporary images and final results (writeable dir)
OUT_DIR = os.getenv("OUT_DIR", ".")  # change via env if you want different folder

# ---------------------------
# Helpers
# ---------------------------

def now_ph_string(fmt="%Y-%m-%d_%H-%M-%S"):
    ph_time = datetime.now(pytz.timezone("Asia/Manila"))
    return ph_time.strftime(fmt)

def clean_item_name(name: str) -> str:
    """Normalize quotes and unicode quirks so Steam matches better."""
    if not name:
        return name
    name = name.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    name = unicodedata.normalize("NFKC", name)
    return name.strip()

def parse_price_to_float(price_text: str):
    """
    Attempt to extract numeric value from returned Steam string.
    Handles cases like:
      '₱34.38', '$45.32', '56,49₴', '2,450.00'
    Returns float or None if cannot parse.
    """
    if not price_text or not isinstance(price_text, str):
        return None

    s = price_text.strip()
    # remove common currency symbols/letters
    s = s.replace("₱", "").replace("PHP", "").replace("$", "").replace("US$", "").replace("Mex$", "")
    s = s.replace("USD", "").replace("₴", "")  # remove stray symbols if present
    s = s.strip()

    # if contains both ',' and '.' -> treat ',' as thousands sep, remove it
    if s.count(",") > 0 and s.count(".") > 0:
        s = s.replace(",", "")
    # if contains only ',' and no '.' -> treat ',' as decimal separator -> swap with '.'
    elif s.count(",") > 0 and s.count(".") == 0:
        s = s.replace(",", ".")
    # finally, keep digits and dot
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except:
        return None

def steam_price_for_item(item_name: str, retries=MAX_RETRIES, timeout=10):
    """
    Query Steam priceoverview for an item name in PHP.
    Returns (price_display_string_or_None, parsed_float_or_None)
    """
    base = "https://steamcommunity.com/market/priceoverview/"
    params = {
        "country": COUNTRY,
        "currency": CURRENCY,
        "appid": APPID,
        "market_hash_name": item_name
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PriceScraper/1.0; +https://example.invalid)"
    }

    for attempt in range(1, retries + 1):
        try:
            r = requests.get(base, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                if data.get("success"):
                    price_str = data.get("lowest_price") or data.get("median_price")
                    if price_str:
                        parsed = parse_price_to_float(price_str)
                        return price_str, parsed
                    # success but no price listed
                    return None, None
                # success field false -> not found
            # else non-200
        except Exception as e:
            # silent, will retry
            pass

        # backoff on error
        time.sleep(ERROR_DELAY)

    return None, None

# ---------------------------
# OCR Helpers
# ---------------------------
def init_ocr_reader():
    if easyocr:
        try:
            reader = easyocr.Reader(OCR_LANGS, gpu=False)  # set gpu=True if you have CUDA and want speed
            return reader
        except Exception as e:
            print("⚠️ easyocr init failed:", e)
            return None
    else:
        return None

def ocr_extract_names_from_image(image_path: str, reader):
    """
    Use easyocr to detect text blocks. Returns list of strings (in detection order).
    We do minimal cleanup and return the list as-is to preserve duplicates.
    """
    if not reader:
        return []

    results = reader.readtext(image_path, detail=0)  # returns text lines
    # results is list of detected strings; we'll clean and filter some noise
    cleaned = []
    for t in results:
        t = t.strip()
        if not t:
            continue
        # very short noisy tokens can be skipped (like single chars)
        if len(t) < 2:
            continue
        # eliminate typical UI counts like 'x5' or purely numeric tokens
        if re.fullmatch(r"[\d,\.]+", t):
            continue
        cleaned.append(t)
    return cleaned

# ---------------------------
# Telegram Handlers
# ---------------------------
bot = Bot(BOT_TOKEN)

def handle_image(update: Update, context: CallbackContext):
    """
    This runs when user sends an image to the bot.
    Steps:
      - save image
      - OCR -> get list of names (duplicates kept)
      - for each name: query steam price (PHP)
      - produce .txt with columns: Item Name <TAB> Price (PHP) <TAB> ParsedValue
      - send summary + attach file back to user
    """
    user = update.effective_user
    chat_id = update.effective_chat.id
    msg = update.message

    # Acknowledge
    context.bot.send_message(chat_id=chat_id, text="🔍 Received image. Processing... This may take a while.")

    # Get highest-res photo
    photo = None
    if msg.photo:
        photo = msg.photo[-1]
    elif msg.document and msg.document.mime_type.startswith("image/"):
        # user sent as file
        photo = msg.document
    else:
        context.bot.send_message(chat_id=chat_id, text="❌ No image found in message.")
        return

    # Save file
    timestamp = now_ph_string("%Y%m%d_%H%M%S")
    image_filename = os.path.join(OUT_DIR, f"telegram_image_{timestamp}.jpg")
    try:
        file = context.bot.getFile(photo.file_id)
        file.download(custom_path=image_filename)
    except Exception as e:
        context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to download image: {e}")
        return

    # Init OCR
    reader = init_ocr_reader()
    if not reader:
        context.bot.send_message(chat_id=chat_id, text="❌ OCR engine not available (easyocr import failed).")
        return

    # OCR extraction
    raw_names = ocr_extract_names_from_image(image_filename, reader)
    if not raw_names:
        context.bot.send_message(chat_id=chat_id, text="⚠️ OCR found no text items.")
        return

    # Clean names and keep duplicates in same order
    cleaned_names = [clean_item_name(n) for n in raw_names if n.strip()]

    # Prepare result file
    outname = f"Dota2_Price_Report_{now_ph_string('%Y-%m-%d_%H-%M')}.txt"
    outpath = os.path.join(OUT_DIR, outname)

    success_count = 0
    fail_count = 0
    total_value = 0.0
    rows = []

    # For each name (duplicates possible), query steam
    for idx, name in enumerate(cleaned_names, start=1):
        # Steam query
        price_str, parsed = steam_price_for_item(name, retries=MAX_RETRIES)
        if price_str:
            rows.append((name, price_str))
            if parsed is not None:
                total_value += parsed
            success_count += 1
            context.bot.send_chat_action(chat_id=chat_id, action="typing")
            time.sleep(SUCCESS_DELAY)
        else:
            # price_str None indicates either "No price listed" or couldn't get response
            # We'll mark it as failed; try one more attempt with minor cleanup (remove extra tokens)
            alt_name = re.sub(r"^[xX]\s*\d+\s*", "", name).strip()
            price_str2, parsed2 = steam_price_for_item(alt_name, retries=1)
            if price_str2:
                rows.append((name, price_str2))
                if parsed2 is not None:
                    total_value += parsed2
                success_count += 1
                time.sleep(SUCCESS_DELAY)
            else:
                rows.append((name, "❌ Not found / Error"))
                fail_count += 1
                time.sleep(ERROR_DELAY)

        # Occasional cooldown
        if idx % COOLDOWN_EVERY == 0:
            time.sleep(COOLDOWN_TIME)

    # Write result file
    try:
        with open(outpath, "w", encoding="utf-8") as rf:
            rf.write("Item Name\tPrice (PHP)\n")
            for nm, pr in rows:
                rf.write(f"{nm}\t{pr}\n")
            rf.write("\n")
            rf.write(f"Generated: {now_ph_string('%Y-%m-%d %H:%M')}\n")
            rf.write(f"Total Items OCR-detected: {len(cleaned_names)}\n")
            rf.write(f"Success: {success_count}\n")
            rf.write(f"Failed: {fail_count}\n")
            rf.write(f"Total Value (parsed sum): ₱{total_value:,.2f}\n")
    except Exception as e:
        context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to write result file: {e}")
        return

    # Compose summary message
    summary_lines = [f"✅ DOTA 2 ITEM SCAN REPORT ({now_ph_string('%Y-%m-%d %H:%M')})", ""]
    for i, (nm, pr) in enumerate(rows, start=1):
        summary_lines.append(f"{i}. {nm} — {pr}")
    summary_lines.append("")
    summary_lines.append("📊 Summary:")
    summary_lines.append(f"✅ Success: {success_count}")
    summary_lines.append(f"❌ Failed: {fail_count}")
    summary_lines.append(f"💰 Total Value (sum of parsed prices): ₱{total_value:,.2f}")
    summary_text = "\n".join(summary_lines)

    # Send summary and file
    try:
        context.bot.send_message(chat_id=chat_id, text=summary_text)
        with open(outpath, "rb") as doc:
            context.bot.send_document(chat_id=chat_id, document=doc, filename=outname)
    except Exception as e:
        context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to send summary or file: {e}")
        return

# ---------------------------
# Main bot startup
# ---------------------------

def main():
    print("Starting bot...")
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Image handler (photos and image documents)
    dp.add_handler(MessageHandler(Filters.photo | Filters.document.image, handle_image))

    updater.start_polling()
    print("Bot started. Waiting for images...")
    updater.idle()

if __name__ == "__main__":
    main()
