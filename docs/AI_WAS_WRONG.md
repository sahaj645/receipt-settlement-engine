# Where the AI was wrong

Three real failure cases I caught while testing, what tipped me off,
and how I fixed each one. The pattern: the model's first answer often
looks plausible, and the reconciler / unit tests are what catches it.

## 1. Misread `260` as `200` on a slightly faded receipt

**Receipt:** R1 (Brew & Bite Café), but I deliberately darkened the
sandwich line in an image editor before testing.

**What Gemini returned:** `{"name": "Grilled Chicken Sandwich",
"line_total": 200}` instead of `260`.

**How I caught it:** the reconciler. With `line_total=200` for the
sandwich, the sum of line items was `980`, but `printed_subtotal=1040`.
The check `_check_line_items_sum` flagged:

> Line items sum to 980.00 but printed subtotal is 1040.00 — gap of -60.00 unexplained.

A user looking at the flag could spot it immediately — the sandwich
should have been ~₹60 more. Without the reconciler, the per-person
split would have been silently wrong and Sameer (who didn't eat the
sandwich) wouldn't have noticed.

**Fix:** none in the model — the reconciler doing its job is the
intended behavior. The brief explicitly asks us to flag rather than
fabricate. I did tighten the extractor prompt (`v0.5`) to emphasize
"copy printed values verbatim, do not recompute" so the model is less
tempted to "fix" what looks like a typo by rounding it down.

---

## 2. Hallucinated a "Service tip 10%" line that wasn't on the bill

**Receipt:** R3 (Pizza Bar). The bill has `Service 5%` but no tip line.

**What Gemini returned:** items as expected, plus
`{"name": "Service tip", "line_total": 156}` appearing inside `items[]`
with no source on the actual receipt. The model was hallucinating a
tip line because tipping is conventional in many cuisines it has seen.

**How I caught it:** the line-items-sum check fired (`1660 + 156 = 1816
≠ 1660`), and the per-person table had a confusing extra item. Also,
the `printed_service` field was correctly `83` (5%), so the duplication
was visible.

**Fix:** prompt rule in `v0.5`: *"Items in `items[]` must be lines
actually printed on the bill. Surcharges, service, taxes, and discounts
go in their respective `printed_*` fields, not in items."* The
ambiguities check became a defense in depth: if the model still slips a
phantom line in, the reconciler will catch the subtotal mismatch.

---

## 3. Assigned "Pasta" to the wrong person when description used a pronoun

**Description:** *"Priya and I shared the pasta. The cheesecake was
Karan's. Everything else was common to all four of us."* (from the
brief's example, paraphrased)

**What Gemini returned:** the pasta was assigned to `["Priya", "me"]`
where `"me"` was added as a fourth person to `people[]`. This broke
the subset-split math because there was a ghost diner with no other
items.

**How I caught it:** manual review — the per-person table had four rows
when the description named three diners plus "I" (who is one of them).
Karan ended up paying ⅓ of the dessert "share" because the model
treated `"me"` as a real person.

**Fix:** prompt rule in `v0.7`: *"Resolve every name. Pronouns (`I`,
`we`, `us`, `me`) must be mapped to a concrete name when context
allows. If it cannot be resolved unambiguously, add a string to
`ambiguities` describing the issue — do NOT guess."* The model now
either picks the right name (often it can: "the user" is whoever the
narrator of the description is, and there are usually enough names to
disambiguate) or surfaces an ambiguity that the reconciler propagates
to `flags`.

---

## Pattern

Every one of these was caught by something *other than* the model that
made the mistake:

- #1 by the reconciler's printed-vs-extracted check
- #2 by the same check plus visual review of the per-person table
- #3 by manual review of the diner count

That's the whole architectural argument for keeping arithmetic in code
and forcing the model to be explicit about ambiguities. Self-policing
LLMs are a worse strategy than independent verification.
