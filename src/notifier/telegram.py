import httpx
import asyncio
from loguru import logger
from src.config import settings
from src.models.domain import Listing, Opportunity

class TelegramNotifier:
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    async def send_message(self, text: str, retries: int = 3):
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram credentials not set.")
            return

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        async with httpx.AsyncClient() as client:
            for attempt in range(retries):
                try:
                    response = await client.post(self.api_url, json=payload, timeout=10.0)
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", 5))
                        logger.warning(f"Rate limited by Telegram. Retrying in {retry_after} seconds...")
                        await asyncio.sleep(retry_after)
                        continue
                    response.raise_for_status()
                    logger.info("Telegram message sent successfully.")
                    return
                except httpx.HTTPError as e:
                    logger.error(f"Failed to send Telegram message (Attempt {attempt+1}/{retries}): {e}")
                    await asyncio.sleep(2 ** attempt)

    async def notify_opportunity(self, listing: Listing, opportunity: Opportunity):
        message = (
            f"🚀 *New Arbitrage Opportunity!*\n\n"
            f"🚙 *Vehicle:* {listing.brand} {listing.model} ({listing.year})\n"
            f"📍 *Location:* {listing.city}\n"
            f"🛣️ *KM:* {listing.km:,}\n\n"
            f"💰 *Price:* {listing.price:,.2f} TL\n"
            f"📊 *Market Avg:* {opportunity.market_average_price:,.2f} TL\n"
            f"🔥 *Discount:* {opportunity.deviation_percentage:.2f}%\n\n"
            f"🔗 [View Listing]({listing.url})"
        )
        await self.send_message(message)
