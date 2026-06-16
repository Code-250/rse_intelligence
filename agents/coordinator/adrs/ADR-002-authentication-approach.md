# ADR-002: Authentication Approach for Product 1

**Date:** 2026-06-16  
**Status:** Accepted  
**Author:** Coordinator Agent

---

## Context
Product 1 (Financial Document Analyzer) requires user authentication to enforce the freemium gate (10 docs/month) and associate documents with owners. We needed to choose between stateful sessions and stateless JWT.

## Decision
**Stateless JWT.** Access token (60 minutes) + refresh token (30 days). No session store required. Signed with `FDA_SECRET_KEY`.

## Rationale
1. No Redis or session store needed — keeps infrastructure simple for MVP
2. Works natively with mobile apps (tokens stored in `SecureStore`)
3. Stateless = horizontally scalable with zero additional config
4. 60-minute access token expiry is short enough to limit exposure if a token leaks

## Consequences
- Token revocation is not instant — a stolen access token remains valid for up to 60 minutes. Mitigation: short expiry + HTTPS only
- Refresh token must be rotated on use (Backend/AI Agent must implement refresh token rotation to prevent replay attacks)
- `FDA_SECRET_KEY` rotation invalidates all active sessions — acceptable for MVP, must be documented in runbook

## Alternatives Considered
- **Session-based auth with Redis:** More robust revocation but adds infrastructure dependency. Revisit for Advisor Copilot where enterprise clients may require instant session termination
- **OAuth2 (Google/GitHub):** Reduces friction but requires users to have those accounts. Wrong for the East African market where LinkedIn/Google penetration is lower
