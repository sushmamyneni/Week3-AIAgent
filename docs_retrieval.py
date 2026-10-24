# Stage 5: retrieval. Find the chunks that best match a question.
#
# What this file does when finished:
#   Step 1: Load the settings and embed a question with the bge query prefix
#   Step 2: Dense search: ask Pinecone for the nearest chunks by meaning
#   Step 3: BM25 search: keyword scoring over data/chunks.jsonl, in memory
#   Step 4: Merge the two result lists with reciprocal rank fusion, keep the top results
#   Step 5: Run the 10 test questions, print sources and scores, and choose the threshold
#
# Usage:
#   python 5a_retrieval.py      runs the quick test at the bottom of the file
import yaml

# ---------------------------------------------------------------------------
# Step 1: Load the settings and embed a question
# ---------------------------------------------------------------------------
from langchain_huggingface import HuggingFaceEmbeddings

# Settings come from config.yaml, so nothing tunable is hardcoded here
cfg = yaml.safe_load(open("config.yaml"))
emb_cfg = cfg["embedding"]
ret_cfg = cfg["retrieval"]

# The same model and settings that embedded the chunks in Stage 4. A question and a chunk
# can only be compared if both vectors come from the same model.
# normalize_embeddings makes every vector length 1, which suits cosine similarity.
embedder = HuggingFaceEmbeddings(
    model_name=emb_cfg["model"],
    encode_kwargs={"normalize_embeddings": True},
)

# embed_query is the method for questions (embed_documents was for chunks).
# The bge model expects a short instruction in front of a search question; the prefix
# comes from config. It is added to questions only, never to the stored chunks.
def embed_question(question):
    return embedder.embed_query(ret_cfg["query_prefix"] + question)

# ---------------------------------------------------------------------------
# Step 2: Dense search against Pinecone
# ---------------------------------------------------------------------------
import os
from dotenv import load_dotenv
from pinecone import Pinecone

# load_dotenv copies the keys from .env into environment variables.
# The key is read from the environment and never printed.
load_dotenv()
pc_cfg = cfg["pinecone"]
index = Pinecone(api_key=os.environ["PINECONE_API_KEY"]).Index(pc_cfg["index_name"])

# Return the top_k nearest chunks, best first, as a list of dicts.
# tool="airflow" or "spark" limits the search to one tool; None searches both.
def dense_search(question, top_k=None, tool=None):
    res = index.query(
        vector=embed_question(question),
        # Default comes from config (retrieval.dense_top_k = 8)
        top_k=top_k or ret_cfg["dense_top_k"],
        namespace=pc_cfg["namespace"],
        # Metadata filter: only chunks whose "tool" field equals the given tool.
        # None means no filter.
        filter={"tool": {"$eq": tool}} if tool else None,
        # include_metadata brings back text, source, heading_path and the other stored fields
        include_metadata=True,
    )
    # m.score is the cosine similarity (higher = closer). **m.metadata copies all the
    # stored fields into the same dict, so a result has id, score, text, source, ...
    return [{"id": m.id, "score": m.score, **m.metadata} for m in res.matches]

# ---------------------------------------------------------------------------
# Step 3: BM25 keyword search over data/chunks.jsonl (in memory, no service needed)
# ---------------------------------------------------------------------------
import json
import re
from pathlib import Path
from rank_bm25 import BM25Okapi

# Very common words carry no meaning for keyword scoring. Without this, a question like
# "How do I set retries on a task?" also matches every "How do I ..." heading in the docs.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
    "how", "i", "if", "in", "is", "it", "my", "of", "on", "or", "should", "that", "the",
    "to", "what", "when", "where", "which", "why", "with", "you", "your",
}

# Split text into lowercase tokens. A dotted name such as spark.sql.shuffle.partitions
# is kept whole (an exact config key match is very valuable) AND split into its
# parts, so "shuffle partitions" in a question can still match it.
def tokenize(text):
    tokens = []
    for t in re.findall(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*", text.lower()):
        if t in STOPWORDS:
            continue
        tokens.append(t)
        if "." in t:
            tokens.extend(t.split("."))
    return tokens


# Load the same chunks that were indexed in Stage 4, and build the BM25 index once.
# chunk["text"] starts with the heading path, so headings are searchable too.
chunks = [json.loads(line) for line in open(Path(cfg["paths"]["data_dir"]) / "chunks.jsonl", encoding="utf-8")]
bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])

