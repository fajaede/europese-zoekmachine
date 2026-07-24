"""Backend API for the Europese Zoekmachine."""

import os
import sys
from pathlib import Path

# Voeg de project root toe aan het Python-pad VOORDAT lokale modules worden geïmporteerd.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import openai
import redis.asyncio as redis
from bs4 import BeautifulSoup
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from meilisearch_python_async import Client as AsyncMeiliClient

from api.routes.generate_seo import router as seo_router


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """Beheer de MeiliSearch client gedurende de levensduur van de applicatie."""
    # Haal configuratie uit environment variables
    meili_host = os.getenv("MEILI_HOST", "http://127.0.0.1:7700")
    meili_master_key = os.getenv("MEILI_MASTER_KEY")
    fastapi_app.state.openai_api_key = os.getenv("OPENAI_API_KEY")
    fastapi_app.state.ollama_host = os.getenv(
        "OLLAMA_HOST", "http://localhost:11434"
    )
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379")) # No change needed, already correct
    redis_password = os.getenv("REDIS_PASSWORD")

    # Veiligheidsmaatregel: Vereis een Redis-wachtwoord.
    if not redis_password:
        print(
            "KRITISCH: REDIS_PASSWORD is niet ingesteld. Redis-client wordt niet geïnitialiseerd."
        )
        fastapi_app.state.redis_client = None

    # Initialiseer de MeiliSearch client en maak deze beschikbaar in de app state
    fastapi_app.state.meili_client = AsyncMeiliClient(
        url=meili_host, api_key=meili_master_key
    )
    fastapi_app.state.meili_index = fastapi_app.state.meili_client.get_index("documents")
    print("MeiliSearch client geïnitialiseerd.")

    # Initialiseer de OpenAI client alleen als er een API key is
    if fastapi_app.state.openai_api_key:
        fastapi_app.state.openai_client = openai.AsyncOpenAI(
            api_key=fastapi_app.state.openai_api_key
        )
        print("OpenAI client geïnitialiseerd.")

    # Initialiseer de Redis client
    if redis_password:
        try:
            fastapi_app.state.redis_client = redis.Redis(
                host=redis_host, port=redis_port, password=redis_password,
                decode_responses=True
            )
            await fastapi_app.state.redis_client.ping()
            print("Redis client geïnitialiseerd en verbonden.")
        except redis.RedisError as e: # No change needed, already correct
            print(f"Kon niet verbinden met Redis: {e}")
            fastapi_app.state.redis_client = None

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
VERCEL_PREVIEW_REGEX = r"https://fajaede-search-frontend-.*-fajaede\.vercel\.app"

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

@app.get("/")
def read_root():
    """Stuurt de root URL door naar de API-documentatie."""
    return RedirectResponse(url="/docs")


@app.get("/health")
def health_check():
    """Simpel health-check endpoint dat de status van de applicatie retourneert."""
    return {"status": "ok"}


@app.get("/api/search")
async def search(request: Request, q: str):
    """Voert een zoekopdracht uit op de MeiliSearch index."""
    if not q:
        return {"results": []}

    # Voor de sitemap-generatie, die een lege query kan sturen.
    # We retourneren hier de ruwe 'hits' zodat de sitemap de URL's kan verwerken.
    limit = 1000 if request.headers.get("X-Sitemap-Request") else 10

    try:
        search_results = await request.app.state.meili_index.search(q, {"limit": limit})
        return {"results": search_results["hits"]}
    except Exception as e:  # Vang alle exceptions
        # Vang de "index_not_found" fout af. Dit kan variëren in de async lib.
        if "index_not_found" in str(e):
            print("Index 'documents' nog niet gevonden. Geef een lege lijst terug.")
            return {"results": []}

        # Voor alle andere fouten, behandel ze als een service-onbeschikbaarheid
        print(f"Error connecting to MeiliSearch: {e}")
        raise HTTPException(
            status_code=503, detail="Zoekservice is momenteel niet beschikbaar."
        ) from e


