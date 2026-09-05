# Setup Guide

Two ways to run Arkon: **Docker** (recommended for production) or **Development** (for local development and contributing).

---

## Option A — Docker (Production)

### Prerequisites

**Software**
- Docker Engine 24+
- Docker Compose v2+
- An API key for your AI provider (Google, OpenAI, or Anthropic)

**Server — recommended specs**

| | **Starter** | **Team** | **Enterprise** |
|---|:---:|:---:|:---:|
| **Team size** | 1–20 | 20–100 | 100+ |
| **vCPU** | 2 cores | 4 cores | 8+ cores |
| **RAM** | 4 GB | 8 GB | 16+ GB |
| **Storage** | 40 GB SSD | 100 GB SSD | 250+ GB NVMe |
| **OS** | Ubuntu 22.04+ | Ubuntu 22.04+ | Ubuntu 22.04+ |

> RAM is the primary bottleneck — the MRP pipeline workers load large LLM context windows during wiki compilation. All AI inference is external (Anthropic / Google / OpenAI), so no GPU is required.

**Domain & HTTPS — required for MCP**

Claude Desktop and Claude.ai connect to Arkon's MCP server using OAuth 2.1, which requires HTTPS. You need:

- A **public domain** pointing to your server (e.g. `arkon.yourcompany.com`)
- A **valid TLS certificate** — Certbot/Let's Encrypt works fine
- A **reverse proxy** (Nginx recommended) forwarding traffic to the containers
- The API specifically needs `X-Forwarded-Proto` passed through so OAuth URLs are generated with `https://`

> For local development without a domain, use a manual Bearer token instead of OAuth. See [MCP & Claude](MCP.md).

### 1. Clone and configure

```bash
git clone https://github.com/nduckmink/arkon.git
cd arkon
cp .env.docker.example .env.docker
```

> Arkon ships **two** env templates: `.env.docker.example` for `docker compose`, and `.env.local.example` for local development (Option B). They differ only in service hostnames (container names vs. `localhost`).

Edit `.env.docker`:

```env
# Required: generate a strong random secret
SECRET_KEY=<run: python -c "import secrets; print(secrets.token_urlsafe(32))">

# Required: admin account created on first startup
DEFAULT_ADMIN_EMAIL=admin@yourcompany.com
DEFAULT_ADMIN_PASSWORD=your-secure-password

# Required: PostgreSQL credentials — must be consistent across all three vars and DATABASE_URL
POSTGRES_USER=arkon
POSTGRES_PASSWORD=your-postgres-password
POSTGRES_DB=arkon
DATABASE_URL=postgresql+asyncpg://arkon:your-postgres-password@postgres:5432/arkon

# Required: MinIO credentials — MINIO_ACCESS_KEY / MINIO_SECRET_KEY initialise the MinIO
# container on first run; changing them after first start requires resetting the volume
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=your-minio-secret

# Optional: restrict CORS in production
CORS_ORIGINS=https://your-domain.com

# Required: your public API URL (used by the frontend browser)
NEXT_PUBLIC_API_URL=https://your-domain.com
```

> The full `.env.docker.example` documents every available variable.

### 2. Start

```bash
docker compose --env-file .env.docker up -d --build
```

This starts all containers:
| Container | Purpose |
|---|---|
| `arkon_postgres` | PostgreSQL 16 with pgvector (port 5432) |
| `arkon_redis` | Redis 7 job queue (port 6379) |
| `arkon_minio` | MinIO file storage (port 9000, console 9001) |
| `arkon_api` | FastAPI backend + MCP server (port 5055) |
| `arkon_worker` | Background worker — document ingestion + wiki compilation |
| `arkon_worker_skills` | Background worker — AI skill processing |
| `arkon_frontend` | Next.js admin portal (port 3119) |

Workers start only after `arkon_api` passes its health check, so there is no race condition on startup.

> **Important:** always pass `--env-file .env.docker` explicitly — both for `build` and `up`. Without it, Docker Compose falls back to `.env` (your local dev config). This causes two classes of errors:
> - MinIO `SignatureDoesNotMatch` — credentials mismatch
> - Frontend still calls `localhost:5055` — `NEXT_PUBLIC_API_URL` is a **build-time** variable baked into the JS bundle by Next.js. Changing `.env.docker` and restarting the container has no effect; you must rebuild the image with the correct `--env-file`.

### 3. First login

Open **http://your-server:3119** and log in with the credentials from `.env.docker`.

### 4. Configure AI providers

Go to **Settings** and configure:

