# MarketData Pipeline Web

React + Vite app for the MarketData Pipeline. Sign-in uses Amplify Authenticator
against Cognito; every page calls the HTTP API described in
`docs/api-contract-frontend.md`.

## Run locally

Requires Node 18+ and pnpm.

```bash
cd apps/web
pnpm install
cp .env.example .env.local   # fill in Cognito and API values
pnpm dev                     # http://localhost:5173
pnpm test                    # vitest
pnpm build
```

| Variable | Meaning |
| --- | --- |
| `VITE_API_URL` | `HttpApiEndpoint` output of the SAM stack. The stage is `$default`, so no `/v1` suffix. |
| `VITE_COGNITO_USER_POOL_ID`, `VITE_COGNITO_CLIENT_ID` | `UserPoolId` and `UserPoolClientId` outputs. |
| `VITE_ENABLE_MOCK_DATA` | `true` shows labelled sample data when the API cannot be reached. Always on under `pnpm dev`; keep `false` for deployed builds. |
| `VITE_MOCK_ROLE` | Role of the sample profile (`admin`, `analyst`, `viewer`) to test permissions locally. |

For a deployed build, generate `.env.production` from the stack instead of copying values by hand; see "Đưa web lên Amplify Hosting" in [infra/aws/README.md](../../infra/aws/README.md).

Self sign-up is hidden: the user pool only allows admins to create accounts.

## Pages

| Route | Roles | Content |
| --- | --- | --- |
| `/dashboard` | all | KPI cards, 24-hour price matrix by source (min/max highlight, stale label, ≥15% anomaly filter), 30-day price history chart, source status, alerts |
| `/sources` | all (edit: admin, analyst; delete: admin) | Source list, add (URL and optional headers for API/web sources), enable/disable, delete; Pilot limit of 3 |
| `/upload` | admin, analyst | CSV only, max 1 MiB and 1,000 rows (same limits as CSVWorker), preview with papaparse, `POST /jobs` then PUT to the presigned S3 URL, job status polling |
| `/catalog` | admin, analyst | Pending / confirmed / rejected SKUs; approve into a catalog product or reject |
| `/reports`, `/reports/:id` | all | Daily AI reports rendered from markdown; warning when the AI step failed |
| `/members` | admin | Members, invite, change role, disable |

## Source structure

- `src/components/` layout, sidebar, role guard, page heading
- `src/hooks/` `useUser` (`GET /me`) and `useApiData` (loading, errors, sample fallback)
- `src/pages/` one folder per feature
- `src/services/` axios client with the Cognito ID token, and one method per API route
- `src/utils/` VND and time formatting (Asia/Ho_Chi_Minh), matrix pivot
- `src/mocks/` sample data in the same shape as the API responses
