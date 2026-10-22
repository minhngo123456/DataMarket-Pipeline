# MarketData Pipeline Web

React + Vite browser app. Steps 5 and 6 provide the frontend scaffold and
Cognito sign-in. Step 7 adds the API client and current application user.

## Local configuration

Copy `.env.example` to `.env.local` and set the Cognito User Pool ID and App
client ID. `.env.local` is ignored by Git.

`VITE_API_URL` defaults to `http://localhost:3001/v1`. While the backend is not
available, `VITE_ENABLE_MOCK_USER=true` lets the local preview display a clearly
marked sample profile. Set it to `false` when the real `/me` endpoint is ready.

## Step 7 API contract

`src/services/api.js` attaches the signed-in user's Cognito ID token to requests
as `Authorization: Bearer <token>`. `src/services/marketDataApi.js` wraps the 18
routes in Hiếu's `docs/api-contract-frontend.md` contract. `src/hooks/useUser.js`
calls `GET /me` and expects the response body to contain:

```json
{
  "user_id": "u-123",
  "tenant_id": "t-123",
  "role": "analyst",
  "tenant": { "name": "Example shop", "product_count": 42, "max_skus": 100 },
  "claims": { "email": "analyst@example.com" }
}
```

The backend must validate the Cognito token and return the application user's
tenant and role. No backend API is included in this workspace yet, so the
current profile shown in local preview is sample data.

The API implementation and its AWS SAM infrastructure are on the remote branch
`feature/hieu-auth-api-ai-report`; they are not deployed by this frontend.

## Step 8 routes and permissions

The signed-in workspace now has a shared sidebar and routes for `/dashboard`,
`/sources`, `/upload`, `/catalog`, `/reports`, `/reports/:id`, and `/members`.
All roles can open the dashboard, sources, and reports. `admin` and `analyst`
can also open upload and catalog; only `admin` can open members. Direct URL
navigation uses the same role guard as the sidebar, and unknown routes return to
`/dashboard`.

The page layouts follow the supplied Figma overview. Values on the dashboard and
feature tables are presentation samples; actions that need backend writes are
labelled as pending API integration. Step 9 can move these samples into shared
fixtures and connect page data to the API contract.

## Source structure

- `src/components/` shared UI components
- `src/hooks/` React hooks such as `useUser`
- `src/pages/` feature pages
- `src/services/` API clients
- `src/utils/` formatting and date helpers
- `src/mocks/` development fixtures
