# Phase 5 plan: phone answers and human-approved tickets (for discussion)

- **Status:** Approved by the owner on 2026-10-22 with the recommended D1–D6: Hindi back in;
  fakes first, Sarvam later only with the owner's authorisation; phone simulator; fake
  ticketing; approver role; ID + one-time code, simulated.
- **Date:** 2026-10-22
- **Scope:** local, **synthetic data only**.
- **Sources:**
  - primary plan §4 (Phase 5: "Phone-based HR-policy answers in Kannada and Hindi, plus
    human-approved ticket proposals");
  - Technical Architecture "Phase 5 — Telephone Pilot", functional requirements F-11/F-17
    (phone Ask/Log flows), "Safe Actions" (action proposal → approval → execution), threat
    model (employee impersonation on the phone line), technology choices (Sarvam Saaras
    and Bulbul, Bolna or Sarvam Samvaad);
  - CLAUDE.md §6.3 (no autonomous actions) and §13 (no paid services without the owner's
    authorisation).

## 1. The goal in plain words

Most Manipal employees on a shop floor don't sit at a laptop. Phase 5 lets them **call a
number and ask in Kannada or Hindi**:

1. The caller dials in and hears **a short consent notice** (the call is processed by
   Lekha).
2. They **prove who they are**: an HR employee ID plus a one-time code. No answer that
   depends on permissions is given before that.
3. **Ask:** "ನನಗೆ ಎಷ್ಟು ರಜೆ ಸಿಗುತ್ತದೆ?" (How much leave do I get?).
   - Lekha answers **briefly, out loud**, from the documents that employee may read, with the
     same grounding and "I don't know" rules as the web app;
   - the full answer with its sources is **sent as a text message**.
4. **Log:** "The compressor on Line 3 is leaking."
   - Lekha **proposes a ticket** and reads it back; the caller confirms;
   - **a named human approves it** before anything reaches the HR or IT system (plan:
     propose → approve → execute);
   - nothing is created without that approval.
5. **Escalation:** if the caller is upset, the question is out of scope, or anything fails,
   Lekha hands over to a person.

The plan calls the phone line "a trust surface, not just an interface": a wrong policy
answer read aloud, a salary leaked in Kannada, or a spoofed caller ID would set adoption back
a year. So this phase is mostly about **safety around voice**, not just speech-to-text.

## 2. What can and can't be real on this laptop

| Piece | Real locally? | Why |
|---|---|---|
| Phone number and calls | **No** | Needs a telephony contract (Bolna or Sarvam Samvaad), an Indian phone number (KYC) and a public webhook URL, all Phase 0. Locally we build a **phone simulator** in the web app (press to talk, hear the reply) behind the same interface |
| Speech to text (Kannada, Hindi) | **Yes, with a choice** (D2) | Paid API (Sarvam, the plan's default) or a free local model (smaller, weaker on Kannada) |
| Text to speech | **Yes, with a choice** (D2) | Same choice |
| One-time code by SMS | **No** | Needs an SMS provider. Simulated: codes appear in a local "outbox" page, never sent |
| Answer details by SMS or WhatsApp | **No** | Same; shown in the outbox, never sent |
| HR/IT ticketing system | **No** | Which system, and access to it, are Phase 0 items. A clearly labelled **fake ticketing system** stands in |
| Speech accuracy on real factory audio | **No** | Needs real recordings and consent. Locally we measure on a small synthetic or recorded set |

## 3. What "done" means (local version)

| Plan exit criterion | Local version |
|---|---|
| Word error rate within thresholds, per language, in real conditions | Measured on a small **synthetic or self-recorded** set in Kannada and Hindi (and English), published per language. Real factory audio **BLOCKED** |
| 50 shop-floor users for 4 weeks | **BLOCKED** (needs telephony, SSO or HR ID linkage, the real-data gate) |
| No disclosure incidents | A **phone red team** passes with zero disclosures: an unverified caller, a wrong ID or code, guessing codes, asking for someone else's data, salary probes, prompt injection by voice |
| No policy incident; nothing executed without approval | Every ticket needs the caller's confirmation **and** a named approver; execution is idempotent; all three steps are audited |
| Human escalation path tested | Upset caller, out-of-scope question and system failure each route to the simulated human queue |
| Consent announcement and recording retention | Played and logged on every call; retention setting for audio (default: transcripts kept, audio deleted after processing) |
| Rollback runbook | A runbook for switching the phone line off, written and rehearsed locally |

## 4. How it would work

```mermaid
sequenceDiagram
    participant C as Caller (simulator)
    participant V as Voice session
    participant S as Speech (STT/TTS)
    participant A as Lekha ask / actions
    participant H as Human approver
    C->>V: call
    V->>C: consent notice (TTS)
    V->>C: "Your employee ID?" … one-time code (outbox)
    C->>V: speech
    V->>S: speech → text (+ language)
    V->>A: ask as the verified employee (same permissions, grounding)
    A-->>V: short answer + sources
    V->>C: spoken answer (TTS); full answer → outbox
    C->>V: "Log a complaint …"
    V->>A: ticket proposal (read back, caller confirms)
    A->>H: approval request
    H-->>A: approve / reject / edit
    A->>A: execute once (idempotency key) → fake ticketing system
```

**New pieces, each behind an interface** (fakes for tests, real adapters where allowed):
- **`lekha/speech`:** speech-to-text and text-to-speech interfaces, language from the first
  sentence, and glossary-aware correction of names (the glossary is reused).
- **`lekha/actions`:** proposals, approvals and executions (the plan's three tables), tool
  definitions (first and only tool: `create_ticket`), idempotency keys, and compensation
  notes.
- **Voice session:** a state machine (consent → verify → ask/log → confirm → done or
  escalate) with time limits, and a cap on failed verification attempts.
- **`lekha/connectors`:** the ticketing interface plus the **fake** ticketing system; an
  outbox for messages (never actually sent).
- **Web app:** a phone simulator page, an approvals page for approvers, and an outbox page
  (dev only).

**Safety rules specific to voice:**
- No answer from restricted material before verification; after it, only what that
  employee could read on the web.
- Spoken answers are short and never read out content from confidential or restricted
  documents (those stay blocked anyway).
- Code attempts are limited and locked out after repeated failures, and every attempt is
  audited.
- Spoken words are untrusted input, like documents: a caller saying "ignore your rules" gets
  the same treatment.
- Lekha never calls, texts or creates anything on its own. Every outward action is a
  proposal waiting for a person.

## 5. Decisions I need from you

**D1. Bring Hindi back?** You deferred Hindi on 2026-10-20, but the plan's Phase 5 is
"Kannada **and Hindi**".
- **Recommendation:** yes, for Phase 5. The text side already handles Devanagari; this adds
  Hindi speech, Hindi golden questions and a Hindi glossary.

**D2. Speech provider for local work.**
- **(a) Sarvam API** (the plan's default; India-based, tuned for Indian languages and
  telephony). Best Kannada and Hindi quality. **Paid** (please check current pricing and any
  starter credits; the plan's figure for text-to-speech is about ₹30 per 10,000
  characters), needs an API key, and **synthetic audio leaves the laptop**. Needs your
  explicit authorisation under CLAUDE.md §13.
- **(b) Free local models** (Whisper-class speech recognition, an open Indian text-to-speech
  model). About **1–3 GB of downloads** on your slow link. Weaker on Kannada, slow on this
  laptop's CPU or GPU. Nothing leaves the machine.
- **(c) Fakes only for now:** build the whole flow with simulated speech (typed text stands
  in for audio), and decide on real speech later.
- **Recommendation: (c) first, then (a)** for a short, budget-capped evaluation once you
  authorise it. The voice *flow* and its safety can be built and tested without paying for
  speech.

**D3. Telephony.**
- **Recommendation:** a **phone simulator** in the web app behind a provider-neutral call
  interface. Bolna or Sarvam Samvaad are chosen later, in a short structured trial as the
  plan says, once Phase 0 provides the contract, number and network access.

**D4. Ticketing.**
- **Recommendation:** one tool, `create_ticket`, against a **clearly labelled fake ticketing
  system** behind a ticketing interface. The real HR or IT system is named in Phase 0.

**D5. Who approves tickets?**
- **Recommendation:** a new **`approver`** role per business unit (seed a synthetic approver,
  e.g. "Hari"). The caller's spoken confirmation is necessary but **not sufficient**; a
  named human approves.
- **Alternative:** the plan allows "approved by caller" for low-risk tickets. Faster, but
  weaker; I'd keep a human approver at least for the pilot.

**D6. Caller verification.**
- **Recommendation:** HR employee ID plus a one-time code, both simulated (synthetic IDs on
  the synthetic users, codes shown in the outbox).
- Real HR-ID linkage needs HR data and is **blocked**. Caller ID alone is never trusted.

## 6. Build order (each step tested; one commit per finished step)

1. **Actions framework:** proposals, approvals, executions; `create_ticket` against the fake
   ticketing system; approver role; approvals API and page. Works from the web app too
   ("log an issue").
2. **Voice session core** with typed text standing in for speech: consent, verification
   (codes, limits), Ask with a short answer and outbox details, Log via proposals,
   escalation queue.
3. **Speech interfaces and fakes;** phone simulator page; language detection from the first
   sentence; glossary-aware name correction.
4. **Real speech adapter** (only if D2 allows), plus a word-error-rate harness on a small
   test set per language.
5. **Phone red team:** impersonation, code guessing, someone else's data, salary probes,
   voice injection, upset caller, outages.
6. **Rollback runbook,** then the **Phase 5 report.**

## 7. Not in Phase 5

- Real calls, SMS or WhatsApp; a real ticketing system; real employees or audio.
- WhatsApp as a full channel (the plan's P1 "beyond simple notifications").
- Any action other than ticket creation; anything that pays, signs or commits.
- MCP and more connectors (Phase 6).

## 8. Risks

- **Speech quality in Kannada and Hindi** is the plan's top phone risk. Locally we can only
  measure on clean or synthetic audio; real factory noise must be tested before any
  go-live.
- **Latency on a call:** today about 20–40 s per answer locally is unusable by phone (a
  caller expects a few seconds). The flow can be built, but **a real phone pilot needs
  production hosting** (Bedrock, GPU).
- **Cost:** paid speech and telephony must be capped (per-user quotas, router rules) before
  any real trial.
- **Impersonation:** caller ID can be spoofed, so verification must never rely on it.
- **Scope creep into actions:** keep exactly one tool, with human approval, until the
  approval flow has been red-teamed.
