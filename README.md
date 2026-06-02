# Fair Split — Receipt Settlement Engine

> A production-grade API that turns a restaurant bill photo and a plain-English description of who ate what into a fully reconciled, per-person breakdown. Tax, service charge, discounts, and a final settle-up — all accounted for to the rupee, with every assumption surfaced and every discrepancy flagged.

**Live:** [`https://receipt-settlement-engine.onrender.com`](https://receipt-settlement-engine.onrender.com)
**Healthcheck:** [`/healthz`](https://receipt-settlement-engine.onrender.com/healthz)
**Source:** [github.com/sahaj645/receipt-settlement-engine](https://github.com/sahaj645/receipt-settlement-engine)

Built for the EpiFi GenAI Product Builder internship assignment.

---

## What it does

You give it two things:

1. A photo of a restaurant bill (JPG, PNG, or WebP), base64-encoded
2. A free-text description like *"Ravi had the cappuccino and sandwich. Neha had the pasta and lime soda. Sameer had the brownie. Sameer paid."*

You get back a structured JSON response with:

- **Per-person table** — what each person ate, their pre-tax subtotal, their share of service and tax, any discount applied, and their final total
- **Grand total** that always reconciles to the printed bill
- **Settle-up** — exactly who owes the payer how much
- **Assumptions** — surfaced explicitly (e.g. "Sameer absorbs +1 rupee rounding residual")
- **Flags** — anything ambiguous, mismatched, or unverifiable, raised loudly rather than silently smoothed

The interface is a single dark-themed page with drag-or-click upload, a textarea for the description, and a paper-receipt-style result card.

---

## Why arithmetic stays in Python

The LLM (Gemini 2.5 Flash) is asked to do **one thing only**: extract structured data from the image. It does OCR on the bill and resolves names from the description. It does **not** compute totals, do proportional allocation, or decide rounding.

All math — subtotals, proportional tax/service/discount allocation, paise-precise accumulation via `Decimal`, rupee rounding, settle-up generation — happens in `app/splitter.py`. It's deterministic, auditable, and unit-tested.

This matters because LLMs hallucinate small arithmetic. More importantly, if the same model that *extracts* numbers also *computes* the totals, the `reconciliation` field becomes meaningless — the model would simply agree with itself. Independent verification requires the math to live in code. See [`docs/PROMPT_LOG.md`](docs/PROMPT_LOG.md) for the full rationale and prompt iteration history.

---

## Architecture

```
┌──────────┐   image + text   ┌──────────┐   structured JSON   ┌─────────────┐   per-person split   ┌────────────┐
│  Browser │ ───────────────► │  FastAPI │ ──────────────────► │   Gemini    │ ──────────────────►  │  Splitter  │
│ index.html│   POST /split    │  /split  │                     │ 2.5 Flash   │                      │  (Python)  │
└──────────┘ ◄─────────────── │          │ ◄────────────────── │ OCR + names │ ◄──────────────────  └─────┬──────┘
                response       └────┬─────┘    extracted bill   │  no math    │                            │
                                    │                           └─────────────┘                            ▼
                                    │                                                              ┌──────────────┐
                                    └────────────────────────────────────────────────────────────► │  Reconciler  │
                                                                                                   │  + flag gen  │
                                                                                                   └──────────────┘
```

| Layer        | Module                   | Responsibility                                                       |
| ------------ | ------------------------ | -------------------------------------------------------------------- |
| HTTP         | `app/main.py`            | FastAPI routes, CORS, global exception handling, static mount        |
| Wire schema  | `app/schemas.py`         | Pydantic models for request/response, matching the brief exactly     |
| Extraction   | `app/extractor.py`       | Gemini wrapper, JSON-mode generation, defensive parsing, single retry|
| Prompts      | `app/prompts.py`         | Versioned system prompt and user-prompt template                     |
| Splitting    | `app/splitter.py`        | Pure-function fairness math, `Decimal`-precise, rupee rounding       |
| Reconciling  | `app/reconciler.py`      | Cross-checks and flag generation                                     |
| Config       | `app/settings.py`        | Env-var loading via `pydantic-settings`                              |
| Frontend     | `static/index.html`      | Single-file dark UI with paper-receipt result card                   |

---

## API contract

### `POST /split`

```http
POST /split HTTP/1.1
Content-Type: application/json

{
  "receipt_base64": "<base64-encoded image bytes, no data-URI prefix>",
  "description":    "<free-text who-had-what string, naming the payer>"
}
```

### Response

```json
{
  "per_person": [
    {
      "name":            "Ravi",
      "items":           ["Cappuccino", "Grilled Chicken Sandwich"],
      "subtotal":        440,
      "tax_share":       23,
      "service_share":   22,
      "discount_share":  0,
      "total":           485
    }
  ],
  "grand_total":     1147,
  "reconciliation":  {"sum_of_person_totals": 1147, "matches_bill": true},
  "paid_by":         "Sameer",
  "settle_up":       [{"from": "Ravi", "to": "Sameer", "amount": 485}],
  "assumptions":     ["Payer 'Sameer' absorbs +1 rupee rounding residual to balance to grand total."],
  "flags":           []
}
```

`reconciliation`, `assumptions`, and `flags` are **always present**, even on success. They are the system's way of policing its own arithmetic.

### Other routes

| Method | Path       | Purpose                                            |
| ------ | ---------- | -------------------------------------------------- |
| GET    | `/`        | Single-page frontend                               |
| GET    | `/healthz` | Liveness probe, returns `{"ok": true}`             |

### Errors

The API returns HTTP **200** even for extraction failures (bad base64, Gemini outage, malformed JSON from the model). The response body keeps the same `SplitResponse` shape with an empty `per_person` and a single `flags` entry like `"error: <message>"`. This is a deliberate design choice: the frontend renders errors inline without needing to special-case 5xx status codes.

### curl example

```bash
curl -X POST https://receipt-settlement-engine.onrender.com/split \
  -H "Content-Type: application/json" \
  -d "{\"receipt_base64\":\"$(base64 -w0 sample.jpg)\",\"description\":\"Ravi had the cappuccino and sandwich. Neha had the pasta. Sameer paid.\"}"
```

---

## Fairness rules (from the brief, exact)

1. Each person pays for the items they consumed.
2. Shared items split equally among the people who shared **that specific item**.
3. Tax + service charge allocated proportional to each person's pre-tax subtotal.
4. Bill-level discount allocated proportional to subtotal.
5. Round to the rupee; the **payer absorbs the leftover paise**. If no payer is known, the largest-subtotal person absorbs it and the system flags the choice in `assumptions`.

---

## Running locally

```bash
git clone https://github.com/sahaj645/receipt-settlement-engine.git
cd receipt-settlement-engine

python -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env                # then edit .env and paste your GEMINI_API_KEY

uvicorn app.main:app --reload
```

Open `http://localhost:8000` — drag a receipt photo onto the upload box, paste a description, click **Split the bill**.

### Tests

```bash
pytest tests/ -v
```

35 tests covering the four sample receipts (R1–R4), fairness rule invariants, paise-drift edge cases, missing-payer handling, and all 12 reconciler flag conditions.

---

## Deployment

The repo ships with [`render.yaml`](render.yaml) — a Render blueprint that wires the service to auto-deploy on push to `main`. To redeploy:

1. New → Blueprint → connect this repo
2. Set `GEMINI_API_KEY` in the service's Environment tab
3. Render handles the rest; healthcheck on `/healthz` keeps the dyno alive

A [`Dockerfile`](Dockerfile) is included as a backup deploy path for any OCI-compatible host (Fly, Railway, GCP Cloud Run).

---

## Project layout

```
receipt-settlement-engine/
├── app/
│   ├── main.py            # FastAPI app, routes, CORS, exception handlers
│   ├── schemas.py         # Pydantic wire + internal models
│   ├── extractor.py       # Gemini 2.5 Flash wrapper, JSON-mode, retry
│   ├── splitter.py        # Pure-function fairness math (Decimal)
│   ├── reconciler.py      # Cross-checks and flag generation
│   ├── prompts.py         # Versioned prompt strings
│   └── settings.py        # Env-var config
├── static/
│   └── index.html         # Single-file frontend, ~380 lines
├── tests/
│   ├── test_splitter.py   # R1-R4 fixtures + edge cases
│   ├── test_reconciler.py # Flag condition coverage
│   └── fixtures/
│       └── sample_extractions.json
├── docs/
│   ├── PROMPT_LOG.md      # Iteration history + arithmetic-in-code rationale
│   ├── EDGE_CASES.md      # 24 cases, table format, verified status
│   └── AI_WAS_WRONG.md    # 3 real misfires, how caught, how fixed
├── render.yaml            # Render blueprint
├── Dockerfile             # Backup deploy path
├── requirements.txt
├── .env.example
└── LICENSE                # MIT
```

---

## Documentation

| Doc                                       | What's in it                                                                                                |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| [`docs/PROMPT_LOG.md`](docs/PROMPT_LOG.md)     | 10 prompt iterations with one-line rationale each, plus an explicit answer to *"did you let the model do the arithmetic?"* |
| [`docs/EDGE_CASES.md`](docs/EDGE_CASES.md)     | A table of 24 edge cases — input shape, system behavior, and verification status — covering every dimension the brief listed plus more |
| [`docs/AI_WAS_WRONG.md`](docs/AI_WAS_WRONG.md) | Three concrete model misfires from testing, how the reconciler caught each one, and how the prompt was tightened |

---

## Design notes

A few decisions worth flagging for the reviewer:

- **Two-layer schemas.** `SplitRequest` / `SplitResponse` are the wire contract. `ExtractedBill` / `SplitResult` are internal. This lets the API contract evolve independently of how intermediate state is represented.
- **Decimal precision throughout.** All accumulation happens in `Decimal` quantized to paise (`0.01`). The final cast to integer rupees happens once, at the end, with `ROUND_HALF_UP`.
- **Two-pass rounding.** Each line item's per-head share is computed, then the line's residual (e.g. ₹100 / 3 = ₹33.33 × 3 = ₹99.99) is absorbed by the first eater so the line sums exactly. Then per-person totals are rounded to rupees, and the grand-total residual is absorbed by the payer. The displayed `subtotal` column also gets a balancing pass so it sums exactly to the printed subtotal.
- **Lazy Gemini SDK init.** The `google-generativeai` import is deferred until the first `/split` call. This keeps the test suite key-free and import-fast.
- **Synonym map in the reconciler.** "pasta" in the description doesn't false-flag when the bill says "Penne Arrabiata". A small handcrafted mapping covers common Indian + Italian dish aliases.

---

## License

[MIT](LICENSE)
