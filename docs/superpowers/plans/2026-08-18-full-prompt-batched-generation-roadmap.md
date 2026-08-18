# Full-Prompt Batched Seedance Generation Roadmap

> This roadmap replaces the unfinished Task 7–9 portion of `2026-08-17-reference-video-segmentation-and-seedance-implementation.md`. Tasks 1–6 from that plan remain valid and are treated as the completed foundation.

**Authoritative specification:** `docs/superpowers/specs/2026-08-18-full-prompt-batched-seedance-generation-design.md`

## Product outcome

The user edits and approves one full-video prompt. The prompt contains every shot in absolute source-video time and includes the confirmed product information and product-image purposes. When generation starts, the server uses the saved immutable generation plan to create one Seedance task per execution segment. Each task receives the physical source-video segment, the deterministically derived relative-time prompt, and all confirmed product reference images. Results remain separate and downloadable; the system does not concatenate them automatically.

## Execution order

1. [Backend full-prompt workflow](2026-08-18-adflow-full-prompt-backend-implementation.md)
   - Replace the unfinished segment-prompt UI contract with a single full-prompt contract.
   - Generate, validate, save, refine, and selling-point-optimize immutable full prompts.
   - Preserve the deterministic product rules and absolute-time shot blocks.
2. [Backend batched Seedance workflow](2026-08-18-adflow-batched-seedance-backend-implementation.md)
   - Add batch identity to generation rows.
   - Atomically create one Generation per saved execution segment.
   - Derive relative-time provider prompts without GPT and submit to either Volcengine or Comfly.
   - Recover, retry, poll, download, and expose grouped batch results.
3. [Frontend and release acceptance](2026-08-18-adflow-batched-generation-frontend-acceptance.md)
   - Keep the existing workflow and layout; add full-prompt optimization, provider choice, one-click batch submission, recovery, playback, and download.
   - Verify responsive behavior and local process/migration startup.
   - Perform controlled real generation with both providers only after explicit quota approval.

## Status mapping from the earlier plan

| Earlier work | Current status | New location |
|---|---|---|
| Tasks 1–6: planning, persistence, segment prompt backend, segment generation backend, segment editor | Completed foundation; retain code unless a new task explicitly changes it | Existing commits through Task 6 |
| Task 7: segment-aware prompt UI | Cancelled as a product direction; do not implement | Plan 1 and Plan 3 replace it with one full prompt |
| Task 8: generation results UI | Still required, but batch-aware | Plan 3 |
| Task 9: release gate and real acceptance | Still required, with both providers and explicit quota approval | Plan 3 |

## Non-negotiable constraints shared by all three plans

- Do not create one prompt or one provider task per shot.
- The official saved prompt uses absolute source times; provider prompts use segment-relative times.
- A shot crossing an inside-shot execution boundary is clipped into both derived prompts and keeps the same four-column body.
- Do not use GPT to split or rewrite the full prompt during batch submission.
- A generation segment must be at most 29 seconds even though both configured providers support 30 seconds.
- Use one selected provider for the whole batch; never silently fail over.
- Send every confirmed product reference image with every segment task.
- Preserve historical `reference_video_edit` prompt revisions and old generation rows, but do not use them for new batch creation.
- Keep the existing `clip_video()` behavior in this project. Split reference clips contain video only; `generate_audio` asks the provider to generate audio and does not preserve the original soundtrack.
- Never expose local paths or temporary signed URLs in API responses.
- Real provider tests spend quota and require explicit user authorization immediately before they run.

## Completion definition

The roadmap is complete only when all three implementation plans are completed in order, all automated gates pass, a page reload restores the full prompt and every batch task, both providers have passed controlled real submissions, and every successful result can be played and downloaded independently.
