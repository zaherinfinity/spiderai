# Cloudflare CDN + Rate Limiting + Turnstile

## 1. Point DNS to Cloudflare
- Add your domain in Cloudflare
- Set DNS A/AAAA (or CNAME) to your origin server
- Proxy status: **Proxied** (orange cloud)

## 2. SSL
- SSL/TLS mode: **Full (strict)** recommended
- Enable Always Use HTTPS

## 3. Rate limiting (Cloudflare dashboard)
Security → WAF → Rate limiting rules (or Security Rules):

Example rules:
| Rule | Expression | Action |
|------|------------|--------|
| Auth abuse | `(http.request.uri.path in {"/login" "/register" "/admin/login"})` | Block if > 20 / 1 min per IP |
| Upload abuse | `http.request.uri.path eq "/upload"` | Block if > 15 / 1 min per IP |
| Global API | `starts_with(http.request.uri.path, "/api/")` | Block if > 100 / 1 min per IP |

Also enable:
- Bot Fight Mode / Super Bot Fight Mode
- DDoS protection (default)
- Browser Integrity Check

## 4. Turnstile (verify screen)
1. Cloudflare Dashboard → Turnstile → Add site
2. Copy **Site Key** and **Secret Key**
3. Set in `.env`:
```
TURNSTILE_SITE_KEY=your_site_key
TURNSTILE_SECRET_KEY=your_secret_key
```
4. Restart the app

When keys are set:
- Login / Register / Admin login show Turnstile
- Upload requires `/verify` once per session

## 5. App-level rate limits (already built-in)
Configured in `.env`:
```
RATE_LIMIT_WINDOW_SEC=60
RATE_LIMIT_MAX_REQUESTS=120
RATE_LIMIT_MAX_AUTH=10
RATE_LIMIT_MAX_UPLOAD=8
```
Uses `CF-Connecting-IP` when behind Cloudflare.

## 6. Origin IP allowlist (optional)
Firewall → Tools → allow only Cloudflare IP ranges to origin ports 80/443.
