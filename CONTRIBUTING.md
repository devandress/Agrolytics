# Contributing to AgroVision

Thank you for your interest in contributing to AgroVision! This guide outlines how to set up your development environment, write code, and submit changes.

## Development Setup

### Requirements

- Python 3.11+
- Docker & Docker Compose 2.0+
- Git

### Install dependencies

```bash
pip install -r requirements.txt
```

### Start development environment

```bash
# Build and start containers (API, PostgreSQL, Redis, Celery)
docker-compose up --build
```

### Verify setup

```bash
# Health check
curl http://localhost:8000/health

# Swagger UI
open http://localhost:8000/docs

# Test user login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"123@gmail.com","password":"12345678"}'
```

## Code Structure

```
app/
├── main.py              # FastAPI app factory
├── core/
│   ├── config.py        # Environment configuration
│   ├── security.py      # JWT + password utilities
│   └── logging.py       # Loguru setup
├── db/
│   ├── base.py          # SQLAlchemy declarative base
│   └── session.py       # Async/sync session factories
├── models/              # SQLAlchemy ORM models
├── schemas/             # Pydantic request/response
├── api/v1/
│   ├── router.py        # Route registration
│   └── endpoints/       # Endpoint implementations
├── services/            # Business logic
└── tasks/               # Celery task definitions
```

## Writing Code

### Style Guide

- **Python style:** PEP 8 (use Black formatter)
- **Type hints:** Required for all function signatures
- **Comments:** Only for non-obvious logic (WHY, not WHAT)
- **Docstrings:** One-line docstrings for functions and classes
- **Database queries:** Use SQLAlchemy ORM; raw SQL only when necessary

### Formatting

```bash
# Format code
black app/ tests/

# Check types
mypy app/ --strict

# Lint
ruff check app/ tests/
```

### Testing

Write tests for new features and bug fixes:

```bash
# Run all tests
pytest

# Run specific test
pytest tests/test_clustering.py::test_kmeans

# Run with coverage
pytest --cov=app --cov-report=html
```

**Test file location:** `tests/test_<module>.py`

**Example test:**
```python
import pytest
from app.models.user import User
from app.core.security import hash_password


@pytest.mark.asyncio
async def test_user_registration(db):
    """Verify user creation with valid input."""
    user = User(
        email="test@example.com",
        hashed_password=hash_password("secure123"),
        role="farmer",
    )
    db.add(user)
    await db.commit()
    assert user.id is not None
```

## Database Migrations

When modifying models, create an Alembic migration:

```bash
# Auto-generate migration (detects model changes)
alembic revision --autogenerate -m "Add user_subscription column"

# Review migration: migrations/versions/XXXX_<message>.py
# Apply migration
alembic upgrade head
```

## Submitting Changes

### Create a branch

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/issue-description
```

### Commit guidelines

- Commits should be focused (one logical change per commit)
- Use present tense: "Add feature" not "Added feature"
- Reference issues: "Fix #123: User can now reset password"

```bash
git add app/models/user.py app/schemas/user.py
git commit -m "Add email verification for user registration

This ensures users verify their email before login.
Closes #42"
```

### Before submitting PR

1. **Format and lint:**
   ```bash
   black app/ tests/
   ruff check --fix app/ tests/
   ```

2. **Run tests:**
   ```bash
   pytest
   ```

3. **Type check:**
   ```bash
   mypy app/ --strict
   ```

4. **Run locally:**
   ```bash
   docker-compose up --build
   # Test changes manually via Swagger UI
   ```

### Create pull request

- Write clear PR title and description
- Reference related issues
- Include before/after screenshots for UI changes
- Request review from maintainers

### PR Review Checklist

Reviewers will check:
- Code follows style guide
- Tests pass and coverage maintained
- No security vulnerabilities
- Database migrations are reversible
- Performance impact analyzed (if relevant)

## Performance Considerations

- **Database queries:** Use `select()` with indexed columns; avoid N+1
- **Async:** Use async operations where possible
- **Caching:** Use Redis for frequently accessed data
- **Background jobs:** Use Celery for long-running tasks (>1s)

Example:
```python
# Good: Use async
async def get_user(user_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()

# Bad: Sync in async context
def get_user(user_id: uuid.UUID):  # Don't use sync in FastAPI handlers
    return session.query(User).filter_by(id=user_id).first()
```

## Debugging

### Enable debug logging

```bash
# In .env
LOG_LEVEL=DEBUG
APP_ENV=development
```

### Access container shell

```bash
# API container
docker-compose exec api bash

# Database
docker-compose exec db psql -U postgres -d agrolytics

# Redis
docker-compose exec redis redis-cli
```

### Celery task debugging

```bash
# Watch task queue
docker-compose logs -f celery-worker

# Connect to Celery shell
docker-compose exec celery-worker celery -A app.tasks.celery_app shell
```

## Reporting Issues

When reporting bugs, include:
- Python version and OS
- Steps to reproduce
- Expected vs. actual behavior
- Error logs (full stack trace)
- Screenshots (if UI-related)

Example:
```
**Title:** User registration fails with PostgreSQL unique constraint violation

**Steps:**
1. POST /api/v1/auth/register with {"email":"test@example.com","password":"secure123"}
2. Response: 500 Internal Server Error

**Error:**
```
psycopg2.errors.UniqueViolation: duplicate key value violates unique constraint "users_email_key"
```

**Expected:** 409 Conflict with clear message about duplicate email
```

## Questions?

- Check existing issues and discussions
- Join our team communication channel
- Contact maintainers directly

Thank you for contributing to AgroVision! 🌱
