# Contributing to Arkon

First off, thank you for considering contributing to Arkon! Every contribution — bug reports, feature requests, code, documentation — is valuable and appreciated.

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Commit Conventions](#commit-conventions)
- [Pull Request Process](#pull-request-process)
- [Reporting Issues](#reporting-issues)
- [License](#license)

---

## Code of Conduct

Be respectful, constructive, and professional. We want Arkon to be a welcoming space for everyone regardless of experience level, identity, or background.

---

## Getting Started

### Prerequisites

| Tool       | Version    | Purpose                           |
|------------|------------|-----------------------------------|
| Python     | 3.11 – 3.13 | Backend runtime                 |
| Node.js    | 20+        | Frontend (Next.js)                |
| PostgreSQL | 15+        | Main database (with pgvector)     |
| Redis      | 7+         | Background job queue              |
| MinIO      | Latest     | S3-compatible file storage        |
| Neo4j      | 5+         | Knowledge graph (optional)        |

### Setup

1. **Fork & clone** the repository:

   ```bash
   git clone https://github.com/<your-fork>/arkon.git
   cd arkon
   ```

2. **Backend setup:**

   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate

   pip install -e ".[dev]"
   ```

3. **Environment:**

   ```bash
   cp .env.local.example .env
   # Edit .env with your local values (see docs/HOW_TO_RUN.md for details)
   ```

4. **Database:**

   ```bash
   alembic upgrade head
   ```

5. **Frontend setup:**

   ```bash
   cd frontend
   npm install
   echo "NEXT_PUBLIC_API_URL=http://localhost:5055" > .env.local
   ```

Refer to [`blob/docs/HOW_TO_RUN.md`](blob/docs/HOW_TO_RUN.md) for full infrastructure setup (Docker commands, etc.).

### Running in Development

You need **3 terminals**:

```bash
# Terminal 1 — API server
uvicorn app.main:app --host 0.0.0.0 --port 5055 --reload

# Terminal 2 — Background worker
python -m arq app.worker.WorkerSettings

# Terminal 3 — Frontend
cd frontend && npm run dev
```

- **Backend API:** http://localhost:5055
- **API Docs (Swagger):** http://localhost:5055/docs
- **Frontend:** http://localhost:3000

---

## Project Structure

```
arkon/
├── app/                    # Backend (FastAPI)
│   ├── ai/                 # AI providers (Google, OpenAI, Anthropic)
│   │   └── providers/      # Provider implementations
│   ├── database/           # SQLAlchemy models & DB init
│   ├── mcp/                # MCP server (Claude Desktop integration)
│   ├── routers/            # API route handlers
│   ├── services/           # Business logic layer
│   ├── config.py           # Pydantic settings
│   ├── main.py             # FastAPI app entry point
│   └── worker.py           # ARQ background worker
├── alembic/                # Database migrations
│   └── versions/           # Migration files
├── frontend/               # Frontend (Next.js + TypeScript)
│   └── src/
│       ├── app/            # Pages (App Router)
│       ├── components/     # React components
│       └── lib/            # Utilities, API client, auth
├── blob/docs/              # Documentation
├── pyproject.toml          # Python dependencies & tooling config
├── docker-compose.yml      # Docker services (Postgres, Redis, MinIO, API, workers, frontend)
├── .env.docker.example     # Env template for `docker compose`
└── .env.local.example      # Env template for local development
```

### Architecture Quick Reference

| Layer         | Tech              | Notes                                |
|---------------|-------------------|--------------------------------------|
| API           | FastAPI           | Async, auto-docs at `/docs`          |
| ORM           | SQLAlchemy 2.0    | Async sessions, pgvector extension   |
| Migrations    | Alembic           | Auto-generate from model changes     |
| Worker        | ARQ + Redis       | Document ingestion pipeline          |
| AI Providers  | Multi-provider    | Google, OpenAI, Anthropic, Ollama    |
| Frontend      | Next.js 15        | App Router, TypeScript, Tailwind CSS |
| Auth          | JWT + bcrypt      | Role-based (admin/employee)          |
| Storage       | MinIO (S3)        | File uploads and image extraction    |
| Graph DB      | Neo4j (optional)  | Entity extraction & knowledge graph  |

---

## Development Workflow

1. **Create a branch** from `main`:

   ```bash
   git checkout -b feat/your-feature-name
   # or
   git checkout -b fix/issue-description
   ```

2. **Make your changes** — keep them focused on a single concern.

3. **Test your changes:**

   ```bash
   # Backend — lint
   ruff check app/

   # Backend — format
   ruff format app/

   # Backend — tests
   pytest

   # Frontend — lint
   cd frontend && npm run lint

   # Frontend — type check
   cd frontend && npx tsc --noEmit
   ```

4. **Commit** with a descriptive message (see [Commit Conventions](#commit-conventions)).

5. **Push** and open a Pull Request.

---

## Coding Standards

### Backend (Python)

- **Formatter/Linter:** [Ruff](https://docs.astral.sh/ruff/) — config in `pyproject.toml`
- **Line length:** 88 characters
- **Style:** PEP 8 with Ruff rules `E`, `F`, `I`
- **Type hints:** Use type annotations for function parameters and return types
- **Async:** All database and I/O operations must be async (`async/await`)
- **Docstrings:** Use `"""triple-quote"""` docstrings for public functions and classes
- **Imports:** Use absolute imports (`from app.services.config_service import ...`)
- **Models:** SQLAlchemy models go in `app/database/models.py`
- **Routes:** Each domain gets its own router file in `app/routers/`
- **Services:** Business logic lives in `app/services/`, not in routers

### Frontend (TypeScript / React)

- **Framework:** Next.js 15 with App Router
- **Language:** TypeScript — no `any` unless absolutely necessary
- **Styling:** Tailwind CSS with the project's design tokens
- **Components:**
  - Shared/reusable → `src/components/ui/` (shadcn/ui)
  - Feature-specific → `src/components/<feature>/`
  - Layout → `src/components/layout/`
- **API calls:** Use the `api()` helper from `src/lib/api.ts`
- **State:** Prefer local state (`useState`) over global state; use context only when needed
- **Naming:** Components in PascalCase, files in kebab-case

### Database Migrations

When changing models, **always** create a migration:

```bash
alembic revision --autogenerate -m "short description of change"
```

- Review the generated migration before committing — auto-generate isn't perfect
- Never edit an existing migration that has been merged to `main`
- Include seed data in migrations when adding new required lookup tables

---

## Commit Conventions

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>
```

### Types

| Type       | When to use                                    |
|------------|------------------------------------------------|
| `feat`     | New feature                                    |
| `fix`      | Bug fix                                        |
| `docs`     | Documentation only                             |
| `style`    | Code style (formatting, no logic change)       |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `perf`     | Performance improvement                        |
| `test`     | Adding or updating tests                       |
| `chore`    | Build process, dependencies, tooling           |
| `ci`       | CI/CD configuration                            |

### Scopes

| Scope      | Covers                         |
|------------|--------------------------------|
| `api`      | Backend routers, services      |
| `db`       | Models, migrations             |
| `worker`   | Background job processing      |
| `ai`       | AI providers, embedding, LLM   |
| `mcp`      | MCP server integration         |
| `ui`       | Frontend components, pages     |
| `auth`     | Authentication, RBAC           |
| `config`   | Settings, environment          |
| `deps`     | Dependency updates             |

### Examples

```
feat(api): add bulk delete endpoint for sources
fix(worker): handle empty PDF files during ingestion
docs: update HOW_TO_RUN with Neo4j setup
refactor(ui): extract color picker into reusable component
chore(deps): bump fastapi to 0.115.0
```

---

## Pull Request Process

1. **Title** should follow commit conventions: `feat(scope): description`

2. **Description** should include:
   - What changed and why
   - Screenshots for UI changes
   - Migration instructions if DB schema changed
   - Breaking changes (if any)

3. **Checklist** before requesting review:
   - [ ] Code compiles / lints without errors
   - [ ] New code has appropriate type hints / TypeScript types
   - [ ] Database migrations are included (if models changed)
   - [ ] Existing tests pass
   - [ ] Sensitive data (API keys, passwords) is never hardcoded

4. **Review** — at least 1 approval is required before merging.

5. **Merge** — squash-merge to keep history clean.

---

## Reporting Issues

When opening an issue, please include:

- **Environment:** OS, Python version, Node.js version, browser
- **Steps to reproduce:** Numbered, specific steps
- **Expected behavior:** What should happen
- **Actual behavior:** What actually happened
- **Logs/screenshots:** Console output, error messages, UI screenshots
- **Related code:** File paths, API endpoints involved

Use the appropriate label:

| Label          | Description                    |
|----------------|--------------------------------|
| `bug`          | Something is broken            |
| `enhancement`  | New feature request            |
| `docs`         | Documentation improvement      |
| `question`     | Need help or clarification     |
| `good first issue` | Beginner-friendly task    |

---

## License

By contributing to Arkon, you agree that your contributions will be licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).

> **Note:** Arkon is licensed for **noncommercial use only**. If you need a commercial license, please contact the Arkon team.

---

_Thank you for helping make Arkon better!_ 🚀
