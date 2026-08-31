"""ChromaDB incident memory layer with secret redaction and OS process advisory locking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast, Literal, Any

from phase2.config import (DEFAULT_CHROMA_DIR, DEFAULT_COLLECTION_NAME,
                           DEFAULT_TOP_K, PROVISIONAL_MIN_SIMILARITY,
                           STRUCTURED_METADATA_ALLOWLIST)
from phase2.locking import StoreLock
from phase2.models import IndexReport, Phase1Dataset, Phase1Incident, Phase2Match
from phase2.normalization import (incident_fingerprint, normalize_template,
                                   redact_secrets)


def _get_sample_text(sample: Any) -> str:
    if hasattr(sample, "content"):
        return str(sample.content)
    if isinstance(sample, dict):
        return str(sample.get("content", ""))
    return str(sample)


class IncidentMemory:
    """Read/Write manager for ChromaDB incident vector store."""

    def __init__(
        self,
        persist_dir: str | Path | None = None,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        embedder: Any = None,
        collection: Any = None,
    ) -> None:
        self.persist_dir = Path(persist_dir or DEFAULT_CHROMA_DIR).resolve()
        self.collection_name = collection_name
        self._embedder = embedder
        self._collection_override = collection
        self._collection: Any = None
        self._client: Any = None

    @property
    def collection(self) -> Any:
        if self._collection_override is not None:
            return self._collection_override

        if self._collection is None:
            import chromadb

            self._client = chromadb.PersistentClient(path=str(self.persist_dir))
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def close(self) -> None:
        self._collection = None
        if hasattr(self, "_client") and self._client is not None:
            try:
                if hasattr(self._client, "_system") and hasattr(self._client._system, "stop"):
                    self._client._system.stop()
            except Exception:
                pass
            self._client = None  # type: ignore[assignment]
        try:
            import chromadb
            if hasattr(chromadb, "clear_system_cache"):
                chromadb.clear_system_cache()
            elif hasattr(chromadb.api.client, "SharedSystemClient"):
                chromadb.api.client.SharedSystemClient.clear_system_cache()
        except Exception:
            pass
        import gc
        gc.collect()

    def _lock_context(self) -> Any:
        if self._collection_override is not None or str(self.persist_dir) == ":memory:":
            class _NoLock:
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return _NoLock()
        return StoreLock(persist_dir=self.persist_dir)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedder is not None:
            if hasattr(self._embedder, "encode"):
                res = self._embedder.encode(texts)
                if hasattr(res, "tolist"):
                    return res.tolist()
                return list(res)
            return self._embedder(texts)

        from sentence_transformers import SentenceTransformer

        if not hasattr(self, "_st_model"):
            self._st_model = SentenceTransformer("all-MiniLM-L6-v2")
        embeddings = self._st_model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def _document(self, target_service: str, template: str, log_samples: list[Any]) -> str:
        clean_template = normalize_template(redact_secrets(template))
        samples_text = " ".join(redact_secrets(_get_sample_text(s)) for s in log_samples[:3])
        return f"service: {target_service} | template: {clean_template} | logs: {samples_text}"

    def add_incidents_batch(self, incidents: list[Phase1Incident]) -> int:
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        ids: list[str] = []

        for inc in incidents:
            event = inc.incident_event
            evidence = inc.telemetry_evidence
            fingerprint = incident_fingerprint(event.target_service, evidence.log_cluster_template)

            doc = self._document(event.target_service, evidence.log_cluster_template, evidence.log_samples)

            trace_ids = list({
                s.trace_id for s in evidence.log_samples if hasattr(s, "trace_id") and s.trace_id
            }) if hasattr(evidence, "log_samples") and evidence.log_samples else []

            raw_meta = {
                "incident_id": event.incident_id,
                "target_service": event.target_service,
                "severity": event.severity,
                "priority_score": event.priority_score,
                "occurrence_count": event.occurrence_count,
                "fingerprint": fingerprint,
                "schema_version": "1.0",
                "dataset_version": "phase2_hardened",
                "dataset_generated_at": getattr(inc, "first_seen", ""),
                "trace_ids_json": json.dumps(trace_ids),
            }

            meta = {k: v for k, v in raw_meta.items() if k in STRUCTURED_METADATA_ALLOWLIST}

            ids.append(f"{event.target_service}::{fingerprint}::{event.incident_id}")
            documents.append(doc)
            metadatas.append(meta)

        if ids:
            with self._lock_context():
                self.collection.upsert(
                    ids=ids,
                    embeddings=self._embed(documents),
                    documents=documents,
                    metadatas=metadatas,
                )
        return len(ids)

    def index_dataset(
        self,
        raw_dataset: dict[str, Any],
        eligible_incident_ids: set[str] | None = None,
        quarantined_incident_ids: set[str] | None = None,
    ) -> IndexReport:
        dataset = Phase1Dataset.model_validate(raw_dataset)
        received = len(dataset.incidents)
        eligible = []
        skipped = 0

        for inc in dataset.incidents:
            inc_id = inc.incident_event.incident_id
            if eligible_incident_ids is not None and inc_id not in eligible_incident_ids:
                skipped += 1
                continue
            template = inc.telemetry_evidence.log_cluster_template.strip()
            if not template or not inc.incident_event.target_service.strip():
                skipped += 1
                continue
            eligible.append(inc)

        indexed = self.add_incidents_batch(eligible)
        quarantined = len(quarantined_incident_ids) if quarantined_incident_ids else 0

        return IndexReport(
            received=received,
            indexed=indexed,
            skipped=skipped,
            quarantined=quarantined,
            dataset_generated_at=dataset.generated_at,
        )

    def find_exact(self, target_service: str, log_template: str) -> list[Phase2Match]:
        fingerprint = incident_fingerprint(target_service, log_template)
        with self._lock_context():
            result = self.collection.get(
                where={"fingerprint": fingerprint},
                include=["documents", "metadatas"],
            )

        matches: list[Phase2Match] = []
        if result and result.get("ids"):
            for inc_id, doc, meta in zip(
                result["ids"], result["documents"], result["metadatas"]
            ):
                if meta.get("target_service") != target_service:
                    continue
                trace_ids = json.loads(meta.get("trace_ids_json", "[]")) if "trace_ids_json" in meta else []
                matches.append(
                    Phase2Match(
                        incident_id=meta.get("incident_id", inc_id),
                        target_service=meta.get("target_service", target_service),
                        severity=meta.get("severity", "UNKNOWN"),
                        log_cluster_template=log_template,
                        fingerprint=fingerprint,
                        similarity=1.0,
                        distance=0.0,
                        match_type="exact",
                        trace_ids=trace_ids,
                    )
                )
        return matches

    def search(
        self,
        query: str,
        target_service: str | None = None,
        query_target_service: str | None = None,
        top_k: int = DEFAULT_TOP_K,
        min_similarity: float = PROVISIONAL_MIN_SIMILARITY,
    ) -> list[Phase2Match]:
        redacted_query = redact_secrets(query)
        query_embedding = self._embed([redacted_query])[0]

        # Only target_service filters results. query_target_service is the
        # ORIGIN service of the query, used solely to label retrieval_scope
        # (same_service vs cross_service); it must never restrict results.
        where_clause: dict[str, Any] | None = None
        if target_service:
            where_clause = {"target_service": target_service}

        with self._lock_context():
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k * 2,
                where=where_clause,
                include=["documents", "metadatas", "distances"],
            )

        matches: list[Phase2Match] = []
        if not results or not results.get("ids") or not results["ids"][0]:
            return matches

        ids = results["ids"][0]
        distances = results["distances"][0]
        metadatas = results["metadatas"][0]

        for inc_id, dist, meta in zip(ids, distances, metadatas):
            # Chroma cosine distance can theoretically range from 0 to 2
            # (distance = 1 - cosine_similarity, and cosine_similarity lies in
            # [-1, 1]). The Phase 2 contract requires similarity in [0, 1],
            # so similarity = 1 - distance is clamped to [0, 1]; a negative
            # cosine similarity (distance > 1) therefore becomes 0.
            similarity = max(0.0, min(1.0, 1.0 - dist))
            if similarity < min_similarity:
                continue

            trace_ids = json.loads(meta.get("trace_ids_json", "[]")) if "trace_ids_json" in meta else []
            match_service = meta.get("target_service", "unknown")
            scope = "same_service"
            if query_target_service and match_service != query_target_service:
                scope = "cross_service"
            matches.append(
                Phase2Match(
                    incident_id=meta.get("incident_id", inc_id),
                    target_service=match_service,
                    severity=meta.get("severity", "UNKNOWN"),
                    log_cluster_template=meta.get("log_cluster_template", ""),
                    fingerprint=meta.get("fingerprint", ""),
                    similarity=round(similarity, 4),
                    distance=round(dist, 6),
                    match_type="semantic",
                    retrieval_scope=cast(Literal['same_service', 'cross_service'], scope),
                    trace_ids=trace_ids,
                )
            )

        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches[:top_k]
