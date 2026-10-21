# ADR-0015: Meeting memory stored as documents, readable only by participants

- **Status:** Accepted by the owner on 2026-10-21
- **Date:** 2026-10-21

## Context
Phase 4 adds meetings to Lekha's memory: people should be able to ask "what did we decide
about X?" and get an answer citing the meeting and the moment.

The plan's data model lists `meeting`, `meeting_participant`, `transcript` and
`transcript_segment`, and it warns that **imports inherit permissions**: a transcript
imported from one person's workspace must not become readable by everyone.

Granola, Teams and SharePoint connectors are blocked on Phase 0 access, so Phase 4 imports
transcript files (owner decision D3).

## Decision
1. **A meeting's transcript is stored as a document of kind `transcript`.** The canonical
   JSON segments sit in object storage, and the transcript has versions, a guest list,
   chunks, vectors and citations like any document.

   A small `meeting` table (one per document) holds:
   - the start time;
   - the organiser;
   - the source (`vtt`, `srt` or `txt`) and its external ID;
   - the participants, in `meeting_participant`.

   Transcript segments live in the stored JSON rather than in a separate table.
2. **The guest list is the participants, and nobody else.** The importer is always a
   participant. Business-unit curators get no implicit access to meetings they didn't attend.
3. **Anyone with a role in the unit may import their own meetings**; curator status isn't
   required. The release gate and clearance rules still apply.
4. **Re-import adds a new version, not a duplicate.** The same meeting is recognised by its
   source ID first, then by the same organiser within ±2 minutes of the same start (the
   plan's dedup rules). The latest import sets the participants.
5. **Timed chunks:** transcript turns are grouped into chunks of about 220 words, never
   splitting a turn. Each chunk keeps its start and end time, and a citation reports the
   time of the exact turn that contains the quote.
6. **Deletion:** the curator rule applies, plus the meeting's organiser.

## Alternatives considered
- **Separate `transcript` and `transcript_segment` tables with their own search path:**
  closer to the plan's diagram, but it duplicates permissions, search, versions, deletion,
  citation and red-team logic. Every duplicate is a place for a permission bug.
- **Unit curators can read all meetings:** convenient for admins, but it breaks the plan's
  "imports inherit permissions" and would surprise participants.
- **Granola or Teams connectors now:** blocked on credentials and tenant access (Phase 0).

## Why it fits Lekha
- One permission path for everything Lekha remembers, already proven by the red team.
- Meetings appear in normal search and answers.
- The plan's privacy rule is honoured by construction.

## Trade-offs
- No per-segment table yet: speaker statistics, segment-level consent and deleting one
  person's segments (plan: consent records) need it later.
- Participants must be Lekha users; external guests are only names inside the transcript.
- A participant whose role in the unit lapses loses access (the access rule needs a role in
  the unit), which is intended.
- Audio transcription is deferred to Phase 5 (D4).

## Security and operational implications
- Transcripts are validated: UTF-8 only, 5 MB limit, ≤ 20,000 turns, markup stripped.
- Transcript text is untrusted data, like document text.
- Tests prove curators who weren't present can't search, list, open or ask about a meeting.
- Backups and deletion already cover the new tables and files.

## Revisit when
- Connectors (Granola via MCP, Teams, SharePoint) get access.
- Per-person consent and segment-level deletion are required (DPDP review).
- Meeting volume makes a segment table worthwhile.
