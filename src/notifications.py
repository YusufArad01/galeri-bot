import requests
from loguru import logger
from src.config import settings

def send_telegram_notification(data: dict):
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.warning("Telegram token or chat ID is missing. Cannot send notification.")
        return

    # Fiyatı daha okunaklı formatla (Örn: 1.250.000)
    try:
        fiyat_str = f"{int(data.get('price', 0)):,}".replace(',', '.')
    except:
        fiyat_str = str(data.get('price', ''))

    # KM'yi daha okunaklı formatla
    try:
        km_str = f"{int(data.get('km', 0)):,}".replace(',', '.')
    except:
        km_str = str(data.get('km', ''))

    # Kâr marjı veya indirim bilgisi varsa formata ekle
    margin = data.get('margin', 0)
    market_avg = data.get('market_avg', 0)
    
    margin_text = ""
    if margin > 0:
        try:
            margin_str = f"{int(margin):,}".replace(',', '.')
            margin_text = f"🔥 Piyasanın ~{margin_str} TL altında!\n"
        except:
            pass

    message = (
        f"🚗 Yeni Fırsat İlanı!\n"
        f"🔹 Marka / Model: {data.get('brand', '')} {data.get('model', '')}\n"
        f"📅 Yıl: {data.get('year', '')}\n"
        f"🛣️ KM: {km_str}\n"
        f"📍 Şehir: {data.get('city', '')}\n"
        f"💰 Fiyat: {fiyat_str} TL\n"
        f"{margin_text}"
        f"🔗 İlan Linki: {data.get('url', '')}"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True # İlan resminin veya sayfasının chat'i kaplamaması için
    }

    try:
        response = requests.post(url, json=payload, timeout=10.0)
        if response.status_code == 200:
            logger.info(f"Telegram notification sent for {data.get('source_listing_id')}")
        else:
            logger.error(f"Failed to send Telegram notification: {response.text}")
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")
