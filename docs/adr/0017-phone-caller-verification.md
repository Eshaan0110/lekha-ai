# ADR-0017: Phone callers are verified by HR number and a one-time code; the line is simulated

- **Status:** Proposed (2026-10-22), awaiting the owner's decision
- **Date:** 2026-10-22

## Context
Phase 5 adds a phone line. The plan calls it "a trust surface, not just an interface":
- threat T-11: a caller claims to be another employee;
- threat T-12: a spoofed caller ID.

The plan's controls are "Employee verification via existing HR number plus OTP", "Caller ID
treated as untrusted; verification by OTP always", and refusing anything beyond public
answers before verification. Owner decisions D3 (phone simulator) and D6 (HR ID + one-time
code, simulated) are approved.

There is no telephony contract, no SMS provider and no HR data yet (all Phase 0).

## Decision
1. **Who is calling is decided only by HR number + one-time code.**
   - The caller says their HR number.
   - A 6-digit code goes to the phone **registered with HR** for that number (synthetic
     `employee_contact` rows locally), never to the calling number.
   - The calling number is stored but never used for anything.
2. **Nothing is answered before verification.** Lekha answers no questions and logs no
   tickets until then; it only asks for the number or code. (The plan allows public policy
   answers before verification. We give none until the pilot defines what "public" means.)
3. **Limits.**
   - Codes are random, stored only as salted SHA-256 hashes, valid for 5 minutes, used once,
     and bound to their call.
   - 3 wrong codes, or 3 unreadable HR numbers, hand the call to a person.
   - At most 3 codes per employee per hour, so codes can't be flooded or collected across
     calls.
   - Idle calls end after 5 minutes; every call ends after 20.
4. **No account enumeration.** The reply after an HR number is the same whether the number:
   - exists or not;
   - belongs to a deactivated user;
   - has hit the code limit.
5. **After verification, exactly the web rules apply.**
   - Questions go through the same permission-filtered, grounded `/ask` pipeline, as the
     verified employee.
   - Tickets go through the same proposal → named approver → execute-once flow (Phase 5
     step 1). The caller's spoken "yes" is necessary but not sufficient.
6. **Hand-over to a person** happens on:
   - a request for a person;
   - distress words;
   - a question Lekha can't answer from the documents;
   - a system failure;
   - failed verification.

   The automated call ends there and the call joins the (simulated) human queue.
7. **The written transcript is kept; codes are masked in it; audio is never stored**
   (step 3 adds speech).
8. **The line is a simulator for now.**
   - `/api/v1/voice/*` plays the part of the telephony provider's webhook. It has no user
     sign-in (callers are anonymous until verified).
   - It exists only in development sign-in mode, which the settings allow only in local and
     test.
   - SMS goes to a simulated outbox that is never sent.

## Alternatives considered
- **Trust caller ID for known numbers.** Rejected: caller ID can be spoofed (T-12).
- **Send the code to the calling number.** Rejected: whoever holds the calling phone would
  verify as anyone whose HR number they know.
- **Answer "public" policy questions before verification.** Deferred: it needs a definition
  of public content per unit. Until then, nothing is safer.
- **Voice biometrics.** Not in the plan, needs consent and enrolment, and can be fooled by
  recordings.
- **A real telephony provider now.** Blocked on Phase 0 (contract, number, KYC, public
  webhook).

## Trade-offs
- An employee whose registered phone is not at hand can't use the line. A hand-over to a
  person is their path.
- The code limit can lock out a real employee for an hour if someone keeps calling with
  their number. This is accepted over letting codes be farmed; the attempts are logged.
- Distress detection is a short word list in three languages. It catches the obvious cases
  and will miss others. A real pilot needs better detection, and a person can always be asked
  for.

## Security and operations
- Every call request is audited (`voice_call` target).
- Codes are logged as neither values nor hashes.
- The outbox holds codes in plain text **because it is a simulator**. A real SMS adapter
  must not keep message bodies longer than delivery needs.
- Before a real provider replaces the simulator, its adapter must verify the provider's
  signed requests, rate-limit per calling number, and run on an internal address.

## Cost
None locally. Real SMS and telephony are paid services and need the owner's authorisation
(CLAUDE.md §13).

## When to revisit
- HR data becomes available (real HR numbers and registered phones).
- Legal approves consent and retention wording.
- A telephony provider is chosen.
- Pilot data shows lockouts or hand-overs are too frequent.
