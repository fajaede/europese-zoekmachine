"""Monitoring endpoints voor developers en operations."""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime
import time

router = APIRouter()

@router.get("/health")
async def health_check(request: Request):
    """Uitgebreide health check met alle service statussen."""
    redis_client = request.app.state.redis_client
    meili_index = request.app.state.meili_index
    
    services = {
        "backend": "ok",
        "redis": "unknown",
        "meilisearch": "unknown",
        "crawler": "unknown"
    }
    
    # Check Redis
    if redis_client:
        try:
            await redis_client.ping()
            services["redis"] = "ok"
        except Exception:
            services["redis"] = "error"
    else:
        services["redis"] = "not_configured"
    
    # Check MeiliSearch
    if meili_index:
        try:
            stats = await meili_index.get_stats()
            services["meilisearch"] = "ok"
        except Exception:
            services["meilisearch"] = "error"
    else:
        services["meilisearch"] = "not_configured"
    
    # Check Crawler
    if redis_client:
        is_running = await redis_client.get("crawler:is_running")
        services["crawler"] = "active" if is_running else "idle"
    
    all_ok = all(v in ["ok", "active"] for v in services.values())
    
    return {
        "status": "healthy" if all_ok else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "services": services
    }

@router.get("/metrics")
async def get_metrics(request: Request):
    """Metrics voor monitoring (Prometheus-stijl)."""
    redis_client = request.app.state.redis_client
    meili_index = request.app.state.meili_index
    
    metrics = {}
    
    # Crawler metrics
    if redis_client:
        queue_size = await redis_client.llen("crawler:queue")
        visited = await redis_client.scard("crawler:visited_urls")
        is_running = await redis_client.get("crawler:is_running")
        
        metrics["crawler_queue_size"] = queue_size
        metrics["crawler_pages_visited"] = visited
        metrics["crawler_is_running"] = 1 if is_running else 0
    
    # MeiliSearch metrics
    if meili_index:
        try:
            ms_stats = await meili_index.get_stats()
            # IndexStats object attributes
            metrics["meilisearch_documents"] = ms_stats.number_of_documents if hasattr(ms_stats, 'number_of_documents') else 0
            metrics["meilisearch_indexing"] = 1 if (hasattr(ms_stats, 'is_indexing') and ms_stats.is_indexing) else 0
        except Exception:
            pass
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": metrics
    }

@router.get("/stats")
async def get_stats(request: Request):
    """Gedetailleerde statistieken voor developers."""
    redis_client = request.app.state.redis_client
    meili_index = request.app.state.meili_index
    
    stats = {
        "timestamp": datetime.utcnow().isoformat(),
        "crawler": {},
        "meilisearch": {},
        "redis": {}
    }
    
    # Crawler stats
    if redis_client:
        queue_size = await redis_client.llen("crawler:queue")
        visited = await redis_client.scard("crawler:visited_urls")
        is_running = await redis_client.get("crawler:is_running")
        
        stats["crawler"] = {
            "status": "active" if is_running else "idle",
            "queue_size": queue_size,
            "pages_visited": visited,
            "queue_keys": len(await redis_client.keys("crawler:*"))
        }
    
    # MeiliSearch stats
    if meili_index:
        try:
            ms_stats = await meili_index.get_stats()
            # IndexStats object - gebruik attributes direct
            stats["meilisearch"] = {
                "documents": ms_stats.number_of_documents if hasattr(ms_stats, 'number_of_documents') else 0,
                "is_indexing": ms_stats.is_indexing if hasattr(ms_stats, 'is_indexing') else False,
                "field_distribution": ms_stats.field_distribution if hasattr(ms_stats, 'field_distribution') else {}
            }
        except Exception as e:
            stats["meilisearch"] = {"error": str(e)}
    
    return stats

@router.get("/crawl/progress")
async def get_crawl_progress(request: Request):
    """Voortgang van de crawler in real-time."""
    redis_client = request.app.state.redis_client
    
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis niet beschikbaar")
    
    queue_size = await redis_client.llen("crawler:queue")
    visited = await redis_client.scard("crawler:visited_urls")
    is_running = await redis_client.get("crawler:is_running")
    
    # Schatting van voortgang
    total_processed = visited + queue_size
    progress_percent = (visited / total_processed * 100) if total_processed > 0 else 0
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "status": "active" if is_running else "idle",
        "queue_size": queue_size,
        "pages_visited": visited,
        "progress_percent": round(progress_percent, 2),
        "estimated_remaining": queue_size
    }
