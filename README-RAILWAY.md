# Taxi Dispatch – Railway test deployment

This package is prepared for a single Railway web service plus Railway PostgreSQL.

## URLs after deployment
- `/` – Dispatch/Admin
- `/driver` – Driver App
- `/health` – application/database health check

## Deploy
1. Create a new GitHub repository and upload the contents of this folder (not the folder itself).
2. In Railway, create a New Project and choose **Deploy from GitHub repo**.
3. Add a **PostgreSQL** service to the same Railway project.
4. In the web service Variables, add `DATABASE_URL` using the PostgreSQL service's `DATABASE_URL` reference if Railway has not already linked it.
5. Deploy. `railway.json` starts `python realtime_server.py` and uses `/health` for health checks.
6. In the web service Settings > Networking, choose **Generate Domain**.
7. Open the generated domain for Dispatch. Add `/driver` for the Driver App.

## Database/startup
The server requires `DATABASE_URL`. At startup it checks the database and applies SQL files in `migrations/` in filename order. Applied migrations are recorded in `schema_migrations`.

If the drivers table is empty, the current development backend seeds demo driver accounts. Replace these before using real operational/customer data.

## Important testing note
This is a test deployment. Do not enter real customer information until staff/admin authentication, production secrets, backups, HTTPS/security review, and access controls have been completed.

## Local test
Create PostgreSQL, copy `.env.example` values into your shell/environment, then:

    pip install -r requirements.txt
    python realtime_server.py

Open `http://localhost:8765/` and `http://localhost:8765/driver`.
