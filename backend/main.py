"""Backend API for the Europese Zoekmachine."""

# Standard library imports
import os
import json
import asyncio
import io
import hashlib
from contextlib import asynccontextmanager
import traceback
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

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
from fastapi.responses import RedirectResponse
from meilisearch_python_async import (
    Client as AsyncMeiliClient,
    errors as meili_errors,
)
from pydantic import BaseModel
from dotenv import load_dotenv

# Local application imports
from api.routes.generate_seo import router as seo_router
from api.routes.builder import router as builder_router

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

    # Initialiseer een lock en een placeholder voor de crawler-instantie
    fastapi_app.state.crawler_lock = asyncio.Lock()
    fastapi_app.state.crawler_instance = None

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

# Integreer de SEO-generator route
app.include_router(seo_router)
app.include_router(builder_router)

@app.get("/")
def read_root():
    """Stuurt de root URL door naar de API-documentatie."""
    return RedirectResponse(url="/docs")


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

    # Gebruik de meegegeven limit, maar overschrijf voor sitemap-verzoeken.
    search_limit = 1000 if request.headers.get("X-Sitemap-Request") else limit

    try:
        if category:
            # Escape single quotes in category value to prevent MeiliSearch errors.
            safe_category = category.replace("'", "\\'")
            search_results = await meili_index.search(
                q, limit=search_limit, filter=[f"category = '{safe_category}'"]
            )
        else:
            search_results = await meili_index.search(q, limit=search_limit)
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


