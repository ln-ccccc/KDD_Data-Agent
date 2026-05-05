import re
from pathlib import Path
from typing import Dict, List, Any
import numpy as np
from rank_bm25 import BM25Okapi
from sklearn.metrics.pairwise import cosine_similarity

from data_agent_baseline.benchmark.schema import PublicTask

# Lazy load model to avoid loading overhead for easy tasks
_embedding_model = None

def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        # Use a lightweight but capable embedding model
        _embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
    return _embedding_model

def list_knowledge_headings(task: PublicTask, query: str = "") -> dict[str, object]:
    """
    Returns a Table of Contents (TOC) of the knowledge.md file by extracting all Markdown headings.
    Useful for quick navigation in Easy/Medium tasks before reading specific sections.
    """
    knowledge_path = task.context_dir / "knowledge.md"
    if not knowledge_path.exists():
        return {"error": "knowledge.md not found in context directory."}
        
    content = knowledge_path.read_text(encoding="utf-8")
    headings = [line.strip() for line in content.split('\n') if line.startswith('#')]
    
    if not headings:
        return {"message": "No headings found. The document might be unstructured."}
        
    return {
        "file": "knowledge.md",
        "headings": headings,
        "instruction": "Use read_knowledge_section to read the specific content under a heading."
    }

def read_knowledge_section(task: PublicTask, heading: str) -> dict[str, object]:
    """
    Read the exact content under a specific heading from knowledge.md.
    Provide the exact heading string (e.g. '# Patient Status Code') returned by list_knowledge_headings.
    """
    knowledge_path = task.context_dir / "knowledge.md"
    if not knowledge_path.exists():
        return {"error": "knowledge.md not found in context directory."}
        
    content = knowledge_path.read_text(encoding="utf-8")
    chunks = chunk_markdown_by_headings(content)
    
    # Exact or substring match for the heading
    for chunk in chunks:
        if heading.lower() in chunk["heading"].lower():
            return {
                "section": chunk["heading"],
                "content": chunk["content"]
            }
            
    return {"error": f"Section matching '{heading}' not found."}

def chunk_markdown_by_headings(content: str) -> List[Dict[str, str]]:
    """Chunk markdown document by headings to preserve business context."""
    lines = content.split('\n')
    chunks = []
    current_heading = "General"
    current_content = []
    
    for line in lines:
        if line.startswith('#'):
            if current_content:
                chunks.append({
                    "heading": current_heading,
                    "content": '\n'.join(current_content).strip()
                })
            current_heading = line.strip()
            current_content = [line]
        else:
            current_content.append(line)
            
    if current_content:
        chunks.append({
            "heading": current_heading,
            "content": '\n'.join(current_content).strip()
        })
        
    # Filter out empty chunks
    return [c for c in chunks if c["content"]]

def search_knowledge_doc(task: PublicTask, query: str, top_k: int = 3) -> dict[str, object]:
    """
    Search the knowledge.md file for specific business rules or term definitions using Hybrid RAG.
    For hard/extreme tasks, it uses BM25 (keyword match) + Embedding (semantic match) with RRF ranking.
    For easy/medium tasks, it defaults to fast BM25 matching.
    """
    knowledge_path = task.context_dir / "knowledge.md"
    if not knowledge_path.exists():
        return {"error": "knowledge.md not found in context directory."}
        
    content = knowledge_path.read_text(encoding="utf-8")
    chunks = chunk_markdown_by_headings(content)
    
    if not chunks:
        return {"error": "knowledge.md is empty."}
        
    # Pre-process chunks for BM25
    tokenized_corpus = [chunk["content"].lower().split(" ") for chunk in chunks]
    bm25 = BM25Okapi(tokenized_corpus)
    tokenized_query = query.lower().split(" ")
    
    # Get BM25 scores
    bm25_scores = bm25.get_scores(tokenized_query)
    
    # Use Hybrid RAG (BM25 + Embedding + RRF) if available
    try:
        model = get_embedding_model()
        # Encode corpus and query
        corpus_embeddings = model.encode([c["content"] for c in chunks])
        query_embedding = model.encode([query])
        
        # Get Semantic scores
        semantic_scores = cosine_similarity(query_embedding, corpus_embeddings)[0]
        
        # Reciprocal Rank Fusion (RRF)
        # RRF Score = 1 / (k + rank)
        k_rrf = 60
        
        bm25_ranks = np.argsort(bm25_scores)[::-1]
        semantic_ranks = np.argsort(semantic_scores)[::-1]
        
        rrf_scores = np.zeros(len(chunks))
        for i in range(len(chunks)):
            # Find rank of item i in bm25
            bm25_rank = np.where(bm25_ranks == i)[0][0] + 1
            # Find rank of item i in semantic
            semantic_rank = np.where(semantic_ranks == i)[0][0] + 1
            
            rrf_scores[i] = (1.0 / (k_rrf + bm25_rank)) + (1.0 / (k_rrf + semantic_rank))
            
        # Final top-k based on RRF
        top_indices = np.argsort(rrf_scores)[::-1][:top_k]
        strategy_used = "Hybrid RAG (BM25 + Semantic Embedding + RRF)"
        
    except Exception as e:
        # Fallback to BM25 if embedding fails (e.g., OOM)
        top_indices = np.argsort(bm25_scores)[::-1][:top_k]
        strategy_used = f"BM25 (Fallback due to embedding error: {str(e)})"
        
    results = []
    for idx in top_indices:
        results.append({
            "section": chunks[idx]["heading"],
            "content": chunks[idx]["content"]
        })
            
    if not results:
        return {
            "query": query,
            "message": "No relevant business rules found for this query.",
            "strategy": strategy_used
        }
        
    return {
        "query": query,
        "strategy": strategy_used,
        "results": results
    }
