# Module 9 — Frontend: React + Next.js + TypeScript

⏱ 6–8 h · Services: `make up-db up-stream up-auth` and your API (`make api`) · Check: `make check-9`
**Tools:** React, Next.js, TypeScript (React Native is only mentioned in `role_paths.md`).

## What you will build
`services/web`: Marta's dashboard (she runs maintenance at the plant).
- `/` — the pump **fleet** ordered by risk, rendered **on the server (SSR)**, with an accessible table.
- `/assets/[id]` — the detail of a pump with its risk history in a chart (SVG, accessible).
- `/alerts` — **live** alerts (SSE) from the module 7 gateway.
- **Login with Keycloak** (OIDC with PKCE) and protected routes.
- Component tests (Vitest + Testing Library).

## Why this module exists
An invisible system doesn't exist for the people who make decisions. The frontend is where the platform becomes useful, and where it shows whether you thought about the user:
is the risk level clear without relying on color alone? does it work with a screen reader? what happens if the API fails? is the content visible
before the JavaScript loads? **Next.js** solves the last one with server rendering.

## Concepts
- **Server vs client component** (App Router): server components run on the server (direct access to the API with the token, no extra JS sent to the browser); client components (`"use client"`) are needed for state and browser events (SSE, forms).
- **SSR and `force-dynamic`**: the page is generated on every request with fresh data.
- **Authentication in Next**: Auth.js (NextAuth v5) with the Keycloak provider, a **public client with PKCE** (no secret in the browser). **Middleware** that protects routes. The access token stays in the **server** session.
- **Accessibility (a11y)**: `<caption>`, `<th scope>`, `lang="en"`, `role="img"` with `aria-label`, don't rely on color alone (text “High/Medium/Low” + `data-level`).
- **TypeScript `strict`**: `next build` also type-checks.
- **Component tests**: you render the component in a simulated DOM and check what the user sees.

## The contract
A Next.js 15 project (App Router) with TypeScript `strict`, a `package.json` with the `build`, `start` and `test` scripts and its `package-lock.json`. Server environment:
`API_URL`, `AUTH_SECRET`, `AUTH_URL`, `KEYCLOAK_ISSUER`, `AUTH_DISABLED` (`1` = no login, for development and tests only), `NEXT_PUBLIC_GATEWAY_URL`. Set `output: "standalone"` (you will use it for the module 12 image).

| Route | Must satisfy |
|---|---|
| `GET /api/health` | `{"status":"ok"}`, **public** |
| `/` | HTML with `<caption>`, `<th scope="col">`, `<html lang="en">`; assets **ordered by risk** (the riskiest first); each risk with an element `data-level="high|medium|low"` **and** text (High/Medium/Low + percentage: 0.90 → “90%”) |
| `/assets/{id}` | detail + chart with `role="img"`/`aria-label`; if the asset doesn't exist → **404 page** (not 500) |
| `/alerts` | a client that connects to `{gateway}/events?access_token=…` and shows new alerts at the top |
| no session | `GET /` → redirect (302/307) to the sign-in route (`/api/auth/signin…`); `/api/auth/providers` lists `keycloak` |
| security | secrets only on the server; no `AUTH_SECRET` nor the API secret in the HTML |

**Tests**: `npm test` passes with **≥ 4 tests** (pure logic —risk thresholds— and components: high level, low level, missing data…).

## Your task
1. `npm create next-app@latest` (TypeScript, App Router) in `services/web`. Remove what you don't use.
2. Write `levelFor(score)` (high ≥ 0.8, medium ≥ 0.5; these are the thresholds from your manuals) **with its test** before the component.
3. A `RiskBadge` component and the fleet page (a server component that calls your API). Look at the browser's view-source: is the content in the HTML?
4. Detail and your own SVG chart (no library: understand how a curve is computed).
5. Login: `next-auth@beta` with `Keycloak({ clientId: "plant-web", client: { token_endpoint_auth_method: "none" }, checks: ["pkce","state"] })`, the token stored in the session and a `middleware.ts` that redirects.
   Register the `[...nextauth]` route and the health endpoint.
6. Live alerts: a client component with `EventSource` and cleanup in the `useEffect`.
7. Try it for real: `make api` with `AUTH_DISABLED=1`, `npm run dev`, and go through the pages with the keyboard and a screen reader (VoiceOver on macOS: ⌘+F5; NVDA on Windows).

## How you will know you are done
`make check-9` runs `npm ci && npm run build` (types included), runs `npm test`, starts YOUR API and YOUR built Next app, and reviews the **HTML the browser receives**: accessible table, order by risk, levels with text, the detail 404, the redirect to login without a session, the `keycloak` provider and that no secrets leak.

## Comprehension questions
1. Why is the fleet a server component and the live alerts a client one?
2. What is the advantage of `data-level` + text over coloring only red/green? Who benefits?
3. The access token is in the server session. Why does `/alerts` need to pass it to the client, and what risk does that carry? (link with the module 8 threat model)
4. What happens if the API takes 10 s? How would you show a useful loading or error state (hint: `loading.tsx`, `error.tsx`)?
5. What would you test with Playwright (end-to-end) that component tests can't?

## Hints
<details><summary>Server component that calls the API with the token</summary>

`const session = await auth(); fetch(`${API_URL}/assets`, { headers: { Authorization: `Bearer ${session.accessToken}` }, cache: "no-store" })`.
</details>
<details><summary>Redirecting without a session</summary>

`export default auth((req) => { if (!req.auth) return NextResponse.redirect(new URL("/api/auth/signin", req.nextUrl.origin)); })` and a `matcher` that excludes `/api/auth`, `/api/health` and the static assets.
</details>
<details><summary>Tests with Vitest</summary>

`vitest.config.ts` with `environment: "jsdom"` and the React plugin; `render(<RiskBadge score={0.9}/>)` and `screen.getByText(/High/)`.
</details>
<details><summary>The HTML has weird comments</summary>

React inserts `<!-- -->` between adjacent texts in SSR. That is normal; don't mistake it for an error.
</details>

## Common traps
- Calling the API with `fetch` from the browser and exposing the token.
- Showing the risk with color only.
- A `useEffect` with `EventSource` without `return () => es.close()` (connection leak).
- `any` types to “make it pass”.
- Forgetting that `next build` type-checks and fails if anything doesn't compile.

## On a real job
Server state management (TanStack Query), a design system (design tokens), e2e tests (Playwright), *feature flags*, browser observability (module 10) and container deployment (module 12).
**Courses:** *Full Stack Open* (React, TypeScript, Next.js).

→ Next: [`10_observability.md`](10_observability.md)
