# Branching workflow

- **`dev`** — development branch. All feature work and fixes land here first.
- **`main`** — live/production branch. Reflects what is deployed and what automated data-sync jobs update.

## Flow

1. Check out `dev` and make changes.
2. Commit and push to `dev`.
3. Verify on the **dev preview URL** (see below).
4. Merge `dev` → `main` only after verification.
5. Push `main` — the live site redeploys automatically.

Do not push automated data updates to `dev`. Scheduled GitHub Actions that commit `data.json` and related files run on `main` only.

## Preview URLs

| Environment | Branch | URL |
|-------------|--------|-----|
| **Dev preview** | `dev` | https://wildturkey1814.github.io/World-Cup-Betting/dev/ |
| **Live** | `main` | https://wildturkey1814.github.io/World-Cup-Betting/ |

Deployments are handled by GitHub Actions (`.github/workflows/deploy-dev-preview.yml` and `deploy-main-live.yml`).

- **Dev preview** — on every push to `dev`, the workflow copies static files into **`main` → `dev/`**, which GitHub Pages serves at `/dev/`. No gh-pages branch switch is required.
- **Live** — served from the **`main`** branch root (or from `gh-pages` root if you switch Pages source later).

### One-time GitHub Pages setting (if the site does not load at all)

1. Open **Settings → Pages** on the repo.
2. Under **Build and deployment**, set **Source** to **Deploy from a branch**.
3. Choose branch **`main`**, folder **`/ (root)`**.
4. Save.

The dev preview URL is **`/dev/`** under that same site — e.g. `https://wildturkey1814.github.io/World-Cup-Betting/dev/`.

## Git commands

```powershell
# Daily development
git checkout dev
# ... edit, commit ...
git push origin dev
# → dev preview redeploys

# Promote to live after verification
git checkout main
git merge dev
git push origin main
# → live site redeploys
```
