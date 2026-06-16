# ADR-001: Monorepo Structure

**Date:** 2026-06-16  
**Status:** Accepted  
**Author:** Coordinator Agent

---

## Context
RSE Intelligence will build four products sharing significant backend infrastructure (LLM client, OCR client, database utilities, authentication patterns). We needed to decide whether to use a monorepo (all products in one repository) or a multi-repo (one repository per product).

## Decision
**Monorepo.** All products live in the single `rse-intelligence` repository under `products/{product-name}/`. Shared code lives in `shared/` and `backend/` (existing RSE pipeline).

## Rationale
1. Shared infrastructure (`llm/client.py`, `shared/ocr/client.py`) is imported directly — no versioning overhead, no package registry
2. A single CI/CD pipeline covers all products — one GitHub Actions config, one set of secrets
3. Easier for the agent system to cross-reference code across products
4. The codebase is small enough that a monorepo is not a performance burden on CI

## Consequences
- All agents work in the same repository — branch naming must include the product prefix (`FDA-`, `AC-`, `DV-`, `RSE-`)
- A bug in `shared/` code affects all products — extra care required when modifying shared modules
- The Coordinator must maintain clear ownership boundaries so agents don't overwrite each other's work

## Alternatives Considered
- **Multi-repo:** Rejected because shared code would require a private PyPI package or git submodules, adding complexity before there's a team large enough to benefit from repo isolation
