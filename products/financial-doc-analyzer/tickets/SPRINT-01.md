# Sprint 1 — Financial Document Analyzer MVP
**Sprint dates:** Week 1-2 of Product 1 build  
**Sprint goal:** Working document upload, OCR extraction, and AI summary end-to-end (backend). Upload screen and results screen (mobile).  
**Sprint owner:** PM Agent  
**Agents assigned:** Backend/AI Developer Agent, Mobile/Frontend Developer Agent

---

## Backlog — Sprint 1

---

### [FDA-001] Set up FastAPI project structure and health endpoint
**Assigned to:** Backend/AI Developer Agent  
**Priority:** P1  
**Deadline:** Day 2  
**Blocked by:** None  

**Description:**  
Bootstrap the Product 1 backend with a clean FastAPI project structure, Alembic for migrations, ruff + black for linting, and a working health endpoint. This is the foundation every other ticket builds on.

**Acceptance Criteria:**
1. `GET /health` returns `{"status": "ok", "service": "financial-doc-analyzer"}` with HTTP 200
2. Project runs with `uvicorn main:app --reload` from `products/financial-doc-analyzer/backend/`
3. `ruff check .` and `black --check .` pass with zero errors on project root
4. Alembic is configured with `alembic.ini` pointing to `DATABASE_URL` from `.env`
5. `pytest` runs with zero failures (one smoke test for the health endpoint)
6. `.env.example` is committed; `.env` is in `.gitignore`

---

### [FDA-002] Database schema — users, documents, analyses, usage tables
**Assigned to:** Backend/AI Developer Agent  
**Priority:** P1  
**Deadline:** Day 3  
**Blocked by:** FDA-001  

**Description:**  
Create the initial Alembic migration for all four core tables using the `fda_` prefix. Migration must be reversible.

**Acceptance Criteria:**
1. `alembic upgrade head` creates all four tables: `fda_users`, `fda_documents`, `fda_analyses`, `fda_usage`
2. `alembic downgrade -1` cleanly removes all four tables
3. Schema matches the spec in `agents/backend-ai-dev/CLAUDE.md` exactly
4. All columns have explicit types, null constraints, and defaults where appropriate
5. `fda_users.email` has a unique index
6. `fda_documents.user_id` has a foreign key to `fda_users.id` with `ON DELETE CASCADE`
7. Migration file is committed and readable

---

### [FDA-003] JWT authentication — register, login, refresh endpoints
**Assigned to:** Backend/AI Developer Agent  
**Priority:** P1  
**Deadline:** Day 5  
**Blocked by:** FDA-002  

**Description:**  
Implement stateless JWT authentication. No session store — access token (60 min) + refresh token (30 days). Passwords hashed with bcrypt.

**Acceptance Criteria:**
1. `POST /api/v1/auth/register` creates a user, returns `{access_token, refresh_token, user_id}`; returns 409 if email already registered
2. `POST /api/v1/auth/login` validates credentials, returns tokens; returns 401 on wrong password
3. `POST /api/v1/auth/refresh` accepts a valid refresh token, returns new access token; returns 401 on expired/invalid token
4. Passwords are bcrypt-hashed — plaintext never stored or logged
5. JWT signed with `FDA_SECRET_KEY` from env
6. All three endpoints have OpenAPI `summary` and `description`
7. 9 unit tests: happy paths for all three endpoints + 6 error cases (409, 401 × 2, expired token, missing token, malformed token)
8. Auth middleware `get_current_user` dependency ready for use on protected routes

---

### [FDA-004] Document upload endpoint with validation and async processing
**Assigned to:** Backend/AI Developer Agent  
**Priority:** P1  
**Deadline:** Day 7  
**Blocked by:** FDA-003  

**Description:**  
The core endpoint. Accept a PDF upload, validate it, store it, trigger async OCR + analysis processing, return immediately with document ID and status `processing`.

**Acceptance Criteria:**
1. `POST /api/v1/documents/upload` (authenticated) accepts `multipart/form-data` with `file` field
2. Returns `{"id": "uuid", "status": "processing", "filename": "report.pdf"}` with HTTP 202 within 500ms
3. Rejects non-PDF files with HTTP 415 and `{"error": "Only PDF files are accepted."}`
4. Rejects files > `FDA_MAX_FILE_SIZE_MB` with HTTP 413 and clear error message
5. Returns HTTP 402 with clear message when free tier limit reached (check `fda_usage`)
6. File stored to `FDA_STORAGE_PATH/{user_id}/{document_id}.pdf`
7. `fda_documents` row created with `status = "processing"` before returning
8. Background task triggered to run OCR + analysis pipeline (FDA-005)
9. 7 unit tests covering all acceptance criteria above

