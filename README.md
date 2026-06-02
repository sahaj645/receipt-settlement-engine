# Fair Split — Receipt Settlement Engine

A production-grade API + minimal frontend that takes a restaurant bill photo and a plain-English "who had what" description, then returns a fully reconciled per-person breakdown including tax, service charge, discount, and a final settle-up.

Built for the EpiFi GenAI Product Builder internship assignment.

> Live URL: **https://receipt-settlement-engine.onrender.com**
> Healthcheck: [`/healthz`](https://receipt-settlement-engine.onrender.com/healthz)

## What it does

Upload a receipt image, paste a description like *"Ravi had the cappuccino and sandwich. Neha had the pasta and lime soda. Sameer had the brownie. Sameer paid."*, and get back exactly who owes whom, in rupees, with every assumption and discrepancy flagged.

## Architecture

```
┌───────────┐   image+text   ┌──────────────┐   structured JSON   ┌──────────┐   per-person split   ┌────────────┐
│  Browser  │ ─────────────► │   FastAPI    │ ──────────────────► │ Gemini   │ ─────────────────►   │  Splitter  │
│ index.html│                │  /split      │                     │ 2.0 Flash│                      │  (Python)  │
└───────────┘ ◄───────────── │              │ ◄────────────────── │ (OCR only│  ◄──────────────     └─────┬──────┘
                response     └──────┬───────┘    extracted bill   │ no math) │                            │
                                    │                             └──────────┘                            ▼
                                    │                                                              ┌────────────┐
                                    └──────────────────────────────────────────────────────────►   │ Reconciler │
                                                                                                   │   +flags   │
                                                                                                   └────────────┘
```

**Arithmetic is done in Python, not by the LLM.** The LLM only extracts structured data. See [`docs/PROMPT_LOG.md`](docs/PROMPT_LOG.md) for the rationale.

## Endpoints

| Method | Path       | Purpose                                  |
| ------ | ---------- | ---------------------------------------- |
| POST   | `/split`   | Main split endpoint                      |
| GET    | `/`        | Serves the single-page frontend          |
| GET    | `/healthz` | Liveness probe for Render                |

### `POST /split`

Request:

```json
{
  "receipt_base64": "<base64 image bytes, no data-URI prefix>",
  "description": "Ravi had the cappuccino and sandwich. Neha had the pasta and lime soda. Sameer had the brownie. Sameer paid."
}
```

Response:

```json
{
  "per_person": [
    {"name": "...", "items": [...], "subtotal": 0, "tax_share": 0, "service_share": 0, "discount_share": 0, "total": 0}
  ],
  "grand_total": 0,
  "reconciliation": {"sum_of_person_totals": 0, "matches_bill": true},
  "paid_by": "...",
  "settle_up": [{"from": "...", "to": "...", "amount": 0}],
  "assumptions": [...],
  "flags": [...]
}
```

### curl example

```bash
curl -X POST https://receipt-settlement-engine.onrender.com/split \
  -H "Content-Type: application/json" \
  -d "{\"receipt_base64\":\"$(base64 -w0 sample.jpg)\",\"description\":\"Ravi had the cappuccino and sandwich. Neha had the pasta. Sameer paid.\"}"
```

## Local development

```bash
git clone https://github.com/sahaj645/receipt-settlement-engine.git
cd receipt-settlement-engine
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # add your GEMINI_API_KEY
uvicorn app.main:app --reload
```

Then open `http://localhost:8000`.

## Tests

```bash
pytest tests/ -v
```

Unit tests cover the splitter against all four sample receipts (R1–R4) plus edge cases.

## Deploy (Render, free tier)

The repo ships with [`render.yaml`](render.yaml). On Render: **New → Blueprint → connect this repo**. Set `GEMINI_API_KEY` in the dashboard. Auto-deploys on push to `main`. Healthcheck is wired to `/healthz`.

A [`Dockerfile`](Dockerfile) is included as a backup deploy path.

## Project layout

```
app/
  main.py        FastAPI app, CORS, routes, static mount
  schemas.py     Pydantic request/response models
  extractor.py   Gemini wrapper: image+text → structured JSON
  splitter.py    Pure math: fairness rules → per_person, settle_up
  reconciler.py  Reconciliation checks, flag generation
  prompts.py     Versioned prompt strings
  settings.py    Env vars via pydantic-settings
static/
  index.html     Single-file frontend
tests/
  test_splitter.py
  test_reconciler.py
docs/
  PROMPT_LOG.md
  EDGE_CASES.md
  AI_WAS_WRONG.md
```

## Docs

- [Prompt log](docs/PROMPT_LOG.md) — iterations + why arithmetic stays in code
- [Edge cases](docs/EDGE_CASES.md) — probed scenarios + behavior
- [Where the AI was wrong](docs/AI_WAS_WRONG.md) — three real misfires + fixes

## License

[MIT](LICENSE)
