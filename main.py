import asyncio
from loguru import logger
from src.database import engine, Base
from src.database import AsyncSessionLocal
from src.proxy.manager import ProxyManager
from src.scraper.engine import ScraperEngine
from src.scraper.vavacars_engine import VavaCarsEngine
from src.config import settings
from src.models.domain import Listing
from sqlalchemy import select

import httpx
import re
import json
import os

async def get_perplexity_market_average(brand: str, model: str, year: int, km: int) -> float:
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        logger.error("PERPLEXITY_API_KEY is missing!")
        return 0.0
        
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    prompt = f"Türkiye ikinci el araç piyasasında {year} model {brand} {model} ({km} Kilometrede) normal durumdaki (ufak boya/değişen olabilir ama pert/ağır hasarlı DEĞİL) bir aracın ortalama güncel satış fiyatı nedir? SADECE tek bir rakam dön. Örnek: 850000"
    
    payload = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": "Sen sadece araçların güncel ortalama piyasa fiyatını 'rakam' olarak veren bir API'sin. Harf, virgül veya nokta kullanma. Sadece tek bir rakam dön."},
            {"role": "user", "content": prompt}
        ]
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://api.perplexity.ai/chat/completions", headers=headers, json=payload, timeout=30.0)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                numbers = re.findall(r'\d+', content.replace('.', '').replace(',', '').replace(' ', ''))
                if numbers:
                    # Rakam çok uzunsa veya saçmaysa koruma (örn. milyonları birleştirirse)
                    val = float(numbers[0])
                    if val > 100000 and val < 20000000:
                        return val
    except Exception as e:
        logger.error(f"Perplexity API error: {e}")
    return 0.0

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized.")

import os
import random

async def run_scraper():
    async with AsyncSessionLocal() as session:
        proxy_manager = ProxyManager(session)
        target = settings.TARGET_URL
        if not target:
            logger.error("TARGET_URL is not set.")
            return
            
        if "vava.cars" in target:
            scraper = VavaCarsEngine(proxy_manager)
            # VavaCars base target
            if "/buy/cars" not in target:
                target = "https://tr.vava.cars/buy/cars"
        else:
            scraper = ScraperEngine(proxy_manager)
            # Ensure 'En Yeni' sorting is applied for Arabam.com
            if "?" not in target:
                target += "?sort=DateDesc"
            elif "sort=" not in target:
                target += "&sort=DateDesc"

        logger.info(f"Fetching category links from: {target}")
        try:
            links = await scraper.fetch_category_links(target)
            logger.success(f"Found {len(links)} listings.")
            
            # Öğrenme hızını artırmak için limit 10'dan 40'a çıkarıldı
            for link in links[:40]:
                logger.info(f"Scraping detail page: {link}")
                
                # Anti-Bot Jitter Mechanism
                jitter = random.uniform(3.0, 7.0)
                logger.debug(f"Applying anti-bot jitter: sleeping for {jitter:.2f}s")
                await asyncio.sleep(jitter)
                
                try:
                    data = await scraper.fetch_listing_data(link)
                    logger.success(f"Scraped Data: {data}")
                    
                    # Deduplication and DB Persistence
                    db_data = data.copy()
                    db_data.pop('seller_type', None)
                    db_data.pop('is_heavy_damage', None)
                    
                    result = await session.execute(select(Listing).where(Listing.source_listing_id == db_data["source_listing_id"]))
                    existing = result.scalar_one_or_none()
                    
                    if existing:
                        logger.info(f"Listing {db_data['source_listing_id']} already exists in Database. Skipping.")
                    else:
                        new_listing = Listing(**db_data)
                        session.add(new_listing)
                        
                        # --- Piyasa Anomalisi ve Akıllı Fiyat Analizi ---
                        brand = data.get('brand', '')
                        model = data.get('model', '')
                        year = data.get('year', 0)
                        km = data.get('km', 0)
                        price = data.get('price', 0.0)
                        
                        from sqlalchemy import func
                        stmt = select(func.avg(Listing.price), func.count(Listing.id)).where(
                            Listing.brand == brand,
                            Listing.model == model,
                            Listing.year == year
                        )
                        stats = await session.execute(stmt)
                        row = stats.first()
                        avg_price = row[0] if row and row[0] else 0
                        sample_size = row[1] if row and row[1] else 0
                        
                        is_opportunity = False
                        margin = 0
                        market_avg = avg_price
                        
                        # Önce ağır hasar filtresi (API maliyetinden kaçınmak için)
                        if data.get('is_heavy_damage'):
                            is_opportunity = False
                            logger.info("Listing rejected: Heavy damage / Pert detected.")
                        else:
                            # 1. Kelime Bazlı Fırsat
                            keywords = ['acil', 'acilinden', 'aciliyetten', 'fırsat', 'nakit']
                            search_text = (data.get('title', '') + " " + data.get('description', '')).lower()
                            keyword_match = any(word in search_text for word in keywords)
                            
                            # 2. Fiyat Anomalisi Analizi
                            if sample_size < 2 or market_avg == 0:
                                logger.info(f"Not enough local DB data for {brand} {model} {year}. Asking Perplexity AI...")
                                px_avg = await get_perplexity_market_average(brand, model, year, km)
                                if px_avg > 0:
                                    logger.info(f"Perplexity AI estimates market average: {px_avg} TL")
                                    market_avg = px_avg
                                else:
                                    logger.warning("Perplexity failed to return a valid average.")
                                
                            if market_avg > 0:
                                discount = market_avg - price
                                discount_percentage = (discount / market_avg) * 100
                                
                                if discount_percentage >= 8:
                                    is_opportunity = True
                                    margin = discount
                                    logger.info(f"Anomaly detected! {discount_percentage:.1f}% below market. Est. Margin: {margin} TL")
                            
                            if keyword_match and not is_opportunity:
                                is_opportunity = True
                                logger.info("Opportunity detected via keywords.")

                        # Veritabanına kaydet
                        await session.commit()
                        logger.success(f"Successfully saved new listing to Database: {data['source_listing_id']}")
                        
                        if is_opportunity:
                            data['margin'] = margin
                            data['market_avg'] = market_avg
                            
                            # Trigger Telegram Notification asynchronously
                            from src.notifications import send_telegram_notification
                            asyncio.create_task(asyncio.to_thread(send_telegram_notification, data))
                            logger.info(f"Telegram notification triggered for {data['source_listing_id']}")
                        else:
                            logger.info(f"Listing {data['source_listing_id']} is not an opportunity. No notification sent.")
                        
                except Exception as e:
                    logger.error(f"Failed to scrape {link}: {e}")
        except Exception as e:
            logger.error(f"Category scraping failed: {e}")

async def main():
    logger.info("Starting Arbitrage Engine in Server Mode (Railway Ready)...")
    await init_db()
    
    interval_minutes = int(os.environ.get("SCRAPE_INTERVAL_MINUTES", 30))
    
    while True:
        try:
            await run_scraper()
        except Exception as e:
            logger.error(f"Fatal error in scraper cycle: {e}")
            
        logger.info(f"Cycle finished. Waiting for {interval_minutes} minutes before the next run...")
        await asyncio.sleep(interval_minutes * 60)

if __name__ == "__main__":
    asyncio.run(main())