---

### [FDA-005] OCR + AI analysis pipeline (Nemotron OCR + DeepSeek/Nemotron LLM)
**Assigned to:** Backend/AI Developer Agent  
**Priority:** P1  
**Deadline:** Day 10  
**Blocked by:** FDA-004  

**Description:**  
The intelligence core. Extract text and tables from the PDF using Nemotron OCR v1, then generate structured analysis using the appropriate LLM (Nemotron 70B for ≤ 20 pages, DeepSeek V4 Flash for > 20 pages).

**Acceptance Criteria:**
1. `shared/ocr/client.py` built with `extract(pdf_path: str) -> dict` — calls `nvidia/nemotron-ocr-v1` via NIM API, returns `{pages: [{text, tables, layout}], full_text: str}`
2. For documents ≤ 20 pages: send full text to `nvidia/llama-3.1-nemotron-70b-instruct` with financial analysis system prompt
3. For documents > 20 pages: send full text to `deepseek-ai/deepseek-v4-flash` (1M context) — entire document in one call, no chunking
4. Analysis output is structured JSON: `{summary: str, key_ratios: {}, risk_flags: [str], verdict: str, model_used: str, processing_ms: int}`
5. On completion: `fda_documents.status` updated to `"completed"`, `fda_analyses` row inserted
6. On failure: `fda_documents.status` updated to `"failed"`, error logged
7. Processing time logged to `fda_analyses.processing_ms`
8. System prompt for financial analysis saved to `products/financial-doc-analyzer/backend/prompts/analysis.py` — not hardcoded in pipeline

---

### [FDA-006] Document retrieval and listing endpoints
**Assigned to:** Backend/AI Developer Agent  
**Priority:** P1  
**Deadline:** Day 10  
**Blocked by:** FDA-004  

**Description:**  
Endpoints for the mobile app to poll status and retrieve results.

**Acceptance Criteria:**
1. `GET /api/v1/documents/{id}` returns full document record including analysis if `status == "completed"`; returns 404 if document not owned by requesting user
2. `GET /api/v1/documents/` returns paginated list of user's documents (newest first); supports `?limit=20&offset=0`
3. `DELETE /api/v1/documents/{id}` deletes document, analysis, and file from storage; returns 204; returns 404 if not owned by user
4. Response model for document includes: `id, filename, status, created_at, analysis` (null if not yet complete)
5. All endpoints require authentication
6. 8 unit tests covering retrieval, listing, deletion, ownership checks, and 404 cases

---

### [FDA-007] CI/CD — GitHub Actions pipeline
**Assigned to:** Deployment Agent  
**Priority:** P1  
**Deadline:** Day 7  
**Blocked by:** FDA-001  

**Description:**  
Set up GitHub Actions to run lint, type check, and tests on every PR. Fail the PR if any check fails.

**Acceptance Criteria:**
1. `.github/workflows/ci.yml` created and runs on `pull_request` events targeting `main`
2. Backend job: `ruff check .` → `black --check .` → `pytest --cov` — all must pass for CI to pass
3. Mobile job (once mobile project is bootstrapped): `tsc --noEmit` → `jest --coverage`
4. Coverage report uploaded as CI artifact
5. PR is blocked from merge if CI fails (branch protection rule configured)
6. CI runs complete in under 5 minutes

---

### [FDA-008] Mobile — Project bootstrap and navigation structure
**Assigned to:** Mobile/Frontend Developer Agent  
**Priority:** P1  
**Deadline:** Day 3  
**Blocked by:** None  

**Description:**  
Bootstrap the React Native Expo project for Product 1. Set up navigation, theme, and the API client layer. This is the foundation for all UI tickets.

**Acceptance Criteria:**
1. `npx expo start` runs without errors in `products/financial-doc-analyzer/mobile/`
2. React Navigation stack configured with all screen placeholders (no content needed yet, just screens that render a title)
3. `constants/theme.ts` created with full design system (colours, spacing, radius, fonts from Mobile Agent CLAUDE.md)
4. `constants/strings.ts` created with English and French strings for all planned UI text
5. `lib/api.ts` created: Axios or fetch wrapper reading `EXPO_PUBLIC_API_URL` from env; adds JWT `Authorization` header automatically
6. React Query (`QueryClient`) configured and wrapping the app
7. `jest` runs with zero failures
8. `.env.example` committed; `.env` gitignored

---

### [FDA-009] Mobile — Auth screens (Welcome, Register, Login)
**Assigned to:** Mobile/Frontend Developer Agent  
**Priority:** P1  
**Deadline:** Day 6  
**Blocked by:** FDA-008, FDA-003 (needs auth API live)  

