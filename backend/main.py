import sys
sys.path.insert(0, "/root/europese-zoekmachine")

from api.developer_api import router as developer_router
"""Backend API for the Europese Zoekmachine."""

# Standard library imports
import os
import hmac
import json
import asyncio
import io
import hashlib
import time
from contextlib import asynccontextmanager
import traceback
from urllib.parse import urljoin, urlparse, urlsplit, urlunparse
from urllib.robotparser import RobotFileParser
import re
from typing import Any

# Third‑party imports
import httpx
import openai
import redis.asyncio as redis
from pypdf import PdfReader, errors as pypdf_errors
from bs4 import BeautifulSoup
from fastapi import (
    Depends,
    FastAPI,
    Request,
    HTTPException,
    BackgroundTasks,

)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from meilisearch_python_async import (
    Client as AsyncMeiliClient,
    errors as meili_errors,
)
from pydantic import BaseModel
from dotenv import load_dotenv
from monitoring import router as monitoring_router
from backend.seo_geo_audit import audit_seo_geo, fetch_public_page

# Local application imports

# Laad environment variables uit het .env bestand in de root directory
load_dotenv()



@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """Beheer de MeiliSearch client gedurende de levensduur van de applicatie."""
    # Haal configuratie uit environment variables
    meili_host = os.getenv("MEILI_HOST", "http://meilisearch:7700")
    meili_master_key = os.getenv("MEILI_MASTER_KEY")
    fastapi_app.state.openai_api_key = os.getenv("OPENAI_API_KEY")
    fastapi_app.state.ollama_host = os.getenv(
        "OLLAMA_HOST", "http://localhost:11434"
    )
    redis_host = os.getenv("REDIS_HOST", "redis")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_password = os.getenv("REDIS_PASSWORD")

    # Initialiseer Redis client eerst, omdat andere delen ervan afhankelijk zijn.
    if not redis_password:
        print(
            "KRITISCH: REDIS_PASSWORD is niet ingesteld. "
            "Redis-client wordt niet geïnitialiseerd."
        )
        fastapi_app.state.redis_client = None
    else:
        try:
            redis_username = os.getenv("REDIS_USERNAME", "default")
            fastapi_app.state.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                decode_responses=True,
            )
            await fastapi_app.state.redis_client.ping()
            print("Redis client geïnitialiseerd en verbonden.")
        except redis.RedisError as e: # No change needed, already correct
            print(f"KRITISCH: Kon niet verbinden met Redis: {e}")
            fastapi_app.state.redis_client = None
            raise RuntimeError(f"Could not connect to Redis: {e}") from e

    # Initialiseer de MeiliSearch client en maak deze beschikbaar in de app state.
    try:
        fastapi_app.state.meili_client = AsyncMeiliClient(
            url=meili_host,
            api_key=meili_master_key,
        )
        client = fastapi_app.state.meili_client
        try:
            fastapi_app.state.meili_index = await client.get_index("documents")
            print("MeiliSearch client en index 'documents' geïnitialiseerd.")
        except meili_errors.MeilisearchApiError as e:
            if e.code == "index_not_found":
                print("MeiliSearch index 'documents' niet gevonden, aanmaken...")
                # Het aanmaken van een index is een asynchrone taak in MeiliSearch.
                # We moeten wachten tot de taak voltooid is.
                task = await client.create_index(uid="documents", primary_key="id")
                task_uid = task.task_uid

                # Handmatig wachten tot de taak is voltooid, omdat wait_for_task niet bestaat.
                while True:
                    task_status = await client.get_task(task_uid)
                    if task_status.status in ("succeeded", "failed"):
                        break
                    await asyncio.sleep(0.1) # Wacht 100ms voor de volgende controle

                print("MeiliSearch index 'documents' succesvol aangemaakt.")

                # Nu de index gegarandeerd bestaat, kunnen we hem ophalen en configureren.
                fastapi_app.state.meili_index = await client.get_index("documents")

                # Configureer de zoekinstellingen voor de nieuwe index
                settings_task = await fastapi_app.state.meili_index.update_settings({
                    "searchableAttributes": ["title", "content"],
                    "filterableAttributes": ["url", "category"],
                })
                settings_uid = settings_task.task_uid

                # Wacht ook op het toepassen van de instellingen.
                while True:
                    task_status = await client.get_task(settings_uid)
                    if task_status.status in ("succeeded", "failed"):
                        break
                    await asyncio.sleep(0.1)
                print("MeiliSearch indexinstellingen geconfigureerd.")
            else:
                print(f"KRITISCH: MeiliSearch API fout bij initialisatie: {e}")
                fastapi_app.state.meili_client = None
                fastapi_app.state.meili_index = None
    except (
        meili_errors.MeilisearchCommunicationError, httpx.ConnectError
    ) as e:
        error_message = f"KRITISCH: Kon niet initialiseren of verbinden met MeiliSearch: {e}"
        print(error_message)
        fastapi_app.state.meili_client = None
        fastapi_app.state.meili_index = None # Zorg ervoor dat de index ook None is
        raise RuntimeError(error_message) from e
    except (TypeError, ValueError) as e: # Vang configuratie- of onverwachte fouten op
        error_message = f"KRITISCH: Onverwachte fout bij MeiliSearch client initialisatie: {e}"
        print(error_message)
        fastapi_app.state.meili_client = None
        fastapi_app.state.meili_index = None
        raise RuntimeError(error_message) from e

    # Initialiseer de OpenAI client alleen als er een API key is
    if fastapi_app.state.openai_api_key:
        fastapi_app.state.openai_client = openai.AsyncOpenAI(
            api_key=fastapi_app.state.openai_api_key
        )
        print("OpenAI client geïnitialiseerd.")


    yield


