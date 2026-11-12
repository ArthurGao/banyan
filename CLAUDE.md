@AGENTS.md

# Claude Code notes

- At the start of a session: check §12 of `docs/banyan-design.md` for the current stage and its acceptance
  list, and read the project KB `/Users/arthurgao/Docs/Knowledgebase/projects/banyan/README.md` (owner
  decisions) and its `reference/lessons.md` (steward contract surprises) before touching what they cover.
- Working inside `backend/` or `frontend/` loads that folder's `CLAUDE.md` as well — follow both.
- The design doc is long: read it by section (`grep -n '^#' docs/banyan-design.md` for the map), not whole.
- Use plan mode for anything that changes the ask pipeline (design §6), the data model (§4), the LLM role
  layer (§7), the steward integration (§8) or touches both backend and frontend.
- Relevant user-level skills: `rag-design` (retrieval, sufficiency), `llm-app-architecture` (roles, model
  choice, no fallback chain, provenance), `backend-reliability` (job table, worker, retries),
  `testing-and-ci`, `web-authz` (the API token), `kb-maintain` (writing the KB).
- Good uses of subagents: (1) check a change against the non-negotiable rules in `AGENTS.md`;
  (2) check a steward call against steward's OpenAPI spec and design §5–§7; (3) review the SSE contract
  between backend and frontend after either side changes it.
- Never answer an llm-steward, lite-rag, Pydantic AI or search-API question from memory: read steward's
  OpenAPI spec / `docs/llm-steward-design.md` (`~/Code/AI/llm-steward`), lite-rag's design
  (`~/Code/AI/lite-rag`), or the pinned version's docs via context7. Record surprises in the KB.
- **Stage close-out** (`AGENTS.md` Workflow step 5). For **every** stage, once its code is done and every
  check passes, do these in order. Do not start the next stage, or plan it, before step 6.
  1. **Simplify.** Run `/simplify` on the stage's full diff and apply its fixes. Re-run every check
     (backend and frontend) so they cover the simplified code.
  2. **Codex review.** From the repo root, read-only, on the stage's whole diff, in the background (it
     takes minutes):
     ```
     codex exec -s read-only -o "$TMPDIR/banyan-<stage>-codex.md" "Review the changes of stage <stage>:
     \`git diff <commit before the stage>\` plus untracked files from \`git status --porcelain\`. Check
     them against docs/banyan-design.md (the stage's section in §12 and the sections it names) and the
     non-negotiable rules in AGENTS.md. Focus: correctness, the path table §6.4, transactions and jobs,
     the steward contract, SSE semantics, tests that verify nothing. For each finding report severity,
     file:line, the concrete failing scenario and a fix." < /dev/null
     ```
     `< /dev/null` stops `codex exec` waiting on stdin. With no commit before the stage (the first
     commit has not been made yet), name the stage's files instead of a `git diff` base.
  3. **Triage every finding.** Read the code it points at and decide: confirmed, or rejected with a reason
     (wrong reading of the code, contradicts the design, out of scope). Do not apply a finding just
     because Codex wrote it, and do not drop one without a reason.
  4. **Fix the confirmed findings.** A bug fix starts with a failing regression test (red, then green).
     A finding that would contradict the design means updating the design in the same change, and asking
     first if it changes product behaviour. If the fixes are large or touch §4, §6 or §8, run Codex
     again on the fix diff only.
  5. **Re-run every check** and the stage's acceptance list in §12.
  6. **Record and report.** Tick the stage's checklist in §12 and add a short close-out note under it
     (what simplify and Codex changed, findings rejected and why). Record owner decisions and lessons in
     the project KB (skill `kb-maintain`). Report with the check output, then propose the commit (paths
     and message) and wait: never commit or push without the owner's go-ahead for that commit.
