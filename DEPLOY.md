# Deploy (free HTTPS) + Android APK

## 1. Deploy on Render (free)

1. Push this repo to **GitHub** (public or private).
2. Go to [https://render.com](https://render.com) → Sign up (GitHub).
3. **New** → **Blueprint** → select this repo (uses `render.yaml`),  
   or **Web Service** → connect repo → settings:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 180 "app:create_app()"`
   - Health check: `/healthz`
4. Wait until the service is live. URL example:  
   `https://cultura-zaragoza.onrender.com/m`

**Notes (free tier):** the service sleeps after inactivity; the first request can take ~30–60s while scrapers fill the cache.

Optional: deploy with Docker (`Dockerfile`) on Render, Fly.io or Railway the same way.

## 2. Generate the APK with PWABuilder

1. Open [https://www.pwabuilder.com](https://www.pwabuilder.com).
2. Paste your HTTPS URL (the site root, e.g. `https://cultura-zaragoza.onrender.com`).
3. Open **Package for stores** → **Android**.
4. Options recommended:
   - Package ID: `com.cultura.zaragoza`
   - App name: `Cultura Zaragoza`
   - Host: your Render URL
   - Start URL: `/m`
5. Download the zip (contains `.apk`, `.aab`, and `assetlinks.json`).

### Install APK on the phone

- Enable **Install unknown apps** for your file manager/browser.
- Open the `.apk` from the zip.

### Address bar in the TWA

Copy `assetlinks.json` from the PWABuilder zip into:

`static/.well-known/assetlinks.json`

Redeploy so it is served at:

`https://YOUR-HOST/.well-known/assetlinks.json`

## 3. Script helper

After the site is online:

```bash
./scripts/generate_apk.sh https://YOUR-HOST.onrender.com
```

This calls the PWABuilder Android packaging API and saves the zip under `android-apk/`.
