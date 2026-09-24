import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

import db
import pdf_generator
import ai_estimator

APP_DIR = Path(__file__).resolve().parent
LOGO_PATH = APP_DIR / 'logo.png'

PAYMENT_DATE_FORMAT = '%b-%d-%Y'
WEEK_DATE_FORMAT = '%Y-%m-%d'
N_ACTIVITY_ROWS = 5
MAX_PAYMENT_ROWS = 30
MAX_MILESTONE_ROWS = 20
MILESTONE_STATUSES = ['Not Started', 'In Progress', 'Complete', 'Blocked']
MAX_CONTRACTOR_ENTRY_ROWS = 25

EB1A_CRITERIA = [
    'EB1A - Awards',
    'EB1A - Membership',
    'EB1A - Published Material About You',
    "EB1A - Judging Others' Work",
    'EB1A - Original Contributions',
    'EB1A - Authorship (Scholarly Articles)',
    'EB1A - Artistic Exhibitions/Showcases',
    'EB1A - Critical/Leading Role',
    'EB1A - High Salary',
    'EB1A - Commercial Success (Arts)',
]
NIW_PRONGS = [
    'NIW Prong 1 - Substantial Merit & National Importance',
    'NIW Prong 2 - Well Positioned to Advance',
    'NIW Prong 3 - Beneficial to Waive Job Offer',
]
MILESTONE_CATEGORIES = ['—'] + EB1A_CRITERIA + NIW_PRONGS + ['Other']

st.set_page_config(page_title='Customer Timesheet Builder', layout='wide')

# ── password gate ─────────────────────────────────────────────────────────────

def _check_password():
    """Show a password prompt and block the rest of the app until it's correct."""

    def _password_entered():
        if st.session_state.get('_pw_input') == st.secrets.get('app_password'):
            st.session_state['_authenticated'] = True
            del st.session_state['_pw_input']
        else:
            st.session_state['_authenticated'] = False

    if st.session_state.get('_authenticated'):
        return True

    st.title('Customer Timesheet Builder')
    st.text_input('Password', type='password', on_change=_password_entered, key='_pw_input')
    if st.session_state.get('_authenticated') is False:
        st.error('Incorrect password.')
    return False


if not _check_password():
    st.stop()

db.init_db()

# ── helpers ───────────────────────────────────────────────────────────────────

def _sf(v, default=None):
    if v is None:
        return default
    s = str(v).strip()
    if not s or s in ('None', 'nan'):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _parse_date(v):
    if isinstance(v, datetime):
        return v
    if hasattr(v, 'timetuple'):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip() if v else ''
    if not s or s in ('None', 'nan'):
        return None
    for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%Y/%m/%d', '%b-%d-%Y',
                '%B-%d-%Y', '%m-%d-%Y', '%m/%d/%y', '%m-%d-%y']:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f'Cannot parse date: {v!r}')


def _fmt_pay_date(v):
    if not v or str(v).strip() in ('', 'None', 'nan'):
        return ''
    try:
        dt = _parse_date(v)
        return dt.strftime(PAYMENT_DATE_FORMAT) if dt else ''
    except ValueError:
        return str(v)


def _safe_parse_date(v):
    """Like _parse_date but never raises — returns None if unparseable/blank."""
    try:
        return _parse_date(v)
    except (ValueError, TypeError):
        return None


def _current_week_monday():
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime(WEEK_DATE_FORMAT)


def _hours(v):
    f = _sf(v, 0.0)
    return f if f is not None else 0.0


def _blank_acts():
    return pd.DataFrame([
        {'Activity': '', 'Mon': '', 'Tue': '', 'Wed': '', 'Thu': '', 'Fri': '', 'Output description': ''}
        for _ in range(N_ACTIVITY_ROWS)
    ])


def _blank_pmts():
    return pd.DataFrame([
        {'Amount': '', 'Payment date': '', 'Notes': ''}
        for _ in range(MAX_PAYMENT_ROWS)
    ])


def _blank_milestones():
    return pd.DataFrame([
        {'Milestone': '', 'Category': '—', 'Target Date': '', '% Complete': 0, 'Status': 'Not Started', 'Notes': ''}
        for _ in range(MAX_MILESTONE_ROWS)
    ])


def _blank_contractor_entries():
    return pd.DataFrame([
        {'Date': '', 'Customer': '', 'Hours': '', 'Description': ''}
        for _ in range(MAX_CONTRACTOR_ENTRY_ROWS)
    ])


def _acts_from_db(customer_id):
    rows = db.last_timesheet_activities(customer_id)
    result = []
    for a in rows[:N_ACTIVITY_ROWS]:
        result.append({
            'Activity': a.get('activity', ''),
            'Mon': str(a.get('mon', '') or ''),
            'Tue': str(a.get('tue', '') or ''),
            'Wed': str(a.get('wed', '') or ''),
            'Thu': str(a.get('thu', '') or ''),
            'Fri': str(a.get('fri', '') or ''),
            'Output description': a.get('output_description', ''),
        })
    while len(result) < N_ACTIVITY_ROWS:
        result.append({'Activity': '', 'Mon': '', 'Tue': '', 'Wed': '', 'Thu': '', 'Fri': '', 'Output description': ''})
    return pd.DataFrame(result)


def _pmts_from_db(customer_id):
    rows = db.get_payments(customer_id)
    result = [
        {'Amount': str(p['amount'] or ''), 'Payment date': p['payment_date'] or '', 'Notes': p['notes'] or ''}
        for p in rows
    ]
    while len(result) < MAX_PAYMENT_ROWS:
        result.append({'Amount': '', 'Payment date': '', 'Notes': ''})
    return pd.DataFrame(result[:MAX_PAYMENT_ROWS])


