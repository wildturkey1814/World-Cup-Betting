# Branching workflow

- **`dev`** — development branch. All feature work and fixes land here first.
- **`main`** — live/production branch. Reflects what is deployed and what automated data-sync jobs update.

## Flow

1. Check out `dev` and make changes.
2. Test and verify on `dev`.
3. Merge `dev` → `main` only after verification.

Do not push automated data updates to `dev`. Scheduled GitHub Actions that commit `data.json` and related files run on `main` only.
