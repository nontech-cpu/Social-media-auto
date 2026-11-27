"""Pluggable public media storage provider with Instagram Graph API pre-flight validation."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import boto3
import cloudinary
import cloudinary.uploader

from app.core.config import Settings, get_settings

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov"}


@dataclass
class UploadedMedia:
    url: str
    media_type: str  # IMAGE | REELS
    filename: str
    size_bytes: int
    provider: str
    is_public_https: bool
    width: int | None = None
    height: int | None = None
    aspect_ratio: float | None = None
    duration_seconds: float | None = None
    warnings: list[str] = field(default_factory=list)


def _extract_image_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """Parse PNG or JPEG dimensions from raw header bytes without regex or external deps."""
    # PNG header: 8-byte signature + IHDR chunk (width at 16:20, height at 20:24 big-endian)
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        return w, h
    # JPEG SOF marker scan
    if len(data) >= 4 and data[:2] == b"\xff\xd8":
        idx = 2
        n = len(data)
        while idx + 9 < n:
            if data[idx] != 0xFF:
                idx += 1
                continue
            marker = data[idx + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h = int.from_bytes(data[idx + 5 : idx + 7], "big")
                w = int.from_bytes(data[idx + 7 : idx + 9], "big")
                return w, h
            if marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
                idx += 2
                continue
            if idx + 4 > n:
                break
            seg_len = int.from_bytes(data[idx + 2 : idx + 4], "big")
            if seg_len < 2:
                break
            idx += 2 + seg_len
    return None, None


def validate_instagram_media_specs(
    *,
    media_type: str,
    size_bytes: int,
    width: int | None,
    height: int | None,
    duration_seconds: float | None,
    settings: Settings,
    target_format: str | None = None,
    is_video: bool = False,
) -> list[str]:
    """Validate uploaded media against Instagram Graph API constraints and return warnings."""
    warnings: list[str] = []
    effective_format = (target_format or media_type).upper()

    if effective_format == "STORIES":
        max_bytes = (
            settings.media_max_story_bytes if is_video or media_type == "REELS" else settings.media_max_image_bytes
        )
        if size_bytes > max_bytes:
            raise ValueError(
                f"Story file size ({size_bytes // (1024 * 1024)} MB) exceeds Instagram's "
                f"{max_bytes // (1024 * 1024)} MB Story limit."
            )
        if (is_video or media_type == "REELS") and duration_seconds is not None:
            if duration_seconds < settings.media_min_reel_seconds:
                raise ValueError(
                    f"Video Story ({duration_seconds:.1f}s) is shorter than {settings.media_min_reel_seconds:.0f}s."
                )
            if duration_seconds > settings.media_max_story_seconds:
                raise ValueError(
                    f"Video Story ({duration_seconds:.0f}s) exceeds Instagram's {settings.media_max_story_seconds:.0f}s limit."
                )
        if width and height and height > 0:
            ratio = width / height
            if ratio > 0.72:
                warnings.append(
                    f"Media aspect ratio {ratio:.2f}:1 ({width}x{height}) is wider than 9:16 vertical — Instagram Stories look best in 9:16 vertical format."
                )
        return warnings

    if media_type == "IMAGE":
        if size_bytes > settings.media_max_image_bytes:
            raise ValueError(
                f"Image size ({size_bytes // (1024 * 1024)} MB) exceeds Instagram's "
                f"{settings.media_max_image_bytes // (1024 * 1024)} MB limit."
            )
        if width and height and height > 0:
            ratio = width / height
            if ratio < 0.8 or ratio > 1.91:
                warnings.append(
                    f"Image aspect ratio {ratio:.2f}:1 ({width}x{height}) is outside Instagram's "
                    "recommended 0.8:1 (4:5) to 1.91:1 range and may be rejected or cropped."
                )
    elif media_type == "REELS":
        if size_bytes > settings.media_max_video_bytes:
            raise ValueError(
                f"Video size ({size_bytes // (1024 * 1024)} MB) exceeds configured "
                f"{settings.media_max_video_bytes // (1024 * 1024)} MB limit."
            )
        if duration_seconds is not None:
            if duration_seconds < settings.media_min_reel_seconds:
                raise ValueError(
                    f"Reel duration ({duration_seconds:.1f}s) is shorter than Instagram's "
                    f"{settings.media_min_reel_seconds:.0f}s minimum."
                )
            if duration_seconds > settings.media_max_reel_seconds:
                raise ValueError(
                    f"Reel duration ({duration_seconds:.0f}s) exceeds Instagram's "
                    f"{settings.media_max_reel_seconds:.0f}s limit."
                )
        if width and height and height > 0:
            ratio = width / height
            if ratio > 1.0:
                warnings.append(
                    f"Video is horizontal ({width}x{height}). Reels perform best in 9:16 vertical format."
                )
    return warnings


class MediaStorageService:
    """Stores images/Reels on Cloudinary, S3/R2, or local disk with spec checks."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _detect_media_type(self, filename: str, content_type: str) -> str:
        ext = Path(filename).suffix.lower()
        if ext in ALLOWED_VIDEO_EXTENSIONS or content_type.startswith("video/"):
            return "REELS"
        if ext in ALLOWED_IMAGE_EXTENSIONS or content_type.startswith("image/"):
            return "IMAGE"
        raise ValueError(
            f"Unsupported media format '{filename}'. Use JPG, PNG, MP4, or MOV."
        )

    async def upload(
        self,
        data: bytes,
        filename: str,
        content_type: str = "",
        *,
        target_format: str | None = None,
    ) -> UploadedMedia:
        if not data:
            raise ValueError("Uploaded file is empty.")
        media_type = self._detect_media_type(filename, content_type)
        local_w, local_h = (
            _extract_image_dimensions(data) if media_type == "IMAGE" else (None, None)
        )
        provider = self.settings.media_storage_provider

        if provider == "cloudinary":
            uploaded = await self._upload_cloudinary(data, filename, media_type)
        elif provider == "s3":
            uploaded = await self._upload_s3(data, filename, content_type, media_type)
        else:
            uploaded = await self._upload_local(data, filename, media_type)

        if uploaded.width is None:
            uploaded.width = local_w
        if uploaded.height is None:
            uploaded.height = local_h
        if uploaded.width and uploaded.height and uploaded.height > 0:
            uploaded.aspect_ratio = round(uploaded.width / uploaded.height, 3)

        uploaded.warnings = validate_instagram_media_specs(
            media_type=uploaded.media_type,
            size_bytes=uploaded.size_bytes,
            width=uploaded.width,
            height=uploaded.height,
            duration_seconds=uploaded.duration_seconds,
            settings=self.settings,
            target_format=target_format,
            is_video=media_type == "REELS",
        )
        return uploaded

    async def _upload_cloudinary(
        self, data: bytes, filename: str, media_type: str
    ) -> UploadedMedia:
        raw_url = (self.settings.cloudinary_url or "").strip()
        if raw_url.startswith("CLOUDINARY_URL="):
            raw_url = raw_url[len("CLOUDINARY_URL=") :].strip()
        if not raw_url:
            raise RuntimeError(
                "CLOUDINARY_URL is not configured. Set CLOUDINARY_URL or switch MEDIA_STORAGE_PROVIDER."
            )
        parsed = urlsplit(raw_url)
        if parsed.scheme != "cloudinary" or not parsed.username or not parsed.password or not parsed.hostname:
            raise RuntimeError(
                "Invalid CLOUDINARY_URL format. Expected cloudinary://API_KEY:API_SECRET@CLOUD_NAME"
            )
        cloudinary.config(
            cloud_name=parsed.hostname,
            api_key=parsed.username,
            api_secret=parsed.password,
            secure=True,
        )
        resource_type = "video" if media_type == "REELS" else "image"
        public_id = f"social_media_ai/{uuid4().hex}"

        def _do_upload() -> dict:
            return cloudinary.uploader.upload(
                data,
                resource_type=resource_type,
                public_id=public_id,
                overwrite=True,
            )

        result = await asyncio.to_thread(_do_upload)
        url = str(result["secure_url"])
        width = int(result["width"]) if result.get("width") else None
        height = int(result["height"]) if result.get("height") else None
        duration = float(result["duration"]) if result.get("duration") else None
        return UploadedMedia(
            url=url,
            media_type=media_type,
            filename=filename,
            size_bytes=len(data),
            provider="cloudinary",
            is_public_https=url.startswith("https://"),
            width=width,
            height=height,
            duration_seconds=duration,
        )

    async def _upload_s3(
        self, data: bytes, filename: str, content_type: str, media_type: str
    ) -> UploadedMedia:
        if not self.settings.s3_bucket or not self.settings.s3_access_key_id:
            raise RuntimeError("S3/R2 bucket and credentials must be configured.")
        ext = Path(filename).suffix.lower() or (".mp4" if media_type == "REELS" else ".jpg")
        key = f"uploads/{uuid4().hex}{ext}"

        def _do_put() -> None:
            client = boto3.client(
                "s3",
                region_name=self.settings.s3_region or None,
                endpoint_url=self.settings.s3_endpoint_url or None,
                aws_access_key_id=self.settings.s3_access_key_id,
                aws_secret_access_key=self.settings.s3_secret_access_key,
            )
            client.put_object(
                Bucket=self.settings.s3_bucket,
                Key=key,
                Body=data,
                ContentType=content_type or ("video/mp4" if media_type == "REELS" else "image/jpeg"),
            )

        await asyncio.to_thread(_do_put)
        base = self.settings.s3_public_base_url.rstrip("/")
        url = f"{base}/{key}" if base else f"https://{self.settings.s3_bucket}.s3.amazonaws.com/{key}"
        return UploadedMedia(
            url=url,
            media_type=media_type,
            filename=filename,
            size_bytes=len(data),
            provider="s3",
            is_public_https=url.startswith("https://"),
        )

    async def _upload_local(
        self, data: bytes, filename: str, media_type: str
    ) -> UploadedMedia:
        upload_dir = Path(self.settings.local_upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix.lower() or (".mp4" if media_type == "REELS" else ".jpg")
        safe_name = f"{uuid4().hex}{ext}"
        target = upload_dir / safe_name

        await asyncio.to_thread(target.write_bytes, data)
        base = self.settings.backend_url.rstrip("/")
        url = f"{base}/uploads/{safe_name}"
        return UploadedMedia(
            url=url,
            media_type=media_type,
            filename=filename,
            size_bytes=len(data),
            provider="local",
            is_public_https=url.startswith("https://") and "localhost" not in url,
        )