def _milestones_from_db(customer_id):
    rows = db.get_milestones(customer_id)
    result = [
        {
            'Milestone': m['title'] or '',
            'Category': m.get('category') or '—',
            'Target Date': m['target_date'] or '',
            '% Complete': m['percent_complete'] or 0,
            'Status': m['status'] or 'Not Started',
            'Notes': m['notes'] or '',
        }
        for m in rows
    ]
    while len(result) < MAX_MILESTONE_ROWS:
        result.append({'Milestone': '', 'Category': '—', 'Target Date': '', '% Complete': 0, 'Status': 'Not Started', 'Notes': ''})
    return pd.DataFrame(result[:MAX_MILESTONE_ROWS])


def _contractor_entries_from_db(contractor_id):
    rows = db.get_contractor_entries(contractor_id)
    result = [
        {
            'Date': e.get('entry_date', '') or '',
            'Customer': e.get('customer_name', '') or '',
            'Hours': str(e.get('hours', '') or ''),
            'Description': e.get('description', '') or '',
        }
        for e in rows
    ]
    while len(result) < MAX_CONTRACTOR_ENTRY_ROWS:
        result.append({'Date': '', 'Customer': '', 'Hours': '', 'Description': ''})
    return pd.DataFrame(result[:MAX_CONTRACTOR_ENTRY_ROWS])


# ── CSV builder (used for generation and history re-download) ─────────────────

