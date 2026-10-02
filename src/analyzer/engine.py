from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.domain import Listing, Opportunity, MarketStats
from loguru import logger

# Expanded list of structural and costly disqualifiers
DISQUALIFIERS = [
    "ağır hasar", "pert", "şase", "podye", "direk", "airbag", "hava yastığı", 
    "motor arızalı", "çıkma", "su almış", "değişenli", "tramer"
]

class AnalyzerEngine:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def check_condition_gate(self, description: str) -> bool:
        desc_lower = description.lower()
        for kw in DISQUALIFIERS:
            if kw in desc_lower:
                logger.info(f"Listing disqualified due to condition text keyword: '{kw}'")
                return False
        return True

    async def get_market_average(self, brand: str, model: str, year: int) -> float | None:
        stmt = select(MarketStats).where(
            MarketStats.brand == brand,
            MarketStats.model == model,
            MarketStats.year >= year - 1,
            MarketStats.year <= year + 1
        )
        result = await self.session.execute(stmt)
        stats = result.scalars().all()
        if not stats:
            return None
        return sum(s.average_price for s in stats) / len(stats)

    async def process_listing(self, listing_id: int):
        stmt = select(Listing).where(Listing.id == listing_id)
        result = await self.session.execute(stmt)
        listing = result.scalars().first()
        if not listing:
            return

        # Prevent duplicate opportunity processing
        stmt = select(Opportunity).where(Opportunity.listing_id == listing_id)
        result = await self.session.execute(stmt)
        existing_opp = result.scalars().first()
        if existing_opp:
            logger.debug(f"Opportunity for listing {listing_id} already processed.")
            return

        market_avg = await self.get_market_average(listing.brand, listing.model, listing.year)
        if not market_avg:
            logger.info(f"Market average not available to evaluate {listing_id}.")
            return

        threshold = 0.15 # Minimum 15% discount required

        # --- GATE 1: Price Deviation ---
        # Ensures price is significantly lower than average before doing expensive analysis
        if listing.price > market_avg * (1 - threshold):
            logger.debug(f"Listing {listing_id} failed price deviation gate.")
            return
            
        deviation = ((market_avg - listing.price) / market_avg) * 100

        # --- GATE 2: Text Condition ---
        # Scans description strictly for disqualifying words
        is_healthy = await self.check_condition_gate(listing.description)
        if not is_healthy:
            opp = Opportunity(
                listing_id=listing_id,
                market_average_price=market_avg,
                deviation_percentage=deviation,
                status="DISQUALIFIED"  # Ensures no notification will be sent
            )
            self.session.add(opp)
            await self.session.commit()
            return

        # Opportunity passes both strict gates
        opp = Opportunity(
            listing_id=listing_id,
            market_average_price=market_avg,
            deviation_percentage=deviation,
            status="NEW"
        )
        self.session.add(opp)
        await self.session.commit()
        logger.info(f"✅ VERIFIED OPPORTUNITY: {listing.title} ({deviation:.2f}% below market)")