# Basis FastAPI-app initialisatie
app = FastAPI(
    title="Europese Zoekmachine Backend",
    description=(
        "De API die de frontend ondersteunt met zoekfunctionaliteit en AI-samenvattingen."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS Middleware: Sta verzoeken toe van je Vercel frontend.
# In productie wil je dit beperken tot je daadwerkelijke domein.
# Lees toegestane origins uit een omgevingsvariabele voor flexibiliteit.
# Voorbeeld: "http://localhost:3000,https://fajaede.eu"
allowed_origins_str = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
origins = [origin.strip() for origin in allowed_origins_str.split(",")]

# Vercel preview URLs hebben een specifiek patroon.
# We gebruiken een regex om alle preview-deployments veilig toe te staan.
VERCEL_PREVIEW_REGEX = (
    r"https://europese-zoekmachine-.*-martinns-projects-8d498cad\.vercel\.app"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=VERCEL_PREVIEW_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Integreer de routers met de juiste prefixes
app.include_router(monitoring_router, prefix="/monitoring", tags=["Monitoring"])
app.include_router(developer_router, prefix="/api/developers", tags=["Developers"])

@app.get("/")
def read_root():
    """Stuurt de root URL door naar de API-documentatie."""
    return RedirectResponse(url="/docs")



@app.get("/developers", response_class=HTMLResponse)
async def developer_dashboard():
    """Developer dashboard pagina."""
    with open(os.path.join(os.path.dirname(__file__), "developer_dashboard.html"), "r") as f:
        return HTMLResponse(content=f.read(), status_code=200)

@app.get("/health")
def health_check():
    """Simpel health-check endpoint dat de status van de applicatie retourneert."""
    return {"status": "ok"}


@app.get("/api/search")
async def search(request: Request, q: str, limit: int = 10, category: str = None):
    """Voert een zoekopdracht uit op de MeiliSearch index."""
    meili_index = request.app.state.meili_index
    if not meili_index:
        raise HTTPException(
            status_code=503, detail="Zoekservice is momenteel niet beschikbaar."
        )

    if not q:
        return {"results": []}

    is_sitemap_request = request.headers.get("X-Sitemap-Request") == "true"
    search_limit = 1000 if is_sitemap_request else limit

    search_params = {"limit": search_limit}
    if is_sitemap_request:
        # Voor sitemap-verzoeken hebben we alleen de URL en publicatiedatum nodig.
        # Dit vermindert de payload en de belasting op MeiliSearch.
        search_params["attributes_to_retrieve"] = ["url", "published_at"]

    try:
        if category:
            # Escape single quotes in category value to prevent MeiliSearch errors.
            safe_category = category.replace("'", "\\'")
            search_params["filter"] = [f"category = '{safe_category}'"]
            search_results = await meili_index.search(
                q, **search_params
            )
        else:
            search_results = await meili_index.search(q, **search_params)
        return {"results": search_results.hits}
    except meili_errors.MeilisearchApiError as e:
        # Specifiek de 'index_not_found' fout afvangen voor een graceful fallback.
        if e.code == "index_not_found":
            print("Index 'documents' nog niet gevonden. Geef een lege lijst terug.")
            return {"results": []}
        # Andere API-fouten kunnen ook optreden
        print(f"MeiliSearch API error: {e}")
        raise HTTPException(
            status_code=503, detail="Zoekservice is momenteel niet beschikbaar."
        ) from e
    except (meili_errors.MeilisearchCommunicationError, httpx.RequestError) as e:
        # Vang netwerkgerelateerde fouten af.
        print(f"Error connecting to MeiliSearch: {e}")
        raise HTTPException(
            status_code=503, detail="Zoekservice is momenteel niet beschikbaar."
        ) from e
    except Exception as e: # Vang alle andere onverwachte fouten af
        traceback.print_exc() # Print de volledige stack trace
        print(f"Onverwachte fout in zoekopdracht: {e}")
        raise HTTPException(
            status_code=500, detail="Er is een onverwachte fout opgetreden bij het zoeken."
        ) from e



def determine_category(url: str, title: str = "", content: str = "") -> str:
    """Bepaal categorie op basis van URL, titel en content."""
    text = f"{url} {title} {content}".lower()
    url_lower = url.lower()
    
    # News categorie - alleen als URL of titel duidelijk news bevat
    if any(term in url_lower for term in [
        "/news", "/nieuws", "/press-release", "/press_release",
        "/featured-news", "/events/", "/visual-stories"
    ]):
        return "news"
    
    # Finance categorie - check URL eerst
    if any(term in url_lower for term in [
        "/budget", "/finance", "/financial", "/economy", "/economic",
        "/funding", "/grants", "/subsidies", "/public-contracts",
        "/import-export", "/doing-business", "/funding-grants-subsidies",
        "/euro/", "/euro-en"
    ]):
        return "finance"
    
    # Default
    return "web"


class Crawler:  # pylint: disable=too-few-public-methods
    """A web crawler that respects rules and indexes content in Meilisearch."""

    def __init__(self, meili_index, redis_client):
        if not redis_client:
            raise ValueError("Redis client is niet beschikbaar voor de crawler.")
        self.redis = redis_client
        self.meili_index = meili_index
        user_agent = "FajaedeBot/1.0 (+https://www.fajaede.nl/)"

        headers = {
            "User-Agent": user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        }

        self.client = httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=30.0,
            http2=False,
        )
        self.content_hashes = set()
        self.robot_parsers = {}
        self.junk_url_patterns = [
            "/login",
            "/register",
            "/wp-login.php",
            "/wp-admin/",
            "?replytocom=",
        ]
        self.start_domain = ""  # Wordt ingesteld in de run() methode

    async def _get_robot_parser(self, url: str) -> RobotFileParser:
        """Haalt de robots.txt parser voor een domein op en cachet deze."""
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        if domain in self.robot_parsers:
            return self.robot_parsers[domain]

        rp = RobotFileParser()
        robots_url = urlunparse((parsed_url.scheme, domain, '/robots.txt', '', '', ''))
        try:
            resp = await self.client.get(robots_url, timeout=5)
            if resp.status_code == 200:
                content = resp.text
                # Debug: print eerste regels van robots.txt
                print(f"robots.txt voor {domain}: {content[:150]}...")
                rp.parse(content.splitlines())
                # Check of parser correct is geladen
                if rp.mtime() == 0:
                    print(f"Warning: robots.txt voor {domain} lijkt leeg of ongeldig")
        except httpx.RequestError as e:
            print(f"Kon robots.txt niet lezen voor {domain}: {e}")
        except Exception as e:
            print(f"Onverwachte fout bij robots.txt voor {domain}: {e}")
        # Als robots.txt niet gevonden wordt (404) of er is een fout, gaan we uit van 'allow all'.
        self.robot_parsers[domain] = rp
        return rp

    async def _is_duplicate(self, soup: BeautifulSoup) -> bool:
        """Controleert op dubbele content via content hashing."""
        text_content = soup.get_text(separator=" ", strip=True)
        if not text_content:
            return True
        content_hash = hashlib.sha256(
            text_content.encode("utf-8")
        ).hexdigest()
        # Gebruik Redis om hashes over sessies heen te onthouden
        if await self.redis.sismember("crawler:content_hashes", content_hash):
            return True
        await self.redis.sadd("crawler:content_hashes", content_hash)
        return False

    async def _process_pdf(self, url: str, pdf_content: bytes):
        """Verwerkt een PDF-bestand: extraheert tekst en indexeert deze."""
        try:
            text_content = ""
            reader = PdfReader(io.BytesIO(pdf_content))
            for page in reader.pages:
                text_content += page.extract_text() + "\n"

            if not text_content.strip():
                print(f"Overgeslagen (lege PDF): {url}")
                return

            if reader.metadata and reader.metadata.title:
                title = reader.metadata.title
            else:
                title = url.split("/")[-1]

            document = {
                "id": hashlib.sha256(url.encode()).hexdigest(),
                "url": url,
                "title": title,
                "content": text_content,
                "structured_data": {}, # PDF's hebben geen gestructureerde data
            }

            # Controleer op dubbele content voordat we indexeren
            if await self._is_duplicate(BeautifulSoup(text_content, "html.parser")):
                print(f"Overgeslagen (dubbele PDF content): {url}")
                return

            await self.meili_index.add_documents([document])
            await self.redis.sadd("crawler:indexed_urls", url)
            print(f"PDF Geïndexeerd: {url}")
        except (pypdf_errors.PdfReadError, IOError, ValueError, TypeError) as e:
            print(f"Fout bij het lezen of parsen van PDF {url}.")
            print(f"    Details: {e}")

    async def _process_page(self, url: str):  # pylint: disable=too-many-locals
        """Verwerkt een pagina: downloadt, parset, indexeert en vindt nieuwe links."""
        if await self.redis.sismember("crawler:visited_urls", url):
            return

        try:
            # Regel: Respecteer robots.txt
            robot_parser = await self._get_robot_parser(url)
            # Fix: als robots.txt niet is geladen (mtime=0), sta alles toe
            if robot_parser.mtime() == 0:
                print(f"Geen robots.txt gevonden voor {url}, sta crawling toe")
            else:
                user_agent = self.client.headers["User-Agent"]
                if not robot_parser.can_fetch(user_agent, url):
                    print(f"Uitgesloten door robots.txt: {url}")
                    return

            # Markeer URL als bezocht na de robots.txt check om race conditions te voorkomen.
            await self.redis.sadd("crawler:visited_urls", url)

            # Regel: Beleefdheidsvertraging
            await asyncio.sleep(15)  # Verhoogd om 429 Too Many Requests te verminderen

            print(f"GET request voor pagina: {url}")

            try:
                res = await self.client.get(url, timeout=30)
                print(
                    f"GET response: status={res.status_code}, "
                    f"url={url}"
                )
                res.raise_for_status()
            except httpx.HTTPStatusError as e:
                print(
                    f"GET mislukt: status={e.response.status_code}, "
                    f"url={url}"
                )
                return
            except httpx.RequestError as e:
                print(f"GET request error voor {url}: {e}")
                return

            content_type = (
                res.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )

            if content_type == "application/pdf":
                if len(res.content) > 5 * 1024 * 1024:
                    print(f"Overgeslagen: PDF groter dan 5 MB: {url}")
                    return

                await self._process_pdf(url, res.content)
                return

            if content_type not in ("text/html", "application/xhtml+xml"):
                print(
                    f"Overgeslagen: unsupported content-type "
                    f"{content_type or 'onbekend'}: {url}"
                )
                return

            print(f"HTML succesvol opgehaald: {len(res.content)} bytes")

            soup = BeautifulSoup(res.content, "html.parser")

            # Regel: Controleer op een canonieke URL.
            # Als die er is en anders is dan de huidige URL, verwerk dan de canonieke URL.
            canonical_link = soup.find("link", rel="canonical")
            if canonical_link and canonical_link.get("href"):
                canonical_url = urljoin(str(res.url), canonical_link["href"])

                # Vergelijk genormaliseerde URLs: een ontbrekende trailing slash
                # is voor de homepage geen afzonderlijke pagina.
                normal_url = url.rstrip("/") or url
                normal_canonical = canonical_url.rstrip("/") or canonical_url

                if normal_canonical != normal_url:
                    print(
                        f"Canonieke link gevonden voor {url} -> {canonical_url}"
                    )
                    await self.redis.lpush("crawler:queue", canonical_url)
                    return

                print(f"Canonieke URL komt overeen: {url} -> {canonical_url}")

            # Variabele om bij te houden of we moeten indexeren.
            should_index = True
            should_follow = True

            # Regel: Controleer op 'noindex' meta tag
            meta_robots = soup.find("meta", attrs={"name": "robots"})
            if meta_robots:
                content = meta_robots.get("content", "").lower()
                if "noindex" in content:
                    print(f"Niet indexeren ('noindex' tag): {url}")
                    should_index = False
                if "nofollow" in content:
                    print(f"Links niet volgen ('nofollow' tag): {url}")
                    should_follow = False

            if should_index:
                # Regel: Controleer op dubbele content
                if await self._is_duplicate(soup):
                    print(f"Overgeslagen (dubbel): {url}")
                    should_index = False

            if should_index:
                # Extraheer titel en content
                title = soup.title.string if soup.title else "Ongetiteld"
                content_soup = BeautifulSoup(
                    str(soup),
                    "html.parser",
                )

                for element in content_soup.select(
                    "script, style, noscript, nav, header, footer, aside, "
                    "form, .cookie, .cookie-banner, .complianz, "
                    "#cookie-law-info-bar, #comments, .comments-area, "
                    ".comment-respond, .comment-list, .related-posts, "
                    ".related, .post-navigation, .site-header, "
                    ".site-footer, .site-navigation, .main-navigation, "
                    ".elementor-location-header, .elementor-location-footer, "
                    ".elementor-nav-menu, .wp-block-navigation"
                ):
                    element.decompose()

                content_candidates = [
                    content_soup.select_one(".entry-content"),
                    content_soup.select_one(".post-content"),
                    content_soup.select_one(".wp-block-post-content"),
                    content_soup.select_one(
                        ".elementor-widget-theme-post-content"
                    ),
                    content_soup.select_one(".elementor-location-single"),
                    content_soup.select_one(".site-main article"),
                    content_soup.find("article"),
                    content_soup.find("main"),
                    content_soup.body,
                ]

                content = ""
                for content_root in content_candidates:
                    if not content_root:
                        continue

                    candidate = content_root.get_text(
                        separator="\n",
                        strip=True,
                    )
                    if len(candidate.split()) >= 100:
                        content = candidate
                        break

                if not content:
                    content = next(
                        (
                            content_root.get_text(
                                separator="\n",
                                strip=True,
                            )
                            for content_root in content_candidates
                            if content_root
                        ),
                        "",
                    )

                # Regel: Controleer op 'thin content'
                if len(content.split()) < 100:
                    print(f"Overgeslagen (te weinig content): {url}")
                else:
                    # Document voorbereiden voor Meilisearch
                    document = {
                        "id": hashlib.sha256(url.encode()).hexdigest(),
                        "url": url,
                        "title": title,
                        "content": content,
                        "category": determine_category(url, title, content),
                        "structured_data": {},  # Nieuw veld voor gestructureerde data
                    }

                    # Concept: Zoek naar gestructureerde data (JSON-LD)
                    structured_data_list = []
                    scripts = soup.find_all("script", type="application/ld+json")
                    for script in scripts:
                        try:
                            if script.string:
                                data = json.loads(script.string)
                                if data and data.get("@type") in ["FAQPage", "HowTo"]:
                                    structured_data_list.append(data)
                        except (json.JSONDecodeError, AttributeError):
                            continue  # Negeer ongeldige JSON

                    if structured_data_list:
                        document["structured_data"] = {"items": structured_data_list}
                        print(f"Gestructureerde data gevonden op: {url}")

                    try:
                        await self.meili_index.add_documents([document])
                        await self.redis.sadd("crawler:indexed_urls", url)
                        print(f"Geïndexeerd: {url}")
                    except Exception as e:
                        print(f"MEILI ERROR bij indexeren {url}: {e}")
                        import traceback
                        traceback.print_exc()

            # Voeg nieuwe links toe aan de wachtrij, tenzij 'nofollow' is ingesteld.
            if should_follow:
                base_url = str(res.url) # Gebruik de uiteindelijke URL na redirects als basis
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    # Normaliseer de URL door het fragment te verwijderen.
                    full_url = urljoin(base_url, href).split("#")[0]

                    # Regel: Volg alleen HTTP/HTTPS links en negeer andere schema's
                    # (mailto:, tel:, etc.)
                    if not full_url.startswith(("http://", "https://")):
                        continue

                    # Regel: Sla links over die eindigen op ongewenste bestandsextensies.
                    excluded_extensions = (".zip", ".xml", ".pdf", ".docx", ".xlsx",
                                           ".pptx", ".epub", ".mobi")
                    if full_url.lower().endswith(excluded_extensions):
                        continue

                    # Valideer of de link binnen het toegestane domein valt.
                    link_netloc = urlparse(full_url).netloc
                    is_in_domain = link_netloc == self.start_domain or \
                                   link_netloc.endswith(f".{self.start_domain}")

                    # Bewaar bij de EU-site alleen Engelse pagina's.
                    path = urlparse(full_url).path.lower()

                    # Vertaalde EU-pagina's eindigen doorgaans op _nl, _it, _fr, enz.
                    if re.search(r"_[a-z]{2}$", path) and not path.endswith("_en"):
                        continue

                    is_junk = any(p in full_url for p in self.junk_url_patterns)
                    if is_in_domain and not is_junk:
                        is_visited = await self.redis.sismember("crawler:visited_urls",
                                                                full_url)
                        if not is_visited:
                            await self.redis.lpush("crawler:queue", full_url)
        except httpx.RequestError as e:
            print(f"Fout bij het crawlen van {url}: {e}")
        except (AttributeError, TypeError) as e:  # Vang parsing- of contentfouten af
            # Voeg een traceback toe voor betere debugging van onverwachte contentfouten
            print(f"Fout bij het verwerken van de content van {url}:")
            traceback.print_exc()
        except httpx.HTTPStatusError as e: # Vang specifiek HTTP status fouten af
            print(f"Fout bij het verwerken van de content van {url}: {e}")
        except (meili_errors.MeilisearchError, redis.RedisError) as e:
            # Vang specifieke fouten van MeiliSearch of Redis af
            print(f"Fout in de data-laag bij verwerken van {url}: {e}")
        except (ValueError, IOError) as e:
            # Vang andere verwachte fouten af, zoals problemen met URL-parsing of I/O
            print(f"Onverwachte fout bij het verwerken van {url}: {e}")

    async def run(self, start_url: str, max_pages: int = 250000):
        """Start het crawlproces vanaf een begin-URL."""
        try:
            # Stel de 'is_running' vlag in Redis in
            await self.redis.set("crawler:is_running", "1", ex=3600) # Vlag vervalt na 1 uur

            # Bepaal het hoofddomein voor de scope van de crawl
            netloc = urlparse(start_url).netloc
            if netloc.startswith("www."):
                self.start_domain = netloc[4:]
            else:
                self.start_domain = netloc

            # Zorg ervoor dat een eventuele oude stop-vlag wordt verwijderd bij een nieuwe start.
            await self.redis.delete("crawler:stop_flag")

            # Voeg de start_url toe aan de wachtrij als deze leeg is
            if await self.redis.llen("crawler:queue") == 0:
                await self.redis.lpush("crawler:queue", start_url)

            print(f"Crawler gestart voor {start_url} met een limiet van {max_pages} pagina's.")

            crawled_count = 0
            while (url := await self.redis.rpop("crawler:queue")) and crawled_count < max_pages:
                if await self.redis.get("crawler:stop_flag"):
                    print("Stopverzoek ontvangen, crawler wordt gestopt.")
                    break
                await self._process_page(url)
                crawled_count += 1
        finally:
            # Verwijder de 'is_running' vlag, ongeacht of de crawl succesvol was of crashte.
            await self.redis.delete("crawler:is_running")
            print("Crawl-sessie voltooid.")


class CrawlRequest(BaseModel):
    """Request body model for starting a crawl."""
    url: str

@app.post("/api/crawl")
async def start_crawl(
    crawl_request: CrawlRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Endpoint om een nieuwe crawl-taak te starten op de achtergrond."""
    redis_client = request.app.state.redis_client
    meili_index = request.app.state.meili_index

    if not redis_client:
        raise HTTPException(
            status_code=503,
            detail="Kan niet crawlen: Redis is niet beschikbaar.",
        )

    if not meili_index:
        raise HTTPException(
            status_code=503,
            detail="Kan niet crawlen: MeiliSearch is niet beschikbaar.",
        )

    if await redis_client.get("crawler:is_running"):
        await redis_client.lpush("crawler:queue", crawl_request.url)
        queue_size = await redis_client.llen("crawler:queue")

        return {
            "message": (
                f"URL toegevoegd aan wachtrij (positie {queue_size}). "
                "Crawl is al bezig."
            ),
            "queue_size": queue_size,
            "status": "queued",
        }

    crawler = Crawler(meili_index, redis_client)
    background_tasks.add_task(crawler.run, crawl_request.url)

    return {
        "message": f"Crawl-taak voor {crawl_request.url} is gestart.",
        "queue_size": 1,
        "status": "started",
    }


def require_admin_key(request: Request) -> None:
    expected_key = os.getenv("ADMIN_API_KEY")
    provided_key = request.headers.get("X-Admin-Key")

    if not expected_key or not provided_key:
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required",
        )

    if not hmac.compare_digest(provided_key, expected_key):
        raise HTTPException(
            status_code=403,
            detail="Invalid admin key",
        )


@app.post("/api/crawl/stop")
async def stop_crawl(request: Request):
    """Stelt een vlag in Redis in om de actieve crawler netjes te stoppen."""
    require_admin_key(request)
    redis_client = request.app.state.redis_client
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis is niet beschikbaar.")

    # Stel een stop-vlag in met een timeout om te voorkomen dat deze permanent blijft.
    await redis_client.set("crawler:stop_flag", "1", ex=3600)  # Vlag vervalt na 1 uur

    return {
        "message": ("Stopverzoek verzonden. De crawler stopt na het verwerken "
                    "van de huidige pagina.")
    }


@app.post("/api/crawl/reset")
async def reset_crawl(request: Request):
    """Stopt de crawler en wist de crawlstatus en zoekindex."""
    require_admin_key(request)

    redis_client = request.app.state.redis_client
    meili_index = request.app.state.meili_index

    if not redis_client or not meili_index:
        raise HTTPException(
            status_code=503,
            detail="Redis of MeiliSearch is niet beschikbaar.",
        )

    await redis_client.set("crawler:stop_flag", "1", ex=60)
    await asyncio.sleep(2)

    await redis_client.delete(
        "crawler:queue",
        "crawler:visited_urls",
        "crawler:content_hashes",
        "crawler:indexed_urls",
    )

    await meili_index.delete_all_documents()
    await redis_client.delete("crawler:stop_flag")

    return {
        "message": (
            "Crawler is gereset: wachtrij en zoekindex "
            "zijn leeggemaakt."
        )
    }


@app.get("/api/crawl/status")
async def get_crawl_status(request: Request):
    """Endpoint om de huidige status van de crawler op te vragen."""
    redis_client = request.app.state.redis_client
    if not redis_client:
        raise HTTPException(
            status_code=503,
            detail="Kan de crawler-status niet ophalen: Redis is niet beschikbaar.",
        )

    is_running = await redis_client.get("crawler:is_running") == "1"
    pages_in_queue = await redis_client.llen("crawler:queue")
    pages_visited = await redis_client.scard("crawler:visited_urls")
    indexed_pages = await redis_client.scard("crawler:indexed_urls")

    return {
        "status": "actief" if is_running else "inactief",
        "pages_in_queue": pages_in_queue,
        "pages_visited": pages_visited,
        "unique_pages_indexed": indexed_pages,
    }


def _prepare_chat_history(history: str = None) -> list[dict]:
    """Helper om de JSON history string te parsen en op te schonen."""
    if not history:
        return []

    try:
        raw_history = json.loads(history)
    except json.JSONDecodeError:
        return []

    chat_history = []
    for msg in raw_history:
        is_valid_role = isinstance(msg, dict) and \
                        msg.get("role") in [
            "user", "assistant"
        ]
        is_thinking_placeholder = (msg.get("content") ==
                                 "fajaedeAI+ is aan het denken...")
        if is_valid_role and not is_thinking_placeholder:
            chat_history.append({"role": msg["role"], "content": msg["content"]})
    return chat_history


async def _fetch_context(request: Request, q: str) -> str:
    """Haal tot maximaal vijf zoekresultaten op en formatteer ze als context."""
    meili_index = request.app.state.meili_index
    if not meili_index:
        raise HTTPException(
            status_code=503, detail="Zoekservice is momenteel niet beschikbaar."
        )

    try:
        # search_results is een SearchResults object
        search_results = await meili_index.search(q, limit=5)
        hits = search_results.hits  # Toegang tot hits via attribuut
        parts: list[str] = []
        for i, hit in enumerate(hits):
            title = hit.get("title", "")
            summary = hit.get("summary", hit.get("content", ""))[:300]
            parts.append(f"Bron {i+1}: {title}\n{summary}")
        if not parts:
            # Geef een lege context terug als er geen resultaten zijn.
            # De LLM zal dan de instructie volgen om aan te geven dat er niets is gevonden.
            return ""
        return "\n\n".join(parts)
    except meili_errors.MeilisearchApiError as e:
        print(f"MeiliSearch API error in _fetch_context: {e}")
        raise HTTPException(
            status_code=503, detail="Kon geen zoekresultaten ophalen voor de AI‑samenvatting."
        ) from e
    except (meili_errors.MeilisearchCommunicationError, httpx.RequestError) as e:
        print(f"Error connecting to MeiliSearch in _fetch_context: {e}")
        raise HTTPException(
            status_code=503, detail="Kon geen zoekresultaten ophalen voor de AI‑samenvatting."
        ) from e
    except Exception as e:  # Catch any other unexpected errors
        print(f"Onverwachte fout bij ophalen context van MeiliSearch: {e}")
        detail_text = "Kon geen zoekresultaten ophalen voor de AI‑samenvatting."
        raise HTTPException(status_code=503, detail=detail_text) from e


def _build_system_prompt() -> str:
    """Return the static system prompt used for AI‑samenvatting."""
    part1 = (
        "Je bent een AI-assistent die vragen beantwoordt op basis van "
        "genummerde zoekresultaten. Jouw taak is om de vraag van de "
        "gebruiker te beantwoorden door de verstrekte context samen te vatten. "
        "Houd je aan de volgende regels:\n" # noqa: E501
    )
    part2 = (
        "1. Gebruik de informatie in de Context als basis. Vul aan met algemene kennis als de context beperkt is.\n"        "2. Geef altijd een nuttig antwoord. Gebruik de fallback alleen als de context werkelijk niets zegt over het onderwerp.\n"
    )
    part3 = ("3. Structureer je antwoord als een FAQ of How‑To als de context dit toelaat. "
             "Gebruik Markdown.\n"
             "4. Voeg aan het einde van ELKE zin een citaat toe met de bronnummers. "
             "Bijvoorbeeld: 'Dit is een feit. [1, 3]'\n")
    part4 = ("5. Combineer citaten. Bijvoorbeeld: [1, 2].\n"
             "6. Schrijf in een heldere, feitelijke en neutrale toon.\n"
             "7. Antwoord altijd in de taal van de vraag van de gebruiker.")
    return part1 + part2 + part3 + part4


def _build_user_prompt(context: str, q: str) -> str:
    """Compose the user prompt incorporating context and question."""
    prompt_text = (
        f"Context:\n---\n{context}\n---\n\n"
        "Beantwoord de volgende vraag op basis van bovenstaande context:\n"
        f"{q}")
    return prompt_text



@app.get("/api/benchmark/seo-geo")
async def benchmark_seo_geo(url: str = None):
    """Return separate on-page SEO and local GEO audit scores."""
    if url:
        try:
            started_at = time.monotonic()
            html, final_url, response_status, html_bytes = await fetch_public_page(url)
            page_response_time_ms = round((time.monotonic() - started_at) * 1000)
            site_root = f"{urlsplit(final_url).scheme}://{urlsplit(final_url).netloc}/"

            async def probe(target: str) -> tuple[int | None, str]:
                try:
                    content, _, status_code, _ = await fetch_public_page(
                        urljoin(site_root, target),
                        timeout_seconds=3.0,
                        max_bytes=200_000,
                        expect_html=False,
                        allow_http_errors=True,
                    )
                    return status_code, content
                except (httpx.RequestError, ValueError):
                    return None, ""

            robots_status, robots_content = await probe("robots.txt")
            robots_allowed = None
            if robots_status is not None and robots_status < 400:
                robots_parser = RobotFileParser()
                robots_parser.parse(robots_content.splitlines())
                robots_allowed = robots_parser.can_fetch("FajaedeAuditBot", final_url)

            declared_sitemaps = [
                line.split(":", 1)[1].strip()
                for line in robots_content.splitlines()
                if line.lower().startswith("sitemap:") and line.split(":", 1)[1].strip()
            ][:3]
            sitemap_targets = declared_sitemaps or ["sitemap.xml", "sitemap_index.xml", "wp-sitemap.xml"]
            sitemap_results = await asyncio.gather(*(probe(target) for target in sitemap_targets))
            sitemap_match = next(
                (
                    (target, result)
                    for target, result in zip(sitemap_targets, sitemap_results)
                    if result[0] is not None and result[0] < 400
                ),
                None,
            )
            if sitemap_match:
                sitemap_url = urljoin(site_root, sitemap_match[0])
                sitemap_status = sitemap_match[1][0]
                sitemap_content = sitemap_match[1][1]
            else:
                sitemap_match = next(
                    (
                        (target, result)
                        for target, result in zip(sitemap_targets, sitemap_results)
                        if result[0] is not None
                    ),
                    None,
                )
                sitemap_url = urljoin(site_root, sitemap_match[0]) if sitemap_match else None
                sitemap_status = sitemap_match[1][0] if sitemap_match else None
                sitemap_content = sitemap_match[1][1] if sitemap_match else ""
            audit = audit_seo_geo(
                html,
                final_url,
                response_status=response_status,
                response_time_ms=page_response_time_ms,
                robots_status=robots_status,
                sitemap_status=sitemap_status,
                sitemap_url=sitemap_url,
                sitemap_valid=(
                    None if sitemap_status is None
                    else sitemap_content if sitemap_status < 400
                    else False
                ),
                robots_allowed=robots_allowed,
            )
            audit["html_bytes"] = html_bytes
            return audit
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="The website did not respond within the audit timeout.") from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail=f"The website returned HTTP {exc.response.status_code}.") from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail="The website could not be fetched for an audit.") from exc
    else:
        test_cases = [
            {
                "name": "EU institutionele pagina",
                "html": """<html lang="en"><head>
                <title>European Commission - Official EU Policies and Services</title>
                <meta name="description" content="Explore European Commission policies, services and information for citizens and businesses across the European Union.">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <link rel="canonical" href="https://commission.europa.eu/">
                </head><body><h1>European Commission</h1><h2>Policies and services</h2>
                <p>Official information and services for citizens and businesses across Europe.</p>
                <script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"European Commission"}</script>
                <img src="/logo.png" alt="European Commission logo"></body></html>""",
                "url": "https://commission.europa.eu"
            },
            {
                "name": "Simpele pagina zonder SEO",
                "html": "<html><body><p>Hello world</p></body></html>",
                "url": "https://example.com"
            }
        ]

        results: list[dict[str, Any]] = []
        for case in test_cases:
            audit = audit_seo_geo(case["html"], case["url"])
            results.append({
                "name": case["name"],
                "seo_score": audit["seo_score"],
                "geo_score": audit["geo_score"],
                "seo_geo_score": audit["seo_geo_score"],
                "max_score": audit["max_score"],
                "scoring_breakdown": audit["scoring_breakdown"],
                "issues": audit["issues"],
                "coverage": audit["coverage"],
            })

        return {
            "benchmark": "Separate SEO and local GEO audit examples",
            "results": results,
            "note": "Add ?url=https://... to run a live, single-page audit.",
        }


@app.get("/api/summarize")
async def summarize(
    request: Request,
    q: str,
    chat_history: list[dict] = Depends(_prepare_chat_history),
) -> dict:
    """Genereert een AI‑samenvatting op basis van zoekresultaten en conversatiegeschiedenis.

    De logica is opgesplitst in kleinere helpers om Pylint‑regels
    C0301 (lijn te lang) en R0914 (te veel locale variabelen) te vermijden.
    """
    if not q:
        return {"ai": "Stel een vraag om een AI‑samenvatting te krijgen."}

    # Controleer of de OpenAI client beschikbaar is.
    if not hasattr(request.app.state, "openai_client") or not request.app.state.openai_client:
        raise HTTPException(
            status_code=503, detail="AI-dienst is momenteel niet beschikbaar."
        )

    # 1. Context ophalen via MeiliSearch
    context = await _fetch_context(request, q)

    # 2. Prompt‑delen samenstellen
    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(context, q)

    # 3. LLM‑aanroep (OpenAI → Ollama fallback)
    response = await _call_llm(request, system_prompt, user_prompt, chat_history)
    return {"ai": response}


async def _call_llm(
    request: Request,
    system_prompt: str,
    user_prompt: str,
    chat_history: list[dict],
) -> str:
    """Attempt OpenAI call, fallback to Ollama, raise on failure."""
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(chat_history)
    messages.append({"role": "user", "content": user_prompt})
    try:
        if hasattr(request.app.state, "openai_client"):
            client = request.app.state.openai_client
            chat_completion = await (
                client.chat.completions.create(
                    messages=messages,
                    model="gpt-3.5-turbo",
                    temperature=0.3,
                )
            )
            return chat_completion.choices[0].message.content
        raise openai.APIError(
            "OpenAI client niet geconfigureerd.", request=None, body=None
        )
    except openai.APIError as e:
        print(f"OpenAI API call mislukt: {e}. Fallback naar lokaal model.")
        if hasattr(request.app.state, "ollama_host"):
            try:
                async with openai.AsyncOpenAI(
                    base_url=f"{request.app.state.ollama_host}/v1",
                    api_key="ollama",
                ) as client:
                    chat_completion = await client.chat.completions.create(
                        model="phi3:mini", messages=messages
                    )
                    return chat_completion.choices[0].message.content
            except openai.APIError as ollama_error:
                print(f"Ollama fallback ook mislukt: {ollama_error}")
        raise HTTPException(
            status_code=503, detail="AI‑diensten zijn momenteel niet beschikbaar."
        ) from e

# Serve dashboard

@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard():
    """Modern monitoring dashboard."""
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    return FileResponse(dashboard_path)


# ============================================
# SERVER-SIDE SEO RENDERING
# ============================================

@app.get("/seo/render")
async def render_page_with_seo(
    content: str,
    url: str = None,
    language: str = "nl",
    template: str = "default"
):
    """
    Genereert een complete HTML pagina met SEO metadata server-side.
    Perfect voor statische pages met automatische SEO.
    """
    try:
        # Genereer SEO data via OpenAI
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        prompt = f"Genereer SEO metadata (taal: {language}). Content: {content[:1000]}. Return JSON: {{title, meta_description, keywords, score}}"
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "SEO expert. Return ALLEEN JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        import json
        seo_data = json.loads(response.choices[0].message.content)
        
        # Kies template
        if template == "default":
            html = generate_default_template(seo_data, content, url)
        elif template == "minimal":
            html = generate_minimal_template(seo_data, content)
        else:
            html = generate_default_template(seo_data, content, url)
        
        return HTMLResponse(content=html, status_code=200)
        
    except Exception as e:
        print(f"SEO render fout: {e}")
        # Fallback naar simpele template
        words = content.split()[:10]
        title = " ".join(words)
        html = f"""<!DOCTYPE html>
<html lang="{language}">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <meta name="description" content="{content[:160]}">
</head>
<body>
    <article>{content}</article>
</body>
</html>"""
        return HTMLResponse(content=html, status_code=200)


def generate_default_template(seo_data: dict, content: str, url: str = None):
    """Genereert een complete HTML pagina met SEO metadata."""
    title = seo_data.get("title", "Pagina")[:60]
    description = seo_data.get("meta_description", "")[:160]
    keywords = ", ".join(seo_data.get("keywords", []))
    score = seo_data.get("score", 0)
    
    url = url or "https://api.fajaede.eu"
    
    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    
    <!-- SEO Metadata -->
    <title>{title}</title>
    <meta name="description" content="{description}">
    <meta name="keywords" content="{keywords}">
    <meta name="robots" content="index, follow">
    
    <!-- Open Graph / Social Media -->
    <meta property="og:title" content="{title}">
    <meta property="og:description" content="{description}">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{url}">
    <meta property="og:site_name" content="Europese Zoekmachine">
    
    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="{title}">
    <meta name="twitter:description" content="{description}">
    
    <!-- Canonical URL -->
    <link rel="canonical" href="{url}">
    
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 40px 20px;
            line-height: 1.6;
            color: #333;
        }}
        header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            border-radius: 12px;
            margin-bottom: 40px;
            text-align: center;
        }}
        header h1 {{
            margin: 0;
            font-size: 2.5em;
        }}
        article {{
            background: #f8f9fa;
            padding: 30px;
            border-radius: 8px;
            border-left: 4px solid #667eea;
        }}
        .seo-badge {{
            display: inline-block;
            background: #4caf50;
            color: white;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12px;
            margin-top: 10px;
        }}
        footer {{
            margin-top: 60px;
            padding-top: 20px;
            border-top: 2px solid #e0e0e0;
            text-align: center;
            color: #666;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <header>
        <h1>{title}</h1>
        <span class="seo-badge">✅ SEO Score: {score}/100</span>
    </header>
    
    <article>
        {content}
    </article>
    
    <footer>
        <p>Gegenereerd met Europese Zoekmachine API</p>
        <p>SEO Score: {score}/100 | Keywords: {keywords}</p>
    </footer>
</body>
</html>"""


def generate_minimal_template(seo_data: dict, content: str):
    """Genereert een minimale HTML pagina."""
    title = seo_data.get("title", "Pagina")[:60]
    description = seo_data.get("meta_description", "")[:160]
    
    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <meta name="description" content="{description}">
</head>
<body>
    <article>
        <h1>{title}</h1>
        {content}
    </article>
</body>
</html>"""
