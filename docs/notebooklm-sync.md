# NotebookLM Sync

Flattens your codebase into word-limited text sources using [repomix](https://github.com/yamadashy/repomix) and uploads them to a NotebookLM notebook on every push to main. Useful for keeping a "project brain" notebook up to date without manual effort.

## Install

```bash
./scripts/manage-ai-configs.sh notebooklm install
```

This installs `.github/workflows/sync-notebooklm.yml` and `.github/repomix.config.json`.

## Configure sources

Create `.github/notebooklm-sources.yaml` (template at [`examples/notebooklm-sources.yaml`](../examples/notebooklm-sources.yaml)). Each entry becomes a separate source in the notebook. NotebookLM allows up to 300 sources at 500,000 words each.

```yaml
sources:
  - name: my-app-core
    include: ["src/**"]
    ignore: ["src/tests/**"]

  - name: my-app-tests
    include: ["tests/**"]

  - name: my-app-docs
    include: ["docs/**", "README.md"]
```

### Source splitting patterns

**Split a large src/ by subfolder** when a single `src/**` source would exceed 500k words:

```yaml
sources:
  - name: app-services
    include: ["src/services/**"]

  - name: app-models
    include: ["src/models/**"]

  - name: app-api
    include: ["src/api/**"]

  # Catch-all for anything not in the above folders
  - name: app-core
    include: ["src/**"]
    ignore: ["src/services/**", "src/models/**", "src/api/**"]
```

**Monorepo with multiple packages:**

```yaml
sources:
  - name: pkg-auth
    include: ["packages/auth/**"]

  - name: pkg-billing
    include: ["packages/billing/**"]

  - name: pkg-shared
    include: ["packages/shared/**"]

  - name: repo-root
    include: ["*.json", "*.yaml", "*.md", ".github/**"]
```

## Add secrets

Settings → Secrets and variables → Actions:

| Secret | How to get it |
|--------|--------------|
| `NLM_COOKIES_JSON` | `pip install notebooklm-mcp-cli && nlm login`, then `cat ~/.notebooklm-mcp-cli/profiles/default/cookies.json` |
| `NLM_NOTEBOOK_ID` | `nlm notebook list --quiet` or `nlm notebook create "My Project Brain"` |

Push both secrets with `gh secret set`:

```bash
gh secret set NLM_COOKIES_JSON --repo <owner>/<repo> < ~/.notebooklm-mcp-cli/profiles/default/cookies.json
echo -n 'YOUR-NOTEBOOK-UUID' | gh secret set NLM_NOTEBOOK_ID --repo <owner>/<repo>
```

Cookies expire every few weeks. When they do, the workflow fails with step-by-step refresh instructions in the job log.
