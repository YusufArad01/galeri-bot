"""Telegram gönderimi + kalıcı outbox.

Bildirim önce `alerts` tablosuna yazılır, sonra gönderilir. Telegram/ağ hatasında kayıt
PENDING kalır ve sonraki döngüde yeniden denenir; böylece geçici bir kesinti fırsat
kaybettirmez. Kalıcı reddedilen mesaj (galerici botu başlatmamış/engellemiş) FAILED olur ve yöneticiye bildirilir.
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
from src.models.domain import Alert, BotState

MAX_LEN = 4096
MAX_ATTEMPTS = 8
PENDING_MAX_AGE = timedelta(hours=24)
PERMANENT_ERRORS = (400, 403, 404)   # bot engellendi, sohbet bulunamadı, ...


def feedback_keyboard(alert_id: int) -> dict:
    return {"inline_keyboard": [[{"text": "👍 Beğendim", "callback_data": f"f:{alert_id}:1"},
                                 {"text": "👎 Beğenmedim", "callback_data": f"f:{alert_id}:0"}]]}


class Telegram:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self.token = token
        self._client = client
        self.last_status = 0   # son başarısız gönderimin HTTP kodu

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def send(self, chat_id: str, text: str, reply_markup: dict | None = None) -> bool:
        if not self.token or not chat_id:
            logger.warning("Telegram token/chat_id eksik; mesaj gönderilemedi.")
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text[:MAX_LEN], "disable_web_page_preview": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=15.0)
        self.last_status = 0
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
                self.last_status = resp.status_code
                logger.error(f"Telegram reddetti (chat {chat_id}): HTTP {resp.status_code} {resp.text[:160]}")
                return False
            return False
        finally:
            if owns:
                await client.aclose()

    async def call(self, method: str, payload: dict, timeout: float = 15.0) -> dict | None:
        """Diğer Telegram yöntemleri (getUpdates, answerCallbackQuery...). Hata olursa None."""
        if not self.token:
            return None
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=timeout + 10)
        try:
            resp = await client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload)
            return resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(f"Telegram {method} hatası: {type(exc).__name__}")
            return None
        finally:
            if owns:
                await client.aclose()


async def notify_once(session: AsyncSession, telegram: Telegram, chat_id: str, kind: str, text: str,
                      cooldown_hours: float = 6.0) -> bool:
    """Aynı türde uyarıyı soğuma süresi dolmadan tekrar göndermez. Gönderim denendiyse True."""
    key = f"alert_last:{kind}"
    row = (await session.execute(select(BotState).where(BotState.key == key))).scalars().first()
    if row and row.value:
        try:
            if datetime.utcnow() - datetime.fromisoformat(row.value) < timedelta(hours=cooldown_hours):
                return False
        except ValueError:
            pass
    ok = await telegram.send(chat_id, text)
    if ok:
        if row:
            row.value = datetime.utcnow().isoformat()
        else:
            session.add(BotState(key=key, value=datetime.utcnow().isoformat()))
        await session.commit()
    return ok


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
        if await telegram.send(alert.chat_id, alert.text, feedback_keyboard(alert.id)):
            alert.status = "SENT"
            alert.sent_at = datetime.utcnow()
            sent += 1
            logger.success(f"Telegram bildirimi gönderildi [{alert.mode}/{alert.dealer_id or '-'}] {alert.source_listing_id}")
        elif telegram.last_status in PERMANENT_ERRORS:
            alert.status = "FAILED"
            await session.commit()
            await notify_once(session, telegram, settings.admin_chat, f"tg_fail:{alert.chat_id}",
                              f"⚠️ {alert.dealer_id or 'Genel mod'} sohbetine ({alert.chat_id}) mesaj gidemedi "
                              f"(HTTP {telegram.last_status}). Galerici botu başlatmamış (/start) ya da engellemiş olabilir.",
                              cooldown_hours=12)
        else:
            still += 1
        await asyncio.sleep(0.3)  # Telegram saniyede ~1 mesaj/sohbet toleransı
    await session.commit()
    return sent, still
