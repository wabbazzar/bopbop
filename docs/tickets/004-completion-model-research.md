# 4. Research models for fast code completion

Status: Queued. Start after the ticket 3 test ride. Research/evaluation deliverable.

## Question

Is the installed GPT OSS model appropriate for fast inline code suggestions, or
should BopBop use a separate model and inference path for that interaction?
Assess the training objective and supported inference format directly. Do not
assume chat instruction tuning, post-training or a model's parameter count
establishes suitability for code completion.

## Research and evaluation scope

- Consult primary model cards, training reports and serving documentation.
  Distinguish causal continuation, fill-in-the-middle (FIM) training with prefix
  and suffix, chat/instruction post-training, and edit/diff objectives. Determine
  which capabilities are documented for GPT OSS versus merely promptable.
- Compare GPT OSS as an explicit baseline with at least two viable local code
  completion models. Verify licenses, downloadable weights/quantizations,
  supported templates, serving compatibility and fit on this machine before
  proposing a candidate. Record sources and versions; avoid paid services.
- Keep completion separate from the agent tool loop. Evaluate a selected prefix
  and suffix, bounded repository context, short output, cancellation and stale
  result rejection. Do not run tools on each keystroke.
- Build a reproducible fixture corpus covering the languages actually used in
  this workspace, including Python, TypeScript and Svelte when applicable.
  Include mid-function holes, imports, identifiers, indentation, suffix overlap,
  comments and cases where abstaining is better. Keep a held-out set.
- Report warm/cold p50 and p95 first-visible-suggestion and total latency,
  output speed, memory/VRAM residency and cache behavior. Measure contention
  with the chat model and any model loading/unloading cost. User-visible latency
  includes debounce and context gathering, not just token generation.
- Evaluate syntax/compilation, relevant tests, suffix preservation, duplication,
  useful accepted characters and manual accept/reject judgments. Report exact
  match only where meaningful. A fast incorrect suggestion is not a success.
- Set a proposed interactive latency target before benchmarking (initial target:
  warm p95 first useful suggestion under 500ms for short completions), then report
  measured feasibility. This target is not a promised result.
- Prototype only enough editor behavior to test debounce, explicit invocation,
  cancellation and stale-buffer rejection. No automatic suggestion feature is
  enabled in normal BopBop as part of this ticket.

## Deliverables and acceptance

1. A sourced comparison explaining training/inference fit, including what is
   unknown about GPT OSS. Separate evidence from assumptions.
2. A repeatable benchmark runner, public-safe fixtures, environment/settings,
   result table and representative successes/failures. Store downloaded weights
   and private logs outside git.
3. A recommendation: model, serving path, context budget, debounce/cancel policy,
   fallback and resource tradeoffs; or an explicit finding that available options
   do not meet the target.
4. A proposed follow-up implementation ticket sized from the evidence.

## User test ride

Let the user compare a few labeled or blinded completion candidates in the
contained prototype. Record responsiveness and acceptance feedback. Present the
recommendation and proposed follow-up; implementation of automatic suggestions
is a subsequent decision.