class Crawler:  # pylint: disable=too-few-public-methods
    """Een webcrawler die regels respecteert en content indexeert in Meilisearch."""

    def __init__(self, meili_client: AsyncMeiliClient, redis_client):
        if not redis_client:
            raise ValueError("Redis client is niet beschikbaar voor de crawler.")
        self.redis = redis_client
        self.meili_index = meili_client.get_index("documents")
        # Gebruik een standaard browser User-Agent om 403 Forbidden-fouten te voorkomen.
        # Veel websites blokkeren onbekende of custom bot User-Agents.
        # De volgorde van headers kan ook van belang zijn voor botdetectie.
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36" # Kleine versie-update
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "Accept-Language": "nl-BE,nl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "DNT": "1", # Do Not Track
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
        }
        self.client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10)
        self.content_hashes = set()
        self.robot_parsers = {}

    def _get_robot_parser(self, url: str) -> RobotFileParser:
        """Haalt de robots.txt parser voor een domein op en cachet deze."""
        domain = urlparse(url).netloc
        if domain not in self.robot_parsers:
            rp = RobotFileParser()
            rp.set_url(urljoin(url, "/robots.txt"))
            try:
                rp.read()
                self.robot_parsers[domain] = rp
            except (httpx.RequestError, ValueError, TypeError) as e:
                print(f"Kon robots.txt niet lezen voor {domain}: {e}")
                # Maak een lege parser aan om door te gaan bij een fout
                self.robot_parsers[domain] = RobotFileParser()
        return self.robot_parsers[domain]

    def _is_duplicate(self, soup: BeautifulSoup) -> bool:
        """Controleert op dubbele content via content hashing."""
        text_content = soup.get_text(separator=" ", strip=True)
        if not text_content:
            return True
        content_hash = hashlib.sha256(text_content.encode("utf-8")).hexdigest()
        if content_hash in self.content_hashes:
            return True
        self.content_hashes.add(content_hash)
        return False

    async def _process_page(self, url: str):
        """Verwerkt een enkele pagina: downloaden, parsen, valideren en indexeren."""
        if await self.redis.sismember("crawler:visited_urls", url):
            return

        # Regel: Respecteer robots.txt
        robot_parser = self._get_robot_parser(url)
        if not robot_parser.can_fetch(self.client.headers["User-Agent"], url):
            print(f"Uitgesloten door robots.txt: {url}")
            return

        try:
            # Regel: Beleefdheidsvertraging
            await asyncio.sleep(1.5)

            # Regel: Gebruik HEAD om content type te checken
            head_res = await self.client.head(url, timeout=5)
            head_res.raise_for_status()
            content_type = head_res.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                print(f"Overgeslagen (geen HTML): {url}")
                return

            # Download de daadwerkelijke pagina
            res = await self.client.get(url, timeout=10)
            res.raise_for_status()

            soup = BeautifulSoup(res.content, "html.parser")

            # Regel: Controleer op 'noindex' meta tag
            meta_robots = soup.find("meta", attrs={"name": "robots"})
            if meta_robots and "noindex" in meta_robots.get("content", "").lower():
                print(f"Overgeslagen ('noindex' tag): {url}")
                return

            # Regel: Controleer op dubbele content
            if self._is_duplicate(soup):
                print(f"Overgeslagen (dubbele content): {url}")
                return

            # Extraheer titel en content
            title = soup.title.string if soup.title else "Ongetiteld"
            # Verwijder script en style tags voor schonere content
            for script_or_style in soup(["script", "style"]):
                script_or_style.decompose()
            content = soup.get_text(separator="\n", strip=True)

            # Regel: Controleer op 'thin content'
            if len(content.split()) < 100:
                print(f"Overgeslagen (te weinig content): {url}")
                return

            # Document voorbereiden voor Meilisearch
            document = {
                "id": hashlib.sha256(url.encode()).hexdigest(),
                "url": url,
                "title": title,
                "content": content,
            }

            await self.meili_index.add_documents([document])
            print(f"Geïndexeerd: {url}")

            # Voeg nieuwe links toe aan de wachtrij
            base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            for link in soup.find_all("a", href=True):
                href = link["href"]
                full_url = urljoin(base_url, href)
                # Crawl alleen links binnen hetzelfde domein en voeg toe aan Redis queue
                if (
                    urlparse(full_url).netloc == urlparse(base_url).netloc
                    and not await self.redis.sismember(
                        "crawler:visited_urls", full_url
                    )
                ):
                    await self.redis.lpush("crawler:queue", full_url)
            # Markeer URL als bezocht in Redis nadat alles succesvol is verwerkt
            await self.redis.sadd("crawler:visited_urls", url)

        except httpx.RequestError as e:
            print(f"Fout bij het crawlen van {url}: {e}")
        except (AttributeError, TypeError) as e:  # Vang parsing- of contentfouten af
            print(f"Fout bij het verwerken van de content van {url}: {e}")

    async def run(self, start_url: str, max_pages: int = 250000):
        """Start het crawlproces vanaf een begin-URL."""
        # Voeg de start_url toe aan de wachtrij als deze leeg is
        if await self.redis.llen("crawler:queue") == 0:
            await self.redis.lpush("crawler:queue", start_url)

        print(f"Crawler gestart voor {start_url} met een limiet van {max_pages} pagina's.")

        crawled_count = 0
        while (url := await self.redis.rpop("crawler:queue")) and crawled_count < max_pages:
            await self._process_page(url)
            crawled_count += 1

        print("Crawl-sessie voltooid.")


@app.post("/api/crawl")
async def start_crawl(request: Request, url: str, background_tasks: BackgroundTasks):
    """Endpoint om een nieuwe crawl-taak te starten op de achtergrond."""
    try:
        crawler = Crawler(
            request.app.state.meili_client, request.app.state.redis_client
        )
        background_tasks.add_task(crawler.run, url)
        return {"message": f"Crawl-taak voor {url} is gestart op de achtergrond."}
    except ValueError as e:
        # Vang de fout af als Redis niet beschikbaar is.
        raise HTTPException(status_code=503, detail=str(e)) from e


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
        is_thinking_placeholder = msg.get("content") == "fajaedeAI+ is aan het denken..."
        if is_valid_role and not is_thinking_placeholder:
            chat_history.append({"role": msg["role"], "content": msg["content"]})
    return chat_history


