"""Instagram Business OAuth routes, account management, and Meta Data Deletion callback."""

from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    create_signed_state,
    get_token_cipher,
    verify_meta_signed_request,
    verify_signed_state,
)
from app.models.entities import BrandProfile, InstagramAccount
from app.services.analytics_engine import recompute_best_time_slots, sync_account_and_compute_slots
from app.services.meta_client import MetaAPIError, MetaGraphClient

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TimezoneUpdateRequest(BaseModel):
    timezone: str


@router.get("/instagram/url")
async def get_instagram_oauth_url(
    brand_id: str = Query(default=""),
    timezone_name: str = Query(default="UTC"),
) -> dict:
    client = MetaGraphClient()
    state = create_signed_state({"brand_id": brand_id, "timezone": timezone_name})
    try:
        oauth_url = client.build_oauth_url(state)
    except MetaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"oauth_url": oauth_url, "state": state}


@router.get("/instagram/callback")
async def instagram_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    format: str = Query(default="redirect"),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    frontend_base = settings.frontend_url.rstrip("/")

    if error:
        if format == "json":
            raise HTTPException(status_code=400, detail=error_description or error)
        params = urlencode({"error": error_description or error})
        return RedirectResponse(f"{frontend_base}/onboarding?{params}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth code or state parameter.")

    try:
        state_data = verify_signed_state(state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    meta_client = MetaGraphClient(settings)
    cipher = get_token_cipher()

    try:
        long_token, expires_at = await meta_client.exchange_code_for_long_lived_token(code)
        discovered, diag = await meta_client.discover_instagram_accounts_with_diagnostics(long_token)
    except MetaAPIError as exc:
        if format == "json":
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        params = urlencode({"error": str(exc)})
        return RedirectResponse(f"{frontend_base}/onboarding?{params}")

    if not discovered:
        if diag["pages_found"] == 0:
            msg = (
                f"Meta returned 0 Facebook Pages (granted permissions: {', '.join(diag['granted_scopes']) or 'none'}). "
                "Create a Facebook Page (not just a Personal Profile), link your Instagram Creator/Business account to that Page, "
                "and make sure you tick that Page in the Facebook Login popup."
            )
        else:
            msg = (
                f"Found Facebook Page(s) ({', '.join(diag['pages_without_ig'])}), but none have an Instagram Professional account linked. "
                "Switch to your Facebook Page -> Settings -> Linked accounts -> Instagram and connect it, then retry."
            )
        if format == "json":
            raise HTTPException(status_code=404, detail=msg)
        return RedirectResponse(f"{frontend_base}/onboarding?{urlencode({'error': msg})}")

    brand_id = state_data.get("brand_id") or None
    if brand_id:
        brand_exists = await db.get(BrandProfile, brand_id)
        if brand_exists is None:
            brand_id = None

    tz_name = state_data.get("timezone") or "UTC"
    connected_accounts: list[InstagramAccount] = []

    for item in discovered:
        ig = item["ig"]
        ig_user_id = str(ig["id"])
        existing = (
            await db.execute(
                select(InstagramAccount).where(InstagramAccount.ig_user_id == ig_user_id)
            )
        ).scalar_one_or_none()

        if existing is None:
            existing = InstagramAccount(
                ig_user_id=ig_user_id,
                username=str(ig.get("username") or ig_user_id),
                encrypted_access_token=cipher.encrypt(long_token),
                timezone=tz_name,
            )
            db.add(existing)

        existing.brand_id = brand_id or existing.brand_id
        existing.fb_page_id = str(item["fb_page_id"])
        existing.fb_page_name = str(item["fb_page_name"])
        existing.username = str(ig.get("username") or existing.username)
        existing.name = str(ig.get("name") or "")
        existing.biography = str(ig.get("biography") or "")
        existing.website = str(ig.get("website") or "")
        existing.profile_picture_url = str(ig.get("profile_picture_url") or "")
        existing.followers_count = int(ig.get("followers_count", 0) or 0)
        existing.follows_count = int(ig.get("follows_count", 0) or 0)
        existing.media_count = int(ig.get("media_count", 0) or 0)
        existing.encrypted_access_token = cipher.encrypt(long_token)
        existing.token_expires_at = expires_at
        existing.is_active = True
        connected_accounts.append(existing)

    await db.commit()
    for acc in connected_accounts:
        await db.refresh(acc)
        try:
            await sync_account_and_compute_slots(db, acc, meta_client, cipher, media_limit=50)
        except Exception:
            # Allow user into dashboard even if initial insights call hits a temporary limit
            pass

    primary = connected_accounts[0]
    if format == "json":
        return {
            "connected": [
                {"id": a.id, "ig_user_id": a.ig_user_id, "username": a.username}
                for a in connected_accounts
            ]
        }

    return RedirectResponse(
        f"{frontend_base}/dashboard?connected=1&account_id={primary.id}"
    )


@router.get("/accounts")
async def list_connected_accounts(db: AsyncSession = Depends(get_db)) -> dict:
    accounts = (
        await db.execute(
            select(InstagramAccount)
            .where(InstagramAccount.is_active.is_(True))
            .order_by(InstagramAccount.created_at.desc())
        )
    ).scalars().all()

    return {
        "accounts": [
            {
                "id": a.id,
                "brand_id": a.brand_id,
                "ig_user_id": a.ig_user_id,
                "fb_page_name": a.fb_page_name,
                "username": a.username,
                "name": a.name,
                "biography": a.biography,
                "website": a.website,
                "profile_picture_url": a.profile_picture_url,
                "followers_count": a.followers_count,
                "media_count": a.media_count,
                "timezone": a.timezone,
                "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
                "token_expires_at": a.token_expires_at.isoformat() if a.token_expires_at else None,
            }
            for a in accounts
        ]
    }


@router.patch("/accounts/{account_id}/timezone")
async def update_account_timezone(
    account_id: str,
    body: TimezoneUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    account = await db.get(InstagramAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Instagram account not found.")
    account.timezone = body.timezone
    await recompute_best_time_slots(db, account)
    await db.commit()
    return {"id": account.id, "timezone": account.timezone}


@router.delete("/accounts/{account_id}")
async def disconnect_account(
    account_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    account = await db.get(InstagramAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Instagram account not found.")
    await db.execute(delete(InstagramAccount).where(InstagramAccount.id == account_id))
    await db.commit()
    return {"deleted": True, "id": account_id}


@router.post("/instagram/data-deletion")
async def meta_data_deletion_callback(
    signed_request: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Automated Meta User Data Deletion Callback endpoint for App Review compliance."""
    settings = get_settings()
    if not settings.meta_app_secret:
        raise HTTPException(status_code=500, detail="META_APP_SECRET is not configured.")
    try:
        payload = verify_meta_signed_request(signed_request, settings.meta_app_secret)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user_id = str(payload.get("user_id") or "")
    if user_id:
        await db.execute(
            delete(InstagramAccount).where(InstagramAccount.ig_user_id == user_id)
        )
        await db.commit()

    code = uuid4().hex[:12]
    status_url = f"{settings.frontend_url.rstrip('/')}/data-deletion?code={code}&ts={int(datetime.now(timezone.utc).timestamp())}"
    return {"url": status_url, "confirmation_code": code}
