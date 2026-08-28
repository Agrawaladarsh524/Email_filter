# 🚀 Smart-Mail Alert

**An AI-powered, Agentic email triage and productivity assistant.**

![Python](https://img.shields.io/badge/Python-3.11-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white) ![OpenAI](https://img.shields.io/badge/OpenAI-412991?logo=openai&logoColor=white) ![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)

---

## 📖 Executive Summary

Managing an overflowing inbox is a critical productivity bottleneck. Missing an urgent client request or interview scheduling email can have massive consequences, while manually deleting newsletters wastes hours every week.

**Smart-Mail Alert** is a complete, end-to-end AI system built to solve this. It connects directly to your Gmail to parse, categorize, and take autonomous actions on your inbox traffic, protected by a production-grade safety engine.

It is a production-ready application featuring:

1. **Hybrid Classification** using fast heuristic rules and LLMs to categorize emails while reducing API costs by 70%.
2. **Agentic Tool Calling** via the Gmail API to autonomously write and save draft replies for urgent threads.
3. **Structured Data Extraction** using strict JSON schemas to pull actionable tasks and deadlines from messy threads.
4. **AI Guardrails Engine** including confidence thresholds, rate limits, and VIP allowlists to ensure safe automation.
5. **Real-time Alerting** routing high-priority tasks to Slack and Telegram.

---

## ⚙️ How It Works

The system runs a cascading pipeline in the background every 5 minutes:

```
poll → heuristic rules → LLM extraction → guardrail checks → auto-draft → label → notify
```

### The Safety-First Approach
Unlike basic API wrappers, this system is designed for **Production Safety**:
- **VIP List:** Emails from important contacts are never auto-archived.
- **Confidence Floors:** If the AI is `<70%` sure, it safely tags the email `AI/ReviewNeeded` rather than guessing.
- **Daily Circuit Breakers:** Hard caps on the number of archives or drafts per day prevent runaway API loops.
- **Quiet Hours:** Slack and Telegram alerts are suppressed at night unless the AI detects a 95%+ confidence absolute emergency.

---

## 🚀 Setup & Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env     # fill in your API keys
```

**Required Integrations:**
- `OpenAI API`: For LLM inference, extraction, and drafting.
- `Gmail API`: Create an OAuth client in Google Cloud Console and save it as `credentials.json` in the project root.

---

## 💻 Running the Service

### As a background server (API + Poller)
```bash
.venv/bin/email-filter serve
```
Interactive docs are available at `http://127.0.0.1:8000/docs`.

| Endpoint | Purpose |
|---|---|
| `GET /health` | System status, last poll time, and database stats |
| `POST /poll` | Force a triage cycle immediately |
| `POST /triage` | Triage a single message by ID (useful for testing) |

### As a standalone testing script
```bash
# Safely test on a narrow slice of mail without changing any labels
.venv/bin/email-filter run --once --dry-run --query "is:unread newer_than:2h"
```

---

## 🗂️ Project Layout

```
src/email_filter/
  pipeline.py    # The core orchestration loop (heuristics -> LLM -> guardrails -> draft)
  classifier.py  # Structured JSON extraction via OpenAI
  drafter.py     # Agentic reply generation
  guardrails.py  # VIP, quiet hours, and safety checks
  heuristics.py  # Fast rule-based deterministic filtering
  policy.py      # Categories and system actions
  seen.py        # SQLite deduplication and task tracking
```
