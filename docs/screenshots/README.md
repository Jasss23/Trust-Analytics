# Screenshot Placeholders

This directory holds optional image attachments for the project README.

Drop PNGs at the paths below (or update the README references). All paths
are referenced from the top-level `README.md` using relative links such as
`docs/screenshots/<file>.png`. Missing files render as broken-image icons
on GitHub but do not break the rest of the README.

Suggested captures (none are required for the submission to be readable;
the README is fully self-contained in text):

| Filename | What to capture |
|---|---|
| `tldr_cli_result.png` | The terminal output of `pluang-agent run --review-mode demo-reject` — the Pipeline Result table with Review / Terminal / Trust columns. Shows the headline result in one frame. |
| `review_panel_q1.png` | The interactive Rich panel for Q1 from `pluang-agent run` (the one already exported as text in `outputs/sample/review_panel_rendered.txt`). A colored screenshot makes the FLAGS / HYPOTHESIS split land harder than the text version. |
| `architecture_diagram.png` | A boxed-and-arrowed version of the ASCII architecture diagram in the README (planner → SQL Agent → pre-flight → Quality Agent → Human Review). Optional — the ASCII diagram is already in the README. |
| `cost_log_excerpt.png` | One screenshot of `logs/cost.jsonl` viewed in a terminal or browser, showing per-call attribution and the `cost_usd` field. |
