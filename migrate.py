from pathlib import Path
import os, sys
import psycopg
from psycopg.rows import dict_row

BASE = Path(__file__).resolve().parent
MIGRATIONS = BASE / 'migrations'
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()

if not DATABASE_URL:
    raise SystemExit('MIGRATION FAILED: DATABASE_URL is required')
files = sorted(MIGRATIONS.glob('*.sql'))
if not files:
    raise SystemExit(f'MIGRATION FAILED: no .sql files found in {MIGRATIONS}. Make sure the migrations folder was uploaded to GitHub.')

try:
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
            cur.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )''')
        conn.commit()
        with conn.cursor() as cur:
            cur.execute('SELECT version FROM schema_migrations')
            applied = {r['version'] for r in cur.fetchall()}
        for f in files:
            if f.name in applied:
                print(f'Migration already applied: {f.name}', flush=True)
                continue
            print(f'Applying migration: {f.name}', flush=True)
            sql = f.read_text(encoding='utf-8')
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute('INSERT INTO schema_migrations(version) VALUES (%s)', (f.name,))
            print(f'Applied migration: {f.name}', flush=True)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.drivers') AS drivers, to_regclass('public.bookings') AS bookings, to_regclass('public.driver_offers') AS offers, to_regclass('public.job_status_history') AS history")
            check = cur.fetchone()
            missing = [k for k,v in check.items() if v is None]
            if missing:
                raise RuntimeError('required tables missing after migrations: ' + ', '.join(missing))
        print('Migrations complete: required PostgreSQL tables are present.', flush=True)
except Exception as exc:
    raise SystemExit(f'MIGRATION FAILED: {exc}')
