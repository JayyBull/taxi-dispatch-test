Taxi Dispatch PostgreSQL backend
================================
1. Create a PostgreSQL database.
2. Set DATABASE_URL, for example:
   postgresql://taxi_user:strong-password@localhost:5432/taxi_dispatch
3. Install: pip install -r requirements.txt
4. Start: python realtime_server.py

Startup checks fail fast if DATABASE_URL is missing, PostgreSQL is unreachable,
or migrations cannot be applied. Migrations in migrations/ are applied in filename order
inside transactions and recorded in schema_migrations.

On first start only, demo drivers are seeded if the drivers table is empty:
101/1010, 103/1030, 107/1070, 109/1090, 112/1120, 115/1150, 118/1180, 120/1200.
Change these PINs before production.

Persistent PostgreSQL data:
- drivers and salted PBKDF2 PIN hashes
- authenticated sessions (only SHA-256 token hashes are stored)
- bookings/current job status
- driver offers and outcomes
- immutable job-status history

SSE connections remain in process memory because they are live network connections,
not business data. The server reloads active offers/jobs from PostgreSQL after restart.
For multi-server production deployment, add Redis/PostgreSQL NOTIFY or another pub/sub bus.