| Setting | Required | Notes |
|---|---|---|
| **Embedding model** | Yes | Used for semantic wiki search. E.g. `text-embedding-004` (Google) |
| **LLM** | Yes | Used for wiki compilation. Choose a large-context model. |
| **Vision model** | No | Enables image captioning during PDF ingestion |

Recommended LLMs for wiki compilation (large context window):
- `gemini-2.5-pro` (Google) — best results
- `gpt-4o` (OpenAI)
- `claude-sonnet-4-5` or newer (Anthropic)

### 5. Run database migrations

```bash
docker exec arkon_api alembic upgrade head
```

> On first startup, the API runs migrations automatically before serving requests. You only need to run this manually after upgrading Arkon.

---

## Option A2 — Deploying to a Linux server

This section covers what changes when running on a remote server (Ubuntu 22.04+ recommended) instead of a local machine.

### 1. Install Docker Engine

On the server (do **not** install Docker Desktop — use Docker Engine directly):

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

Verify:

```bash
docker version
docker compose version   # must be v2+
```

### 2. Open firewall ports

Arkon needs these ports accessible from users' browsers:

```bash
sudo ufw allow 3119/tcp   # Frontend
sudo ufw allow 5055/tcp   # API (and MCP endpoint)
sudo ufw allow 9000/tcp   # MinIO (presigned file URLs)
sudo ufw enable
```

> Do **not** expose port 5432 (PostgreSQL), 6379 (Redis), or 9001 (MinIO console) externally.

### 3. Clone and configure

```bash
git clone https://github.com/nduckmink/arkon.git
cd arkon
cp .env.docker.example .env.docker
```

Edit `.env.docker` — the key differences from local Docker setup:

```env
# Generate strong secrets
SECRET_KEY=<python3 -c "import secrets; print(secrets.token_urlsafe(32))">
POSTGRES_PASSWORD=<strong-random-password>
POSTGRES_DB=arkon
DATABASE_URL=postgresql+asyncpg://arkon:<strong-random-password>@postgres:5432/arkon
MINIO_SECRET_KEY=<strong-random-password>

# Admin account
DEFAULT_ADMIN_EMAIL=admin@yourcompany.com
DEFAULT_ADMIN_PASSWORD=<strong-password>

# Use the server's public IP or domain — NOT localhost
MINIO_PUBLIC_ENDPOINT=<server-ip-or-domain>:9000
NEXT_PUBLIC_API_URL=http://<server-ip-or-domain>:5055

# Restrict CORS to your frontend URL
CORS_ORIGINS=http://<server-ip-or-domain>:3119
```

> **`MINIO_PUBLIC_ENDPOINT` is the most important difference.** On a local machine it's `localhost:9000`. On a server it must be the server's public IP or domain, otherwise presigned image/file URLs will point to an unreachable address.

### 4. Start

```bash
docker compose --env-file .env.docker up -d --build
```

Check all containers are healthy:

```bash
docker compose ps
```

All services should show `healthy` or `running`. The `worker` and `worker_skills` containers start only after `arkon_api` passes its health check (~30 seconds).

### 5. Verify

```bash
# API health
curl http://localhost:5055/health

# Should return: {"status": "ok", ...}
```

Open `http://<server-ip>:3119` from your browser and log in.

---

### Optional: Nginx reverse proxy (custom domain + SSL)

Required for MCP with Claude Desktop / Claude.ai — OAuth 2.1 needs HTTPS.

**Recommended DNS setup**

| Record | Points to | Purpose |
|---|---|---|
| `arkon.yourcompany.com` | server IP | Frontend + API + MCP |
| `minio.yourcompany.com` | server IP | MinIO presigned file URLs |

You can serve frontend and API from the same domain using path routing (shown below), or use separate subdomains — either works.

**Nginx config** (`/etc/nginx/sites-available/arkon`)

```nginx
# Redirect HTTP → HTTPS
server {
    listen 80;
    server_name arkon.yourcompany.com minio.yourcompany.com;
    return 301 https://$host$request_uri;
}

# API + MCP server
server {
    listen 443 ssl http2;
    server_name arkon.yourcompany.com;

    ssl_certificate     /etc/letsencrypt/live/arkon.yourcompany.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/arkon.yourcompany.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    add_header X-Frame-Options        "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;

    client_max_body_size 100M;

    # MCP endpoint (Streamable HTTP — no SSE buffering needed)
    location /mcp {
        proxy_pass http://127.0.0.1:5055;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;   # required for OAuth https:// URLs
        proxy_set_header Connection        "";
        proxy_read_timeout 300s;
    }

    # OAuth endpoints + REST API
    location / {
        proxy_pass http://127.0.0.1:5055;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;   # required for OAuth https:// URLs
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        "upgrade";
        proxy_read_timeout 300s;
    }
}

# MinIO (for presigned file URLs)
server {
    listen 443 ssl http2;
    server_name minio.yourcompany.com;

    ssl_certificate     /etc/letsencrypt/live/minio.yourcompany.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/minio.yourcompany.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    client_max_body_size 500M;

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

> **`X-Forwarded-Proto $scheme` is critical.** Without it, Arkon's OAuth metadata endpoint returns `http://` URLs, and Claude Desktop will fail to complete the OAuth flow.

