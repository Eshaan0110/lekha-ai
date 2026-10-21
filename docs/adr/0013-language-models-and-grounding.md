# ADR-0013: Language models and the grounding rules for answers

- **Status:** Accepted by the owner on 2026-10-21 (Phase 3 plan decisions D1 and D4)
- **Date:** 2026-10-21

## Context
Phase 3 answers questions with a language model (LLM). The production default, Claude via
AWS Bedrock Mumbai (plan ADR-003), is blocked until the AWS account exists. The laptop
already has a local model (`qwen3.5:9b` in Ollama). The plan requires every answer to cite
real sources, to abstain when evidence is weak, and to treat documents as untrusted data.
Tests must never depend on a paid API or a real model (CLAUDE.md §7).

## Decision
1. **One `LanguageModel` interface** (`model_layer/llm.py`) with adapters chosen by
   `LEKHA_LLM`:
   - `none`: the default; `/ask` answers 503;
   - `ollama`: local development only, address must be loopback, cloud tags refused,
     refused in staging and production;
   - `bedrock`: the Converse API, contract-tested with botocore's Stubber and never run live
     until the AWS account exists.

   Tests inject a deterministic fake that records every prompt.
2. **The application owns the prompt.** It is a fixed template, with sources fenced as
   `<<<source>>>` data and fence-like text neutralised. The model has no tools and can't
   cause any action.
3. **Grounding gate before anything is returned:**
   - no readable sources, or a best relevance below the embedder's floor → abstain
     *without* calling the model;
   - otherwise the model must reply in JSON;
   - every citation's quoted span must appear in the cited source;
   - unsupported citation markers are stripped;
   - no verified citation → abstain.
4. **Everything is logged** in `query_log`, `answer_log` and `citation` (tenant RLS), never
   in application logs. Answers quoting a deleted document are wiped.

## Alternatives considered
- **Claude Haiku via the Anthropic API for local work:** faster and better, but a paid
  account that sends text abroad. Not authorised.
- **No model until Bedrock:** would leave the gate, verifier and evaluation untested on real
  model behaviour.
- **Letting the model call retrieval as a tool:** more flexible, but it widens the
  injection surface. The plan's fixed pipeline is safer.

## Why it fits Lekha
- Providers are interchangeable behind one interface (CLAUDE.md §8, Phase 3).
- Nothing leaves the laptop locally.
- Permissions are applied before the model sees anything.
- The verifier means a citation is never fabricated.

## Trade-offs
- The local 9B model is slow on this GPU (about 20–40 s per answer) and weaker than Claude,
  especially in Kannada.
- Span verification proves a quote exists, not that it is true or relevant. A verbatim
  quote of injected text would verify, so injection resistance also depends on the prompt
  and is measured on the golden set.
- The relevance floor is calibrated on the golden set itself (BGE-M3: 0.50), so it needs a
  separate validation set before real use.

## Security and operational implications
- Fail closed: a model outage gives 503; unparseable output → abstain; a rate-limit store
  outage gives 503.
- Question and answer text are personal-data-adjacent. Retention and the `audit_reader`
  access are still to be built.

## Cost
Local: none. Production: Bedrock per-token charges (plan cost model), with a cost ceiling
still to be added.

## Revisit when
- The AWS account and Bedrock access exist: run the golden set on Claude and compare.
- Sarvam is validated for restricted data.
- A reranker or answer cache is justified by golden-set evidence.