@app.get("/api/summarize")
async def summarize(request: Request, q: str, chat_history: list[dict] = Depends(_prepare_chat_history)):
    """Genereert een AI-samenvatting op basis van zoekresultaten en conversatiegeschiedenis."""
    try:
        if not q:
            return {"ai": "Stel een vraag om een AI-samenvatting te krijgen."}

        # 1. Haal context op via de zoekfunctie
        try:
            search_results = await request.app.state.meili_index.search(q, {'limit': 5})
            context_hits = search_results.get("hits", [])
            context_parts = []
            for i, hit in enumerate(context_hits):
                title = hit.get('title', '')
                summary = hit.get('summary', hit.get('content', ''))[:300]
                context_parts.append(f"Bron {i+1}: {title}\n{summary}")

            context = "\n\n".join(context_parts) if context_parts else (
                "Geen zoekresultaten gevonden. Beantwoord de vraag op basis van "
                "algemene kennis, maar vermeld dat er geen specifieke resultaten "
                "in de zoekindex beschikbaar zijn."
            )
        except Exception as e:
            print(f"Fout bij ophalen context van MeiliSearch: {e}")
            detail_message = (
                "Kon geen zoekresultaten ophalen voor de AI-samenvatting."
            )
            raise HTTPException(status_code=503, detail=detail_message) from e

        # 2. Bouw de prompt
        system_prompt = (
            "Je bent een AI-assistent die vragen beantwoordt op basis van "
            "genummerde zoekresultaten. "
            "Jouw taak is om de vraag van de gebruiker te beantwoorden door "
            "de verstrekte context samen "
            "te vatten. Houd je aan de volgende regels:\n"
            "1. Baseer je antwoord UITSLUITEND op de informatie in de 'Context'. "
            "Verzin geen informatie.\n"
            "2. Als de context geen antwoord bevat, zeg dan: 'De zoekresultaten "
            "bevatten onvoldoende informatie om deze vraag te beantwoorden.'\n"
            "3. Structureer je antwoord als een FAQ of How-To als de context dit toelaat. "
            "Gebruik Markdown voor de opmaak (bijv. met `#` voor titels en `*` of `1.` "
            "voor lijsten).\n"
            "4. Voeg aan het einde van ELKE zin een citaat toe met de nummers van de bronnen "
            "die je voor die zin hebt gebruikt. Bijvoorbeeld: 'Dit is een feit. [1, 3] "
            "Een ander feit. [2]'\n"
            "5. Combineer citaten als meerdere bronnen één zin ondersteunen. "
            "Bijvoorbeeld: [1, 2].\n"
            "6. Schrijf in een heldere, feitelijke en neutrale toon.\n"
            "7. Antwoord altijd in de taal van de vraag van de gebruiker."
        )
        user_prompt = (
            f"Context:\n---\n{context}\n---\n\n"
            f"Beantwoord de volgende vraag op basis van bovenstaande context:\n{q}"
        )

        # Voeg de bestaande conversatiegeschiedenis toe aan de messages voor de LLM
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(chat_history)
        messages.append({"role": "user", "content": user_prompt})

        # 3. Probeer OpenAI, indien geconfigureerd
        try:
            if hasattr(request.app.state, "openai_client"):
                print("Poging tot OpenAI API aanroep...")
                client = request.app.state.openai_client
                chat_completion = await (
                    client.chat.completions.create(
                        messages=messages,
                        model="gpt-3.5-turbo",
                        temperature=0.3,
                    )
                )
                response = chat_completion.choices[0].message.content
                print("Antwoord succesvol gegenereerd door OpenAI.")
                return {"ai": response}
            # Als OpenAI niet is geconfigureerd, wordt dit overgeslagen
# en gaan we direct naar de fallback.
            raise openai.APIError(
                "OpenAI client niet geconfigureerd.",
                request=None,
                body=None,
            )
        except openai.APIError as e:
            print(f"OpenAI API call mislukt: {e}. Fallback naar lokaal model.")
            if hasattr(request.app.state, "ollama_host"):
                try:
                    print("Poging tot Ollama (phi3:mini) fallback...")
                    async with openai.AsyncOpenAI(
                        base_url=f"{request.app.state.ollama_host}/v1", api_key="ollama"
                    ) as client:
                        chat_completion = await client.chat.completions.create(
                            model="phi3:mini", messages=messages
                        )
                        response = chat_completion.choices[0].message.content
                        print("Antwoord succesvol gegenereerd door Ollama (phi3:mini).")
                        return {"ai": response}
                except openai.APIError as ollama_error:
                    print(f"Ollama fallback ook mislukt: {ollama_error}")
        raise HTTPException(
            status_code=503, detail="AI-diensten zijn momenteel niet beschikbaar."
        )

    except Exception as e:
        # Vang alle onverwachte fouten af die hierboven niet zijn gespecificeerd
        print(f"Onverwachte fout in summarize endpoint: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Er is een onverwachte fout opgetreden bij het genereren van het antwoord.",
        ) from e