Enable the site and get certificates:

```bash
sudo ln -s /etc/nginx/sites-available/arkon /etc/nginx/sites-enabled/
sudo nginx -t

sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d arkon.yourcompany.com -d minio.yourcompany.com

sudo systemctl reload nginx
```

Update `.env.docker` to use your domain:

```env
NEXT_PUBLIC_API_URL=https://arkon.yourcompany.com
MINIO_PUBLIC_ENDPOINT=minio.yourcompany.com
MINIO_SECURE=true
CORS_ORIGINS=https://arkon.yourcompany.com
```

Rebuild and restart:

```bash
docker compose --env-file .env.docker up -d --build
```

---

## Option B — Development

### Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.11 – 3.13 | Backend runtime |
| Node.js | 20+ | Frontend (Next.js) |
| PostgreSQL | 15+ with pgvector | Main database |
| Redis | 7+ | Background job queue |
| MinIO | Latest | File storage |

### 1. Infrastructure

Start infrastructure services with Docker:

```bash
# PostgreSQL with pgvector
docker run -d --name arkon-pg \
  -e POSTGRES_USER=arkon \
  -e POSTGRES_PASSWORD=arkon_secret \
  -e POSTGRES_DB=arkon \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# Redis
docker run -d --name arkon-redis -p 6379:6379 redis:7-alpine

# MinIO
docker run -d --name arkon-minio \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin123 \
  -p 9000:9000 -p 9001:9001 \
  minio/minio server /data --console-address ":9001"
```

### 2. Configure environment

```bash
cp .env.local.example .env
```

For local development, the defaults in `.env.local.example` work out of the box except:

```env
SECRET_KEY=dev-only-not-for-production
DEFAULT_ADMIN_EMAIL=admin@arkon.local
DEFAULT_ADMIN_PASSWORD=admin123
MINIO_SECRET_KEY=minioadmin123
```

### 3. Python backend

```bash
# Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Run database migrations
alembic upgrade head
```

### 4. Frontend

```bash
cd frontend
npm install
```

Create `frontend/.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:5055
```

### 5. Start all services (3 terminals)

**Terminal 1 — API server:**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 5055 --reload
```

On first startup you should see:
```
SUCCESS  MinIO bucket ready
SUCCESS  Default admin created: admin@arkon.local
SUCCESS  Arkon MCP Server ready at /mcp
SUCCESS  Arkon API started successfully
```

**Terminal 2 — Wiki worker:**
```bash
python -m arq app.worker.WorkerSettings
```

**Terminal 3 — Frontend:**
```bash
cd frontend
npm run dev
```

Open **http://localhost:3000**.

> Documents will stay at `pending` status until the worker is running.

---

## First steps after setup

### 1. Configure AI providers
Settings → configure embedding model, LLM, and vision model.

### 2. Create a department
Admin Portal → Departments → New Department.

### 3. Create a knowledge type
Admin Portal → Knowledge Types → New Type (e.g. "SOP", "Product Docs").

### 4. Upload a document
Knowledge Base → Upload → select file or paste URL → choose knowledge type → submit.

Watch the progress indicator. Once complete, click Wiki to browse the compiled pages.

### 5. Create employees
Admin Portal → Employees → New Employee → assign department and role.

Employees connect to Arkon via OAuth — no manual token generation needed. They sign in through the browser when they first connect Claude Desktop or Claude.ai.

> If you need a Bearer token for API testing or local dev (no HTTPS), use **Employee → Generate Token** on the employee detail page.

### 6. Connect Claude Desktop or Claude.ai

In **Claude Desktop** or **Claude.ai → Settings → Connectors**, add a custom connector:

- **Name:** `Arkon`
- **URL:** `https://arkon.yourcompany.com/mcp`

Click **Connect** → browser opens Arkon login form → sign in → done.

See [MCP & Claude](MCP.md) for the full connection guide, tool reference, and tips on getting Claude to consistently use Arkon.

