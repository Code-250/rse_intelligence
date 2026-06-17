# Database migrations (Alembic)

Migrations are hand-written (no autogenerate) and must be reversible — every
revision implements both `upgrade()` and `downgrade()`.

```bash
# From products/financial-doc-analyzer/backend/ with DATABASE_URL set:
alembic upgrade head      # apply all migrations
alembic downgrade -1      # roll back the most recent migration
alembic revision -m "msg" # create a new empty revision
```

The database URL is read from the `DATABASE_URL` environment variable in
`env.py`; it is never stored in `alembic.ini`.