**Description:**  
Three screens that handle user onboarding. JWT tokens stored in `SecureStore` (Expo).

**Acceptance Criteria:**
1. `WelcomeScreen`: app name, one-line value prop, "Create Account" and "Log In" CTAs — navigates to correct screen
2. `RegisterScreen`: email + password fields with Zod validation (email format, password ≥ 8 chars); submits to `POST /api/v1/auth/register`; shows inline field errors; navigates to HomeScreen on success
3. `LoginScreen`: same pattern; submits to `POST /api/v1/auth/login`; shows "Invalid credentials" on 401
4. Tokens stored in `SecureStore` after successful auth — never in AsyncStorage
5. Loading state shown during API calls (button disabled + spinner)
6. Network error handled: "Connection failed. Please try again." with retry option
7. All screens render correctly on both iOS (iPhone 15 Pro) and Android (Pixel 7) simulators
8. Jest render tests for all three screens (no API calls — mock React Query)

---

### [FDA-010] Mobile — Home screen and document upload flow
**Assigned to:** Mobile/Frontend Developer Agent  
**Priority:** P1  
**Deadline:** Day 10  
**Blocked by:** FDA-009, FDA-004 (needs upload API live)  

**Description:**  
The core user flow: home screen showing recent documents + usage meter, file picker, upload progress, and polling for results.

**Acceptance Criteria:**
1. `HomeScreen`: shows list of recent documents (from `GET /api/v1/documents/`); "X of 10 free documents used" usage meter; prominent "Analyze a Document" CTA
2. Empty state: "No documents yet. Upload your first financial report." with CTA — not a blank screen
3. Document picker opens on CTA tap (Expo Document Picker, PDF only); shows selected filename before upload
4. Upload screen shows progress bar (0–100%) during upload to `POST /api/v1/documents/upload`
5. After upload: polls `GET /api/v1/documents/{id}` every 2 seconds with animated "Analyzing..." indicator
6. On `status === "completed"`: navigates to `ResultsScreen` with document ID
7. On `status === "failed"`: shows error with "Try Again" option
8. Freemium gate: if API returns 402, shows bottom sheet explaining the limit with "Upgrade to Premium" CTA
9. Jest tests for HomeScreen render (with mocked documents list) and empty state

---

### [FDA-011] Mobile — Results screen (Summary, Ratios, Risk Flags tabs)
**Assigned to:** Mobile/Frontend Developer Agent  
**Priority:** P1  
**Deadline:** Day 12  
**Blocked by:** FDA-010, FDA-005 (needs analysis API live)  

**Description:**  
The value screen — where the user sees their document analysis. Three tabs loading progressively.

**Acceptance Criteria:**
1. Three tabs: "Summary", "Key Ratios", "Risk Flags"
2. Summary tab loads first and is visible immediately; other tabs may still be loading
3. Summary tab: AI-generated plain-English paragraph(s); model used shown at bottom in small text
4. Key Ratios tab: table of extracted ratios (P/E, P/B, revenue, margins, etc.); "N/A" where not found
5. Risk Flags tab: list of identified risks; empty state "No significant risk flags identified" if none
6. Share button (top right): shares the Summary as plain text via native share sheet
7. "Download PDF Report" button: generates a simple PDF of the analysis and saves to device (Expo Sharing)
8. Pull-to-refresh re-fetches the analysis
9. Document filename and upload date shown in header
10. Jest render test with mocked analysis data

---

## Sprint 1 Summary

| Ticket | Owner | Due | Status |
|--------|-------|-----|--------|
| FDA-001 Project bootstrap | Backend | Day 2 | 🔲 Not started |
| FDA-002 Database schema | Backend | Day 3 | 🔲 Not started |
| FDA-003 JWT auth API | Backend | Day 5 | 🔲 Not started |
| FDA-004 Upload endpoint | Backend | Day 7 | 🔲 Not started |
| FDA-005 OCR + AI pipeline | Backend | Day 10 | 🔲 Not started |
| FDA-006 Document endpoints | Backend | Day 10 | 🔲 Not started |
| FDA-007 CI/CD pipeline | Deployment | Day 7 | 🔲 Not started |
| FDA-008 Mobile bootstrap | Mobile | Day 3 | 🔲 Not started |
| FDA-009 Auth screens | Mobile | Day 6 | 🔲 Not started |
| FDA-010 Home + upload flow | Mobile | Day 10 | 🔲 Not started |
| FDA-011 Results screen | Mobile | Day 12 | 🔲 Not started |

**Definition of Done for Sprint 1:**  
A user can register, upload a PDF, wait for analysis, and see a structured summary — on both iOS and Android simulators. CI runs on every PR. Richard can review and approve PRs via GitHub.
