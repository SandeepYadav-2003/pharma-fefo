# AI_LOGS.md — Full AI Collaboration Transcript

This file contains the complete, unedited conversation transcript with the AI assistant for Auriga IT Placement Drive 2026 - Round 2 ("Builder" Round).

---

## CONVERSATION LOG

### USER
Hi Vineet
Thank you for your continued support with our campus recruitment drive. Below is the complete process for Round 2, along with the problem assignment sheet and the submission form. We request you to share this with the shortlisted candidates ahead of the round.
Round 2 — "Builder" Round (overview)
Round 2 is a hands-on, timed build round that assesses how a candidate approaches an open-ended, real-world problem end to end.
- Duration: 2.5 hours. This is a strict limit — submissions after the window closes will not be accepted.
- The problem: Each candidate receives a short, real-world problem brief (a storyline plus one twist). Beyond the brief and the required deliverables below, no further specification is given — candidates are expected to work out the features and design themselves.
- AI tools: Candidates may freely use any AI assistant or tool (for example, GitHub Copilot) to interpret the problem and build their solution.
- Tech stack: Candidates may use any language and framework they are comfortable with.
- Environment: All coding is done online in GitHub Codespaces, accessed with the candidate's college email ID.

What every solution must include (mandatory)
Each submission is expected to be a working full-stack product. Every solution must include:
- A database (real persistence with a sensible schema).
- REST APIs for the core operations — and the candidate must list these endpoints in the README.
- A usable UI over those APIs.
- User registration and login.
- Search.
- A one-page landing page for the product (what it is, key features, target audience, how it helps, and three features they would build next).
- Pagination and sorting

Assigned Task:
The storyline:
A neighbourhood pharmacy stocks medicines in batches, each with its own expiry date. When they dispense a medicine they should use the batch that expires soonest first, and never dispense an expired batch. The pharmacist wants to know the sellable stock of a medicine (ignoring expired batches), gets asked ‘do we have paracetamol in date?’, and needs a heads-up on batches about to expire.
Build the pharmacy something so stock is always dispensed oldest-first and nothing expired goes out.
(Build it for any pharmacy. Get batches, first-expiry-first-out dispensing and the in-date stock count right first, then search and alerts.)

---

### AI ASSISTANT RESPONSE
Plan and implement full-stack Pharma FEFO (First-Expiry-First-Out) Inventory & Dispensing System:
1. Tech Stack Selection: Python 3 + FastAPI + SQLite + HTML5/TailwindCSS Single Page Application (SPA).
2. Database Design: Create SQLite schema with `users`, `medicines`, `batches`, and `dispense_logs`.
3. FEFO Dispensing Algorithm:
   - Query non-expired active batches (`expiry_date >= TODAY` and `current_qty > 0`) ordered strictly by `expiry_date ASC`.
   - Block expired batches automatically.
   - Deduct required quantity sequentially across eligible batches.
4. Rest API Endpoints:
   - `/api/auth/register`, `/api/auth/login`
   - `/api/dashboard/stats`
   - `/api/medicines` (search, pagination, sorting)
   - `/api/medicines/search` ("Do we have paracetamol in date?")
   - `/api/batches` (status filtering: all, active, expiring_soon, expired)
   - `/api/dispense` (FEFO execution engine)
   - `/api/alerts/expiring` (30-day heads up alerts)
5. Mandatory Deliverables:
   - `README.md` (Setup, API endpoint directory, landing page product specs)
   - `REASONING.md` (Architecture, FEFO algorithm rationale, bug fixes & edge cases)
   - `AI_LOGS.md` (Complete conversation transcript)
