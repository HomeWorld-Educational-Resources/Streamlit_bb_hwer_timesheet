import json
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import streamlit as st

# Connection string comes from Streamlit secrets — never hardcode it here,
# and never commit secrets.toml to git. See .streamlit/secrets.toml:
#   db_url = "postgresql://user:password@host:port/dbname"
DB_URL = st.secrets["db_url"]


@contextmanager
def _db():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
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
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id                 SERIAL PRIMARY KEY,
                name               TEXT    NOT NULL,
                company_project    TEXT    DEFAULT '',
                default_rate       REAL,
                max_contract_spend REAL,
                contract_note      TEXT    DEFAULT '',
                footnote           TEXT    DEFAULT '',
                created_at         TIMESTAMP DEFAULT NOW(),
                updated_at         TIMESTAMP DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS customers_name_lower_idx
                ON customers (LOWER(name));

            CREATE TABLE IF NOT EXISTS payments (
                id           SERIAL PRIMARY KEY,
                customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                sort_order   INTEGER DEFAULT 0,
                amount       REAL,
                payment_date TEXT    DEFAULT '',
                notes        TEXT    DEFAULT '',
                created_at   TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS timesheets (
                id                          SERIAL PRIMARY KEY,
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
                created_at                  TIMESTAMP DEFAULT NOW()
            );
        """)


# ── customers ─────────────────────────────────────────────────────────────────

def all_customers():
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM customers ORDER BY LOWER(name)")
        return [dict(r) for r in cur.fetchall()]


def customer_by_name(name):
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM customers WHERE LOWER(name) = LOWER(%s)", (name,))
        r = cur.fetchone()
        return dict(r) if r else None


def upsert_customer(name, company_project='', default_rate=None,
                    max_contract_spend=None, contract_note='', footnote=''):
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM customers WHERE LOWER(name) = LOWER(%s)", (name,))
        existing = cur.fetchone()
        if existing:
            cur.execute("""
                UPDATE customers
                SET company_project=%s, default_rate=%s, max_contract_spend=%s,
                    contract_note=%s, footnote=%s, updated_at=NOW()
                WHERE id=%s
            """, (company_project, default_rate, max_contract_spend,
                  contract_note, footnote, existing['id']))
            return existing['id']
        cur.execute("""
            INSERT INTO customers
                (name, company_project, default_rate, max_contract_spend, contract_note, footnote)
            VALUES (%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (name, company_project, default_rate, max_contract_spend,
              contract_note, footnote))
        return cur.fetchone()['id']


def delete_customer(cid):
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM customers WHERE id=%s", (cid,))


# ── payments ──────────────────────────────────────────────────────────────────

def get_payments(customer_id):
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM payments WHERE customer_id=%s ORDER BY sort_order, id",
            (customer_id,)
        )
        return [dict(r) for r in cur.fetchall()]


def replace_payments(customer_id, payments):
    """Replace all payments for a customer. payments = list of {amount, payment_date, notes}."""
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM payments WHERE customer_id=%s", (customer_id,))
        for i, p in enumerate(payments):
            cur.execute("""
                INSERT INTO payments (customer_id, sort_order, amount, payment_date, notes)
                VALUES (%s,%s,%s,%s,%s)
            """, (customer_id, i,
                  p.get('amount'), p.get('payment_date', ''), p.get('notes', '')))


# ── timesheets ────────────────────────────────────────────────────────────────

def save_timesheet(customer_id, week_start, week_number, hourly_rate,
                   prior_balance, max_contract_spend_override,
                   contract_note, footnote, activities, payments_snapshot,
                   file_name):
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO timesheets
                (customer_id, week_start, week_number, hourly_rate, prior_balance,
                 max_contract_spend_override, contract_note, footnote,
                 activities_json, payments_snapshot_json, file_name)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (customer_id, week_start, week_number, hourly_rate, prior_balance,
              max_contract_spend_override, contract_note, footnote,
              json.dumps(activities), json.dumps(payments_snapshot), file_name))
        return cur.fetchone()['id']


def list_timesheets(customer_id):
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, week_start, week_number, hourly_rate, file_name, created_at
            FROM timesheets WHERE customer_id=%s
            ORDER BY week_start DESC, created_at DESC
        """, (customer_id,))
        return [dict(r) for r in cur.fetchall()]


def get_timesheet(ts_id):
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM timesheets WHERE id=%s", (ts_id,))
        r = cur.fetchone()
        if not r:
            return None
        d = dict(r)
        d['activities'] = json.loads(d.pop('activities_json', '[]'))
        d['payments_snapshot'] = json.loads(d.pop('payments_snapshot_json', '[]'))
        return d


def last_timesheet_activities(customer_id):
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT activities_json FROM timesheets WHERE customer_id=%s
            ORDER BY created_at DESC LIMIT 1
        """, (customer_id,))
        r = cur.fetchone()
        return json.loads(r['activities_json']) if r else []