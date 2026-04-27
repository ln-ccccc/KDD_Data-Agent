import json
import logging
from pathlib import Path
from typing import Any, Dict, List
import numpy as np

# Lazy load models to avoid loading overhead for simple tasks
_schema_embedding_model = None

def get_schema_embedding_model():
    global _schema_embedding_model
    if _schema_embedding_model is None:
        from sentence_transformers import SentenceTransformer
        # Use Qwen2-1.5B-instruct for Schema Linking (sweet spot for cross-lingual and complex schema)
        # Note: If memory is too tight on the target environment, we can fallback to bge-m3 or all-MiniLM-L6-v2
        # But per the plan, Alibaba-NLP/gte-Qwen2-1.5B-instruct is the target.
        # We will use 'Alibaba-NLP/gte-Qwen2-1.5B-instruct'
        
        try:
            logging.info("Loading Alibaba-NLP/gte-Qwen2-1.5B-instruct for Schema Linking...")
            _schema_embedding_model = SentenceTransformer('Alibaba-NLP/gte-Qwen2-1.5B-instruct', trust_remote_code=True)
            logging.info("Model loaded successfully.")
        except Exception as e:
            logging.warning(f"Failed to load gte-Qwen2-1.5B-instruct: {e}. Falling back to all-MiniLM-L6-v2")
            _schema_embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
            
    return _schema_embedding_model

def build_schema_index_blocks(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Converts a SchemaCatalog JSON object into retrievable 'document blocks'.
    Each block represents a Table or a specific Column with its distinct values.
    """
    blocks = []
    
    tables = catalog.get("tables", [])
    for table in tables:
        table_name = table.get("table_name", "unknown")
        source_file = table.get("source_file", "unknown")
        columns = table.get("columns", [])
        
        # 1. Table-level block (helps identify which table is relevant overall)
        table_col_names = [c.get("name", "") for c in columns]
        table_text = f"Table: {table_name}\nSource: {source_file}\nColumns: {', '.join(table_col_names)}"
        
        blocks.append({
            "type": "table",
            "table_name": table_name,
            "source_file": source_file,
            "text_for_embedding": table_text,
            "display_text": table_text
        })
        
        # 2. Column-level blocks (helps identify specific columns and their values)
        for col in columns:
            col_name = col.get("name", "")
            col_type = col.get("type", "")
            
            # Support both sqlite format (sample_values array) and file format (sample_values_str)
            samples = []
            if "sample_values" in col:
                samples = col["sample_values"]
                samples_str = f"Sample values: {samples}" if samples else ""
            else:
                samples_str = col.get("sample_values_str", "")
                
            col_text = f"Table: {table_name}\nColumn: {col_name}\nType: {col_type}\n{samples_str}"
            
            blocks.append({
                "type": "column",
                "table_name": table_name,
                "column_name": col_name,
                "source_file": source_file,
                "text_for_embedding": col_text,
                "display_text": col_text
            })
            
    return blocks

def retrieve_schema_context(catalog_path: Path, query: str, top_k: int = 5) -> Dict[str, Any]:
    """
    Given a user query and a path to schema_catalog.json, 
    perform Hybrid Retrieval (BM25 + Dense Embedding) to return Top-K schema blocks.
    """
    if not catalog_path.exists():
        return {"error": f"Schema catalog not found at {catalog_path}"}
        
    try:
        with open(catalog_path, 'r', encoding='utf-8') as f:
            catalog = json.load(f)
    except Exception as e:
        return {"error": f"Failed to parse schema catalog: {e}"}
        
    blocks = build_schema_index_blocks(catalog)
    if not blocks:
        return {"error": "Schema catalog is empty."}
        
    # Prepare text corpus
    corpus = [b["text_for_embedding"] for b in blocks]
    
    # 1. BM25 Lexical Search
    from rank_bm25 import BM25Okapi
    tokenized_corpus = [text.lower().split(" ") for text in corpus]
    bm25 = BM25Okapi(tokenized_corpus)
    tokenized_query = query.lower().split(" ")
    bm25_scores = bm25.get_scores(tokenized_query)
    
    # 2. Dense Embedding Search (Qwen2-1.5B)
    try:
        from sklearn.metrics.pairwise import cosine_similarity
        model = get_schema_embedding_model()
        
        # Format query with instruction if using Qwen2-1.5B-instruct
        instruction = "Given a natural language query, retrieve the relevant database tables and columns that answer the query: "
        formatted_query = f"{instruction}{query}"
        
        corpus_embeddings = model.encode(corpus)
        query_embedding = model.encode([formatted_query])
        
        semantic_scores = cosine_similarity(query_embedding, corpus_embeddings)[0]
        
        # 3. Reciprocal Rank Fusion (RRF)
        k_rrf = 60
        bm25_ranks = np.argsort(bm25_scores)[::-1]
        semantic_ranks = np.argsort(semantic_scores)[::-1]

        rrf_scores = np.zeros(len(corpus))
        for i in range(len(corpus)):
            bm25_rank = np.where(bm25_ranks == i)[0][0] + 1
            semantic_rank = np.where(semantic_ranks == i)[0][0] + 1
            rrf_scores[i] = (1.0 / (k_rrf + bm25_rank)) + (1.0 / (k_rrf + semantic_rank))

        top_indices = np.argsort(rrf_scores)[::-1][:top_k]
        strategy = "Hybrid (BM25 + gte-Qwen2-1.5B RRF)"
        
    except Exception as e:
        logging.warning(f"Dense retrieval failed: {e}. Falling back to BM25.")
        top_indices = np.argsort(bm25_scores)[::-1][:top_k]
        strategy = "BM25 Only (Fallback)"
        
    # Format results
    results = []
    for idx in top_indices:
        block = blocks[idx]
        results.append(block["display_text"])
        
    return {
        "query": query,
        "strategy": strategy,
        "constrained_schema": results
    }
