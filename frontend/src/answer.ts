// Splits an answer into text and citation markers, so "[2]" becomes a clickable chip.
// Only markers for citations that were actually verified become chips; the server already
// strips the others, and anything unexpected stays plain text.

export type AnswerPart = { kind: "text"; text: string } | { kind: "cite"; number: number };

export function splitAnswer(answer: string, verified: ReadonlySet<number>): AnswerPart[] {
  const parts: AnswerPart[] = [];
  const marker = /\[(\d{1,3})\]/g;
  let last = 0;
  for (const match of answer.matchAll(marker)) {
    const number = Number(match[1]);
    if (!verified.has(number)) continue;
    const start = match.index ?? 0;
    if (start > last) parts.push({ kind: "text", text: answer.slice(last, start) });
    parts.push({ kind: "cite", number });
    last = start + match[0].length;
  }
  if (last < answer.length) parts.push({ kind: "text", text: answer.slice(last) });
  return parts;
}

const REASONS: Record<string, string> = {
  no_sources: "Nothing you have access to covers this.",
  weak_evidence: "The closest documents you can read don't clearly cover this.",
  model_abstained: "The sources found don't answer this clearly.",
  no_verified_citations: "An answer was drafted but its quotes couldn't be verified, so it was withheld.",
  invalid_model_output: "The answer couldn't be checked, so it was withheld.",
};

export function explainAbstention(reason: string | null): string {
  return (reason && REASONS[reason]) || "Lekha couldn't find enough evidence to answer.";
}
