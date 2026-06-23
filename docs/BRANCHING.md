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

Deployments are handled by GitHub Actions (`.github/workflows/deploy-dev-preview.yml` and `deploy-main-live.yml`), which publish static files to the `gh-pages` branch.

### One-time GitHub Pages setting

If the site still deploys directly from `main`, switch it once:

1. Open **Settings → Pages** on the repo.
2. Under **Build and deployment**, set **Source** to **Deploy from a branch**.
3. Choose branch **`gh-pages`**, folder **`/ (root)`**.
4. Save.

After the first workflow run, both `/` (live) and `/dev/` (preview) are served from `gh-pages`.

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
