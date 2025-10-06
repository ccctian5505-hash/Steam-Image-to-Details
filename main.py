import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Upload to telegra.ph to get a temporary public URL
def upload_to_telegraph(file_path):
    with open(file_path, 'rb') as f:
        response = requests.post("https://telegra.ph/upload", files={"file": ('file', f, 'image/jpeg')})
    return "https://telegra.ph" + response.json()[0]["src"]

# Use Google Images reverse search (free)
def reverse_image_search(image_url):
    search_url = "https://www.google.com/searchbyimage?&image_url=" + image_url
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(search_url, headers=headers)
    soup = BeautifulSoup(response.text, "lxml")

    # Try to extract the first relevant text result
    guess = soup.find("a", {"class": "VFACy"})
    if guess:
        return guess.text.strip()
    else:
        # fallback: look for title in meta
        meta = soup.find("meta", {"property": "og:title"})
        return meta["content"] if meta else None

# Get Steam Market price
def get_steam_price(item_name):
    query = item_name.replace(" ", "+")
    url = f"https://steamcommunity.com/market/search?q={query}"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, "lxml")

    result = soup.find("span", {"class": "market_listing_item_name"})
    if not result:
        return None

    price = soup.find("span", {"class": "normal_price"})
    return {
        "name": result.text.strip(),
        "price": price.text.strip() if price else "Unknown",
        "url": url
    }

# Handle photos
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_path = "temp.jpg"
    await file.download_to_drive(file_path)

    await update.message.reply_text("🔍 Searching item by image... Please wait...")

    try:
        image_url = upload_to_telegraph(file_path)
        guess = reverse_image_search(image_url)
        if not guess:
            await update.message.reply_text("❌ No similar items found online.")
            return

        data = get_steam_price(guess)
        if data:
            await update.message.reply_text(
                f"✅ **Item Found:** {data['name']}\n💰 **Price:** {data['price']}\n🔗 {data['url']}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"❌ Item not found on Steam Market.\n🔎 Google Guess: {guess}")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Send me a photo of a game item and I’ll try to identify it!")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

print("🤖 Free Reverse Image Bot is running...")
app.run_polling()
