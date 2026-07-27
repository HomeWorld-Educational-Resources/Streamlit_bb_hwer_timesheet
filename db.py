import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / 'timesheet.db'


@contextmanager
def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS customers (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                name               TEXT    NOT NULL,
                company_project    TEXT    DEFAULT '',
                default_rate       REAL,
                max_contract_spend REAL,
                contract_note      TEXT    DEFAULT '',
                footnote           TEXT    DEFAULT '',
                created_at         TEXT    DEFAULT (datetime('now')),
                updated_at         TEXT    DEFAULT (datetime('now')),
                UNIQUE(name COLLATE NOCASE)
            );
            CREATE TABLE IF NOT EXISTS payments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                sort_order   INTEGER DEFAULT 0,
                amount       REAL,
                payment_date TEXT    DEFAULT '',
                notes        TEXT    DEFAULT '',
                created_at   TEXT    DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS timesheets (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id                 INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                week_start                  TEXT    NOT NULL,
                week_number                 INTEGER DEFAULT 0,
                hourly_rate                 REAL,
                prior_balance               REAL    DEFAULT 0,
                max_contract_spend_override REAL,
                contract_note               TEXT    DEFAULT '',
                footnote                    TEXT    DEFAULT '',
                activities_json             TEXT    DEFAULT '[]',
                payments_snapshot_json      TEXT    DEFAULT '[]',
                file_name                   TEXT    DEFAULT '',
                created_at                  TEXT    DEFAULT (datetime('now'))
            );
        """)


# ── customers ─────────────────────────────────────────────────────────────────

def all_customers():
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM customers ORDER BY name COLLATE NOCASE"
        ).fetchall()]


def customer_by_name(name):
    with _db() as conn:
        r = conn.execute(
            "SELECT * FROM customers WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return dict(r) if r else None


def upsert_customer(name, company_project='', default_rate=None,
                    max_contract_spend=None, contract_note='', footnote=''):
    with _db() as conn:
        existing = conn.execute(
            "SELECT id FROM customers WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing:
            conn.execute("""
                UPDATE customers
                SET company_project=?, default_rate=?, max_contract_spend=?,
                    contract_note=?, footnote=?, updated_at=datetime('now')
                WHERE id=?
            """, (company_project, default_rate, max_contract_spend,
                  contract_note, footnote, existing['id']))
            return existing['id']
        cur = conn.execute("""
            INSERT INTO customers
                (name, company_project, default_rate, max_contract_spend, contract_note, footnote)
            VALUES (?,?,?,?,?,?)
        """, (name, company_project, default_rate, max_contract_spend,
              contract_note, footnote))
        return cur.lastrowid


def delete_customer(cid):
    with _db() as conn:
        conn.execute("DELETE FROM customers WHERE id=?", (cid,))


# ── payments ──────────────────────────────────────────────────────────────────

def get_payments(customer_id):
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM payments WHERE customer_id=? ORDER BY sort_order, id",
            (customer_id,)
        ).fetchall()]


def replace_payments(customer_id, payments):
    """Replace all payments for a customer. payments = list of {amount, payment_date, notes}."""
    with _db() as conn:
        conn.execute("DELETE FROM payments WHERE customer_id=?", (customer_id,))
        for i, p in enumerate(payments):
            conn.execute("""
                INSERT INTO payments (customer_id, sort_order, amount, payment_date, notes)
                VALUES (?,?,?,?,?)
            """, (customer_id, i,
                  p.get('amount'), p.get('payment_date', ''), p.get('notes', '')))


# ── timesheets ────────────────────────────────────────────────────────────────

def save_timesheet(customer_id, week_start, week_number, hourly_rate,
                   prior_balance, max_contract_spend_override,
                   contract_note, footnote, activities, payments_snapshot,
                   file_name):
    with _db() as conn:
        cur = conn.execute("""
            INSERT INTO timesheets
                (customer_id, week_start, week_number, hourly_rate, prior_balance,
                 max_contract_spend_override, contract_note, footnote,
                 activities_json, payments_snapshot_json, file_name)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (customer_id, week_start, week_number, hourly_rate, prior_balance,
              max_contract_spend_override, contract_note, footnote,
              json.dumps(activities), json.dumps(payments_snapshot), file_name))
        return cur.lastrowid


def list_timesheets(customer_id):
    with _db() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT id, week_start, week_number, hourly_rate, file_name, created_at
            FROM timesheets WHERE customer_id=?
            ORDER BY week_start DESC, created_at DESC
        """, (customer_id,)).fetchall()]


def get_timesheet(ts_id):
    with _db() as conn:
        r = conn.execute("SELECT * FROM timesheets WHERE id=?", (ts_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d['activities'] = json.loads(d.pop('activities_json', '[]'))
        d['payments_snapshot'] = json.loads(d.pop('payments_snapshot_json', '[]'))
        return d


def last_timesheet_activities(customer_id):
    with _db() as conn:
        r = conn.execute("""
            SELECT activities_json FROM timesheets WHERE customer_id=?
            ORDER BY created_at DESC LIMIT 1
        """, (customer_id,)).fetchone()
        return json.loads(r['activities_json']) if r else []
