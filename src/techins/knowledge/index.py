"""混合检索：元数据过滤 → 关键词（BM25）与字符向量（TF-IDF）并行召回 → 倒数排序融合 → 元数据加权重排 → 引用封装。

切片数量超过阈值后，关键词召回改为全文索引（SQLite 三字索引或 MySQL ngram 全文索引），只对候选取字符向量，避免把整库切片装进内存。
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import func, select, text

from ..db import engine, session_scope
from ..models import KnowledgeChunk, KnowledgeCitation, KnowledgeDoc
from ..textutil import tokenize

logger = logging.getLogger("techins.knowledge")
# 超过这个切片数就不再构建内存索引。当前书库大约 30 万条，内存索引会把检索进程打满。
_FTS_MIN_CHUNKS = 20000
_FTS_TABLE = "knowledge_chunk_fts"
_TOKEN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{3,}")

KB_NAMES = {
    "K01": "法规监管库",
    "K02": "科技政策库",
    "K03": "条款与费率库",
    "K04": "风险知识库",
    "K05": "损失与理赔案例库",
    "K06": "行业与技术知识库",
    "K07": "精算数据库",
    "K08": "企业档案库",
    "K09": "欺诈模式库",
    "K10": "应急与减损知识库",
}
PRIVATE_KBS = {"K08"}


@dataclass
class _Row:
    chunk_id: int
    doc_id: int
    kb: str
    title: str
    version: int
    position: str
    text: str
    lines: list
    stages: list
    risk_types: list
    tags: list
    data_level: int
    enterprise_id: int | None
    status: str


class KnowledgeIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[_Row] = []
        self._tokens: list[list[str]] = []
        self._df: Counter = Counter()
        self._avgdl = 1.0
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None
        self._dirty = True
        self._fts_mode: bool | None = None
        self._doc_meta: list[dict] | None = None

    def invalidate(self) -> None:
        self._dirty = True
        self._fts_mode = None
        self._doc_meta = None
        try:
            from ..services.knowledge_admin import invalidate_library_stats

            invalidate_library_stats()
        except Exception:
            pass

    def _build(self) -> None:
        with session_scope() as s:
            # 只取切片和文档元数据。整篇正文会随每个切片重复读出，书库变大后会把进程撑满。
            rows = s.execute(
                select(
                    KnowledgeChunk.id,
                    KnowledgeChunk.doc_id,
                    KnowledgeChunk.position,
                    KnowledgeChunk.text,
                    KnowledgeDoc.kb,
                    KnowledgeDoc.title,
                    KnowledgeDoc.version,
                    KnowledgeDoc.lines,
                    KnowledgeDoc.stages,
                    KnowledgeDoc.risk_types,
                    KnowledgeDoc.tags,
                    KnowledgeDoc.data_level,
                    KnowledgeDoc.enterprise_id,
                    KnowledgeDoc.status,
                ).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
            ).all()
            built = [
                _Row(chunk_id, doc_id, kb, title, version, position or "", text or "", lines or [], stages or [], risk_types or [], tags or [], data_level, enterprise_id, status)
                for chunk_id, doc_id, position, text, kb, title, version, lines, stages, risk_types, tags, data_level, enterprise_id, status in rows
            ]
        tokens = [tokenize(r.title + " " + r.text) for r in built]
        df: Counter = Counter()
        for toks in tokens:
            df.update(set(toks))
        self._rows, self._tokens, self._df = built, tokens, df
        self._avgdl = (sum(len(t) for t in tokens) / len(tokens)) if tokens else 1.0
        if built:
            self._vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 3), sublinear_tf=True, min_df=1)
            self._matrix = self._vectorizer.fit_transform([r.title + " " + r.text for r in built])
        self._dirty = False

    def _bm25(self, query_tokens: list[str], idx: list[int], k1: float = 1.5, b: float = 0.75) -> dict[int, float]:
        n = len(self._rows)
        scores = {}
        for i in idx:
            toks = self._tokens[i]
            if not toks:
                continue
            tf = Counter(toks)
            dl = len(toks)
            score = 0.0
            for q in query_tokens:
                if q not in tf:
                    continue
                idf = math.log(1 + (n - self._df[q] + 0.5) / (self._df[q] + 0.5))
                score += idf * tf[q] * (k1 + 1) / (tf[q] + k1 * (1 - b + b * dl / self._avgdl))
            if score > 0:
                scores[i] = score
        return scores

    def sync_document(self, session, doc: KnowledgeDoc) -> None:
        """全文索引已经建好时，把刚写入的切片补进行里，避免每新增一篇就重建整库。"""
        if engine.dialect.name != "sqlite" or not self._fts_table_exists(session):
            return
        conn = session.connection()
        rows = conn.exec_driver_sql("SELECT id, text FROM knowledge_chunks WHERE doc_id = ?", (doc.id,)).fetchall()
        if not rows:
            return
        fts_n = int(conn.exec_driver_sql(f"SELECT count(*) FROM {_FTS_TABLE}").scalar() or 0)
        chunk_n = int(conn.exec_driver_sql("SELECT count(*) FROM knowledge_chunks").scalar() or 0)
        if fts_n + len(rows) != chunk_n:
            return
        cur = _dbapi(session).cursor()
        cur.executemany(
            f"INSERT INTO {_FTS_TABLE}(rowid, body, kb, data_level) VALUES (?, ?, ?, ?)",
            [(cid, f"{doc.title}\n{text or ''}", doc.kb, int(doc.data_level or 0)) for cid, text in rows],
        )
        cur.close()

    def _use_fts(self) -> bool:
        if self._fts_mode is not None:
            return self._fts_mode
        if engine.dialect.name not in ("sqlite", "mysql"):
            self._fts_mode = False
            return False
        with session_scope() as s:
            total = int(s.scalar(select(func.count(KnowledgeChunk.id))) or 0)
        self._fts_mode = total >= _FTS_MIN_CHUNKS
        return self._fts_mode

    def _fts_table_exists(self, session) -> bool:
        row = session.connection().exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (_FTS_TABLE,)).first()
        return row is not None

    def _ensure_fts(self, session) -> None:
        if engine.dialect.name == "mysql":
            # 全文索引在启动和数据迁移时创建。这里再打开连接执行 DDL 会和当前事务互相等待。
            return
        conn = session.connection()
        conn.exec_driver_sql(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5(
                body,
                kb UNINDEXED,
                data_level UNINDEXED,
                tokenize = 'trigram'
            )
            """
        )
        fts_n = int(conn.exec_driver_sql(f"SELECT count(*) FROM {_FTS_TABLE}").scalar() or 0)
        chunk_n = int(conn.exec_driver_sql("SELECT count(*) FROM knowledge_chunks").scalar() or 0)
        if fts_n == chunk_n:
            return
        started = time.perf_counter()
        logger.info("重建知识全文索引，切片 %s 条", chunk_n)
        conn.exec_driver_sql(f"DELETE FROM {_FTS_TABLE}")
        meta = {
            doc_id: (title or "", kb, int(level or 0))
            for doc_id, title, kb, level in conn.exec_driver_sql("SELECT id, title, kb, data_level FROM knowledge_docs")
        }
        read = _dbapi(session).cursor()
        write = _dbapi(session).cursor()
        read.execute("SELECT id, doc_id, text FROM knowledge_chunks")
        done = 0
        while True:
            batch = read.fetchmany(2000)
            if not batch:
                break
            payload = []
            for chunk_id, doc_id, text in batch:
                title, kb, level = meta.get(doc_id, ("", "", 0))
                payload.append((chunk_id, f"{title}\n{text or ''}", kb, level))
            write.executemany(f"INSERT INTO {_FTS_TABLE}(rowid, body, kb, data_level) VALUES (?, ?, ?, ?)", payload)
            done += len(batch)
            if done % 20000 == 0:
                logger.info("全文索引已写入 %s / %s", done, chunk_n)
        read.close()
        write.close()
        logger.info("全文索引完成，%s 条，用时 %.0f 秒", done, time.perf_counter() - started)

    def _search_fts(
        self,
        query: str,
        *,
        kbs: list[str] | None,
        lines: list[str] | None,
        stages: list[str] | None,
        risk_types: list[str] | None,
        max_level: int,
        enterprise_id: int | None,
        top_k: int,
        include_expired: bool,
    ) -> list[dict]:
        match = _fts_match(query)
        with session_scope() as s:
            self._ensure_fts(s)
            if match:
                rows = self._query_fts(s, match, kbs=kbs, max_level=max_level)
                rows = self._add_title_hits(s, query, rows, kbs=kbs, max_level=max_level)
            else:
                rows = self._query_like(s, query, kbs=kbs, max_level=max_level)
        kept = []
        for row in rows:
            if not include_expired and row["status"] != "effective":
                continue
            if row["data_level"] > max_level:
                continue
            if row["kb"] in PRIVATE_KBS and enterprise_id is not None and row["enterprise_id"] not in (None, enterprise_id):
                continue
            kept.append(row)
        return _fuse(query, kept, lines=lines, stages=stages, risk_types=risk_types, top_k=top_k)

    def _query_fts(self, session, match: str, *, kbs: list[str] | None, max_level: int) -> list[dict]:
        if engine.dialect.name == "mysql":
            return self._query_mysql_fts(session, match, kbs=kbs, max_level=max_level)
        sql = f"""
            SELECT c.id, c.doc_id, c.position, c.text,
                   d.kb, d.title, d.version, d.`lines`, d.stages, d.risk_types,
                   d.data_level, d.enterprise_id, d.status, hits.fts_rank
            FROM (
                SELECT rowid AS chunk_id, bm25({_FTS_TABLE}) AS fts_rank
                FROM {_FTS_TABLE}
                WHERE {_FTS_TABLE} MATCH ?
                  AND data_level <= ?
        """
        params: list = [match, max_level]
        if kbs:
            sql += f" AND kb IN ({','.join('?' for _ in kbs)})"
            params.extend(kbs)
        sql += """
                ORDER BY fts_rank
                LIMIT 400
            ) AS hits
            JOIN knowledge_chunks c ON c.id = hits.chunk_id
            JOIN knowledge_docs d ON d.id = c.doc_id
        """
        return [_hit_dict(row) for row in _bind_execute(session, sql, params)]

    def _query_mysql_fts(self, session, match: str, *, kbs: list[str] | None, max_level: int) -> list[dict]:
        # 千万级切片时 InnoDB ngram 全文检索容易撑爆结果缓存甚至拖垮实例，改为标题/来源检索。
        chunk_n = int(session.connection().exec_driver_sql("SELECT COUNT(*) FROM knowledge_chunks").scalar() or 0)
        if chunk_n >= 300_000:
            logger.info("切片数 %s，跳过 MySQL FULLTEXT，改用标题检索", chunk_n)
            return self._query_mysql_title_fallback(session, match, kbs=kbs, max_level=max_level)
        params: dict = {"q": match, "max_level": max_level}
        kb_sql = ""
        if kbs:
            keys = []
            for i, kb in enumerate(kbs):
                key = f"kb{i}"
                params[key] = kb
                keys.append(f":{key}")
            kb_sql = f" AND d.kb IN ({','.join(keys)})"
        sql = f"""
            SELECT c.id, c.doc_id, c.position, c.text,
                   d.kb, d.title, d.version, d.`lines`, d.stages, d.risk_types,
                   d.data_level, d.enterprise_id, d.status,
                   hits.fts_rank
            FROM (
                SELECT c.id AS chunk_id,
                       MATCH(c.`text`) AGAINST(:q IN BOOLEAN MODE) AS fts_rank
                FROM knowledge_chunks c
                WHERE MATCH(c.`text`) AGAINST(:q IN BOOLEAN MODE)
                ORDER BY fts_rank DESC
                LIMIT 400
            ) hits
            JOIN knowledge_chunks c ON c.id = hits.chunk_id
            JOIN knowledge_docs d ON d.id = c.doc_id
            WHERE d.data_level <= :max_level
              {kb_sql}
            ORDER BY hits.fts_rank DESC
            LIMIT 400
        """
        try:
            return [_hit_dict(row) for row in session.connection().execute(text(sql), params)]
        except Exception as exc:
            message = str(exc)
            if "188" not in message and "result cache limit" not in message.lower() and "2013" not in message:
                raise
            logger.warning("MySQL 全文检索失败，回退到标题匹配：%s (%s)", match, message[:160])
            try:
                session.rollback()
            except Exception:
                pass
            return self._query_mysql_title_fallback(session, match, kbs=kbs, max_level=max_level)

    def _query_mysql_title_fallback(self, session, match: str, *, kbs: list[str] | None, max_level: int) -> list[dict]:
        tokens = [t for t in _TOKEN.findall(match or "") if len(t) >= 2][:6]
        if not tokens:
            return []
        params: dict = {"max_level": max_level}
        kb_sql = ""
        if kbs:
            keys = []
            for i, kb in enumerate(kbs):
                key = f"kb{i}"
                params[key] = kb
                keys.append(f":{key}")
            kb_sql = f" AND d.kb IN ({','.join(keys)})"
        title_clauses = []
        source_clauses = []
        for i, token in enumerate(tokens):
            tkey = f"t{i}"
            skey = f"s{i}"
            params[tkey] = f"%{token}%"
            params[skey] = f"%{token}%"
            title_clauses.append(f"d.title LIKE :{tkey}")
            source_clauses.append(f"d.source LIKE :{skey}")
        sql = f"""
            SELECT c.id, c.doc_id, c.position, c.text,
                   d.kb, d.title, d.version, d.`lines`, d.stages, d.risk_types,
                   d.data_level, d.enterprise_id, d.status,
                   0 AS fts_rank
            FROM knowledge_docs d
            JOIN knowledge_chunks c ON c.doc_id = d.id AND c.seq = 1
            WHERE d.data_level <= :max_level
              AND (({' OR '.join(title_clauses)}) OR ({' OR '.join(source_clauses)}))
              {kb_sql}
            ORDER BY d.id DESC
            LIMIT 100
        """
        return [_hit_dict(row) for row in session.connection().execute(text(sql), params)]

    def _doc_catalog(self, session) -> list[dict]:
        if self._doc_meta is not None:
            return self._doc_meta
        rows = session.connection().exec_driver_sql(
            """
            SELECT id, kb, title, version, `lines`, stages, risk_types, data_level, enterprise_id, status
            FROM knowledge_docs
            """
        )
        self._doc_meta = [
            {
                "id": doc_id,
                "kb": kb,
                "title": title or "",
                "version": version or 1,
                "lines": _json_list(lines),
                "stages": _json_list(stages),
                "risk_types": _json_list(risk_types),
                "data_level": int(data_level or 0),
                "enterprise_id": enterprise_id,
                "status": status or "",
            }
            for doc_id, kb, title, version, lines, stages, risk_types, data_level, enterprise_id, status in rows
        ]
        return self._doc_meta

    def _add_title_hits(self, session, query: str, rows: list[dict], *, kbs: list[str] | None, max_level: int) -> list[dict]:
        """书名里含检索词、但切片没进全文召回前排时，补一条代表切片。否则同名书会互相挤掉。"""
        tokens = [token for token in _TOKEN.findall(query or "") if len(token) >= 4]
        if not tokens:
            return rows
        token = max(tokens, key=len)
        present = {row["doc_id"] for row in rows}
        matched = []
        for doc in self._doc_catalog(session):
            if doc["id"] in present or doc["data_level"] > max_level:
                continue
            if kbs and doc["kb"] not in kbs:
                continue
            hay = _compact(doc["title"])
            if token not in hay:
                continue
            matched.append(doc)
        if not matched:
            return rows
        matched.sort(key=lambda doc: len(token) / max(1, len(_compact(doc["title"]))), reverse=True)
        matched = matched[:40]
        id_list = [doc["id"] for doc in matched]
        found = _bind_execute(
            session,
            f"""
            SELECT c.id, c.doc_id, c.position, c.text
            FROM knowledge_chunks c
            WHERE c.doc_id IN ({",".join("?" for _ in id_list)})
              AND c.id = (SELECT min(id) FROM knowledge_chunks WHERE doc_id = c.doc_id)
            """,
            id_list,
        ).fetchall()
        by_doc = {doc["id"]: doc for doc in matched}
        extra = []
        for chunk_id, doc_id, position, text in found:
            doc = by_doc[doc_id]
            extra.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "position": position or "",
                    "text": text or "",
                    "kb": doc["kb"],
                    "title": doc["title"],
                    "version": doc["version"],
                    "lines": doc["lines"],
                    "stages": doc["stages"],
                    "risk_types": doc["risk_types"],
                    "data_level": doc["data_level"],
                    "enterprise_id": doc["enterprise_id"],
                    "status": doc["status"],
                    "bm25": 1.0,
                }
            )
        return rows + extra

    def _query_like(self, session, query: str, *, kbs: list[str] | None, max_level: int) -> list[dict]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql = """
            SELECT c.id, c.doc_id, c.position, c.text,
                   d.kb, d.title, d.version, d.`lines`, d.stages, d.risk_types,
                   d.data_level, d.enterprise_id, d.status, 0
            FROM knowledge_chunks c
            JOIN knowledge_docs d ON d.id = c.doc_id
            WHERE d.data_level <= ?
              AND (d.title LIKE ? ESCAPE '\\' OR c.text LIKE ? ESCAPE '\\')
        """
        params: list = [max_level, f"%{escaped}%", f"%{escaped}%"]
        if kbs:
            sql += f" AND d.kb IN ({','.join('?' for _ in kbs)})"
            params.extend(kbs)
        sql += " LIMIT 80"
        return [_hit_dict(row) for row in _bind_execute(session, sql, params)]

    def search(
        self,
        query: str,
        *,
        kbs: list[str] | None = None,
        lines: list[str] | None = None,
        stages: list[str] | None = None,
        risk_types: list[str] | None = None,
        max_level: int = 3,
        enterprise_id: int | None = None,
        top_k: int = 6,
        include_expired: bool = False,
    ) -> list[dict]:
        if self._use_fts():
            return self._search_fts(
                query,
                kbs=kbs,
                lines=lines,
                stages=stages,
                risk_types=risk_types,
                max_level=max_level,
                enterprise_id=enterprise_id,
                top_k=top_k,
                include_expired=include_expired,
            )
        with self._lock:
            if self._dirty:
                self._build()
        if not self._rows:
            return []
        idx = []
        for i, r in enumerate(self._rows):
            if kbs and r.kb not in kbs:
                continue
            if not include_expired and r.status != "effective":
                continue
            if r.data_level > max_level:
                continue
            if r.kb in PRIVATE_KBS and enterprise_id is not None and r.enterprise_id not in (None, enterprise_id):
                continue
            idx.append(i)
        if not idx:
            return []
        q_tokens = tokenize(query)
        bm25 = self._bm25(q_tokens, idx)
        dense: dict[int, float] = {}
        if self._vectorizer is not None:
            qv = self._vectorizer.transform([query])
            sims = (self._matrix[idx] @ qv.T).toarray().ravel()
            for i, sim in zip(idx, sims):
                if sim > 0:
                    dense[i] = float(sim)
        fused: dict[int, float] = {}
        for ranking in (bm25, dense):
            for rank, (i, _) in enumerate(sorted(ranking.items(), key=lambda kv: -kv[1])):
                fused[i] = fused.get(i, 0.0) + 1.0 / (60 + rank)
        results = []
        for i, score in fused.items():
            r = self._rows[i]
            boost = 1.0
            if lines and set(lines) & set(r.lines):
                boost += 0.25
            if stages and set(stages) & set(r.stages):
                boost += 0.1
            if risk_types and set(risk_types) & set(r.risk_types):
                boost += 0.3
            results.append((score * boost, i, bm25.get(i, 0.0), dense.get(i, 0.0)))
        results.sort(key=lambda t: -t[0])
        out = []
        for score, i, b, d in results[:top_k]:
            r = self._rows[i]
            out.append(
                {
                    "doc_id": r.doc_id,
                    "chunk_id": r.chunk_id,
                    "kb": r.kb,
                    "kb_name": KB_NAMES.get(r.kb, r.kb),
                    "title": r.title,
                    "version": r.version,
                    "position": r.position,
                    "text": r.text,
                    "score": round(score * 1000, 3),
                    "bm25": round(b, 3),
                    "vector": round(d, 3),
                    "lines": r.lines,
                    "risk_types": r.risk_types,
                    "data_level": r.data_level,
                }
            )
        return out

    def similarity_matrix(self, texts: list[str]) -> np.ndarray:
        vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 3), sublinear_tf=True)
        m = vec.fit_transform(texts)
        return (m @ m.T).toarray()


