"""Telegram gönderimi + kalıcı outbox.

Bildirim önce `alerts` tablosuna yazılır, sonra gönderilir. Telegram/ağ hatasında kayıt
PENDING kalır ve sonraki döngüde yeniden denenir; böylece geçici bir kesinti fırsat
kaybettirmez. (Eski sürümde mesaj fire-and-forget gönderiliyordu, hata sadece loga düşüyordu.)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.domain import Alert

MAX_LEN = 4096
MAX_ATTEMPTS = 8
PENDING_MAX_AGE = timedelta(hours=24)


class Telegram:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self.token = token
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def send(self, chat_id: str, text: str) -> bool:
        if not self.token or not chat_id:
            logger.warning("Telegram token/chat_id eksik; mesaj gönderilemedi.")
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text[:MAX_LEN], "disable_web_page_preview": True}
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            for attempt in range(1, 4):
                try:
                    resp = await client.post(url, json=payload)
                except httpx.HTTPError as exc:
                    # İstisna metni token içeren URL taşıyabilir; sadece türünü logla.
                    logger.warning(f"Telegram ağ hatası ({attempt}/3): {type(exc).__name__}")
                    await asyncio.sleep(2 * attempt)
                    continue
                if resp.status_code == 200:
                    return True
                if resp.status_code == 429:
                    try:
                        wait = float(resp.json().get("parameters", {}).get("retry_after", 5))
                    except ValueError:
                        wait = 5.0
                    logger.warning(f"Telegram limit: {wait}s bekleniyor")
                    await asyncio.sleep(wait + 0.5)
                    continue
                if resp.status_code >= 500:
                    await asyncio.sleep(2 * attempt)
                    continue
                # 400/403/404: kalıcı hata (bot engellendi, chat bulunamadı, ...)
                logger.error(f"Telegram reddetti (chat {chat_id}): HTTP {resp.status_code} {resp.text[:160]}")
                return False
            return False
        finally:
            if owns:
                await client.aclose()


async def enqueue_alert(session: AsyncSession, *, source_listing_id: str, mode: str, dealer_id: str,
                        chat_id: str, text: str, reason: str) -> Alert | None:
    """Alert kaydı oluşturur. Aynı ilan+mod+galerici için zaten varsa None (mükerrer bildirim yok)."""
    alert = Alert(source_listing_id=source_listing_id, mode=mode, dealer_id=dealer_id,
                  chat_id=chat_id, text=text, reason=reason)
    try:
        async with session.begin_nested():
            session.add(alert)
            await session.flush()
    except IntegrityError:
        return None
    return alert


def dealer_hours_open(now_utc: datetime) -> bool:
    """Galerici mesajları yalnız Türkiye saatiyle [DEALER_ACTIVE_START, DEALER_ACTIVE_END) arasında gider."""
    hour = (now_utc + timedelta(hours=3)).hour
    return settings.DEALER_ACTIVE_START <= hour < settings.DEALER_ACTIVE_END


async def flush_alerts(session: AsyncSession, telegram: Telegram, now: datetime | None = None) -> tuple[int, int]:
    """PENDING bildirimleri gönderir. (gönderilen, hâlâ bekleyen) döner.
    Saat dışında düşen galerici bildirimleri kuyrukta bekler, açılış saatinde gider (kaybolmaz)."""
    now = now or datetime.utcnow()
    dealer_open = dealer_hours_open(now)
    cutoff = now - PENDING_MAX_AGE
    rows = (await session.execute(
        select(Alert).where(Alert.status == "PENDING").order_by(Alert.id)
    )).scalars().all()
    sent = still = 0
    for alert in rows:
        if alert.created_at and alert.created_at < cutoff or alert.attempts >= MAX_ATTEMPTS:
            alert.status = "FAILED"
            continue
        if alert.mode == "dealer" and not dealer_open:
            still += 1
            continue
        alert.attempts += 1
        if await telegram.send(alert.chat_id, alert.text):
            alert.status = "SENT"
            alert.sent_at = datetime.utcnow()
            sent += 1
            logger.success(f"Telegram bildirimi gönderildi [{alert.mode}/{alert.dealer_id or '-'}] {alert.source_listing_id}")
        else:
            still += 1
        await asyncio.sleep(0.3)  # Telegram saniyede ~1 mesaj/sohbet toleransı
    await session.commit()
    return sent, still
