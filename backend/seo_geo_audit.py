"""On-page SEO and local GEO scoring for public website pages."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

MAX_HTML_BYTES = 2_000_000
MAX_REDIRECTS = 5
USER_AGENT = "FajaedeAuditBot/1.0 (+https://api.fajaede.eu)"


def _resolve_public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid URL.") from exc

    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
    ):
        raise ValueError("Only public HTTP(S) URLs on standard ports are accepted.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValueError("Private and local addresses cannot be audited.")

    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(result[4][0].split("%", 1)[0])
                for result in socket.getaddrinfo(hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise ValueError("The website hostname could not be resolved.") from exc
        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError("Private and local addresses cannot be audited.")
    else:
        if not literal_address.is_global:
            raise ValueError("Private and local addresses cannot be audited.")

    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


async def validate_public_url(value: str) -> str:
    """Validate a URL and its resolved addresses without blocking the event loop."""
    return await asyncio.to_thread(_resolve_public_url, value)


async def fetch_public_page(
    url: str,
    *,
    timeout_seconds: float = 8.0,
    max_bytes: int = MAX_HTML_BYTES,
    expect_html: bool = True,
    allow_http_errors: bool = False,
) -> tuple[str, str, int, int]:
    """Fetch a public page while validating every redirect target."""
    current_url = await validate_public_url(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }

    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        headers=headers,
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("The website returned a redirect without a destination.")
                    current_url = await validate_public_url(urljoin(current_url, location))
                    continue

                if response.is_error and not allow_http_errors:
                    response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if expect_html and content_type and "html" not in content_type and "xhtml" not in content_type:
                    raise ValueError("The URL did not return an HTML page.")

                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("The HTML page exceeds the audit size limit.")
                    chunks.append(chunk)

                encoding = response.encoding or "utf-8"
                html = b"".join(chunks).decode(encoding, errors="replace")
                return html, str(response.url), response.status_code, size

    raise ValueError("The website exceeded the redirect limit.")


def _flatten_jsonld(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _flatten_jsonld(item)
    elif isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if graph is not None:
            yield from _flatten_jsonld(graph)


def _jsonld_entities(soup: BeautifulSoup) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw_json = script.string or script.get_text()
        try:
            entities.extend(_flatten_jsonld(json.loads(raw_json)))
        except (json.JSONDecodeError, TypeError):
            continue
    return entities


def _schema_types(entity: dict[str, Any]) -> set[str]:
    raw_types = entity.get("@type", [])
    if isinstance(raw_types, str):
        raw_types = [raw_types]
    if not isinstance(raw_types, list):
        return set()
    return {
        item.rsplit("/", 1)[-1].rsplit("#", 1)[-1].lower()
        for item in raw_types
        if isinstance(item, str)
    }


def _itemprop_values(root: Any, property_name: str) -> list[str]:
    values: list[str] = []
    for element in root.find_all(attrs={"itemprop": True}):
        properties = element.get("itemprop", [])
        if isinstance(properties, str):
            properties = properties.split()
        if property_name not in properties:
            continue
        value = element.get("content") or element.get("href") or element.get("src") or element.get_text(" ", strip=True)
        if value:
            values.append(str(value).strip())
    return values


def _microdata_local_entities(soup: BeautifulSoup, local_types: set[str]) -> list[dict[str, Any]]:
    local_roots: list[Any] = []
    for root in soup.find_all(attrs={"itemscope": True, "itemtype": True}):
        raw_types = root.get("itemtype", [])
        if isinstance(raw_types, str):
            raw_types = raw_types.split()
        type_names = {str(item).rsplit("/", 1)[-1].rsplit("#", 1)[-1].lower() for item in raw_types}
        if type_names & local_types:
            local_roots.append(root)

    entities: list[dict[str, Any]] = []
    for root in local_roots:
        address_root = root.find(attrs={"itemprop": "address", "itemscope": True})
        geo_root = root.find(attrs={"itemprop": "geo", "itemscope": True})
        address: dict[str, str] = {}
        if address_root is not None:
            for field in ("streetAddress", "addressLocality", "postalCode", "addressCountry", "addressRegion"):
                values = _itemprop_values(address_root, field)
                if values:
                    address[field] = values[0]
        geo: dict[str, str] = {}
        if geo_root is not None:
            for field in ("latitude", "longitude"):
                values = _itemprop_values(geo_root, field)
                if values:
                    geo[field] = values[0]

        entity: dict[str, Any] = {
            "@type": "LocalBusiness",
            "name": next(iter(_itemprop_values(root, "name")), ""),
            "telephone": next(iter(_itemprop_values(root, "telephone")), ""),
            "address": address,
            "geo": geo,
            "areaServed": next(iter(_itemprop_values(root, "areaServed")), ""),
            "sameAs": _itemprop_values(root, "sameAs"),
            "review": _itemprop_values(root, "review"),
            "aggregateRating": _itemprop_values(root, "aggregateRating"),
        }
        entities.append(entity)
    return entities


def _make_check(
    code: str,
    label: str,
    weight: int,
    passed: bool | None,
    details: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "passed": passed,
        "points": weight if passed else 0 if passed is False else None,
        "max_points": weight,
        "details": details,
    }


def _score_checks(checks: list[dict[str, Any]]) -> tuple[int, int]:
    assessed_weight = sum(check["max_points"] for check in checks if check["passed"] is not None)
    earned_points = sum(check["points"] or 0 for check in checks)
    score = round(earned_points * 100 / assessed_weight) if assessed_weight else 0
    coverage = round(assessed_weight * 100 / sum(check["max_points"] for check in checks))
    return score, coverage


def audit_seo_geo(
    html_content: str,
    page_url: str,
    *,
    response_status: int = 200,
    response_time_ms: int | None = None,
    robots_status: int | None = None,
    sitemap_status: int | None = None,
    sitemap_url: str | None = None,
    sitemap_valid: str | bool | None = None,
    robots_allowed: bool | None = None,
) -> dict[str, Any]:
    """Return separate, evidence-backed SEO and local GEO scores and findings."""
    soup = BeautifulSoup(html_content, "html.parser")
    entities = _jsonld_entities(soup)
    text_content = soup.get_text(" ", strip=True)
    words = len(text_content.split())

    titles = soup.find_all("title")
    title_text = titles[0].get_text(" ", strip=True) if titles else ""
    descriptions = soup.find_all("meta", attrs={"name": lambda value: isinstance(value, str) and value.lower() == "description"})
    description_text = str(descriptions[0].get("content", "")).strip() if descriptions else ""
    h1_count = len(soup.find_all("h1"))
    images = soup.find_all("img")
    images_with_alt = sum(1 for image in images if image.has_attr("alt") and str(image.get("alt", "")).strip())
    robots_values = [
        str(meta.get("content", "")).lower()
        for meta in soup.find_all("meta", attrs={"name": lambda value: isinstance(value, str) and value.lower() in {"robots", "googlebot"}})
    ]
    is_noindex = any("noindex" in value or "none" in value.split(",") for value in robots_values)
    canonical_links = [
        link for link in soup.find_all("link", href=True)
        if "canonical" in [str(rel).lower() for rel in (link.get("rel") or [])]
    ]
    html_tag = soup.find("html")
    has_lang = bool(html_tag and str(html_tag.get("lang", "")).strip())
    has_viewport = soup.find("meta", attrs={"name": lambda value: isinstance(value, str) and value.lower() == "viewport"}) is not None
    valid_jsonld = bool(entities)
    has_schema_markup = valid_jsonld or soup.find(attrs={"itemtype": True}) is not None
    if isinstance(sitemap_valid, str):
        try:
            sitemap_root = ET.fromstring(sitemap_valid)
            sitemap_root_name = sitemap_root.tag.rsplit("}", 1)[-1].lower()
            sitemap_valid = sitemap_root_name in {"urlset", "sitemapindex"}
        except ET.ParseError:
            sitemap_valid = False

    seo_checks = [
        _make_check("title", "Title tag", 10, bool(title_text) and 10 <= len(title_text) <= 60,
                    f"{len(title_text)} characters; recommended range 10-60."),
        _make_check("meta_description", "Meta description", 10, bool(description_text) and 70 <= len(description_text) <= 160,
                    f"{len(description_text)} characters; recommended range 70-160."),
        _make_check("headings", "Heading structure", 10, h1_count == 1 and bool(soup.find("h2")),
                    f"{h1_count} H1 tag(s); {'H2 headings found' if soup.find('h2') else 'no H2 headings found'}."),
        _make_check("content", "Readable page content", 10, words >= 300,
                    f"{words} visible words; 300 is the audit's minimum content threshold."),
        _make_check("canonical", "Canonical URL", 10, bool(canonical_links),
                    "Canonical link found." if canonical_links else "No canonical link found."),
        _make_check("indexability", "Page indexability", 10,
                    not is_noindex and response_status < 400 and robots_allowed is not False,
                    "No noindex directive or robots.txt block found; the page returned successfully."
                    if not is_noindex and response_status < 400 and robots_allowed is not False
                    else "A noindex directive, robots.txt block, or unsuccessful HTTP response was detected."),
        _make_check("structured_data", "Structured data", 10, has_schema_markup,
                    "JSON-LD or microdata found." if has_schema_markup else "No JSON-LD or microdata found."),
        _make_check("image_alt", "Image alternative text", 10, not images or images_with_alt == len(images),
                    f"{images_with_alt} of {len(images)} images have non-empty alt text."),
        _make_check("mobile_language", "Mobile and language metadata", 10, has_viewport and has_lang,
                    f"Viewport {'present' if has_viewport else 'missing'}; HTML language {'present' if has_lang else 'missing'}."),
        _make_check("crawl_files", "robots.txt and sitemap.xml", 10,
                    None if robots_status is None or sitemap_status is None
                    or (sitemap_status < 400 and sitemap_valid is None)
                    else (robots_status < 400 or robots_status == 404) and sitemap_status < 400 and sitemap_valid is True,
                    f"robots.txt HTTP {robots_status if robots_status is not None else 'not checked'}; "
                    f"sitemap HTTP {sitemap_status if sitemap_status is not None else 'not checked'}"
                    f"{f' at {sitemap_url}' if sitemap_url else ''}; "
                    f"{'valid sitemap XML found' if sitemap_valid is True else 'valid sitemap XML not confirmed'}."
                    if sitemap_valid is not None else
                    f"robots.txt HTTP {robots_status if robots_status is not None else 'not checked'}; "
                    f"sitemap HTTP {sitemap_status if sitemap_status is not None else 'not checked'}"
                    f"{f' at {sitemap_url}' if sitemap_url else ''}; sitemap XML not checked."),
    ]

    local_types = {
        "localbusiness", "store", "restaurant", "professionalservice", "medicalbusiness",
        "legalservice", "realestateagent", "automotivebusiness", "foodestablishment",
        "lodgingbusiness", "financialservice", "dentist", "physician",
    }
    local_entities = [entity for entity in entities if _schema_types(entity) & local_types]
    local_entities.extend(_microdata_local_entities(soup, local_types))
    local_address_entities = [
        entity for entity in local_entities
        if isinstance(entity.get("address"), dict)
        and all(
            str(entity["address"].get(field, "")).strip()
            for field in ("streetAddress", "addressLocality", "postalCode", "addressCountry")
        )
    ]
    coordinate_entities = [
        entity for entity in local_entities
        if isinstance(entity.get("geo"), dict)
        and entity["geo"].get("latitude") is not None
        and entity["geo"].get("longitude") is not None
    ]
    service_area_entities = [
        entity for entity in local_entities
        if entity.get("areaServed") or (
            isinstance(entity.get("address"), dict)
            and (entity["address"].get("addressRegion") or entity["address"].get("addressCountry"))
        )
    ]
    same_as_entities = [entity for entity in local_entities if entity.get("sameAs")]
    review_entities = [
        entity for entity in entities
        if _schema_types(entity) & {"review", "aggregaterating"}
    ] or [
        entity for entity in local_entities
        if entity.get("aggregateRating") or entity.get("review")
    ]
    telephone_entities = [entity for entity in local_entities if str(entity.get("telephone", "")).strip()]
    business_names = [str(entity.get("name", "")).strip() for entity in local_entities if str(entity.get("name", "")).strip()]
    address_element = soup.find("address")
    has_tel_link = any(str(link.get("href", "")).lower().startswith("tel:") for link in soup.find_all("a", href=True))
    map_urls = [
        str(element.get(attribute, "")).lower()
        for element in soup.find_all(["a", "iframe"])
        for attribute in ("href", "src")
    ]
    has_map_link = any(
        domain in map_url
        for map_url in map_urls
        for domain in ("google.com/maps", "maps.google.", "maps.app.goo.gl", "g.page/")
    )
    geo_meta = any(
        str(meta.get("name", "")).lower() in {"geo.region", "geo.placename", "geo.position", "icbm"}
        and bool(str(meta.get("content", "")).strip())
        for meta in soup.find_all("meta")
    )
    visible_address = address_element is not None and bool(address_element.get_text(" ", strip=True))
    has_local_contact = bool(business_names and local_address_entities and telephone_entities)

    geo_checks = [
        _make_check("local_business_schema", "Local business structured data", 20, bool(local_entities),
                    "LocalBusiness-type JSON-LD found." if local_entities else "No LocalBusiness-type JSON-LD found."),
        _make_check("postal_address", "Complete postal address", 15, bool(local_address_entities) or visible_address,
                    "Structured street, locality and postal code or a non-empty address element found."
                    if local_address_entities or visible_address else "No complete structured address or address element found."),
        _make_check("business_phone", "Business phone", 10, bool(telephone_entities) or has_tel_link,
                    "Structured business telephone or clickable tel: link found."
                    if telephone_entities or has_tel_link else "No structured business telephone or tel: link found."),
        _make_check("map", "Map reference", 10, has_map_link,
                    "A Google Maps link or embed was found." if has_map_link else "No Google Maps link or embed found."),
        _make_check("coordinates", "Geographic coordinates", 10, bool(coordinate_entities) or geo_meta,
                    "Structured coordinates or geographic meta tags found."
                    if coordinate_entities or geo_meta else "No structured coordinates or geographic meta tags found."),
        _make_check("service_area", "Service area or region", 10, bool(service_area_entities),
                    "A structured service area or business address region was found."
                    if service_area_entities else "No structured service area or business address region found."),
        _make_check("business_identity", "Business identity and profiles", 10, bool(business_names and same_as_entities),
                    "A named local business includes sameAs profile links."
                    if business_names and same_as_entities else "No named local business with sameAs profile links found."),
        _make_check("reviews", "Review signals", 10, bool(review_entities),
                    "Review or aggregate-rating structured data found."
                    if review_entities else "No review or aggregate-rating structured data found."),
        _make_check("nap_markup", "Business name, address and phone", 5, has_local_contact,
                    "Business name, complete structured address and phone are present in local-business markup."
                    if has_local_contact else "The page does not expose a name, complete structured address and phone together."),
    ]

    seo_score, seo_coverage = _score_checks(seo_checks)
    geo_score, geo_coverage = _score_checks(geo_checks)
    limitations = [
        "This is an on-page HTML audit, not a whole-site crawl.",
        "Google Business Profile ownership, map-pack rankings, and NAP consistency across external directories are not verified.",
        "Core Web Vitals and real-user performance require a separate Lighthouse or PageSpeed Insights measurement.",
        "AI search citations and generative-engine visibility are not measured by this local GEO score.",
    ]

    return {
        "url": page_url,
        "seo_score": seo_score,
        "geo_score": geo_score,
        "seo_geo_score": round((seo_score + geo_score) / 2),
        "max_score": 100,
        "html_length": len(html_content),
        "response_status": response_status,
        "response_time_ms": response_time_ms,
        "coverage": {"seo": seo_coverage, "geo": geo_coverage},
        "scoring_breakdown": {"seo": seo_checks, "geo": geo_checks},
        "issues": {
            "seo": [check for check in seo_checks if check["passed"] is False],
            "geo": [check for check in geo_checks if check["passed"] is False],
        },
        "limitations": limitations,
    }