---

## Environment variables reference

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_USER` | `arkon` | PostgreSQL username — used to initialise the postgres container |
| `POSTGRES_PASSWORD` | — | PostgreSQL password — must match the value in `DATABASE_URL` |
| `POSTGRES_DB` | `arkon` | PostgreSQL database name — must match the value in `DATABASE_URL` |
| `DATABASE_URL` | — | Full asyncpg connection string — must be consistent with `POSTGRES_*` vars |
| `SECRET_KEY` | — | JWT signing secret. Must be changed in production. |
| `DEFAULT_ADMIN_EMAIL` | `admin@arkon.local` | Admin account email (created on first startup) |
| `DEFAULT_ADMIN_PASSWORD` | `admin123` | Admin account password |
| `MINIO_ENDPOINT` | `minio:9000` | MinIO server address used internally by the API (Docker service name; local: `localhost:9000`) |
| `MINIO_PUBLIC_ENDPOINT` | _(same as `MINIO_ENDPOINT`)_ | Public MinIO address embedded in presigned URLs. Must be browser-accessible: `localhost:9000` on local Docker, `<server-ip>:9000` on a remote server |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO root user — initialises the container on first run |
| `MINIO_SECRET_KEY` | — | MinIO root password — initialises the container on first run; changing it after first start requires `docker compose down -v` |
| `MINIO_BUCKET` | `arkon-files` | Bucket name for uploaded files |
| `MINIO_SECURE` | `false` | Use HTTPS for MinIO (`true` in production) |
| `REDIS_HOST` | `redis` | Redis host (Docker: service name; local: `localhost`) |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | _(empty)_ | Redis password |
| `WORKER_MAX_JOBS` | `3` | Max concurrent background jobs |
| `CORS_ORIGINS` | `*` | Allowed CORS origins (comma-separated) |
| `NEXT_PUBLIC_API_URL` | `http://localhost:5055` | Public API URL (used by the browser) |
| `INTERNAL_API_URL` | `http://api:5055` | Internal API URL used by the Next.js server for proxying (Docker only) |

AI provider settings (embedding, LLM, vision, API keys) are configured through the Admin Portal → Settings, not in env files.

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `connection refused` on port 5432 | PostgreSQL not running |
| `pgvector extension not found` | Use `pgvector/pgvector` Docker image |
| Documents stuck at `pending` | Wiki worker not running |
| Wiki pages not created after upload | Check LLM config in Settings; check worker logs |
| Frontend shows API error | Backend not running, or `NEXT_PUBLIC_API_URL` incorrect |
| CORS errors in browser | Add frontend URL to `CORS_ORIGINS` in `.env.docker` (or `.env` for dev) |
| `requires Python 3.11` | Use `py -3.11 -m venv .venv` to select correct version |
| MCP connection refused | Ensure the API is accessible from outside (check firewall/proxy) |
| OAuth "Couldn't connect" in Claude Desktop | Server not reachable, or `/.well-known/oauth-authorization-server` returning 404 — verify the API is running and Nginx is routing correctly |
| OAuth login form shows `http://` URLs | Nginx not forwarding `X-Forwarded-Proto` — add `proxy_set_header X-Forwarded-Proto $scheme;` to both `/mcp` and `/` location blocks |
| Claude Desktop connected but tools not used | Add instructions to Claude's Custom Instructions or a Project — see [MCP & Claude](MCP.md) |
| MinIO `SignatureDoesNotMatch` | Credentials mismatch — likely caused by running `docker compose up` without `--env-file .env.docker`, which makes Docker Compose use your local `.env` to initialise MinIO. Fix: `docker compose down -v` then `docker compose --env-file .env.docker up -d --build` |
| MinIO `Invalid Request (invalid hostname)` | `MINIO_ENDPOINT` contains an underscore (e.g. `arkon_minio`). Use the Docker Compose service name instead: `minio:9000` |
| Images/files not loading in browser (`ERR_NAME_NOT_RESOLVED`) | Presigned URLs are pointing to an internal hostname. Set `MINIO_PUBLIC_ENDPOINT` to a browser-accessible address: `localhost:9000` for local Docker, `<server-ip>:9000` for a remote server |
| Frontend still calls `localhost:5055` after changing `NEXT_PUBLIC_API_URL` | `NEXT_PUBLIC_*` variables in Next.js are baked into the bundle at **build time**, not runtime. Changing `.env.docker` and restarting the container has no effect. You must rebuild the image: `docker compose --env-file .env.docker build --no-cache frontend && docker compose --env-file .env.docker up -d` |
