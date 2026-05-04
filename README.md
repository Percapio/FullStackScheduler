# Scheduler

## 1. What this project is

Scheduler is a small desktop tool that takes a production-schedule spreadsheet
(the kind a manufacturing shop keeps in Excel — jobs, quantities, ship dates,
notes about kits, PCBs, and the SMT lines) and turns it into a searchable,
trackable database with a web UI on top. Instead of squinting at a giant
`.xlsx` and emailing a new copy around every time something changes, the team
imports the workbook once, and from then on works in a browser: filter jobs,
see what shipped, leave notes, and re-import an updated workbook later without
losing history.

Under the hood it is a Python FastAPI backend with a SQLite database and a
Vue 3 frontend, packaged so it can run on a single Windows machine on the LAN.

## 2. Getting started (for a college-level student)

You will need:

- **Python 3.11+** (with `pip`)
- **Node.js 20+** (with `npm`)
- **Git**
- A copy of the repository: `git clone <repo-url>` and `cd Schedule`

### Backend

```powershell
# from the repo root
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt   # if a requirements file is present
# (otherwise install the deps the imports need: fastapi, uvicorn,
# sqlalchemy, alembic, pydantic-settings, openpyxl)

# Start the API (this also runs the Alembic migrations on first launch
# and creates the SQLite database under backend/outputs/db/schedule.db)
python run.py
```

The API will listen on `http://localhost:8000`. Open
`http://localhost:8000/docs` in a browser to poke at the endpoints.

### Frontend

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Vite will print a local URL (usually `http://localhost:5173`). Open it in a
browser. The frontend reads the backend URL from `frontend/.env.development`
(`VITE_API_BASE=http://localhost:8000`) — change it if your backend lives
somewhere else.

### Ingesting your first workbook

Once both are running and you have placed a workbook under `backend/data/`
(see Section 3), import it from the command line:

```powershell
python -m backend.app.ingest "backend/data/YOUR_WORKBOOK.xlsx"
```

The importer prints a summary (`inserted=… updated=… errored=…`). Refresh the
frontend and the jobs should appear.

### Running the tests

```powershell
pytest                # backend tests
cd frontend && npm test    # frontend tests
```

## 3. What the end-user must provide before ingestion works

The repository ships **without** the production data — `*.xlsx` files are
listed in [.gitignore](.gitignore) and are never committed. Before the
ingestion pipeline can do anything useful you have to supply your own
workbook and make sure its shape matches what the reader expects.

### 3a. Drop your workbook here

Place your `.xlsx` file under [backend/data/](backend/data/). Any filename is
fine; you pass the path explicitly when you run the importer. For reference,
the workbooks the project was built around look like:

- `ADV ALL IN ONE SCHEDULE (2024+).xlsx`
- `ADV ALL IN ONE SCHEDULE (2023) - HISTORY.xlsx`
- `Schedule Shipped History 2024+.xlsx`

### 3b. The workbook must contain a sheet named `SCHD`

The sheet name is hard-coded as the default in
[backend/app/reader.py](backend/app/reader.py#L9). If your workbook uses a
different name, either:

- rename the worksheet in Excel to `SCHD`, **or**
- pass `--sheet "Your Sheet Name"` to the ingest command, **or**
- change the `SHEET_NAME` constant in [backend/app/reader.py](backend/app/reader.py#L9).

### 3c. The header row must use these exact column names

Row 1 of the sheet must contain headers from the set defined in
[backend/app/reader.py](backend/app/reader.py#L11-L17) and mapped in
[backend/app/ingest.py](backend/app/ingest.py#L45-L67). Missing headers are
silently skipped, but any row whose `JOB` cell cannot be parsed will be
flagged as an error in the import:

```
SHIPPED, PCB NOTES, KIT NOTES, SCHEDULING NOTES,
LINE 1, LINE 2, LINE 3, JOB, QTY, SHIP DATE,
PROG, MFG NOTES, SMT LINES, SMT PLCMNTS,
SHIP METHOD, CUSTOMER, SALES P, DOC REL, KIT REL,
CODE, BOM COMPARE / PHOTOS
```

The `JOB` column is the most opinionated: each cell needs to decompose into a
part number plus a build type (e.g. `128764 NEW` or `128764\nRONC 123456`).
The decomposition rules live in [backend/app/extractors.py](backend/app/extractors.py).

### 3d. Files you may need to touch before ingestion

| File | Why you might edit it |
| --- | --- |
| [backend/app/reader.py](backend/app/reader.py) | Change the default sheet name (`SHEET_NAME`), or add/remove headers in `KNOWN_HEADERS` / `MARKDOWN_HEADERS` if your workbook layout differs. |
| [backend/app/ingest.py](backend/app/ingest.py) | Adjust `_COLUMN_MAP` if you renamed any header or want a header to land in a different staging column. |
| [backend/app/extractors.py](backend/app/extractors.py) | Adjust the `JOB`-cell parser if your shop uses different build-type tokens or part-number formats. |
| [backend/app/config.py](backend/app/config.py) | Override `SCHEDULER_DATABASE_URL` (or set it in a `.env` file at the repo root) to point at a database location other than `backend/outputs/db/schedule.db`. |
| [frontend/.env.development](frontend/.env.development) | Point `VITE_API_BASE` at the backend if it is not running on `http://localhost:8000`. |

### 3e. Optional: a `.env` file at the repo root

Settings are read with the prefix `SCHEDULER_` (see
[backend/app/config.py](backend/app/config.py#L28)). Create a `.env` next to
`run.py` if you want to override defaults without editing code, e.g.:

```
SCHEDULER_DATABASE_URL=sqlite:///C:/path/to/schedule.db
SCHEDULER_PORT=8001
```