def _build_csv(customer_name, company, week_dt, week_number,
               rate, prior_bal, max_spend, contract_note, footnote,
               activities, payments):
    """
    activities = list of dicts with lowercase keys: activity, mon, tue, wed, thu, fri, output_description
    payments   = list of dicts: amount, payment_date, notes
    """
    total_hours = sum(_hours(a.get('mon')) + _hours(a.get('tue')) + _hours(a.get('wed'))
                      + _hours(a.get('thu')) + _hours(a.get('fri')) for a in activities)
    amount_week = total_hours * (rate or 0.0)
    total_paid = sum(_sf(p.get('amount'), 0.0) for p in payments if _sf(p.get('amount')) is not None)
    # Prior Balance is already net of all payments received to date, so it is
    # not subtracted again here — doing so would double-count payments.
    total_due = max(0.0, (prior_bal or 0.0) + amount_week)

    buf = io.StringIO()
    w = csv.writer(buf)

    def row(*cols):
        w.writerow(cols)

    def blank():
        w.writerow([])

    row('TIMESHEET REPORT')
    row('Generated', datetime.now().strftime('%Y-%m-%d %H:%M'))
    blank()
    row('CUSTOMER INFO')
    row('Customer', customer_name)
    row('Company / Project', company or '')
    row('Week Starting', week_dt.strftime(WEEK_DATE_FORMAT))
    row('Week Number', week_number or '')
    row('Hourly Rate', f'${(rate or 0):.2f}')
    row('Prior Balance', f'${(prior_bal or 0):.2f}')
    if max_spend:
        row('Max Contract Spend', f'${max_spend:.2f}')
    if contract_note:
        row('Contract Note', contract_note)
    if footnote:
        row('Footnote', footnote)
    blank()
    row('ACTIVITIES')
    row('Activity', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Total Hours', 'Output Description')
    for a in activities:
        mon = _hours(a.get('mon'))
        tue = _hours(a.get('tue'))
        wed = _hours(a.get('wed'))
        thu = _hours(a.get('thu'))
        fri = _hours(a.get('fri'))
        row_total = mon + tue + wed + thu + fri
        row(a.get('activity', ''),
            mon or '', tue or '', wed or '', thu or '', fri or '',
            row_total or '',
            a.get('output_description', ''))
    blank()
    row('WEEK SUMMARY')
    row('Total Hours', total_hours)
    row('Hourly Rate', f'${(rate or 0):.2f}')
    row('Amount This Week', f'${amount_week:.2f}')
    row('Prior Balance', f'${(prior_bal or 0):.2f}')
    row('Total Due', f'${total_due:.2f}')
    blank()
    active_payments = [p for p in payments if _sf(p.get('amount')) is not None]
    if active_payments:
        row('PAYMENT HISTORY')
        row('Amount', 'Payment Date', 'Notes')
        for p in active_payments:
            row(f"${_sf(p['amount'], 0):.2f}", p.get('payment_date', ''), p.get('notes', ''))
        blank()
        row('BALANCE SUMMARY')
        row('Total Paid', f'${total_paid:.2f}')
        row('Amount This Week', f'${amount_week:.2f}')
        row('Prior Balance', f'${(prior_bal or 0):.2f}')
        row('Outstanding', f'${total_due:.2f}')

    return buf.getvalue().encode('utf-8-sig')  # BOM for Excel compat


def _build_contractor_summary_csv(period_label, grouped, grand_hours, grand_amount):
    """grouped = list of {contractor, rate, rows: [{customer, hours, amount}], total_hours, total_amount}"""
    buf = io.StringIO()
    w = csv.writer(buf)

    def row(*cols):
        w.writerow(cols)

    def blank():
        w.writerow([])

    row('CONTRACTOR PAYMENT SUMMARY')
    row('Period', period_label)
    row('Generated', datetime.now().strftime('%Y-%m-%d %H:%M'))
    blank()

    for g in grouped:
        row('CONTRACTOR', g['contractor'])
        rate = g.get('rate')
        row('Hourly Rate', f'${rate:,.2f}' if rate else 'Not set')
        row('Customer', 'Hours', 'Amount')
        for r in g['rows']:
            row(r['customer'], f"{r['hours']:g}", f"${r['amount']:,.2f}" if rate else '')
        row('Subtotal', f"{g['total_hours']:g}", f"${g['total_amount']:,.2f}" if rate else '')
        blank()

    row('GRAND TOTAL', f'{grand_hours:g}', f'${grand_amount:,.2f}')

    return buf.getvalue().encode('utf-8-sig')  # BOM for Excel compat




def _select_customer(cust):
    ss = st.session_state
    ss.customer_id = cust['id']
    ss.cust_name = cust['name']
    ss.cust_company = str(cust['company_project'] or '')
    ss.cust_rate = str(cust['default_rate'] or '')
    ss.cust_max_spend = str(cust['max_contract_spend'] or '')
    ss.cust_contract_note = str(cust['contract_note'] or '')
    ss.cust_footnote = str(cust['footnote'] or '')
    ss.week_rate = str(cust['default_rate'] or '')
    ss.week_max_override = str(cust['max_contract_spend'] or '')
    ss.week_prior_bal = '0'
    ss.activities_df = _acts_from_db(cust['id'])
    ss.payments_df = _pmts_from_db(cust['id'])
    ss.milestones_df = _milestones_from_db(cust['id'])
    ss.editor_v += 1
    ss.generated_bytes = None
    ss.generated_name = None
    ss.generated_pdf_bytes = None
    ss.generated_pdf_name = None


def _clear_for_new():
    ss = st.session_state
    ss.customer_id = None
    ss.cust_name = ''
    ss.cust_company = ''
    ss.cust_rate = ''
    ss.cust_max_spend = ''
    ss.cust_contract_note = ''
    ss.cust_footnote = ''
    ss.week_rate = ''
    ss.week_max_override = ''
    ss.week_prior_bal = '0'
    ss.activities_df = _blank_acts()
    ss.payments_df = _blank_pmts()
    ss.milestones_df = _blank_milestones()
    ss.editor_v += 1
    ss.generated_bytes = None
    ss.generated_name = None
    ss.generated_pdf_bytes = None
    ss.generated_pdf_name = None


def _select_contractor(contractor):
    ss = st.session_state
    ss.contractor_id = contractor['id']
    ss.contractor_name = contractor['name']
    ss.contractor_email = str(contractor.get('email') or '')
    ss.contractor_notes = str(contractor.get('notes') or '')
    ss.contractor_rate = str(contractor.get('hourly_rate') or '')
    ss.contractor_entries_df = _contractor_entries_from_db(contractor['id'])
    ss.editor_v += 1


def _clear_for_new_contractor():
    ss = st.session_state
    ss.contractor_id = None
    ss.contractor_name = ''
    ss.contractor_email = ''
    ss.contractor_notes = ''
    ss.contractor_rate = ''
    ss.contractor_entries_df = _blank_contractor_entries()
    ss.editor_v += 1


def _init():
    ss = st.session_state
    if 'app_init' in ss:
        return
    ss.app_init = True
    ss.customer_id = None
    ss.editor_v = 0
    ss.status = 'Ready.'
    ss.status_type = 'info'
    ss.generated_bytes = None
    ss.generated_name = None
    ss.generated_pdf_bytes = None
    ss.generated_pdf_name = None
    ss.activities_df = _blank_acts()
    ss.payments_df = _blank_pmts()
    ss.milestones_df = _blank_milestones()
    # customer form widget keys
    ss.cust_name = ''
    ss.cust_company = ''
    ss.cust_rate = ''
    ss.cust_max_spend = ''
    ss.cust_contract_note = ''
    ss.cust_footnote = ''
    # week form widget keys
    ss.week_start = _current_week_monday()
    ss.week_number = 1
    ss.week_rate = ''
    ss.week_prior_bal = '0'
    ss.week_max_override = ''

    # contractor state
    ss.contractor_id = None
    ss.contractor_name = ''
    ss.contractor_email = ''
    ss.contractor_notes = ''
    ss.contractor_rate = ''
    ss.contractor_entries_df = _blank_contractor_entries()

    customers = db.all_customers()
    if customers:
        _select_customer(customers[0])

    contractors = db.all_contractors()
    if contractors:
        _select_contractor(contractors[0])


# ── action functions ──────────────────────────────────────────────────────────

def _do_save_customer():
    ss = st.session_state
    name = ss.cust_name.strip()
    if not name:
        ss.status = 'Error: Customer name is required.'
        ss.status_type = 'error'
        return False
    cid = db.upsert_customer(
        name,
        ss.cust_company.strip(),
        _sf(ss.cust_rate),
        _sf(ss.cust_max_spend),
        ss.cust_contract_note.strip(),
        ss.cust_footnote.strip(),
    )
    ss.customer_id = cid
    ss.status = f'Customer "{name}" saved.'
    ss.status_type = 'success'
    return True


def _do_delete_customer():
    ss = st.session_state
    if not ss.customer_id:
        return
    name = ss.cust_name
    db.delete_customer(ss.customer_id)
    _clear_for_new()
    customers = db.all_customers()
    if customers:
        _select_customer(customers[0])
    ss.status = f'Customer "{name}" deleted.'
    ss.status_type = 'info'


def _do_save_payments(payments_df):
    ss = st.session_state
    if not ss.customer_id:
        ss.status = 'Error: Save customer first before saving payments.'
        ss.status_type = 'error'
        return
    rows = []
    for _, r in payments_df.iterrows():
        amt = _sf(r.get('Amount'))
        if amt is None:
            continue
        rows.append({
            'amount': amt,
            'payment_date': _fmt_pay_date(r.get('Payment date', '')),
            'notes': str(r.get('Notes', '')).strip(),
        })
    db.replace_payments(ss.customer_id, rows)
    ss.payments_df = _pmts_from_db(ss.customer_id)
    ss.editor_v += 1
    ss.status = f'Saved {len(rows)} payment record(s).'
    ss.status_type = 'success'


def _do_save_milestones(milestones_df):
    ss = st.session_state
    if not ss.customer_id:
        ss.status = 'Error: Save customer first before saving milestones.'
        ss.status_type = 'error'
        return
    rows = []
    for _, r in milestones_df.iterrows():
        title = str(r.get('Milestone', '')).strip()
        if not title:
            continue
        pct = _sf(r.get('% Complete'), 0.0) or 0.0
        pct = max(0.0, min(100.0, pct))
        cat = str(r.get('Category', '') or '—').strip()
        rows.append({
            'title': title,
            'target_date': str(r.get('Target Date', '')).strip(),
            'percent_complete': pct,
            'status': str(r.get('Status', '') or 'Not Started').strip(),
            'notes': str(r.get('Notes', '')).strip(),
            'category': '' if cat == '—' else cat,
        })
    db.replace_milestones(ss.customer_id, rows)
    ss.milestones_df = _milestones_from_db(ss.customer_id)
    ss.editor_v += 1
    ss.status = f'Saved {len(rows)} milestone(s).'
    ss.status_type = 'success'


def _do_save_contractor():
    ss = st.session_state
    name = ss.contractor_name.strip()
    if not name:
        ss.status = 'Error: Contractor name is required.'
        ss.status_type = 'error'
        return False
    cid = db.upsert_contractor(name, ss.contractor_email.strip(), ss.contractor_notes.strip(),
                               _sf(ss.contractor_rate))
    ss.contractor_id = cid
    ss.status = f'Contractor "{name}" saved.'
    ss.status_type = 'success'
    return True


def _do_delete_contractor():
    ss = st.session_state
    if not ss.contractor_id:
        return
    name = ss.contractor_name
    db.delete_contractor(ss.contractor_id)
    _clear_for_new_contractor()
    contractors = db.all_contractors()
    if contractors:
        _select_contractor(contractors[0])
    ss.status = f'Contractor "{name}" deleted.'
    ss.status_type = 'info'


def _do_save_contractor_entries(entries_df, customer_name_to_id):
    ss = st.session_state
    if not ss.contractor_id:
        ss.status = 'Error: Save contractor first before logging contributions.'
        ss.status_type = 'error'
        return
    rows = []
    for _, r in entries_df.iterrows():
        cust_name = str(r.get('Customer', '')).strip()
        hours = _sf(r.get('Hours'))
        desc = str(r.get('Description', '')).strip()
        date_val = str(r.get('Date', '')).strip()
        if not cust_name and hours is None and not desc and not date_val:
            continue
        rows.append({
            'customer_id': customer_name_to_id.get(cust_name),
            'entry_date': date_val,
            'hours': hours or 0.0,
            'description': desc,
        })
    db.replace_contractor_entries(ss.contractor_id, rows)
    ss.contractor_entries_df = _contractor_entries_from_db(ss.contractor_id)
    ss.editor_v += 1
    ss.status = f'Saved {len(rows)} contribution entr{"y" if len(rows) == 1 else "ies"}.'
    ss.status_type = 'success'


def _do_generate(activities_df):
    ss = st.session_state
    if not ss.customer_id:
        ss.status = 'Error: Save customer first.'
        ss.status_type = 'error'
        return
    week_start_str = ss.week_start.strip()
    if not week_start_str:
        ss.status = 'Error: Week start date is required.'
        ss.status_type = 'error'
        return
    try:
        week_dt = _parse_date(week_start_str)
    except ValueError as e:
        ss.status = f'Error: {e}'
        ss.status_type = 'error'
        return

    rate = _sf(ss.week_rate) if ss.week_rate.strip() else _sf(ss.cust_rate)
    rate = rate or 0.0
    prior_bal = _sf(ss.week_prior_bal) or 0.0
    max_spend = _sf(ss.week_max_override) if ss.week_max_override.strip() else _sf(ss.cust_max_spend)
    week_num = ss.week_number

    activities = []
    for _, row in activities_df.iterrows():
        activities.append({
            'activity': str(row.get('Activity') or ''),
            'mon': _hours(row.get('Mon')),
            'tue': _hours(row.get('Tue')),
            'wed': _hours(row.get('Wed')),
            'thu': _hours(row.get('Thu')),
            'fri': _hours(row.get('Fri')),
            'output_description': str(row.get('Output description') or ''),
        })

    payments = db.get_payments(ss.customer_id)
    payments_snapshot = [
        {'amount': p['amount'], 'payment_date': p['payment_date'], 'notes': p['notes']}
        for p in payments
    ]

    csv_bytes = _build_csv(
        customer_name=ss.cust_name,
        company=ss.cust_company,
        week_dt=week_dt,
        week_number=week_num,
        rate=rate,
        prior_bal=prior_bal,
        max_spend=max_spend,
        contract_note=ss.cust_contract_note,
        footnote=ss.cust_footnote,
        activities=activities,
        payments=payments_snapshot,
    )

    safe_name = ''.join(
        c if c.isalnum() or c in (' ', '-', '_') else '_' for c in ss.cust_name
    ).strip() or 'Customer'
    file_name = f'{safe_name} - {week_dt.strftime("%Y")} {week_dt.strftime("%b")} Week {week_num} timesheet.csv'

    db.save_timesheet(
        customer_id=ss.customer_id,
        week_start=week_dt.strftime(WEEK_DATE_FORMAT),
        week_number=int(week_num),
        hourly_rate=rate,
        prior_balance=prior_bal,
        max_contract_spend_override=max_spend,
        contract_note=ss.cust_contract_note,
        footnote=ss.cust_footnote,
        activities=activities,
        payments_snapshot=payments_snapshot,
        file_name=file_name,
    )

    # PDF
    try:
        pdf_bytes = pdf_generator.generate_pdf(
            customer_name=ss.cust_name,
            company=ss.cust_company,
            week_dt=week_dt,
            week_number=week_num,
            rate=rate,
            prior_bal=prior_bal,
            max_spend=max_spend,
            contract_note=ss.cust_contract_note,
            footnote=ss.cust_footnote,
            activities=activities,
            payments=payments_snapshot,
            logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None,
        )
        ss.generated_pdf_bytes = pdf_bytes
        ss.generated_pdf_name = file_name.replace('.csv', '.pdf')
    except Exception as pdf_err:
        ss.generated_pdf_bytes = None
        ss.generated_pdf_name = None
        ss.status = f'Generated CSV (PDF error: {pdf_err})'
        ss.status_type = 'success'

    ss.generated_bytes = csv_bytes
    ss.generated_name = file_name
    if ss.generated_pdf_bytes:
        ss.status = f'Generated: {file_name} + PDF'
    ss.status_type = 'success'


# ── render ────────────────────────────────────────────────────────────────────

_init()
ss = st.session_state

# ── Sidebar: customer management ──────────────────────────────────────────────
with st.sidebar:
    st.title('Timesheet Builder')
    st.divider()

    customers = db.all_customers()
    cust_names = [c['name'] for c in customers]
    options = cust_names + ['+ New Customer']

    current_sel = ss.cust_name if ss.customer_id and ss.cust_name in cust_names else '+ New Customer'
    selected = st.selectbox('Customer', options=options, index=options.index(current_sel))

    # Detect and handle selection change
    if selected == '+ New Customer' and ss.customer_id is not None:
        _clear_for_new()
        st.rerun()
    elif selected != '+ New Customer':
        cust = next((c for c in customers if c['name'] == selected), None)
        if cust and cust['id'] != ss.customer_id:
            _select_customer(cust)
            st.rerun()

    st.divider()
    st.subheader('Customer Details')

    st.text_input('Name *', key='cust_name', placeholder='Full customer name')
    st.text_input('Company / Project', key='cust_company')

    sc1, sc2 = st.columns(2)
    with sc1:
        st.text_input('Hourly Rate ($)', key='cust_rate')
    with sc2:
        st.text_input('Max Contract ($)', key='cust_max_spend')

    st.text_input('Contract Note', key='cust_contract_note')
    st.text_input('Footnote', key='cust_footnote')

    sb1, sb2 = st.columns(2)
    with sb1:
        if st.button('Save Customer', use_container_width=True, type='primary'):
            if _do_save_customer():
                st.rerun()
    with sb2:
        if ss.customer_id and st.button('Delete', use_container_width=True):
            _do_delete_customer()
            st.rerun()

    st.divider()
    st.subheader('Brand Logo')
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=120)
        st.caption('logo.png — used in PDF exports')
    uploaded = st.file_uploader('Upload logo (PNG/JPG)', type=['png', 'jpg', 'jpeg'],
                                label_visibility='collapsed')
    if uploaded:
        with open(LOGO_PATH, 'wb') as _f:
            _f.write(uploaded.read())
        ss.status = 'Logo saved — will appear in next PDF.'
        ss.status_type = 'success'
        st.rerun()


