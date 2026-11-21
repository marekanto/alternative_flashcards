# alternative_flashcards
alternative_flashcards

## Automatic infrastructure repair

The Issue workflow now has a bounded two-layer recovery path:

1. The application Orchestrator may rebuild source context up to
   `CONTEXT_REBUILD_MAX` times after `need_context`, and retry a product patch
   up to `QA_MAX_ATTEMPTS` times. Transient DeepSeek HTTP/network failures are
   retried separately with bounded backoff (`DEEPSEEK_API_MAX_ATTEMPTS`).
2. If the smoke test or Orchestrator still fails, a separate Maintenance Agent
   may propose a repair only to `agent/orchestrator.py`,
   `agent/context_builder.py`, `agent/deepseek_client.py`,
   `agent/task_refiner.py`, `tests/smoke_test.py`, or the three workflow YAML
   files. It cannot edit `index.html`, product assets, dependencies, security
   validators, reviewer prompts, its own code, or secrets. It must pass a
   second independent model review, smoke tests, Python compilation, workflow
   YAML parsing (when applicable),
   and `git diff --check`. By default, the repair proposal uses `deepseek-flash`
   while the separate reviewer uses `deepseek-v4-pro` with reasoning enabled;
   `DEEPSEEK_REVIEW_MODEL` can override the reviewer model.
3. A maintenance PR is created and merged only after required GitHub checks
   pass. GitHub branch protection and required human approvals are never
   bypassed. The original Issue task is retried once after a successful
   infrastructure merge.
4. If recovery or any required gate fails, the issue is marked `ai-blocked`,
   removed from the active queue, and the queue does not advance.

The manual AI Development workflow also uses the same no-bypass merge policy.
A separate `ci.yml` workflow runs smoke tests, Python compilation, and validation
of every workflow YAML and shell `run` block on PRs targeting `main`. Add the
check **AltCards required quality gates** to the required status checks for `main`;
otherwise GitHub will not treat this CI job as a merge requirement.
The default DeepSeek model is `deepseek-flash`; both workflows set this model
explicitly so the main agent and the infrastructure-repair agent use the same
current API model.


### GitHub setup for unattended PR checks

For fully unattended maintenance and application PR validation, add a repository
secret named `ALT_CARDS_AUTOMATION_TOKEN`. Use a GitHub App installation token
or a fine-grained personal access token scoped to this repository, with only the
permissions required by these workflows: Contents read/write, Checks read,
Commit statuses read, Pull requests read/write, Issues read/write for the Issue
workflow, and Workflows write so a maintenance PR can update
`.github/workflows/*`. Both PR-producing workflows prefer
this secret and fall back to the built-in `GITHUB_TOKEN` when it is absent.

GitHub may require approval before workflows triggered by PRs created with the
built-in `GITHUB_TOKEN` run. Without the optional automation token, required
checks can therefore remain pending and the merge step will fail closed rather
than bypassing checks. Keep the repository setting that allows GitHub Actions to
create pull requests enabled. Do not grant the automation token branch-protection
bypass or administrative permissions. Human-required reviews and branch
protection remain in force.

The repair loop runs only after the workflow has started and checked out the
repository. Invalid workflow YAML, GitHub-hosted runner startup failures, checkout
failures, missing secrets, and permission failures cannot be repaired from inside
that same run; they require manual or out-of-band intervention.
