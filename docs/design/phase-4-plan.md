# Phase 4 plan: daily-use companion (proposal)

- **Status:** Proposed. Waiting for the owner's decisions D1–D6 below.
- **Date:** 2026-10-21
- **Scope:** local, **synthetic data only**. The owner said "go ahead" on 2026-10-21. A real
  20-user pilot is out of reach until Phase 0 delivers SSO, the AWS account and the real-data
  approval.
- **Sources:**
  - primary plan §4 (Phase 4: "Meeting notes import, glossary of names, pilot UI, user
    support channel");
  - Technical Architecture "Phase 4 — Daily-Use Companion", functional requirements
    (view source passage, meeting import, minutes, glossary), data model (`meeting`,
    `transcript_segment`, `glossary_entry`, `entity`);
  - CLAUDE.md §8.
- **Owner priority:** a prototype to show leadership. So the **web app comes first**.

## 1. The goal in plain words

Phase 3 made Lekha answer questions through an API. Phase 4 makes it something people can
**use every day**:

1. **A web app:**
   - sign in, ask, see the answer, click a citation to read the source passage;
   - upload documents, say whether an answer helped.
2. **Meeting memory:** import meeting transcripts, ask "what did we decide about X?", and
   draft minutes with action items that a person must approve.
3. **A glossary:** company names, abbreviations and people's names, so "MPL" finds
   "Manipal Pharma Labs".
4. **Pilot support:** a feedback button, a list of questions Lekha couldn't answer, and a
   one-page user guide.

## 2. What "done" means (adapted to local and synthetic data)

| Plan exit criterion | Local version |
|---|---|
| 20 users weekly-active for 4 weeks | **BLOCKED** (no real users until SSO and the real-data gate). Replaced by a scripted end-to-end demo run by synthetic users |
| Citation correctness holds | Golden set re-run including meeting questions: still ≥ 0.90, 0 permission violations |
| No security incident | Meetings inherit permissions (only participants and explicit grants); red team extended to meetings and glossary; 0 anomalies |
| Sponsor attests it's useful | **The leadership demo** is the local stand-in; a real attestation needs the pilot |

## 3. Build order (each step tested; one commit per finished step)

1. **Demo web app** (`frontend/`, React + TypeScript, Vite), plus the small API pieces it
   needs:
   - local-only "sign in as" picker for the synthetic users (dev sign-in, never in
     production);
   - **Ask** page: answer, numbered citations with document, version and quote; click to see
     the source passage (new `GET /api/v1/chunks/{id}`, same permission rule); "I don't know"
     shown clearly, with its reason;
   - **Documents** page: what you may read; **upload** for curators, with processing status;
   - **Feedback:** helpful / not helpful with an optional comment (new table and endpoint);
   - a **demo data loader** script and a **demo script**: permissions (Asha vs Ravi), "I
     don't know", Kannada, a new version replacing an old one, deletion, and a
     prompt-injection document that changes nothing.
2. **Meeting import:**
   - transcripts from files: plain text, WebVTT/SRT, and a Teams-style JSON export;
   - tables `meeting`, `meeting_participant`, `transcript`, `transcript_segment`;
   - **permissions = participants + explicit grants** (the plan: an import must not become
     readable by everyone);
   - dedup by source ID, then organiser + start time (± 2 min);
   - segments are indexed like documents, and citations show the meeting and timestamp.
3. **Minutes with human approval:**
   - draft minutes and proposed action items (owner, due date) generated from a transcript,
     with citations to segments;
   - status `draft` → `approved` by the meeting organiser; only approved minutes become
     searchable memory;
   - nothing is sent anywhere (no outbound actions, CLAUDE.md §6.3).
4. **Glossary and entities:**
   - `glossary_entry`, `entity` and `entity_alias` tables; curator endpoints and a small UI
     page;
   - the keyword half of search expands aliases ("MPL" ↔ "Manipal Pharma Labs");
   - seeded with synthetic terms (Clavis seeding is blocked).
5. **Pilot support:**
   - an "unanswered questions" list for curators (abstentions with consent to log);
   - the audit-reader view still owed from Phase 1 (`GET /api/v1/audit`, `audit_reader`
     role, business-unit scoped);
   - a one-page user guide.
6. **Evaluation and red team** extended to meetings and glossary, then the **Phase 4 report**,
   then stop.

## 4. Decisions I need from you

**D1. Web app technology.**
- **Recommendation:** **React + TypeScript with Vite**, as CLAUDE.md §5 specifies. Node.js
  24 is already installed; the packages are about 100–150 MB to download once (about 20–30
  minutes on your connection).
- **Alternative:** a single page served by FastAPI with no build tools. There's nothing to
  download, but it departs from the approved stack and would be thrown away later.

**D2. Answer speed in the demo.** The local model takes about 18–29 s per answer. That
works, but feels slow in front of leadership.
- **(a) Recommended:** keep the local model and show the answer "streaming in" plus a
  progress indicator. No cost, no data leaves the laptop.
- **(b)** Use Claude Haiku via the Anthropic API **for the demo only**, with synthetic data:
  about 2–4 s per answer, a few rupees per demo. It needs your explicit authorisation, an
  API key, and data leaving the laptop (synthetic only).
- **(c)** Download a smaller local model (about 2.5 GB, around 8–12 s per answer, lower
  quality).

**D3. Meeting sources.**
- **Recommendation:** **file imports** (text, VTT, SRT, Teams-style JSON) now.
- Granola needs its MCP credentials, and Teams and SharePoint need tenant access: all
  blocked on Phase 0. I'll build behind one `MeetingSource` interface so they plug in later.

**D4. Audio transcription** (the plan's F-11).
- **Recommendation:** **defer to Phase 5**, where speech is central. It needs a paid
  Sarvam API or a multi-GB AI4Bharat download. Phase 4 imports text transcripts only.

**D5. SharePoint and Google Drive connectors.**
- **Recommendation:** **defer.** They are blocked on tenant access; the upload page covers
  the demo.

**D6. Admin UI** (users, roles, business units, sources, retention).
- **Recommendation:** **only the pieces the demo and pilot need:** document list, upload,
  unanswered questions, glossary curation. A full admin console comes later.

## 5. Not in Phase 4

- Phone, WhatsApp, speech, ticket actions: Phase 5.
- MCP, more connectors, a second business: Phase 6.
- Load testing, high availability, disaster recovery: Phase 7.
- Real users or real documents; any deployment.

## 6. Risks

- **Demo speed:** see D2.
- **npm downloads on a slow link.** Mitigation: a lockfile and one-time install.
- **Meeting permissions are subtle** (participants, consent, confidential meetings).
  Mitigation: deny by default and red-team them.
- **Minutes quality from a 9B local model** may be modest; human approval is mandatory
  anyway.
- **The plan's "minutes in < 60 s"** likely can't be met locally; it will be measured and
  reported.