def _bind_execute(session, sql: str, params: list):
    binds = {f"p{i}": value for i, value in enumerate(params)}
    rewritten = []
    index = 0
    for char in sql:
        if char == "?":
            rewritten.append(f":p{index}")
            index += 1
        else:
            rewritten.append(char)
    return session.connection().execute(text("".join(rewritten)), binds)


def ensure_fulltext() -> None:
    """MySQL 用 ngram 全文索引代替 SQLite FTS5。索引已存在时直接返回。"""
    if engine.dialect.name != "mysql":
        return
    with engine.begin() as conn:
        table = conn.execute(text("SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'knowledge_chunks' LIMIT 1")).first()
        if table is None:
            return
        exists = conn.execute(text("SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'knowledge_chunks' AND index_name = 'ft_knowledge_chunks_text' LIMIT 1")).first()
        if exists is not None:
            return
        logger.info("创建 MySQL 知识全文索引")
        conn.exec_driver_sql("ALTER TABLE knowledge_chunks ADD FULLTEXT INDEX ft_knowledge_chunks_text (`text`) WITH PARSER ngram")


def _dbapi(session):
    raw = session.connection().connection
    return getattr(raw, "driver_connection", raw)


def _fts_match(query: str) -> str | None:
    parts = []
    seen = set()
    for token in sorted(_TOKEN.findall(query or ""), key=len, reverse=True):
        if token in seen:
            continue
        seen.add(token)
        parts.append('"' + token.replace('"', "") + '"')
        if len(parts) >= 6:
            break
    if not parts:
        return None
    return " OR ".join(parts)


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _hit_dict(row) -> dict:
    chunk_id, doc_id, position, text, kb, title, version, lines, stages, risk_types, data_level, enterprise_id, status, fts_rank = row
    return {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "position": position or "",
        "text": text or "",
        "kb": kb,
        "title": title or "",
        "version": version or 1,
        "lines": _json_list(lines),
        "stages": _json_list(stages),
        "risk_types": _json_list(risk_types),
        "data_level": int(data_level or 0),
        "enterprise_id": enterprise_id,
        "status": status or "",
        "bm25": -float(fts_rank or 0.0),
    }


