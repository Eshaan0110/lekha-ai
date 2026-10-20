# ADR-0012: Embedding models and how they run locally

- **Status:** Proposed (the owner approved downloading BGE-M3 on 2026-10-20; the choice of
  ONNX Runtime over PyTorch, and the interim MiniLM model, await formal acceptance)
- **Date:** 2026-10-21

## Context
Phase 2 turns every chunk into an embedding: a list of numbers that captures its meaning.
The approved baseline is **BGE-M3 with local inference** (CLAUDE.md §5). On the development
laptop:
- the internet connection is slow (about 70–120 KB/s);
- there is 15.4 GB of RAM, an RTX 3050 with 4 GB, and no PyTorch installed.

The usual route, PyTorch plus sentence-transformers, means about 5 GB of downloads. Tests
must never need a model, a GPU or the network (CLAUDE.md §5, §7).

## Decision
1. **One `Embedder` interface** in `model_layer/` with three implementations, chosen by
   `LEKHA_EMBEDDER`:
   - `fake`: a word-hashing **simulation**, the default and the one used in tests;
   - `minilm`: all-MiniLM-L6-v2, a small real model for English only. It is an interim
     option, run with numpy;
   - `bge-m3`: the approved multilingual model.
2. **BGE-M3 runs from the official ONNX export** published in the BAAI/bge-m3 repository,
   using ONNX Runtime on the CPU. No PyTorch is needed. `LEKHA_EMBEDDER_DEVICE=cuda` uses the
   GPU only if a CUDA-enabled ONNX Runtime is installed; otherwise it falls back to the CPU
   and logs a warning.
3. **Model files are checksum-pinned.**
   - BGE-M3: `model.onnx`, `model.onnx_data` and `tokenizer.json` must match the
     repository's published SHA-256.
   - MiniLM: the weights must match a recorded SHA-256.
   - Any other file is refused at startup.
4. **One Qdrant collection per model.** The index takes its vector size from the embedder
   and refuses a collection built for a different size. Vectors from different models are
   never mixed; switching models means re-indexing.
5. **Model files live in `models/`**, which is git-ignored. They are fetched only with the
   owner's OK.

## Alternatives considered
- **PyTorch + sentence-transformers:** the reference implementation, with simple GPU use.
  It needs about 2.5 GB more to download and a much larger install.
- **A hosted embedding API:** sends document text to a third party, which conflicts with data
  residency and the "no paid services" rule.
- **Waiting for BGE-M3 before any real search:** the MiniLM step let real meaning search be
  shown the same day, with no large download.

## Why it fits Lekha
Real multilingual search runs locally, keeps the approved model, adds only small
dependencies (onnxruntime, tokenizers, numpy), and stays swappable behind one interface.

## Trade-offs
- CPU inference is slower than a GPU. Measured: about 0.06 s per short text, and about 5 s
  to load and verify the model.
- The numpy MiniLM forward pass is our own code. To guard against mistakes, a test compares
  its output with the official library's vectors (maximum difference 1.2e-7).
- Dense embeddings are weak at exact identifiers. Measured: BGE-M3 found 87% of corpus
  documents by reference code alone. Keyword (hybrid) search, planned for Phase 3, covers
  that gap.

## Security and operational implications
- Checksums stop a tampered or partial model file from being used silently.
- Models run in-process with no network calls at inference time.
- Weights are never committed. The `models/` folder needs backing up only if re-downloading
  is impractical.

## Cost
No licence costs (both models are open-weight; BGE-M3 is MIT and MiniLM is Apache-2.0) and no
cloud charges. About 2.3 GB of disk for BGE-M3.

## Revisit when
- A GPU-backed server is provisioned (consider CUDA ONNX Runtime or PyTorch).
- Phase 3 adds hybrid search or a reranker.
- A model with better Kannada quality becomes available.
- The owner prefers dropping the MiniLM option once BGE-M3 is standard.
