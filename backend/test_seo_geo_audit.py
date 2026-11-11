import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend.seo_geo_audit import audit_seo_geo, _resolve_public_url, fetch_public_resource


class SeoGeoAuditTests(unittest.TestCase):
    def test_complete_page_gets_separate_full_scores(self):
        visible_words = " ".join(["Useful page content for visitors."] * 60)
        html = f"""<!doctype html>
        <html lang="nl"><head>
          <title>Voorbeeld Bedrijf | Lokale diensten in Amsterdam</title>
          <meta name="description" content="Ontdek onze lokale diensten in Amsterdam. Neem contact op voor deskundig advies, duidelijke prijzen en persoonlijke service voor uw bedrijf.">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <link rel="canonical" href="https://example.com/">
          <script type="application/ld+json">
          {{"@context":"https://schema.org","@type":"LocalBusiness","name":"Voorbeeld Bedrijf",
           "telephone":"+31201234567","address":{{"@type":"PostalAddress","streetAddress":"Voorbeeldstraat 1",
           "addressLocality":"Amsterdam","postalCode":"1000 AA","addressCountry":"NL"}},
           "geo":{{"@type":"GeoCoordinates","latitude":52.37,"longitude":4.89}},
           "areaServed":"Amsterdam","sameAs":["https://www.linkedin.com/company/example"],
           "aggregateRating":{{"@type":"AggregateRating","ratingValue":"4.8","reviewCount":"24"}}}}
          </script>
        </head><body>
          <h1>Voorbeeld Bedrijf</h1><h2>Onze diensten</h2>
          <p>{visible_words}</p>
          <img src="/logo.png" alt="Logo van Voorbeeld Bedrijf">
          <a href="tel:+31201234567">Bel ons</a>
          <a href="https://maps.google.com/?q=Amsterdam">Bekijk kaart</a>
          <address>Voorbeeldstraat 1, 1000 AA Amsterdam</address>
        </body></html>"""

        result = audit_seo_geo(
            html,
            "https://example.com/",
            response_status=200,
            response_time_ms=250,
            robots_status=200,
            sitemap_status=200,
            sitemap_valid="<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'></urlset>",
        )

        self.assertEqual(result["seo_score"], 100)
        self.assertEqual(result["geo_score"], 100)
        self.assertEqual(result["seo_geo_score"], 100)
        self.assertEqual(result["coverage"], {"seo": 100, "geo": 100})
        self.assertEqual(result["issues"], {"seo": [], "geo": []})

    def test_invalid_sitemap_xml_fails_the_crawl_files_check(self):
        result = audit_seo_geo(
            "<html><body>Content</body></html>",
            "https://example.com/",
            robots_status=200,
            sitemap_status=200,
            sitemap_valid="<html>Not a sitemap</html>",
        )

        crawl_check = next(
            check for check in result["scoring_breakdown"]["seo"]
            if check["code"] == "crawl_files"
        )
        self.assertFalse(crawl_check["passed"])
        self.assertIn("valid sitemap XML not confirmed", crawl_check["details"])

    def test_generic_location_keywords_do_not_increase_geo_score(self):
        result = audit_seo_geo(
            "<html><body><p>location address city country region map local</p></body></html>",
            "https://example.com/",
        )

        self.assertEqual(result["geo_score"], 0)
        self.assertEqual(result["coverage"]["geo"], 100)
        self.assertEqual(result["coverage"]["seo"], 90)

    def test_noindex_and_robots_block_are_reported_as_indexability_failures(self):
        result = audit_seo_geo(
            '<html><head><meta name="robots" content="noindex"></head><body></body></html>',
            "https://example.com/",
            robots_allowed=False,
        )

        indexability = next(
            check for check in result["scoring_breakdown"]["seo"]
            if check["code"] == "indexability"
        )
        self.assertFalse(indexability["passed"])
        self.assertIn("noindex", indexability["details"])

    def test_local_business_microdata_is_recognized(self):
        html = """<html><body>
        <div itemscope itemtype="https://schema.org/LocalBusiness">
          <span itemprop="name">Voorbeeld Bedrijf</span>
          <a itemprop="telephone" href="tel:+31201234567">Bel</a>
          <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
            <span itemprop="streetAddress">Voorbeeldstraat 1</span>
            <span itemprop="addressLocality">Amsterdam</span>
            <span itemprop="postalCode">1000 AA</span>
            <meta itemprop="addressCountry" content="NL">
          </div>
        </div></body></html>"""

        result = audit_seo_geo(html, "https://example.com/")

        geo_checks = {check["code"]: check for check in result["scoring_breakdown"]["geo"]}
        self.assertTrue(geo_checks["local_business_schema"]["passed"])
        self.assertTrue(geo_checks["postal_address"]["passed"])
        self.assertTrue(geo_checks["business_phone"]["passed"])

    def test_invalid_or_private_urls_are_rejected(self):
        for url in (
            "ftp://example.com/",
            "http://localhost/",
            "http://127.0.0.1/",
            "http://127.1/",
            "http://169.254.169.254/",
            "http://100.64.0.1/",
            "http://[::ffff:127.0.0.1]/",
            "http://192.168.1.1/",
            "http://user:password@example.com/",
            "http://example.com:8080/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _resolve_public_url(url)


class PublicFetchSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_to_private_address_is_rejected_before_second_request(self):
        requests = []

        async def handler(request):
            requests.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

        async def validate_url(value):
            if "127.0.0.1" in value:
                raise ValueError("Private and local addresses cannot be audited.")
            return value

        original_client = httpx.AsyncClient

        def create_mock_client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original_client(**kwargs)

        with patch("backend.seo_geo_audit.validate_public_url", new=AsyncMock(side_effect=validate_url)), \
             patch("backend.seo_geo_audit.httpx.AsyncClient", side_effect=create_mock_client):
            with self.assertRaisesRegex(ValueError, "Private and local"):
                await fetch_public_resource("https://example.com/")

        self.assertEqual(requests, ["https://example.com/"])

    async def test_download_size_is_bounded_even_without_content_length(self):
        original_client = httpx.AsyncClient

        def create_mock_client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * 32)
            )
            return original_client(**kwargs)

        with patch("backend.seo_geo_audit.validate_public_url", new=AsyncMock(side_effect=lambda value: value)), \
             patch("backend.seo_geo_audit.httpx.AsyncClient", side_effect=create_mock_client):
            with self.assertRaisesRegex(ValueError, "size limit"):
                await fetch_public_resource("https://example.com/", max_bytes=8)


if __name__ == "__main__":
    unittest.main()
