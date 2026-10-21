# Leadership demo: script and setup

A 10-minute walkthrough of the local prototype. **Everything is synthetic:** invented
policies and invented people, on one laptop. Nothing is deployed and no real company data is
used.

## Before the meeting (about 15 minutes)

1. Start Docker Desktop, then in a terminal at `D:\lekha-ai`:
   ```powershell
   ./dev.ps1 up
   ./dev.ps1 migrate
   ./dev.ps1 seed -Reset      # rebuild the synthetic people
   ```
2. In three more terminals: `./dev.ps1 run` (API), `./dev.ps1 worker` (processing) and
   `./dev.ps1 web` (web app).
3. `./dev.ps1 demo-load` loads 42 synthetic policies and 4 synthetic meetings. Wait for "All
   documents processed". It is safe to run again; anything already loaded is skipped.
4. **Stop the worker** (Ctrl+C in its terminal). It is no longer needed, and it frees about
   2.5 GB of memory for the AI model.
5. Close other heavy apps (browsers with many tabs, Teams).
6. Open <http://127.0.0.1:5173>, sign in as **Asha** and ask one question to **warm up** the
   model: the first answer takes about 60–75 s while the model loads.

Expected speed once warm: about **30–40 s** per answer on this laptop. Permission refusals
and "I don't know" for unrelated questions come back in about 1 s. Production will use
Claude on AWS (a few seconds per answer) once the AWS account exists.

## The walkthrough

| # | Show | Say |
|---|---|---|
| 1 | The sign-in screen with six people | "Real sign-in will be Group single sign-on. Today we switch between invented people to show permissions." |
| 2 | **Asha** asks *"How many days of paid annual leave do I get?"* | "While it works you can see each step: it searches only documents Asha may read, writes an answer, then checks every quote." |
| 3 | The answer, then click the source card | "Every answer cites its source, names the version, and the exact sentence is highlighted. If a quote can't be found word for word in the source, it is thrown away." |
| 4 | Asha asks *"What is the Grade 7 salary band?"* | "Salary bands exist, but only Legal (Meera) may read them. Asha gets 'I don't know'. The AI never even saw that document." |
| 5 | Switch to **Meera**, ask the same | "Same question, different person, different permissions: Meera gets the answer with its source." |
| 6 | Switch to **Ravi** (Beta plant), ask *"How many days of paid annual leave do I get?"* | "Ravi works at another business unit. Alpha's leave policy is invisible to him." |
| 7 | Ravi asks *"What shift allowance do night shift workers get?"* | "His own plant's policy works normally." |
| 8 | **Asha** asks *"Is there a gym in the office?"* | "No document says, so Lekha says it doesn't know instead of guessing." |
| 9 | **Asha** asks in Kannada: *ಪ್ರತಿ ವರ್ಷ ಎಷ್ಟು ದಿನಗಳ ಸಾಂದರ್ಭಿಕ ರಜೆ ಸಿಗುತ್ತದೆ?* | "Kannada questions and documents work; Hindi is planned." |
| 10 | **Asha** → Meetings: open *Weekly safety review*; then Ask *"By when must forklift drivers be retrained?"* | "Meetings are memory too. The answer cites the meeting and the exact moment, 12:40. Only the people who were there can see it." |
| 10b | In the safety review, **Draft minutes**, then **Approve** (Asha organised it) | "Lekha drafts minutes, but a person approves them. Every decision and action quotes the transcript, and it leaves owners blank rather than guess. Drafting takes about 2 minutes locally." |
| 11 | **Chitra** → Meetings | "Chitra curates Alpha's documents, but she wasn't in these meetings, so she sees none of them." |
| 12 | **Chitra** → Documents → upload a Word file, share with Asha | "Curators upload policies; they become searchable in about a minute, with permissions attached." |

**Safety points to mention:**
- Permissions are applied *before* the AI sees anything.
- Documents are treated as data: two demo documents contain hidden instructions ("ignore
  your rules…"), and Lekha ignores them.
- Every question and answer is logged; deleting a document also wipes stored answers that
  quoted it.
- Measured on a 100-question synthetic test: answers and citations correct, 0 permission
  leaks, and it says "I don't know" on every unanswerable question.

## What is honestly not there yet

- **Speed:** 30–40 s locally (target 6 s). It needs Claude on AWS or a GPU server.
- **Real sign-in and real documents:** blocked on IT (SSO, AWS account) and Legal
  (approval for real data).
- **Meetings, glossary, phone and WhatsApp:** later steps of the roadmap.
