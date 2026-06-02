# Prompt log

Every change to the Gemini extractor prompt is a commit and a version bump
in `app/prompts.py::EXTRACTOR_PROMPT_VERSION`. One line per iteration.

| Version | Change                                                                                                          | Why                                                                                  |
| ------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| v0.1    | Single freeform request: "extract items and who had what, then split the bill"                                  | Quick smoke test. Failed immediately — model fabricated a "service tip" line.        |
| v0.2    | Added explicit "do not compute totals, only extract" instruction                                                | The model kept producing per-person totals that didn't add up — pushed math to code. |
| v0.3    | Pinned a JSON schema in the prompt body with field types                                                        | Free-text JSON varied between runs (sometimes wrapped in markdown fences).           |
| v0.4    | Switched SDK call to `response_mime_type="application/json"` + lowered temperature to 0.1                       | Mime-mode is more reliable than asking nicely; low temp because extraction ≠ writing.|
| v0.5    | Added `printed_*` fields with explicit "copy verbatim, do not recompute"                                        | Model kept "fixing" obvious printed errors silently, defeating the reconciler.       |
| v0.6    | Made `assignments[]` indexed (item_index) rather than name-keyed                                                | Duplicate item names ("Garlic Naan x 4") broke name-keyed assignments.               |
| v0.7    | Added rule that pronouns must resolve to named diners or go to `ambiguities[]`                                  | "Priya and I shared the pasta" was being assigned to a `"me"` ghost person.          |
| v0.8    | Added rule that the model must NOT guess a payer; null + ambiguity note instead                                 | "Sameer was the host" was being inferred as payer; brief says flag, don't assume.    |
| v0.9    | Added retry-on-invalid-JSON with explicit "your last output wasn't valid JSON" reminder                         | One free-tier response in ~30 had stray prose before the `{`. One retry catches it.  |
| v0.10   | Added `printed_discount` as a positive magnitude with sign normalization in code                                | Some bills print `-228`, others `(228)`, others `15% off`. Normalize once, in code.  |

## Did you let the model do the arithmetic, or extract structured data and compute totals in code? Why?

**Arithmetic is in code, not the LLM.**

LLMs hallucinate small arithmetic. Even Gemini 2.0 Flash will confidently
return `52 + 54.60 = 106.60` *most* of the time, but on a bill where the
service charge prints `5%` rounded oddly it will sometimes drop a paise or
round the wrong direction — and you'll never know until a user notices.

More importantly, the `reconciliation` field becomes meaningless if the
same model that *extracts* numbers also *computes* the totals: the model
will just agree with itself. Auditing the split requires the math to be
independent of the extraction.

So the LLM only does OCR and natural-language assignment ("Priya had the
pasta"). All summation, proportional allocation, rounding, and settle-up
math happens in `app/splitter.py` — pure Python, `Decimal`-precise, unit
tested against the four sample receipts plus edge cases. The result is
deterministic, auditable, and reproducible.