# Return the top_k best keyword matches, best first, in the same shape as dense_search.
# BM25 scores are not capped at 1 and depend on the question, so they are only
# comparable within one result list, never against dense scores.
def bm25_search(question, top_k=None, tool=None):
    scores = bm25.get_scores(tokenize(question))
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    out = []
    for i in ranked:
        # A score of 0 means no question word appears in the chunk, so stop
        if scores[i] <= 0:
            break
        if tool and chunks[i]["tool"] != tool:
            continue
        out.append({**chunks[i], "score": float(scores[i])})
        if len(out) == (top_k or ret_cfg["bm25_top_k"]):
            break
    return out

# ---------------------------------------------------------------------------
# Step 4: Merge dense and BM25 results with reciprocal rank fusion (RRF)
# ---------------------------------------------------------------------------

# Each chunk earns 1 / (k + rank) from every list it appears in, and the amounts are added.
# Only ranks are used, so the two score scales never have to be compared.
# Returns the top_n chunks, best first, each with its rrf, dense_score and bm25_score
# (None when that search did not return the chunk).
def rrf_merge(dense, keyword, top_n):
    k = ret_cfg["rrf_k"]
    merged = {}
    for name, results in (("dense", dense), ("bm25", keyword)):
        for rank, r in enumerate(results, start=1):
            # The first time an id is seen, copy its fields (without the list's own "score")
            base = {key: val for key, val in r.items() if key != "score"}
            m = merged.setdefault(r["id"], {**base, "rrf": 0.0, "dense_score": None, "bm25_score": None})
            m["rrf"] += 1 / (k + rank)
            m[f"{name}_score"] = r["score"]
    return sorted(merged.values(), key=lambda m: m["rrf"], reverse=True)[:top_n]

# The function the rest of the pipeline calls: search, merge, and decide relevance.
#   Dense search runs once per tool. A tool takes part only if its closest chunk is within
#   tool_margin of the overall best, so a question about one tool gets all final_top_k
#   slots from that tool, while a cross-tool question (both tools close) splits them evenly.
#   tool="spark" (or "airflow") limits the search to that tool.
# best_dense_score is the cosine score of the closest chunk over all tools searched.
# It is what the threshold is compared against, because dense scores are comparable
# across questions (BM25 scores and RRF scores are not).
def retrieve(question, tool=None):
    tools = [tool] if tool else ret_cfg["tools"]
    dense = {t: dense_search(question, tool=t) for t in tools}
    best = max((d[0]["score"] for d in dense.values() if d), default=0.0)

    # Tools whose closest chunk is within tool_margin of the best one
    active = [t for t in tools if dense[t] and dense[t][0]["score"] >= best - ret_cfg["tool_margin"]]

    # Split the slots evenly between the active tools (6 -> 6, or 3 + 3)
    # A question that spans both tools needs room from each, so it gets more slots in total
    total = ret_cfg["cross_tool_final_top_k"] if len(active) > 1 else ret_cfg["final_top_k"]
    keep = total // len(active) if active else 0
    chunks = []
    for t in active:
        chunks += rrf_merge(dense[t], bm25_search(question, tool=t), keep)
    # RRF scores come from the same formula in every list, so they can be sorted together
    chunks.sort(key=lambda c: c["rrf"], reverse=True)

    return {
        "chunks": chunks,
        "tools": active,
        "best_dense_score": best,
        # False means the docs probably do not cover the question: Stage 6 refuses
        "relevant": best >= ret_cfg["relevance_threshold"],
    }

# -------------------------------------------------------
# Quick test: runs only when this file is run directly, not when it is imported
# -------------------------------------------------------
# Quick test: runs only when this file is run directly, not when it is imported
if __name__ == "__main__":
    for q in ["How do I set retries on a task?",
              "How do I change the number of shuffle partitions in Spark?",
              "What is the Snowflake credit cost?"]:
        out = retrieve(q)
        print("\n" + q, "| best dense score:", round(out["best_dense_score"], 3))
        for c in out["chunks"]:
            d = "-" if c["dense_score"] is None else round(c["dense_score"], 3)
            b = "-" if c["bm25_score"] is None else round(c["bm25_score"], 1)
            print(f'  rrf {c["rrf"]:.4f} | dense {d} | bm25 {b} | {c["source"].split("docs/", 1)[1]} | {c["heading_path"][:50]}')