def _compact(text: str) -> str:
    return "".join(_TOKEN.findall(text or ""))


def _fuse(query: str, rows: list[dict], *, lines: list[str] | None, stages: list[str] | None, risk_types: list[str] | None, top_k: int) -> list[dict]:
    if not rows:
        return []
    dense: dict[int, float] = {}
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 3), sublinear_tf=True)
    matrix = vectorizer.fit_transform([row["title"] + " " + row["text"] for row in rows])
    sims = (matrix @ vectorizer.transform([query]).T).toarray().ravel()
    for i, sim in enumerate(sims):
        if sim > 0:
            dense[i] = float(sim)
    lexical = {i: row["bm25"] for i, row in enumerate(rows) if row["bm25"] > 0}
    fused: dict[int, float] = {}
    for ranking in (lexical, dense):
        for rank, (i, _) in enumerate(sorted(ranking.items(), key=lambda kv: -kv[1])):
            fused[i] = fused.get(i, 0.0) + 1.0 / (60 + rank)
    needle = _compact(query)
    ranked = []
    for i, score in fused.items():
        row = rows[i]
        boost = 1.0
        if lines and set(lines) & set(row["lines"]):
            boost += 0.25
        if stages and set(stages) & set(row["stages"]):
            boost += 0.1
        if risk_types and set(risk_types) & set(row["risk_types"]):
            boost += 0.3
        if len(needle) >= 4 and needle in _compact(row["title"]):
            boost += 1.0
        ranked.append((score * boost, i, row["bm25"], dense.get(i, 0.0)))
    ranked.sort(key=lambda item: -item[0])
    first, rest, seen = [], [], set()
    for item in ranked:
        doc_id = rows[item[1]]["doc_id"]
        if doc_id in seen:
            rest.append(item)
        else:
            seen.add(doc_id)
            first.append(item)
    out = []
    for score, i, bm25, vector in (first + rest)[:top_k]:
        row = rows[i]
        out.append(
            {
                "doc_id": row["doc_id"],
                "chunk_id": row["chunk_id"],
                "kb": row["kb"],
                "kb_name": KB_NAMES.get(row["kb"], row["kb"]),
                "title": row["title"],
                "version": row["version"],
                "position": row["position"],
                "text": row["text"],
                "score": round(score * 1000, 3),
                "bm25": round(bm25, 3),
                "vector": round(vector, 3),
                "lines": row["lines"],
                "risk_types": row["risk_types"],
                "data_level": row["data_level"],
            }
        )
    return out


knowledge_index = KnowledgeIndex()


def record_citations(doc_ids: list[int]) -> None:
    if not doc_ids:
        return
    with session_scope() as s:
        for doc in s.scalars(select(KnowledgeDoc).where(KnowledgeDoc.id.in_(set(doc_ids)))):
            times = doc_ids.count(doc.id)
            doc.cite_count = (doc.cite_count or 0) + times
            for _ in range(times):
                s.add(KnowledgeCitation(doc_id=doc.id))
    try:
        from ..services.knowledge_admin import invalidate_library_stats

        invalidate_library_stats()
    except Exception:
        pass
