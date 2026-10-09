# Co-driving BopBop: tickets 1–4

Work proceeds in this order. Complete and commit one ticket, provide a short
test ride, and wait for the user to try it before starting the next ticket.
Feedback belongs in the current ticket before advancing. These checkpoints are
requested by the user; a completed implementation does not imply acceptance.

2026-10-09: ticket 1 was accepted. The user authorized automatic progression
from ticket 2's anchored-composer slice into its inspector slice. The next
test-ride checkpoint is after ticket 2, before editor integration.

| Ticket | Deliverable | Status |
|---|---|---|
| [1. Live call help](001-live-call-help.md) | Tool-owned docs beside the command being composed | Accepted |
| [2. Inspector pane](002-inspector-pane.md) | Results, request context and file/diff inspection | In progress; anchored composer then inspector |
| [3. Neovim connection](003-neovim-connection.md) | Selected code → prompt → reviewable edit in the same session | Queued; after ticket 2 test ride |
| [4. Completion model research](004-completion-model-research.md) | Evaluate models and training objectives for fast code suggestions | Queued; after ticket 3 test ride |

Ticket 4 is research and a contained evaluation, followed by a recommendation.
Automatic code suggestions are a later implementation decision. Existing
schema-driven tool-name menus do not use an LLM and are independent of that work.

The standalone agent remains the execution engine. UI references consume tool
definitions; tools own their schemas, examples and documentation. Exact model
requests, tool evidence and editor snapshots must have clear provenance.

Research starting points, reviewed 2026-10-09:

- [ptpython signatures/docstrings](https://github.com/prompt-toolkit/ptpython/blob/main/examples/ptpython_config/config.py)
- [prompt_toolkit layouts](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/full_screen_apps.html)
- [Textual workers](https://textual.textualize.io/guide/workers/)
- [Aider editor workflow](https://aider.chat/docs/usage/watch.html)
- [Neovim API](https://neovim.io/doc/user/api/)
- [CodeCompanion local models](https://codecompanion.olimorris.dev/guides/running-local-models)