class Crawler:  # pylint: disable=too-few-public-methods
    """A web crawler that respects rules and indexes content in Meilisearch."""

    def __init__(self, meili_index, redis_client):
        if not redis_client:
            raise ValueError("Redis client is niet beschikbaar voor de crawler.")
        self.redis = redis_client
        self.meili_index = meili_index
        # Gebruik een standaard browser User-Agent om 403 Forbidden-fouten te voorkomen.
        # Veel websites blokkeren onbekende of custom bot User-Agents.
        # De volgorde van headers kan ook van belang zijn voor botdetectie.
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,image/apng,*/*;q=0.8,"
                      "application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "nl-BE,nl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "DNT": "1", # Do Not Track
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
        }
        self.client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10)
        self.content_hashes = set()
        self.robot_parsers = {}
        self.junk_url_patterns = ["/login", "/register", "?replytocom="]
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
                rp.parse(resp.text.splitlines())
        except httpx.RequestError as e:
            print(f"Kon robots.txt niet lezen voor {domain}: {e}")
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
            if not robot_parser.can_fetch(self.client.headers["User-Agent"], url):
                print(f"Uitgesloten door robots.txt: {url}")
                return

            # Markeer URL als bezocht na de robots.txt check om race conditions te voorkomen.
            await self.redis.sadd("crawler:visited_urls", url)

            # Regel: Beleefdheidsvertraging
            await asyncio.sleep(5) # Verhoogd om 429 Too Many Requests te verminderen

            # Regel: Gebruik HEAD om content type te checken
            # Implementeer een retry-mechanisme voor 429-fouten
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    head_res = await self.client.head(url, timeout=10)
                    head_res.raise_for_status()
                    break  # Succes, verlaat de loop
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429 and attempt < max_retries - 1:
                        wait_time = 2 ** (attempt + 1)  # Exponentiële backoff: 2, 4, 8s
                        print(
                            f"429 Too Many Requests voor {url}. "
                            f"Wacht {wait_time}s voor poging {attempt + 2}..."
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        raise  # Geef de fout door na de laatste poging of bij andere HTTP-fouten
            else: # Wordt uitgevoerd als de for-loop zonder 'break' eindigt
                return # Stop verwerking als alle retries falen
            content_type = head_res.headers.get("Content-Type", "")
            content_length = int(head_res.headers.get("Content-Length", 0))

            # Bepaal het pad op basis van content type
            if "application/pdf" in content_type:
                if content_length > 5 * 1024 * 1024: # 5MB limiet
                    print(f"Overgeslagen (PDF te groot: {content_length / 1024 / 1024:.2f}MB): {url}")
                    return
                # Langere timeout voor PDF's
                res = await self.client.get(url, timeout=30)
                res.raise_for_status()
                await self._process_pdf(url, res.content)
                return # Stop verdere verwerking voor PDF's
            elif "text/html" not in content_type:
                print(f"Overgeslagen (geen HTML of PDF): {url}")
                return

            # Download de daadwerkelijke pagina
            res = await self.client.get(url, timeout=10)
            res.raise_for_status()

            soup = BeautifulSoup(res.content, "html.parser")

            # Regel: Controleer op een canonieke URL.
            # Als die er is en anders is dan de huidige URL, verwerk dan de canonieke URL.
            canonical_link = soup.find("link", rel="canonical")
            if canonical_link and canonical_link.get("href"):
                canonical_url = urljoin(str(res.url), canonical_link["href"])
                if canonical_url != url:
                    print(
                        f"Canonieke link gevonden voor {url} -> {canonical_url}"
                    )
                    # Voeg de canonieke URL toe aan de wachtrij om te verwerken.
                    await self.redis.lpush("crawler:queue", canonical_url)
                    return # Stop met het verwerken van de huidige (niet-canonieke) URL.

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
                    print(f"Overgeslagen (dubbele content): {url}")
                    should_index = False

            if should_index:
                # Extraheer titel en content
                title = soup.title.string if soup.title else "Ongetiteld"
                # Verwijder script en style tags voor schonere content
                for script_or_style in soup(["script", "style"]):
                    script_or_style.decompose()
                content = soup.get_text(separator="\n", strip=True)

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

                    await self.meili_index.add_documents([document])
                    print(f"Geïndexeerd: {url}")

            # Voeg nieuwe links toe aan de wachtrij, tenzij 'nofollow' is ingesteld.
            if should_follow:
                base_url = str(res.url) # Gebruik de uiteindelijke URL na redirects als basis
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    # Normaliseer de URL door het fragment te verwijderen.
                    full_url = urljoin(base_url, href).split("#")[0]

                    # Valideer of de link binnen het toegestane domein valt.
                    link_netloc = urlparse(full_url).netloc
                    is_in_domain = (
                        link_netloc == self.start_domain
                        or link_netloc.endswith(f".{self.start_domain}")
                    )

                    is_junk = any(p in full_url for p in self.junk_url_patterns)
                    if is_in_domain and not is_junk:
                        is_visited = await self.redis.sismember("crawler:visited_urls", full_url)
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
        # Bepaal het hoofddomein voor de scope van de crawl
        # Strip 'www.' to crawl all subdomains of the root domain.
        netloc = urlparse(start_url).netloc
        if netloc.startswith("www."):
            self.start_domain = netloc[4:]
        else:
            self.start_domain = netloc

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

        print("Crawl-sessie voltooid.")


class CrawlRequest(BaseModel):
    """Request body model for starting a crawl."""
    url: str

@app.post("/api/crawl")
async def start_crawl(crawl_request: CrawlRequest, request: Request, background_tasks: BackgroundTasks):
    """Endpoint om een nieuwe crawl-taak te starten op de achtergrond."""
    # Controleer of de benodigde services beschikbaar zijn.
    if not request.app.state.redis_client:
        raise HTTPException(
            status_code=503,
            detail="Kan niet crawlen: Redis is niet beschikbaar.",
        )
    if not request.app.state.meili_index:
        raise HTTPException(
            status_code=503,
            detail="Kan niet crawlen: MeiliSearch is niet beschikbaar.",
        )

    # Probeer de lock te verkrijgen zonder te wachten.
    if not request.app.state.crawler_lock.locked():
        async with request.app.state.crawler_lock:
            # Creëer en start de crawler.
            crawler = Crawler(
                request.app.state.meili_index, request.app.state.redis_client
            )
            request.app.state.crawler_instance = crawler
            background_tasks.add_task(crawler.run, crawl_request.url)
            return {"message": f"Crawl-taak voor {crawl_request.url} is gestart."}
    else:
        # Als de lock al bezet is, is er al een crawl-taak actief.
        return {
            "message": "Een crawl-taak is al actief. Wacht tot deze is voltooid."
        }


@app.post("/api/crawl/stop")
async def stop_crawl(request: Request):
    """Stelt een vlag in Redis in om de actieve crawler netjes te stoppen."""
    redis_client = request.app.state.redis_client
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis is niet beschikbaar.")

    # Stel een stop-vlag in met een timeout om te voorkomen dat deze permanent blijft.
    await redis_client.set("crawler:stop_flag", "1", ex=3600)  # Vlag vervalt na 1 uur

    return {
        "message": "Stopverzoek verzonden. De crawler stopt na het verwerken van de huidige pagina."
    }


@app.post("/api/crawl/reset")
async def reset_crawl(request: Request):
    """Stopt de crawler en wist de wachtrij, bezochte URLs en de MeiliSearch-index."""
    redis_client = request.app.state.redis_client
    meili_index = request.app.state.meili_index

    if not redis_client or not meili_index:
        raise HTTPException(
            status_code=503, detail="Redis of MeiliSearch is niet beschikbaar."
        )

    # 1. Stop de actieve crawler
    await redis_client.set("crawler:stop_flag", "1", ex=60)

    # 2. Wacht even om de lock vrij te geven als de crawler actief was
    await asyncio.sleep(2)

    # 3. Wis alle crawler-gerelateerde data in Redis
    await redis_client.delete("crawler:queue", "crawler:visited_urls", "crawler:content_hashes")

    # 4. Wis alle documenten in de MeiliSearch-index
    await meili_index.delete_all_documents()

    return {"message": "Crawler is gereset: wachtrij en zoekindex zijn leeggemaakt."}


@app.get("/api/crawl/status")
async def get_crawl_status(request: Request):
    """Endpoint om de huidige status van de crawler op te vragen."""
    redis_client = request.app.state.redis_client
    if not redis_client:
        raise HTTPException(
            status_code=503,
            detail="Kan de crawler-status niet ophalen: Redis is niet beschikbaar.",
        )

    is_running = request.app.state.crawler_lock.locked()
    pages_in_queue = await redis_client.llen("crawler:queue")
    pages_visited = await redis_client.scard("crawler:visited_urls")
    content_hashes = await redis_client.scard("crawler:content_hashes")

    return {
        "status": "actief" if is_running else "inactief",
        "pages_in_queue": pages_in_queue,
        "pages_visited": pages_visited,
        "unique_pages_indexed": content_hashes,
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
        is_valid_role = isinstance(msg, dict) and msg.get("role") in [
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
        search_results = await meili_index.search(q, limit=5) # search_results is een SearchResults object
        hits = search_results.hits # Toegang tot hits via attribuut, niet via dictionary sleutel
        parts: list[str] = []
        for i, hit in enumerate(hits):
            title = hit.get("title", "")
            summary = hit.get("summary", hit.get("content", ""))[:300]
            parts.append(f"Bron {i+1}: {title}\n{summary}")
        if not parts:
            return (
                "Geen zoekresultaten gevonden. Beantwoord de vraag op basis van "
                "algemene kennis, maar vermeld dat er geen specifieke resultaten "
                "in de zoekindex beschikbaar zijn."
            )
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
        "1. Baseer je antwoord UITSLUITEND op de informatie in de 'Context'. "
        "Verzin geen informatie.\n"
        "2. Als de context geen antwoord bevat, zeg dan: 'De zoekresultaten "
        "bevatten onvoldoende informatie om deze vraag te beantwoorden.'\n"
    )
    part3 = (
        "3. Structureer je antwoord als een FAQ of How‑To als de context dit "
        "toelaat. Gebruik Markdown.\n" # noqa: E501
        "4. Voeg aan het einde van ELKE zin een citaat toe met de bronnummers. " # noqa: E501
        "Bijvoorbeeld: 'Dit is een feit. [1, 3]'\n" # noqa: E501
    )
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
