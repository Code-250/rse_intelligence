# RSE Intelligence — Multi-Agent Operating System

## Overview
Six AI agents run this company. Richard Munyemana is the human authority.

| Agent | Directory | Status |
|---|---|---|
| 🧠 Coordinator | `agents/coordinator/` | ✅ Active |
| 🔧 Backend/AI Developer | `agents/backend-ai-dev/` | ✅ Active |
| 📱 Mobile/Frontend Developer | `agents/mobile-frontend-dev/` | ✅ Active (activated 2026-06-16) |
| 🎫 Project Manager | `agents/project-manager/` | ✅ Active |
| 📣 Sales & Marketing | `agents/sales-marketing/` | 🔲 Activates at Product 1 Beta |
| 🚀 Deployment | `agents/deployment/` | 🔲 Activates at Week 6 of Product 1 |

## How to Invoke an Agent
Each agent has a `CLAUDE.md` in its directory. When you spawn a Claude agent for a specific role, point it at that file as its system context.

```python
# Example: spawn the Backend/AI Developer Agent on a ticket
agent = ClaudeAgent(
    system_prompt=open("agents/backend-ai-dev/CLAUDE.md").read(),
    working_directory="products/financial-doc-analyzer/backend/",
    tools=["read", "write", "edit", "bash", "github"],
)
agent.run("Implement ticket FDA-003: JWT authentication endpoints")
```

## Current Sprint
**Product 1 — Financial Document Analyzer — Sprint 1**  
Tickets: `products/financial-doc-analyzer/tickets/SPRINT-01.md`  
PR Queue: `products/financial-doc-analyzer/tickets/PR_QUEUE.md`

## Non-Negotiables
1. No PR merges without Richard's GitHub approval
2. No marketing spend without Richard's sign-off
3. No production deployment without staging validation
4. Rollback procedure must exist before any migration runs
5. Compliance framing: Advisor Copilot is a "draft assistant", not autonomous advice

## Directory Structure
```
agents/
├── coordinator/           # CTO layer — orchestrates all agents
│   ├── CLAUDE.md
│   ├── adrs/              # Architecture Decision Records
│   └── log/               # Daily stand-up logs and decisions
├── backend-ai-dev/        # Python, FastAPI, NIM, PostgreSQL
│   └── CLAUDE.md
├── mobile-frontend-dev/   # React Native, React Web, UI/UX
│   └── CLAUDE.md
├── project-manager/       # Tickets, PR reviews, bug triage
│   └── CLAUDE.md
├── sales-marketing/       # Analytics, ad strategy, revenue models
│   └── CLAUDE.md
└── deployment/            # CI/CD, staging, production, rollbacks
    └── CLAUDE.md

products/
├── financial-doc-analyzer/   # Product 1 — ACTIVE SPRINT
│   ├── backend/
│   ├── mobile/
│   └── tickets/
├── advisor-copilot/          # Product 2 — planned
├── document-vault/           # Product 3 — planned
└── rse-investor-app/         # Product 4 — planned

shared/
├── llm/    # Shared LLM client (imported from backend/llm/)
├── ocr/    # Shared OCR client (built in Product 1 sprint)
└── db/     # Shared DB utilities
```
