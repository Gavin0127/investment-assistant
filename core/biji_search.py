"""Hybrid retrieval service for local Biji notes."""

from __future__ import annotations

import re
from typing import Any


class BijiHybridSearchService:
    """Combine SQLite FTS and vector sidecar results into one ranked list."""

    def __init__(self, *, db, vector_store, xueqiu_db=None):
        self.db = db
        self.vector_store = vector_store
        self.xueqiu_db = xueqiu_db

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        terms = [term.strip() for term in re.split(r"\s+", str(query or "").strip()) if term.strip()]
        return terms or [str(query or "").strip()]

    @classmethod
    def _chunk_match_score(cls, chunk_text: str, query: str) -> float:
        text = str(chunk_text or "")
        if not text:
            return 0.0
        terms = [term for term in cls._query_terms(query) if term]
        if not terms:
            return 0.0
        hits = sum(1 for term in terms if term in text)
        return hits / len(terms)

    def _search_fts_candidates(self, query: str, top_k: int) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen_note_ids: set[str] = set()
        raw_queries = [str(query or "").strip()] + self._query_terms(query)

        for candidate_query in raw_queries:
            if not candidate_query:
                continue
            for note in self.db.search_notes_fts(candidate_query):
                note_id = str(note.get("note_id") or "")
                if not note_id or note_id in seen_note_ids:
                    continue
                seen_note_ids.add(note_id)
                candidates.append(note)
                if len(candidates) >= top_k:
                    return candidates
        return candidates

    def _search_xueqiu_candidates(self, query: str, top_k: int) -> list[dict[str, Any]]:
        if self.xueqiu_db is None:
            return []
        candidates: list[dict[str, Any]] = []
        seen_post_ids: set[str] = set()
        raw_queries = [str(query or "").strip()] + self._query_terms(query)

        for candidate_query in raw_queries:
            if not candidate_query:
                continue
            try:
                posts, _ = self.xueqiu_db.list_posts(query=candidate_query, per_page=top_k)
            except Exception:
                continue
            for post in posts or []:
                post_id = str(post.get("id") or "")
                if not post_id or post_id in seen_post_ids:
                    continue
                seen_post_ids.add(post_id)
                candidates.append(post)
                if len(candidates) >= top_k:
                    return candidates
        return candidates

    def search(self, query: str, top_k: int = 10) -> dict[str, Any]:
        fts_hits = self._search_fts_candidates(query, top_k=top_k)
        xueqiu_hits = self._search_xueqiu_candidates(query, top_k=top_k)
        try:
            vector_hits = self.vector_store.search(query, top_k=top_k)
        except Exception:
            vector_hits = []

        results: dict[tuple[str, str], dict[str, Any]] = {}

        for index, note in enumerate(fts_hits[:top_k]):
            note_id = str(note.get("note_id") or "")
            if not note_id:
                continue
            chunks = list(getattr(self.db, "list_chunks")(note_id) or [])
            if chunks:
                ranked_chunks = sorted(
                    chunks,
                    key=lambda chunk: self._chunk_match_score(chunk.get("text") or "", query),
                    reverse=True,
                )
                best_chunk = ranked_chunks[0]
                best_score = self._chunk_match_score(best_chunk.get("text") or "", query)
                if best_score <= 0:
                    continue
                key = (note_id, str(best_chunk.get("chunk_id") or "fts"))
                results[key] = {
                    "note_id": note_id,
                    "title": note.get("title") or "",
                    "section_type": best_chunk.get("section_type") or "",
                    "score": 0.5 + 0.2 * best_score + 0.15 * max(top_k - index, 0) / max(top_k, 1),
                    "text": best_chunk.get("text") or "",
                    "markdown_path": best_chunk.get("markdown_path") or "",
                }

        for hit in vector_hits[:top_k]:
            key = (str(hit.get("note_id") or ""), str(hit.get("chunk_id") or ""))
            if not key[0]:
                continue
            existing = results.get(key)
            candidate = {
                "note_id": key[0],
                "title": hit.get("title") or "",
                "section_type": hit.get("section_type") or "",
                "score": float(hit.get("score") or 0.0),
                "text": hit.get("text") or "",
                "markdown_path": hit.get("markdown_path") or "",
            }
            if existing is None or candidate["score"] > float(existing.get("score") or 0.0):
                results[key] = candidate

        for index, post in enumerate(xueqiu_hits[:top_k]):
            post_id = str(post.get("id") or "")
            if not post_id:
                continue
            raw_text = " ".join(
                str(post.get(field) or "")
                for field in ("title", "description", "text", "retweet_text", "retweet_description")
            ).strip()
            match_score = self._chunk_match_score(raw_text, query)
            if match_score <= 0:
                continue

            user_id = post.get("user_id")
            user = self.xueqiu_db.get_user(user_id) if user_id is not None else None
            title = (
                post.get("title")
                or (user or {}).get("nickname")
                or f"雪球用户 {user_id}"
            )
            target = str(post.get("target") or "").strip()
            if target and not target.startswith("http"):
                source_url = f"https://xueqiu.com{target}"
            else:
                source_url = target

            key = (f"xueqiu:{post_id}", f"xueqiu:{post_id}")
            candidate = {
                "note_id": f"xueqiu:{post_id}",
                "title": title,
                "section_type": "xueqiu_post",
                "score": 0.45 + 0.4 * match_score + 0.1 * max(top_k - index, 0) / max(top_k, 1),
                "text": post.get("text") or post.get("description") or "",
                "markdown_path": "",
                "source_type": "xueqiu",
                "source_url": source_url,
                "user_id": user_id,
                "created_at": post.get("created_at"),
            }
            existing = results.get(key)
            if existing is None or candidate["score"] > float(existing.get("score") or 0.0):
                results[key] = candidate

        ranked = sorted(
            results.values(),
            key=lambda item: (-float(item.get("score") or 0.0), item.get("note_id") or ""),
        )
        return {
            "query": query,
            "results": ranked[:top_k],
        }
