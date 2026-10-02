import asyncio
from loguru import logger
import os

from src.database import engine, Base, AsyncSessionLocal
from src.proxy.manager import ProxyManager
from src.scraper.engine import ScraperEngine
from src.config import settings
from src.models.domain import Listing
from sqlalchemy import select

async def debug_run():
    logger.info("--- STARTING DRY RUN DEBUG ---")
    
    # Initialize DB (just in case)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with AsyncSessionLocal() as session:
        proxy_manager = ProxyManager(session)
        scraper = ScraperEngine(proxy_manager)
        
        target = settings.TARGET_URL
        if not target:
            target = "https://www.arabam.com/ikinci-el/otomobil"
            
        if "?" not in target:
            target += "?sort=DateDesc"
        elif "sort=" not in target:
            target += "&sort=DateDesc"

        logger.info(f"Target URL: {target}")
        
        try:
            links = await scraper.fetch_category_links(target)
            logger.info(f"Extracted Links Count: {len(links)}")
            
            if not links:
                logger.error("NO LINKS FOUND. Possible CSS change or Cloudflare block.")
                return
                
            for link in links[:3]:
                logger.info(f"Scraping: {link}")
                try:
                    data = await scraper.fetch_listing_data(link)
                    logger.success(f"Data: {data['brand']} {data['model']} | Price: {data['price']} | Title: {data['title']}")
                    
                    keywords = ['acil', 'acilinden', 'aciliyetten', 'fırsat', 'nakit']
                    search_text = (data.get('title', '') + " " + data.get('description', '')).lower()
                    is_opportunity = any(word in search_text for word in keywords)
                    
                    logger.info(f"Opportunity filter matched? {is_opportunity}")
                    
                except Exception as e:
                    logger.error(f"Error scraping {link}: {e}")
                    
        except Exception as e:
            logger.error(f"Category fetch error: {e}")
            
    logger.info("--- DRY RUN FINISHED ---")

if __name__ == "__main__":
    # Force headful for debug
    os.environ["HEADLESS"] = "false"
    asyncio.run(debug_run())
