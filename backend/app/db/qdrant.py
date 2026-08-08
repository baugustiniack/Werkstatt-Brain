"""Qdrant-Client (Collections: cad_snippets, workshop_knowledge, visual_inventory – SPEC Kap. 2.2)."""

import logging
import random

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from app.config import settings

logger = logging.getLogger(__name__)

CAD_SNIPPETS_COLLECTION = "cad_snippets"
WORKSHOP_KNOWLEDGE_COLLECTION = "workshop_knowledge"
VISUAL_INVENTORY_COLLECTION = "visual_inventory"

COLLECTIONS: dict[str, str] = {
    CAD_SNIPPETS_COLLECTION: "Code-Muster für geometrische Primitives (build123d)",
    WORKSHOP_KNOWLEDGE_COLLECTION: "Freitext-Notizen, Datenblätter, gelernte Faustregeln",
    VISUAL_INVENTORY_COLLECTION: "Multimodale Embeddings von Fräser-Fotos, Materialien & CAD-Renders",
}


def random_placeholder_vector(size: int | None = None) -> list[float]:
    """Erzeugt einen normierten Zufallsvektor.

    Solange keine echte Embedding-Pipeline (bge-m3/nomic-embed-text/OpenCLIP,
    SPEC Kap. 2.2) angebunden ist, wird dieser Platzhalter verwendet, damit
    Punkte bereits jetzt strukturell in Qdrant abgelegt werden können. Eine
    echte semantische Suche ist damit noch NICHT möglich – Erweiterungspunkt:
    Ersatz durch echte Embeddings, sobald ein Modell verfügbar ist.
    """
    dim = size or settings.qdrant_vector_size
    raw = [random.uniform(-1.0, 1.0) for _ in range(dim)]
    norm = sum(v * v for v in raw) ** 0.5 or 1.0
    return [v / norm for v in raw]


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)


def ensure_qdrant_collections(client: QdrantClient | None = None) -> None:
    qdrant = client or get_qdrant_client()
    existing = {collection.name for collection in qdrant.get_collections().collections}

    for collection_name in COLLECTIONS:
        if collection_name in existing:
            logger.info("Qdrant collection already exists: %s", collection_name)
            continue

        qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=settings.qdrant_vector_size,
                distance=Distance.COSINE,
            ),
        )
        logger.info("Created Qdrant collection: %s", collection_name)


def verify_qdrant_connection() -> None:
    client = get_qdrant_client()
    ensure_qdrant_collections(client)
    collections = {collection.name for collection in client.get_collections().collections}
    missing = set(COLLECTIONS) - collections
    if missing:
        raise RuntimeError(f"Missing Qdrant collections after init: {', '.join(sorted(missing))}")
    logger.info("Qdrant connection verified (%d collections)", len(collections))