# ── Main: tabs ────────────────────────────────────────────────────────────────
tab_sheet, tab_payments, tab_milestones, tab_contractors, tab_history = st.tabs(
    ['New Timesheet', 'Payments', 'Milestones', 'Contractors', 'History']
)

# ── Tab 1: New Timesheet ──────────────────────────────────────────────────────
with tab_sheet:
    st.subheader('Week Setup')
    wc1, wc2, wc3, wc4, wc5 = st.columns(5)
    with wc1:
        st.text_input('Week Start (YYYY-MM-DD)', key='week_start')
    with wc2:
        st.selectbox('Week Number', options=[1, 2, 3, 4], key='week_number')
    with wc3:
        st.text_input('Hourly Rate Override ($)', key='week_rate',
                      help='Leave blank to use customer default rate')
    with wc4:
        st.text_input('Prior Balance ($)', key='week_prior_bal')
    with wc5:
        st.text_input('Max Contract Override ($)', key='week_max_override',
                      help='Leave blank to use customer default')

    st.divider()
    st.subheader('Activities')

    activities_df = st.data_editor(
        ss.activities_df,
        use_container_width=True,
        hide_index=True,
        num_rows='fixed',
        column_config={
            'Activity': st.column_config.TextColumn('Activity', width='large'),
            'Mon': st.column_config.TextColumn('Mon', width='small'),
            'Tue': st.column_config.TextColumn('Tue', width='small'),
            'Wed': st.column_config.TextColumn('Wed', width='small'),
            'Thu': st.column_config.TextColumn('Thu', width='small'),
            'Fri': st.column_config.TextColumn('Fri', width='small'),
            'Output description': st.column_config.TextColumn('Output description', width='large'),
        },
        key=f'acts_editor_{ss.editor_v}',
    )

    st.divider()
    gc1, gc2, gc3 = st.columns([2, 2, 3])
    with gc1:
        if st.button('Generate CSV + PDF', type='primary', use_container_width=True):
            _do_generate(activities_df)
            st.rerun()

    if ss.generated_bytes and ss.generated_name:
        with gc2:
            st.download_button(
                label='⬇ CSV',
                data=ss.generated_bytes,
                file_name=ss.generated_name,
                mime='text/csv',
                use_container_width=True,
            )
        if ss.generated_pdf_bytes and ss.generated_pdf_name:
            with gc3:
                st.download_button(
                    label='⬇ PDF (BaoBunny)',
                    data=ss.generated_pdf_bytes,
                    file_name=ss.generated_pdf_name,
                    mime='application/pdf',
                    use_container_width=True,
                    type='primary',
                )

