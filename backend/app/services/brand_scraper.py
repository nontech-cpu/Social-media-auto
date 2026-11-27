"""Website brand metadata extractor and hashtag text parser (zero regex)."""

from dataclasses import dataclass
from ipaddress import ip_address
from socket import getaddrinfo
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

HEX_CHARS = set("0123456789abcdefABCDEF")


@dataclass
class ScrapedBrand:
    website_url: str
    brand_name: str
    description: str
    logo_url: str
    colors: list[str]
    raw_text_snippet: str


def extract_hashtags_from_text(text: str) -> list[str]:
    """Extract normalized lowercase hashtags from caption text without regex."""
    if not text:
        return []
    seen: set[str] = set()
    tags: list[str] = []
    for raw_token in text.replace("\n", " ").replace("\r", " ").split(" "):
        token = raw_token.strip()
        if not token.startswith("#"):
            continue
        # Handle concatenated hashtags like #ai#reels by splitting on '#'
        for part in token.split("#"):
            if not part:
                continue
            cleaned_chars: list[str] = []
            for ch in part:
                if ch.isalnum() or ch == "_":
                    cleaned_chars.append(ch)
                else:
                    break
            tag = "".join(cleaned_chars).lower()
            if len(tag) >= 2 and tag not in seen:
                seen.add(tag)
                tags.append(f"#{tag}")
    return tags


def _is_valid_hex_color(candidate: str) -> bool:
    if not candidate.startswith("#"):
        return False
    body = candidate[1:]
    return len(body) in (3, 6) and all(ch in HEX_CHARS for ch in body)


def _extract_hex_colors_from_css(css_text: str, limit: int = 6) -> list[str]:
    colors: list[str] = []
    seen: set[str] = set()
    idx = 0
    n = len(css_text)
    while idx < n and len(colors) < limit:
        if css_text[idx] == "#":
            j = idx + 1
            while j < n and css_text[j] in HEX_CHARS and (j - idx - 1) < 6:
                j += 1
            candidate = css_text[idx:j]
            if _is_valid_hex_color(candidate):
                normalized = candidate.upper()
                if normalized not in seen and normalized not in ("#FFFFFF", "#000000", "#FFF", "#000"):
                    seen.add(normalized)
                    colors.append(normalized)
            idx = j
        else:
            idx += 1
    return colors


def _validate_public_url(url: str) -> str:
    candidate = url.strip()
    if not candidate.startswith(("http://", "https://")):
        candidate = "https://" + candidate
    parsed = urlsplit(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Invalid website URL.")
    hostname = parsed.hostname
    try:
        resolved = getaddrinfo(hostname, None)
        for item in resolved:
            addr = ip_address(item[4][0])
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                raise ValueError("Private or local network URLs are not permitted.")
    except OSError as exc:
        raise ValueError(f"Could not resolve hostname: {hostname}") from exc
    return candidate


async def scrape_brand_from_website(url: str) -> ScrapedBrand:
    """Fetch a public website and extract brand name, description, logo, and theme colors."""
    target_url = _validate_public_url(url)
    headers = {
        "User-Agent": "SocialMediaAI-BrandFetcher/1.0 (+https://localhost)",
        "Accept": "text/html,application/xhtml+xml",
    }
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        response = await client.get(target_url, headers=headers)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")

    def meta_content(*names: str) -> str:
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find(
                "meta", attrs={"name": name}
            )
            if tag and tag.get("content"):
                return str(tag["content"]).strip()
        return ""

    title = meta_content("og:site_name", "og:title", "twitter:title")
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        title = urlsplit(str(response.url)).hostname or "Brand"

    description = meta_content("og:description", "description", "twitter:description")

    logo_url = meta_content("og:image", "twitter:image")
    if not logo_url:
        icon_link = soup.find("link", rel=lambda r: r and any("icon" in x.lower() for x in r))
        if icon_link and icon_link.get("href"):
            logo_url = str(icon_link["href"]).strip()
    if logo_url:
        logo_url = urljoin(str(response.url), logo_url)

    colors: list[str] = []
    theme_color = meta_content("theme-color")
    if _is_valid_hex_color(theme_color):
        colors.append(theme_color.upper())

    style_chunks = [style.get_text() for style in soup.find_all("style") if style.get_text()]
    for color in _extract_hex_colors_from_css("\n".join(style_chunks), limit=6):
        if color not in colors:
            colors.append(color)

    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all(["h1", "h2", "p"])[:12]]
    raw_snippet = " ".join(p for p in paragraphs if p)[:1500]

    return ScrapedBrand(
        website_url=str(response.url),
        brand_name=title[:200],
        description=description[:1000],
        logo_url=logo_url[:1000],
        colors=colors[:5],
        raw_text_snippet=raw_snippet,
    )
