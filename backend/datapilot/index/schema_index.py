"""Schema index (SPEC §5.3, §7.1): column cards ranked by BM25 + Gemini embeddings, FK join paths,
and the schema renderer used in prompts.

Embeddings are optional: without a Gemini key the ranker is lexical only (and the ablation records it).
Card embeddings are computed once per schema hash and cached in the app database.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

import networkx as nx
from sqlalchemy import delete, select

from datapilot.config import get_settings
from datapilot.db.schema import schema_index
from datapilot.db.session import run_db
from datapilot.index.catalog import Column, Database, Table
from datapilot.llm import quota
from datapilot.llm.providers import gemini_embed

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[A-Za-z]+|\d+")


def tokenize(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)  # camelCase → camel Case
    out = []
    for t in _TOKEN.findall(text.replace("_", " ")):
        t = t.lower()
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]  # crude plural folding: invoices → invoice
        out.append(t)
    return out


def column_card(table: Table, col: Column) -> str:
    parts = [f"{table.name}.{col.name}: {col.type or 'ANY'}"]
    if col.description:
        parts.append(col.description)
    if col.value_description:
        parts.append(col.value_description[:160])
    if col.samples and not col.pii:
        parts.append("e.g. " + ", ".join(col.samples))
    return " — ".join(parts)


@dataclass
class DbIndex:
    keys: list[tuple[str, str]]
    cards: list[str]
    tokens: list[list[str]]
    df: Counter
    avgdl: float
    embeddings: list[list[float]] | None = None
    graph: nx.Graph = field(default_factory=nx.Graph)


_indexes: dict[str, DbIndex] = {}


def build_index(db: Database) -> DbIndex:
    keys, cards = [], []
    for t in db.tables.values():
        for c in t.columns:
            keys.append((t.name, c.name))
            cards.append(column_card(t, c))
    tokens = [tokenize(card.split(" — ")[0] + " " + card) for card in cards]
    df: Counter = Counter()
    for toks in tokens:
        df.update(set(toks))
    idx = DbIndex(keys, cards, tokens, df, sum(map(len, tokens)) / max(1, len(tokens)), graph=_fk_graph(db))
    _indexes[db.db_id] = idx
    return idx


def get_index(db: Database) -> DbIndex:
    idx = _indexes.get(db.db_id)
    return idx if idx is not None else build_index(db)


def _fk_graph(db: Database) -> nx.Graph:
    g = nx.Graph()
    g.add_nodes_from(db.tables)
    for fk in db.foreign_keys:
        ref = db.table(fk.ref_table)
        if ref is None:
            continue
        g.add_edge(fk.table, ref.name, on=f'"{fk.table}"."{fk.column}" = "{ref.name}"."{fk.ref_column}"')
    # Undeclared keys (common in BIRD): same-named id column that is the PK of exactly one table.
    pk_owner: dict[str, str] = {}
    for t in db.tables.values():
        pks = [c.name for c in t.columns if c.pk]
        if len(pks) == 1 and pks[0].lower() not in ("id",):
            pk_owner.setdefault(pks[0].lower(), t.name)
    for t in db.tables.values():
        for c in t.columns:
            owner = pk_owner.get(c.name.lower())
            if owner and owner != t.name and not g.has_edge(t.name, owner):
                g.add_edge(t.name, owner, on=f'"{t.name}"."{c.name}" = "{owner}"."{c.name}"', inferred=True)
    return g


def join_paths(db: Database, tables: list[str]) -> tuple[list[str], list[str]]:
    """Connect the selected tables through the FK graph. Returns (all tables incl. bridges, join conditions)."""
    g = get_index(db).graph
    tables = [t for t in tables if t in g]
    if len(tables) < 2:
        return tables, []
    keep = list(tables)
    joins: list[str] = []
    anchor = tables[0]
    for t in tables[1:]:
        try:
            path = nx.shortest_path(g, anchor, t)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        for a, b in zip(path, path[1:], strict=False):
            for x in (a, b):
                if x not in keep:
                    keep.append(x)
            cond = g.edges[a, b]["on"]
            if cond not in joins:
                joins.append(cond)
    return keep, joins


def _bm25(idx: DbIndex, query: str) -> list[float]:
    q = tokenize(query)
    n = len(idx.tokens)
    scores = []
    for toks in idx.tokens:
        tf = Counter(toks)
        s = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n - idx.df[term] + 0.5) / (idx.df[term] + 0.5))
            f = tf[term]
            s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(toks) / idx.avgdl))
        scores.append(s)
    return scores


async def ensure_embeddings(db: Database) -> bool:
    """Load or compute column-card embeddings for this schema hash. Returns True when vectors are available."""
    s = get_settings()
    idx = get_index(db)
    if idx.embeddings is not None:
        return True
    if not s.gemini_ready:
        return False

    def load(conn):  # type: ignore[no-untyped-def]
        return conn.execute(
            select(schema_index.c.table_name, schema_index.c.column_name, schema_index.c.embedding).where(
                (schema_index.c.db_id == db.db_id)
                & (schema_index.c.schema_hash == db.schema_hash)
                & (schema_index.c.embed_model == s.embed_model)
            )
        ).all()

    rows = await run_db(load)
    stored = {(r.table_name, r.column_name): r.embedding for r in rows if r.embedding}
    if len(stored) == len(idx.keys):
        idx.embeddings = [stored[k] for k in idx.keys]
        return True
    if not quota.available("embed"):
        return False
    try:
        vecs = await gemini_embed(idx.cards, "RETRIEVAL_DOCUMENT")
        quota.record("embed", s.embed_model, sum(len(c) // 4 for c in idx.cards))
    except Exception as e:  # noqa: BLE001
        log.warning("schema embeddings unavailable for %s: %s", db.db_id, e)
        return False
    idx.embeddings = vecs
    pii = {(t.name, c.name) for t in db.tables.values() for c in t.columns if c.pii}

    def store(conn):  # type: ignore[no-untyped-def]
        conn.execute(delete(schema_index).where(schema_index.c.db_id == db.db_id))
        conn.execute(
            schema_index.insert(),
            [
                {
                    "db_id": db.db_id,
                    "table_name": k[0],
                    "column_name": k[1],
                    "card": card,
                    "is_pii": k in pii,
                    "embedding": v,
                    "embed_model": s.embed_model,
                    "schema_hash": db.schema_hash,
                }
                for k, card, v in zip(idx.keys, idx.cards, vecs, strict=True)
            ],
        )

    await run_db(store)
    return True


async def embed_query(text: str) -> list[float] | None:
    s = get_settings()
    if not s.gemini_ready or not quota.available("embed"):
        return None
    try:
        vec = (await gemini_embed([text], "RETRIEVAL_QUERY"))[0]
        quota.record("embed", s.embed_model, len(text) // 4)
        return vec
    except Exception as e:  # noqa: BLE001
        log.info("query embedding failed: %s", e)
        return None


def rank_columns(
    db: Database, query: str, k: int = 25, query_vec: list[float] | None = None
) -> list[tuple[str, str, float]]:
    idx = get_index(db)
    lex = _bm25(idx, query)
    mx = max(lex) or 1.0
    scores = [v / mx for v in lex]
    if query_vec is not None and idx.embeddings is not None:
        cos = [sum(a * b for a, b in zip(query_vec, e, strict=False)) for e in idx.embeddings]
        lo, hi = min(cos), max(cos)
        span = (hi - lo) or 1.0
        scores = [0.4 * s + 0.6 * (c - lo) / span for s, c in zip(scores, cos, strict=True)]
    order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
    return [(idx.keys[i][0], idx.keys[i][1], round(scores[i], 4)) for i in order if scores[i] > 0]


def _ddl_type(c: Column) -> str:
    return c.type or ""


def _qi(name: str) -> str:
    return f'"{name}"' if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else name


def render_schema(
    db: Database,
    selection: dict[str, list[str]] | None = None,
    *,
    with_samples: bool = True,
    with_descriptions: bool = True,
) -> str:
    """Compact CREATE TABLE text for prompts. PII samples are never included (SPEC §10.4)."""
    out = []
    tables = selection.keys() if selection else db.tables.keys()
    for tname in tables:
        t = db.table(tname)
        if t is None:
            continue
        wanted = {c.lower() for c in (selection or {}).get(tname, [])} if selection else None
        lines: list[tuple[str, str]] = []
        for c in t.columns:
            fk = next((f for f in t.foreign_keys if f.column == c.name), None)
            is_key = c.pk or fk is not None or c.name in t.indexed_columns
            if wanted is not None and c.name.lower() not in wanted and not is_key:
                continue
            line = f"  {_qi(c.name)} {_ddl_type(c)}".rstrip()
            if c.pk:
                line += " PRIMARY KEY"
            if fk:
                line += f" REFERENCES {_qi(fk.ref_table)}({_qi(fk.ref_column)})"
            notes = []
            if with_descriptions and c.description and c.description.lower() != c.name.lower():
                notes.append(c.description[:140])
            if with_descriptions and c.value_description:
                notes.append(c.value_description[:140])
            if with_samples and c.samples and not c.pii and not (is_key and not c.is_text):
                notes.append("e.g. " + ", ".join(repr(s) for s in c.samples))
            if c.pii:
                notes.append("personal data")
            lines.append((line, " | ".join(notes)))
        body = []
        for i, (line, note) in enumerate(lines):
            sep = "," if i < len(lines) - 1 else ""
            body.append(f"{line}{sep}" + (f"  -- {note}" if note else ""))
        desc = f". {t.description}" if t.description else ""
        out.append(f"CREATE TABLE {_qi(t.name)} (  -- {t.row_count:,} rows{desc}\n" + "\n".join(body) + "\n);")
    return "\n".join(out)