# ── Tab 2: Payments ───────────────────────────────────────────────────────────
with tab_payments:
    customer_label = ss.cust_name or 'No customer selected'
    st.subheader(f'Payment History — {customer_label}')
    st.caption('Add all payments received from this customer. These are included in every timesheet you generate.')

    payments_df = st.data_editor(
        ss.payments_df,
        use_container_width=True,
        hide_index=True,
        num_rows='dynamic',
        column_config={
            'Amount': st.column_config.TextColumn('Amount ($)', width='small'),
            'Payment date': st.column_config.TextColumn('Payment Date (e.g. Jan-26-2026)', width='medium'),
            'Notes': st.column_config.TextColumn('Notes', width='large'),
        },
        key=f'pmts_editor_{ss.editor_v}',
    )

    if st.button('Save Payments', type='primary'):
        _do_save_payments(payments_df)
        st.rerun()

# ── Tab: Milestones ────────────────────────────────────────────────────────────
with tab_milestones:
    if not ss.customer_id:
        st.info('Select or create a customer to track milestones.')
    else:
        st.subheader(f'Milestone Progress — {ss.cust_name}')
        st.caption('Track project milestones, target dates, and completion status for this customer.')
        st.caption('Category tags each milestone to an EB1A criterion or NIW prong for the progress charts below.')

        milestones_df = st.data_editor(
            ss.milestones_df,
            use_container_width=True,
            hide_index=True,
            num_rows='dynamic',
            column_config={
                'Milestone': st.column_config.TextColumn('Milestone', width='medium'),
                'Category': st.column_config.SelectboxColumn('Category', options=MILESTONE_CATEGORIES, width='medium'),
                'Target Date': st.column_config.TextColumn('Target Date (e.g. Jan-26-2026)', width='medium'),
                '% Complete': st.column_config.NumberColumn('% Complete', min_value=0, max_value=100, step=5, width='small'),
                'Status': st.column_config.SelectboxColumn('Status', options=MILESTONE_STATUSES, width='small'),
                'Notes': st.column_config.TextColumn('Notes', width='large'),
            },
            key=f'milestones_editor_{ss.editor_v}',
        )

        if st.button('Save Milestones', type='primary'):
            _do_save_milestones(milestones_df)
            st.rerun()

        st.divider()

        saved = db.get_milestones(ss.customer_id)
        active = [m for m in saved if (m.get('title') or '').strip()]

        if not active:
            st.info('No milestones saved yet — add rows above and click Save Milestones.')
        else:
            overall = sum(m.get('percent_complete', 0) or 0 for m in active) / len(active)
            done = sum(1 for m in active if (m.get('status') or '') == 'Complete')

            mc1, mc2, mc3 = st.columns(3)
            mc1.metric('Overall Progress', f'{overall:.0f}%')
            mc2.metric('Milestones Complete', f'{done} / {len(active)}')
            mc3.metric('In Progress', sum(1 for m in active if (m.get('status') or '') == 'In Progress'))

            st.subheader('Progress Table')
            progress_view = pd.DataFrame([{
                'Milestone': m['title'],
                'Category': m.get('category') or '—',
                'Target Date': m.get('target_date', ''),
                'Status': m.get('status', ''),
                'Progress': (m.get('percent_complete', 0) or 0) / 100.0,
            } for m in active])

            st.dataframe(
                progress_view,
                use_container_width=True,
                hide_index=True,
                column_config={
                    'Progress': st.column_config.ProgressColumn(
                        'Progress', min_value=0, max_value=1, format='%.0f%%'
                    ),
                },
            )

            st.subheader('Progress by Milestone')
            chart_df = pd.DataFrame({
                m['title']: [m.get('percent_complete', 0) or 0] for m in active
            }).T
            chart_df.columns = ['% Complete']
            st.bar_chart(chart_df, horizontal=True, height=max(200, 40 * len(active)))

            # ── NIW Prong Progress ───────────────────────────────────────────
            niw_tagged = [m for m in active if (m.get('category') or '') in NIW_PRONGS]
            if niw_tagged:
                st.subheader('NIW Prong Progress')
                st.caption('Average completion of milestones tagged to each NIW prong.')
                prong_avgs = {}
                for prong in NIW_PRONGS:
                    prong_ms = [m for m in niw_tagged if m.get('category') == prong]
                    if prong_ms:
                        prong_avgs[prong] = sum(m.get('percent_complete', 0) or 0 for m in prong_ms) / len(prong_ms)
                if prong_avgs:
                    prong_df = pd.DataFrame({'% Complete': prong_avgs})
                    st.bar_chart(prong_df, horizontal=True, height=max(150, 60 * len(prong_avgs)))

            # ── EB1A Criteria Coverage ───────────────────────────────────────
            eb1a_tagged = [m for m in active if (m.get('category') or '') in EB1A_CRITERIA]
            if eb1a_tagged:
                st.subheader('EB1A Criteria Coverage')
                st.caption('EB1A petitions generally need evidence across at least 3 of the 10 criteria — average completion shown per criterion with at least one milestone.')
                crit_avgs = {}
                for crit in EB1A_CRITERIA:
                    crit_ms = [m for m in eb1a_tagged if m.get('category') == crit]
                    if crit_ms:
                        crit_avgs[crit] = sum(m.get('percent_complete', 0) or 0 for m in crit_ms) / len(crit_ms)
                if crit_avgs:
                    st.metric('Criteria Covered', f'{len(crit_avgs)} / 10')
                    crit_df = pd.DataFrame({'% Complete': crit_avgs})
                    st.bar_chart(crit_df, horizontal=True, height=max(200, 40 * len(crit_avgs)))

            # ── AI Assessment ─────────────────────────────────────────────────
            st.subheader('AI Assessment')
            ai_providers = ai_estimator.available_providers()
            tagged_categories = sorted(set(
                m.get('category') for m in active
                if (m.get('category') or '') in (NIW_PRONGS + EB1A_CRITERIA)
            ))

            if not ai_providers:
                st.info(
                    'No AI provider configured. Add `anthropic_api_key` and/or `openai_api_key` '
                    'to your Streamlit secrets to enable AI-powered strength/gap assessments '
                    'per category. See ai_estimator.py for the exact secrets format.'
                )
            elif not tagged_categories:
                st.info('Tag at least one milestone with an EB1A criterion or NIW prong category above to enable AI assessment.')
            else:
                st.caption('AI reads the milestones tagged to a category and estimates overall strength, gaps, and next steps. Nothing is auto-applied to your data — review before acting on it.')
                ac1, ac2 = st.columns(2)
                with ac1:
                    ai_category = st.selectbox('Category to assess', options=tagged_categories, key='ai_category_select')
                with ac2:
                    ai_provider = st.selectbox('AI Provider', options=ai_providers, key='ai_provider_select')

                if st.button('Get AI Assessment'):
                    cat_milestones = [m for m in active if m.get('category') == ai_category]
                    with st.spinner(f'Assessing {ai_category} with {ai_provider}...'):
                        try:
                            result = ai_estimator.estimate_category(
                                ai_provider, ai_category, cat_milestones, ss.cust_name
                            )
                            ss.ai_assessment_result = result
                            ss.ai_assessment_category = ai_category
                            ss.status = 'AI assessment complete.'
                            ss.status_type = 'success'
                        except Exception as e:
                            ss.ai_assessment_result = None
                            st.error(f'AI assessment failed: {e}')

                if ss.get('ai_assessment_result') and ss.get('ai_assessment_category') == ai_category:
                    result = ss.ai_assessment_result
                    st.metric('AI-Estimated Strength', f"{result.get('strength_percent', 0)}%")
                    st.write(result.get('summary', ''))
                    gaps = result.get('gaps') or []
                    suggestions = result.get('suggestions') or []
                    gcol, scol = st.columns(2)
                    with gcol:
                        st.markdown('**Gaps**')
                        for g in gaps:
                            st.markdown(f'- {g}')
                    with scol:
                        st.markdown('**Suggestions**')
                        for s in suggestions:
                            st.markdown(f'- {s}')

