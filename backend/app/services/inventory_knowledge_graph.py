"""Inventory Knowledge Graph – lernfähige Schicht über der Inventar-DB.

Der Graph wird aus aktuellen DB-Einträgen (Tools, Materialien, Assets) aufgebaut
und persistiert. Der Inventory Manager kann:
1) den Graph abfragen (gelernte Nachbarschaften / Assoziationen)
2) weiterhin direkt auf die DB zugreifen
3) nach erfolgreichen Matches Kanten verstärken (Lernfähigkeit)
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Relationen
REL_HAS_TAG = "HAS_TAG"
REL_MATERIAL_TYPE = "OF_MATERIAL_TYPE"
REL_SIMILAR_DIAMETER = "SIMILAR_DIAMETER"
REL_RELATED_FEATURE = "RELATED_FEATURE"
REL_USED_WITH = "USED_WITH"  # Tool ↔ Material (gelernt)
REL_MATCHED_PART = "MATCHED_PART"  # Tool/Material/Asset ↔ Feature/Keyword (gelernt)
REL_SAME_CATEGORY = "SAME_CATEGORY"


def _kg_path() -> Path:
    path = Path(getattr(settings, "inventory_kg_path", None) or "/data/inventory_knowledge_graph.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str, *, prefix: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9äöüÄÖÜß_-]+", "_", (text or "").strip().lower())
    cleaned = cleaned.strip("_")[:80] or "unknown"
    return f"{prefix}:{cleaned}"


def _empty_graph() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "trained_at": None,
        "stats": {},
        "nodes": {},  # id -> {id, type, label, props}
        "edges": [],  # {from, to, rel, weight, learned, props}
    }


class InventoryKnowledgeGraph:
    """In-Memory Graph mit JSON-Persistenz."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data or _empty_graph()
        self.nodes: dict[str, dict[str, Any]] = dict(self.data.get("nodes") or {})
        self.edges: list[dict[str, Any]] = list(self.data.get("edges") or [])

    # ── Persistenz ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls) -> InventoryKnowledgeGraph:
        path = _kg_path()
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
            return cls(raw)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Knowledge Graph unlesbar (%s): %s", path, exc)
            return cls()

    def save(self) -> Path:
        path = _kg_path()
        self.data["nodes"] = self.nodes
        self.data["edges"] = self.edges
        self.data["updated_at"] = _now_iso()
        self.data["stats"] = self.compute_stats()
        path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def compute_stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = defaultdict(int)
        for node in self.nodes.values():
            by_type[str(node.get("type") or "unknown")] += 1
        learned = sum(1 for e in self.edges if e.get("learned"))
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "learned_edges": learned,
            "by_type": dict(by_type),
            "trained_at": self.data.get("trained_at"),
            "updated_at": self.data.get("updated_at"),
        }

    # ── Mutationen ──────────────────────────────────────────────────────────

    def upsert_node(
        self,
        node_id: str,
        *,
        node_type: str,
        label: str,
        props: dict[str, Any] | None = None,
    ) -> None:
        existing = self.nodes.get(node_id) or {}
        merged_props = dict(existing.get("props") or {})
        if props:
            merged_props.update(props)
        self.nodes[node_id] = {
            "id": node_id,
            "type": node_type,
            "label": label or existing.get("label") or node_id,
            "props": merged_props,
        }

    def add_or_strengthen_edge(
        self,
        src: str,
        dst: str,
        rel: str,
        *,
        weight: float = 1.0,
        learned: bool = False,
        props: dict[str, Any] | None = None,
    ) -> None:
        if src == dst or src not in self.nodes or dst not in self.nodes:
            return
        for edge in self.edges:
            if edge.get("from") == src and edge.get("to") == dst and edge.get("rel") == rel:
                edge["weight"] = float(edge.get("weight") or 0) + float(weight)
                if learned:
                    edge["learned"] = True
                if props:
                    edge_props = dict(edge.get("props") or {})
                    edge_props.update(props)
                    edge["props"] = edge_props
                edge["updated_at"] = _now_iso()
                return
        self.edges.append(
            {
                "from": src,
                "to": dst,
                "rel": rel,
                "weight": float(weight),
                "learned": bool(learned),
                "props": props or {},
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        )

    # ── Training aus DB ─────────────────────────────────────────────────────

    def rebuild_from_db(self) -> dict[str, Any]:
        """Baut den Graph neu aus Tools, Materialien und Assets; behält gelernte Kanten."""
        from app.db.postgres import SessionLocal
        from app.models.stock_material import StockMaterial
        from app.models.tool import Tool
        from app.models.unprocessed_asset import UnprocessedAsset

        # Gelernte Kanten merken (bleiben über Retrain erhalten)
        learned_edges = [e for e in self.edges if e.get("learned")]

        self.nodes = {}
        self.edges = []

        db = SessionLocal()
        try:
            tools = db.query(Tool).limit(500).all()
            materials = db.query(StockMaterial).limit(500).all()
            assets = db.query(UnprocessedAsset).limit(800).all()
        finally:
            db.close()

        tool_ids: list[str] = []
        for tool in tools:
            nid = f"tool:{tool.id}"
            tool_ids.append(nid)
            self.upsert_node(
                nid,
                node_type="tool",
                label=tool.name,
                props={
                    "diameter_mm": float(tool.diameter_mm) if tool.diameter_mm is not None else None,
                    "status": getattr(tool.status, "value", str(tool.status)),
                    "max_rpm": tool.max_rpm,
                },
            )
            diam = float(tool.diameter_mm) if tool.diameter_mm is not None else None
            if diam is not None:
                bucket = f"diameter:{round(diam * 2) / 2:.1f}"  # 0.5mm buckets
                self.upsert_node(bucket, node_type="diameter_bucket", label=f"Ø {bucket.split(':', 1)[1]} mm")
                self.add_or_strengthen_edge(nid, bucket, REL_SIMILAR_DIAMETER, weight=1.0)

        for mat in materials:
            nid = f"material:{mat.id}"
            self.upsert_node(
                nid,
                node_type="material",
                label=mat.material_type,
                props={
                    "dimensions_xyz_mm": mat.dimensions_xyz_mm,
                    "grain_direction": mat.grain_direction,
                    "notes": (mat.notes or "")[:300] if hasattr(mat, "notes") else None,
                },
            )
            mtype = _slug(mat.material_type or "unbekannt", prefix="material_type")
            self.upsert_node(mtype, node_type="material_type", label=mat.material_type or "unbekannt")
            self.add_or_strengthen_edge(nid, mtype, REL_MATERIAL_TYPE, weight=1.0)

        for asset in assets:
            nid = f"asset:{asset.id}"
            title = asset.title or asset.file_path or str(asset.id)[:8]
            tags = list(asset.tags or [])
            vision = asset.vision_result if isinstance(asset.vision_result, dict) else {}
            category = vision.get("category")
            self.upsert_node(
                nid,
                node_type="asset",
                label=title,
                props={
                    "file_type": asset.file_type.value if asset.file_type else None,
                    "status": asset.status.value if asset.status else None,
                    "tags": tags[:12],
                    "category": category,
                    "description": ((asset.ai_notes or asset.notes or asset.user_notes or "")[:400] or None),
                },
            )
            for tag in tags[:10]:
                tid = _slug(str(tag), prefix="tag")
                self.upsert_node(tid, node_type="tag", label=str(tag))
                self.add_or_strengthen_edge(nid, tid, REL_HAS_TAG, weight=1.0)
            if category:
                cid = _slug(str(category), prefix="category")
                self.upsert_node(cid, node_type="category", label=str(category))
                self.add_or_strengthen_edge(nid, cid, REL_SAME_CATEGORY, weight=1.0)
            # Features/Keywords aus Beschreibung
            blob = f"{title} {asset.user_notes or ''} {asset.ai_notes or asset.notes or ''}"
            for word in re.findall(r"[A-Za-zÄÖÜäöüß0-9]{4,}", blob)[:8]:
                fid = _slug(word, prefix="feature")
                self.upsert_node(fid, node_type="feature", label=word)
                self.add_or_strengthen_edge(nid, fid, REL_RELATED_FEATURE, weight=0.5)

        # Cross-links: Tools ähnlicher Durchmesser
        for i, a in enumerate(tool_ids):
            da = (self.nodes[a].get("props") or {}).get("diameter_mm")
            if da is None:
                continue
            for b in tool_ids[i + 1 : i + 6]:
                db_ = (self.nodes[b].get("props") or {}).get("diameter_mm")
                if db_ is None:
                    continue
                if abs(float(da) - float(db_)) <= 0.5:
                    self.add_or_strengthen_edge(a, b, REL_SIMILAR_DIAMETER, weight=0.8)

        # Gelernte Kanten wieder einspielen (nur wenn Nodes noch existieren)
        for edge in learned_edges:
            src, dst = edge.get("from"), edge.get("to")
            if src in self.nodes and dst in self.nodes:
                self.add_or_strengthen_edge(
                    str(src),
                    str(dst),
                    str(edge.get("rel") or REL_MATCHED_PART),
                    weight=float(edge.get("weight") or 1.0),
                    learned=True,
                    props=edge.get("props") if isinstance(edge.get("props"), dict) else None,
                )

        self.data["trained_at"] = _now_iso()
        path = self.save()
        stats = self.compute_stats()
        logger.info(
            "Inventory KG trainiert: %s Nodes, %s Edges → %s",
            stats["nodes"],
            stats["edges"],
            path,
        )
        return {"path": str(path), **stats}

    # ── Lernen aus Matches ──────────────────────────────────────────────────

    def learn_from_match(
        self,
        *,
        tool: dict[str, Any] | None,
        stock: dict[str, Any] | None,
        assets: list[dict[str, Any]] | None,
        part: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Verstärkt Assoziationen nach einem erfolgreichen Inventory-Match."""
        part = part or {}
        constraints = part.get("material_tool_constraints") or {}
        features: list[str] = []
        name = part.get("name")
        if name:
            features.append(str(name))
        if constraints.get("material_type"):
            features.append(str(constraints["material_type"]))
        for feat in part.get("manufacturing_features") or []:
            if isinstance(feat, str):
                features.append(feat)
            elif isinstance(feat, dict) and feat.get("name"):
                features.append(str(feat["name"]))

        feature_nodes: list[str] = []
        for feat in features[:10]:
            fid = _slug(feat, prefix="feature")
            self.upsert_node(fid, node_type="feature", label=feat)
            feature_nodes.append(fid)

        reinforced = 0
        tool_id = None
        if tool and tool.get("id"):
            tool_id = f"tool:{tool['id']}"
            if tool_id not in self.nodes:
                self.upsert_node(
                    tool_id,
                    node_type="tool",
                    label=str(tool.get("name") or tool_id),
                    props={"diameter_mm": tool.get("diameter_mm")},
                )
            for fid in feature_nodes:
                self.add_or_strengthen_edge(tool_id, fid, REL_MATCHED_PART, weight=1.0, learned=True)
                reinforced += 1

        stock_id = None
        if stock and stock.get("id"):
            stock_id = f"material:{stock['id']}"
            if stock_id not in self.nodes:
                self.upsert_node(
                    stock_id,
                    node_type="material",
                    label=str(stock.get("material_type") or stock_id),
                    props={"dimensions_xyz_mm": stock.get("dimensions_xyz_mm")},
                )
            for fid in feature_nodes:
                self.add_or_strengthen_edge(stock_id, fid, REL_MATCHED_PART, weight=1.0, learned=True)
                reinforced += 1
            if tool_id:
                self.add_or_strengthen_edge(tool_id, stock_id, REL_USED_WITH, weight=1.0, learned=True)
                reinforced += 1

        for asset in assets or []:
            aid = asset.get("id")
            if not aid:
                continue
            anode = f"asset:{aid}"
            if anode not in self.nodes:
                self.upsert_node(
                    anode,
                    node_type="asset",
                    label=str(asset.get("title") or aid),
                    props={"tags": asset.get("tags") or []},
                )
            for fid in feature_nodes:
                self.add_or_strengthen_edge(anode, fid, REL_MATCHED_PART, weight=0.8, learned=True)
                reinforced += 1

        self.save()
        return {"reinforced_edges": reinforced, "stats": self.compute_stats()}

    # ── Abfrage ─────────────────────────────────────────────────────────────

    def _adjacency(self) -> dict[str, list[tuple[str, dict[str, Any]]]]:
        adj: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for edge in self.edges:
            src, dst = edge.get("from"), edge.get("to")
            if not src or not dst:
                continue
            adj[str(src)].append((str(dst), edge))
            adj[str(dst)].append((str(src), edge))  # undirected traversal
        return adj

    def query(
        self,
        *,
        keywords: list[str] | None = None,
        material_type: str | None = None,
        tool_diameter_mm: float | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        """Nachbarschaftssuche um Seed-Nodes (Features, Materialtyp, Durchmesser)."""
        if not self.nodes:
            return {
                "hits": [],
                "seed_nodes": [],
                "summary": "Knowledge Graph leer – bitte zuerst anlernen.",
                "stats": self.compute_stats(),
            }

        seeds: list[str] = []
        for kw in keywords or []:
            fid = _slug(kw, prefix="feature")
            if fid in self.nodes:
                seeds.append(fid)
            tid = _slug(kw, prefix="tag")
            if tid in self.nodes:
                seeds.append(tid)
            # Fuzzy: Label-Match
            low = kw.lower()
            for nid, node in self.nodes.items():
                if low in (node.get("label") or "").lower() and nid not in seeds:
                    seeds.append(nid)
                    if len(seeds) >= 20:
                        break

        if material_type:
            mtype = _slug(material_type, prefix="material_type")
            if mtype in self.nodes:
                seeds.append(mtype)

        if tool_diameter_mm is not None:
            bucket = f"diameter:{round(float(tool_diameter_mm) * 2) / 2:.1f}"
            if bucket in self.nodes:
                seeds.append(bucket)

        seeds = list(dict.fromkeys(seeds))[:20]
        if not seeds:
            # Fallback: stärkste gelernte Kanten
            learned = sorted(
                (e for e in self.edges if e.get("learned")),
                key=lambda e: float(e.get("weight") or 0),
                reverse=True,
            )[:limit]
            hits = []
            for edge in learned:
                for endpoint in (edge.get("from"), edge.get("to")):
                    node = self.nodes.get(str(endpoint) or "")
                    if node and node.get("type") in {"tool", "material", "asset"}:
                        hits.append(
                            {
                                "node": node,
                                "score": float(edge.get("weight") or 0),
                                "via": edge.get("rel"),
                                "learned": True,
                            }
                        )
            return {
                "hits": hits[:limit],
                "seed_nodes": [],
                "summary": f"Keine direkten Seeds – {len(hits[:limit])} gelernte Assoziationen.",
                "stats": self.compute_stats(),
            }

        adj = self._adjacency()
        scores: dict[str, float] = defaultdict(float)
        via: dict[str, str] = {}
        visited: set[str] = set(seeds)
        queue: deque[tuple[str, int]] = deque((s, 0) for s in seeds)

        while queue:
            current, depth = queue.popleft()
            if depth >= 2:
                continue
            for neighbor, edge in adj.get(current, []):
                if neighbor in visited and depth > 0:
                    # trotzdem Score erhöhen
                    pass
                w = float(edge.get("weight") or 1.0)
                bonus = 1.5 if edge.get("learned") else 1.0
                decay = 1.0 / (1 + depth)
                scores[neighbor] += w * bonus * decay
                via.setdefault(neighbor, str(edge.get("rel") or ""))
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, depth + 1))

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        hits: list[dict[str, Any]] = []
        for nid, score in ranked:
            node = self.nodes.get(nid)
            if not node:
                continue
            if node.get("type") not in {"tool", "material", "asset"}:
                continue
            hits.append(
                {
                    "node": node,
                    "score": round(score, 3),
                    "via": via.get(nid),
                    "learned": any(
                        e.get("learned") and (e.get("from") == nid or e.get("to") == nid) for e in self.edges
                    ),
                }
            )
            if len(hits) >= limit:
                break

        return {
            "hits": hits,
            "seed_nodes": [{"id": s, "label": (self.nodes.get(s) or {}).get("label")} for s in seeds],
            "summary": (
                f"{len(hits)} KG-Treffer aus {len(seeds)} Seeds "
                f"({self.compute_stats().get('nodes', 0)} Nodes insgesamt)."
            ),
            "stats": self.compute_stats(),
        }


def train_inventory_knowledge_graph() -> dict[str, Any]:
    kg = InventoryKnowledgeGraph.load()
    return kg.rebuild_from_db()


def get_knowledge_graph_stats() -> dict[str, Any]:
    kg = InventoryKnowledgeGraph.load()
    return kg.compute_stats()


def query_knowledge_graph(**kwargs: Any) -> dict[str, Any]:
    return InventoryKnowledgeGraph.load().query(**kwargs)


def learn_inventory_match(**kwargs: Any) -> dict[str, Any]:
    kg = InventoryKnowledgeGraph.load()
    return kg.learn_from_match(**kwargs)
