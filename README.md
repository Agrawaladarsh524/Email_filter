<div align="center">

# 📧 Email Filter

**Automated Gmail Triage · LLM Agent · RAG Memory · Multi-Channel Alerts**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-Tool_Calling-412991?style=for-the-badge&logo=openai&logoColor=white)
![Gmail](https://img.shields.io/badge/Gmail-API-EA4335?style=for-the-badge&logo=gmail&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-RAG_Store-003B57?style=for-the-badge&logo=sqlite&logoColor=white)

</div>

---

An intelligent email triage system that goes beyond simple LLM wrappers. It implements a **4-tier cascading pipeline** — deterministic heuristics, semantic embeddings, retrieval-augmented generation (RAG), and agentic tool calling — to classify, label, and act on incoming emails with built-in safety guardrails.

## Architecture

```
┌─────────────┐
│  Gmail API   │  ← OAuth 2.0
└──────┬──────┘
       │
       ▼
┌─────────────┐     ┌──────────────────┐
│   Poller     │────▶│  Seen Store (DB)  │  ← Skip already processed
└──────┬──────┘     └──────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────┐
│              CASCADING PIPELINE                   │
│                                                   │
│  Tier 1: Heuristics ──── pattern match? ── done  │
│       │ no                                        │
│  Tier 2: Embeddings ──── >95% similar? ── done   │
│       │ no                                        │
│  Tier 3: LLM Agent ──── tool calls ──────────┐   │
│       │                                      │   │
│  Tier 4: Guardrails ──── VIP? cap? conf? ────┤   │
│                                              │   │
└──────────────────────────────────────────────┼───┘
                                               │
       ┌───────────────────────────────────────┘
       ▼
┌─────────────┐  ┌───────────┐  ┌────────────┐
│ Gmail Labels │  │   Slack    │  │  Telegram   │
│ Archive/Star │  │   Alert    │  │   Alert     │
└─────────────┘  └───────────┘  └────────────┘
```

## Key Features

| Feature | Description |
|---------|-------------|
| **Agentic Tool Calling** | LLM outputs structured tool requests (`ClassifyAndLabel`, `ArchiveEmail`, `DraftReply`, `MarkImportant`, `ExtractTasks`, `SendAlert`) via OpenAI function calling |
| **Semantic Embeddings** | Converts emails to vectors using `text-embedding-3-small`. Cosine similarity against past emails enables instant classification without LLM calls |
| **RAG Memory** | SQLite-backed vector store. Past classifications and human corrections are injected into the LLM prompt as personalized context |
| **Guardrails Engine** | Deterministic Python interceptor — VIP sender protection, daily action caps, confidence thresholds, quiet hours suppression |
| **Human-in-the-Loop** | `POST /feedback/{id}` endpoint lets users correct classifications. Corrections feed back into RAG memory so the system never repeats the same mistake |
| **Multi-Channel Alerts** | Slack (rich blocks) and Telegram notifications for urgent/important emails |
| **Auto Draft Generation** | LLM generates context-aware reply drafts saved directly to Gmail |
| **Cost Optimization** | Embedding shortcut skips the LLM entirely for emails similar to previously verified ones |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, Uvicorn |
| AI/ML | OpenAI SDK (GPT-4o-mini, text-embedding-3-small), NumPy |
| Database | SQLite (state tracking, RAG vector store, action counts) |
| Integrations | Gmail API (OAuth 2.0), Slack API, Telegram Bot API |
| Validation | Pydantic, Pydantic Settings |
| Resilience | Tenacity (retry logic), per-message error isolation |

## Project Structure

```
src/email_filter/
├── api.py            # FastAPI server, polling loop, feedback endpoint
├── cli.py            # CLI entrypoints (run / serve)
├── config.py         # Pydantic settings from .env
├── classifier.py     # LLM agent with tool schemas
├── embeddings.py     # OpenAI embeddings + cosine similarity
├── rag_store.py      # SQLite vector store and RAG retrieval
├── pipeline.py       # 4-tier cascading triage orchestrator
├── policy.py         # Category → action mapping
├── heuristics.py     # Rule-based pre-classifier
├── guardrails.py     # VIP checks, quiet hours
├── drafter.py        # LLM draft reply generation
├── seen.py           # SQLite state: seen emails, rate limits, tasks
└── clients/
    ├── gmail.py      # Gmail API wrapper (OAuth)
    ├── slack.py      # Slack webhook client
    └── telegram.py   # Telegram bot client
```

## Getting Started

### 1. Install

```bash
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
```

Fill in your keys:

```env
OPENAI_API_KEY=sk-...
GMAIL_QUERY=is:unread
SLACK_WEBHOOK_URL=https://hooks.slack.com/...    # optional
TELEGRAM_BOT_TOKEN=...                           # optional
TELEGRAM_CHAT_ID=...                             # optional
```

Place your Google OAuth `credentials.json` in the project root.

### 3. Run

```bash
# Test run — classify without modifying Gmail
email-filter run --once --dry-run

# Single live cycle
email-filter run --once

# Start the API server with background polling
email-filter serve
```

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Service status, Gmail connectivity, config summary |
| `POST` | `/poll` | Trigger an immediate poll cycle |
| `POST` | `/triage` | Triage a single message by ID |
| `GET` | `/processed` | Recent triage history (newest first) |
| `GET` | `/stats` | Classification distribution counts |
| `POST` | `/feedback/{message_id}` | Submit human correction → updates RAG memory |

## How the Guardrails Work

The LLM **proposes** actions. Python **decides** whether to execute them.

```
LLM says: "ArchiveEmail"
    → Is sender on VIP list?         → Block
    → Daily archive cap reached?     → Block
    → Confidence below threshold?    → Block
    → All checks pass?               → Execute
```

Every tool call flows through this interceptor before touching any external API.