# ── Tab: Contractors ─────────────────────────────────────────────────────────
with tab_contractors:
    st.subheader('Contractors')
    st.caption('Manage contractors and log the time they contribute to each customer.')

    contractors = db.all_contractors()
    contractor_names = [c['name'] for c in contractors]
    c_options = contractor_names + ['+ New Contractor']

    cc1, cc2 = st.columns([2, 1])
    with cc1:
        current_c_sel = (ss.contractor_name if ss.contractor_id and ss.contractor_name in contractor_names
                         else '+ New Contractor')
        c_selected = st.selectbox('Contractor', options=c_options, index=c_options.index(current_c_sel))

    if c_selected == '+ New Contractor' and ss.contractor_id is not None:
        _clear_for_new_contractor()
        st.rerun()
    elif c_selected != '+ New Contractor':
        picked = next((c for c in contractors if c['name'] == c_selected), None)
        if picked and picked['id'] != ss.contractor_id:
            _select_contractor(picked)
            st.rerun()

    with st.expander('Contractor Details', expanded=not ss.contractor_id):
        st.text_input('Name *', key='contractor_name', placeholder='Contractor full name')
        st.text_input('Email', key='contractor_email')
        st.text_input('Hourly Rate ($)', key='contractor_rate')
        st.text_area('Notes', key='contractor_notes', height=80)

        dc1, dc2 = st.columns(2)
        with dc1:
            if st.button('Save Contractor', use_container_width=True, type='primary'):
                if _do_save_contractor():
                    st.rerun()
        with dc2:
            if ss.contractor_id and st.button('Delete Contractor', use_container_width=True):
                _do_delete_contractor()
                st.rerun()

    st.divider()

    if not ss.contractor_id:
        st.info('Select or create a contractor above to log their contributions.')
    else:
        st.subheader(f'Contributions — {ss.contractor_name}')
        st.caption('Log hours this contractor spent serving each customer.')

        customer_names_list = [c['name'] for c in db.all_customers()]
        customer_name_to_id = {c['name']: c['id'] for c in db.all_customers()}

        entries_df = st.data_editor(
            ss.contractor_entries_df,
            use_container_width=True,
            hide_index=True,
            num_rows='dynamic',
            column_config={
                'Date': st.column_config.TextColumn('Date (e.g. Jan-26-2026)', width='medium'),
                'Customer': st.column_config.SelectboxColumn('Customer', options=customer_names_list, width='medium'),
                'Hours': st.column_config.TextColumn('Hours', width='small'),
                'Description': st.column_config.TextColumn('Description', width='large'),
            },
            key=f'contractor_entries_editor_{ss.editor_v}',
        )

        if st.button('Save Contributions', type='primary'):
            _do_save_contractor_entries(entries_df, customer_name_to_id)
            st.rerun()

        st.divider()

        saved_entries = db.get_contractor_entries(ss.contractor_id)
        active_entries = [e for e in saved_entries if (e.get('description') or e.get('hours'))]

        if not active_entries:
            st.info('No contributions logged yet — add rows above and click Save Contributions.')
        else:
            total_hours = sum(e.get('hours', 0) or 0 for e in active_entries)
            st.metric('Total Hours Logged', f'{total_hours:g}')

            st.subheader('By Customer')
            by_customer = {}
            for e in active_entries:
                key = e.get('customer_name') or 'Unassigned'
                by_customer[key] = by_customer.get(key, 0) + (e.get('hours', 0) or 0)
            hours_chart = pd.DataFrame({'Hours': by_customer})
            st.bar_chart(hours_chart, horizontal=True, height=max(200, 40 * len(by_customer)))

    st.divider()
    st.subheader('Payment Summary')
    st.caption('Filter by date range and see totals owed per contractor, across all contractors.')

    today = datetime.now().date()
    month_start = today.replace(day=1)
    fc1, fc2 = st.columns(2)
    with fc1:
        range_start = st.date_input('Start Date', value=month_start, key='summary_start_date')
    with fc2:
        range_end = st.date_input('End Date', value=today, key='summary_end_date')

    all_entries = db.all_contractor_entries()
    in_range = []
    skipped_unparseable = 0
    for e in all_entries:
        if not (e.get('description') or e.get('hours')):
            continue
        dt = _safe_parse_date(e.get('entry_date'))
        if dt is None:
            skipped_unparseable += 1
            continue
        if range_start <= dt.date() <= range_end:
            in_range.append(e)

    if skipped_unparseable:
        st.caption(f'⚠ {skipped_unparseable} entr{"y" if skipped_unparseable == 1 else "ies"} skipped (unparseable or missing date).')

    if not in_range:
        st.info('No contractor entries found in this date range.')
    else:
        by_contractor = {}
        for e in in_range:
            key = e.get('contractor_name') or 'Unknown'
            by_contractor.setdefault(key, {'rate': e.get('contractor_rate'), 'rows': {}})
            cust_key = e.get('customer_name') or 'Unassigned'
            by_contractor[key]['rows'][cust_key] = by_contractor[key]['rows'].get(cust_key, 0) + (e.get('hours', 0) or 0)

        grouped = []
        grand_hours = 0.0
        grand_amount = 0.0
        for contractor_name, info in sorted(by_contractor.items()):
            rate = info['rate']
            rows = [{'customer': c, 'hours': h, 'amount': (h * rate) if rate else 0.0}
                    for c, h in sorted(info['rows'].items())]
            total_hours = sum(r['hours'] for r in rows)
            total_amount = sum(r['amount'] for r in rows) if rate else 0.0
            grouped.append({
                'contractor': contractor_name,
                'rate': rate,
                'rows': rows,
                'total_hours': total_hours,
                'total_amount': total_amount,
            })
            grand_hours += total_hours
            grand_amount += total_amount

        sc1, sc2 = st.columns(2)
        sc1.metric('Total Hours (period)', f'{grand_hours:g}')
        sc2.metric('Total Amount Owed', f'${grand_amount:,.2f}')

        summary_rows = []
        for g in grouped:
            for r in g['rows']:
                summary_rows.append({
                    'Contractor': g['contractor'],
                    'Customer': r['customer'],
                    'Hours': r['hours'],
                    'Rate': f"${g['rate']:,.2f}" if g['rate'] else 'Not set',
                    'Amount': f"${r['amount']:,.2f}" if g['rate'] else '—',
                })
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

        period_label = f"{range_start.strftime('%b %d, %Y')} – {range_end.strftime('%b %d, %Y')}"
        summary_csv = _build_contractor_summary_csv(period_label, grouped, grand_hours, grand_amount)
        exc1, exc2 = st.columns(2)
        with exc1:
            st.download_button(
                '⬇ CSV', data=summary_csv,
                file_name=f'contractor_payment_summary_{range_start}_{range_end}.csv',
                mime='text/csv', use_container_width=True,
            )
        with exc2:
            try:
                summary_pdf = pdf_generator.generate_contractor_summary_pdf(
                    period_label, grouped, grand_hours, grand_amount,
                    logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None,
                )
                st.download_button(
                    '⬇ PDF', data=summary_pdf,
                    file_name=f'contractor_payment_summary_{range_start}_{range_end}.pdf',
                    mime='application/pdf', use_container_width=True, type='primary',
                )
            except Exception as e:
                st.error(f'Could not generate PDF: {e}')

# ── Tab: History ────────────────────────────────────────────────────────────
with tab_history:
    customer_label = ss.cust_name or 'No customer selected'
    st.subheader(f'Timesheet History — {customer_label}')

    if not ss.customer_id:
        st.info('Select or create a customer to view their history.')
    else:
        timesheets = db.list_timesheets(ss.customer_id)
        if not timesheets:
            st.info('No timesheets generated yet for this customer.')
        else:
            for ts in timesheets:
                label = f"Week {ts['week_number']}  ·  {ts['week_start']}  ·  Generated {ts['created_at'][:10]}"
                with st.expander(label):
                    full_ts = db.get_timesheet(ts['id'])
                    try:
                        week_dt = _parse_date(full_ts['week_start'])
                        _kw = dict(
                            customer_name=ss.cust_name,
                            company=ss.cust_company,
                            week_dt=week_dt,
                            week_number=full_ts['week_number'],
                            rate=full_ts['hourly_rate'] or 0.0,
                            prior_bal=full_ts['prior_balance'] or 0.0,
                            max_spend=full_ts['max_contract_spend_override'],
                            contract_note=full_ts['contract_note'] or '',
                            footnote=full_ts['footnote'] or '',
                            activities=full_ts['activities'],
                            payments=full_ts['payments_snapshot'],
                        )
                        csv_bytes = _build_csv(**_kw)
                        fname_base = ts['file_name'] or 'timesheet.csv'
                        hc1, hc2 = st.columns(2)
                        with hc1:
                            st.download_button(
                                label=f'⬇ CSV',
                                data=csv_bytes,
                                file_name=fname_base,
                                mime='text/csv',
                                key=f'dl_csv_{ts["id"]}',
                                use_container_width=True,
                            )
                        with hc2:
                            try:
                                pdf_bytes = pdf_generator.generate_pdf(
                                    **_kw,
                                    logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None,
                                )
                                st.download_button(
                                    label='⬇ PDF',
                                    data=pdf_bytes,
                                    file_name=fname_base.replace('.csv', '.pdf'),
                                    mime='application/pdf',
                                    key=f'dl_pdf_{ts["id"]}',
                                    use_container_width=True,
                                    type='primary',
                                )
                            except Exception:
                                pass
                    except Exception as e:
                        st.error(f'Could not regenerate: {e}')

                    acts = full_ts['activities']
                    if any(a.get('activity') for a in acts):
                        acts_display = pd.DataFrame([{
                            'Activity': a.get('activity', ''),
                            'Mon': a.get('mon', ''), 'Tue': a.get('tue', ''),
                            'Wed': a.get('wed', ''), 'Thu': a.get('thu', ''),
                            'Fri': a.get('fri', ''),
                            'Total': (_hours(a.get('mon')) + _hours(a.get('tue')) +
                                      _hours(a.get('wed')) + _hours(a.get('thu')) +
                                      _hours(a.get('fri'))),
                            'Output': a.get('output_description', ''),
                        } for a in acts])
                        st.dataframe(acts_display, use_container_width=True, hide_index=True)

# ── Status bar ────────────────────────────────────────────────────────────────
st.divider()
if ss.status_type == 'error':
    st.error(ss.status)
elif ss.status_type == 'success':
    st.success(ss.status)
else:
    st.info(ss.status)
