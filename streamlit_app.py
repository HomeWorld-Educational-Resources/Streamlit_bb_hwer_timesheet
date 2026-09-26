import csv
import io
import zipfile
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

CAREER_CATEGORIES = ['Milestone', 'Goal', 'Strategic Plan', 'Proposed Activity', 'Skill/Experience Gained']
MAX_CAREER_ROWS = 20

IMPACT_CATEGORIES = ['Media Coverage', 'Paper Citations', 'GitHub Stars', 'Downloads', 'White Papers', 'Honors', 'Other']
MAX_IMPACT_ROWS = 20

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


def _blank_career_items():
    return pd.DataFrame([
        {'Category': 'Milestone', 'Item': '', 'Target Date': '', '% Complete': 0, 'Status': 'Not Started', 'Notes': ''}
        for _ in range(MAX_CAREER_ROWS)
    ])


def _career_items_from_db(customer_id):
    rows = db.get_career_items(customer_id)
    result = [
        {
            'Category': it.get('category') or 'Milestone',
            'Item': it.get('title') or '',
            'Target Date': it.get('target_date') or '',
            '% Complete': it.get('percent_complete', 0) or 0,
            'Status': it.get('status') or 'Not Started',
            'Notes': it.get('notes') or '',
        }
        for it in rows
    ]
    while len(result) < MAX_CAREER_ROWS:
        result.append({'Category': 'Milestone', 'Item': '', 'Target Date': '', '% Complete': 0, 'Status': 'Not Started', 'Notes': ''})
    return pd.DataFrame(result[:MAX_CAREER_ROWS])


def _blank_impact_items():
    return pd.DataFrame([
        {'Category': 'Media Coverage', 'Item': '', 'Metric/Value': '', 'BB Supported': False, 'Date': '', 'Notes': ''}
        for _ in range(MAX_IMPACT_ROWS)
    ])


def _impact_items_from_db(customer_id):
    rows = db.get_impact_items(customer_id)
    result = [
        {
            'Category': it.get('category') or 'Media Coverage',
            'Item': it.get('title') or '',
            'Metric/Value': it.get('metric_value') or '',
            'BB Supported': bool(it.get('bb_supported', False)),
            'Date': it.get('item_date') or '',
            'Notes': it.get('notes') or '',
        }
        for it in rows
    ]
    while len(result) < MAX_IMPACT_ROWS:
        result.append({'Category': 'Media Coverage', 'Item': '', 'Metric/Value': '', 'BB Supported': False, 'Date': '', 'Notes': ''})
    return pd.DataFrame(result[:MAX_IMPACT_ROWS])


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


def _build_ai_bundle_prompt_text(customer_name, milestones):
    tagged = [m for m in milestones if (m.get('category') or '') in
             (NIW_PRONGS + EB1A_CRITERIA)]
    categories_present = sorted(set(m.get('category') for m in tagged))

    lines = [
        'You are assisting with an immigration petition — EB1A (extraordinary ability) and/or '
        'NIW (National Interest Waiver). Be a rigorous, honest evaluator; do not inflate the '
        'assessment and do not invent evidence that is not listed in milestones.csv.',
        '',
        f'Client: {customer_name}',
        '',
        'milestones.csv (attached) lists this client\'s evidence/work items. Each row has: '
        'Category, Milestone, Target Date, % Complete, Status, Notes.',
        '',
        'For EACH category below that has at least one milestone in the CSV, provide:',
        '  1. An estimated strength score (0-100%) based only on the listed evidence.',
        '  2. A short (2-3 sentence) assessment of current strength.',
        '  3. Specific gaps or weaknesses.',
        '  4. Concrete, actionable next steps to strengthen that category.',
        '',
        'Categories present in this client\'s data:',
    ]
    for cat in categories_present:
        definition = ai_estimator.CATEGORY_DEFINITIONS.get(cat, '')
        lines.append(f'  - {cat}: {definition}')
    if not categories_present:
        lines.append('  (No milestones are tagged to an EB1A/NIW category yet — assess general progress instead.)')
    lines += [
        '',
        'Present your response as one section per category, in the order listed above, '
        'each with the four numbered items.',
    ]
    return '\n'.join(lines)


def _build_bundle_zip(prompt_text, info_lines, csv_filename, csv_header, csv_rows):
    """Shared ZIP writer: README_PROMPT.txt + customer_info.txt + one data CSV."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('README_PROMPT.txt', prompt_text)
        zf.writestr('customer_info.txt', '\n'.join(info_lines))
        csv_buf = io.StringIO()
        w = csv.writer(csv_buf)
        w.writerow(csv_header)
        for row in csv_rows:
            w.writerow(row)
        zf.writestr(csv_filename, csv_buf.getvalue())
    buf.seek(0)
    return buf.getvalue()


def _build_ai_bundle_zip(customer, milestones):
    info_lines = [
        f"Customer: {customer.get('name', '')}",
        f"Company/Project: {customer.get('company_project', '')}",
        f"Contract Note: {customer.get('contract_note', '')}",
        f"Footnote: {customer.get('footnote', '')}",
        f"Bundle Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    csv_rows = [[
        m.get('category') or '', m.get('title') or '', m.get('target_date') or '',
        m.get('percent_complete', 0) or 0, m.get('status') or '', m.get('notes') or '',
    ] for m in milestones]
    return _build_bundle_zip(
        _build_ai_bundle_prompt_text(customer.get('name', ''), milestones),
        info_lines, 'milestones.csv',
        ['Category', 'Milestone', 'Target Date', '% Complete', 'Status', 'Notes'],
        csv_rows,
    )


def _build_career_bundle_prompt_text(customer_name, target_role, items):
    categories_present = sorted(set(it.get('category') for it in items if it.get('category')))
    lines = [
        "You are a career coach assessing a client's progress toward a specific target role. "
        "Be a rigorous, honest evaluator; do not inflate the assessment and do not invent "
        "evidence that is not listed in career_items.csv.",
        '',
        f'Client: {customer_name}',
        f'Target role (dream job/position/promotion): {target_role or "not specified"}',
        '',
        "career_items.csv (attached) lists this client's career items. Each row has: "
        "Category, Item, Target Date, % Complete, Status, Notes.",
        '',
        'For EACH category below that has at least one item in the CSV, provide:',
        '  1. An estimated strength score (0-100%) based only on the listed items.',
        '  2. A short (2-3 sentence) assessment of current progress.',
        '  3. Specific gaps or weaknesses relative to the target role.',
        '  4. Concrete, actionable next steps.',
        '',
        "Categories present in this client's data:",
    ]
    for cat in categories_present:
        lines.append(f'  - {cat}: {ai_estimator.CAREER_CATEGORY_DEFINITIONS.get(cat, "")}')
    if not categories_present:
        lines.append('  (No career items logged yet — assess general readiness instead.)')
    lines += [
        '',
        'Present your response as one section per category, in the order listed above, '
        'each with the four numbered items, then one overall paragraph on readiness for the target role.',
    ]
    return '\n'.join(lines)


def _build_career_bundle_zip(customer, items):
    info_lines = [
        f"Customer: {customer.get('name', '')}",
        f"Target Role: {customer.get('career_target', '')}",
        f"Company/Project: {customer.get('company_project', '')}",
        f"Bundle Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    csv_rows = [[
        it.get('category') or '', it.get('title') or '', it.get('target_date') or '',
        it.get('percent_complete', 0) or 0, it.get('status') or '', it.get('notes') or '',
    ] for it in items]
    return _build_bundle_zip(
        _build_career_bundle_prompt_text(customer.get('name', ''), customer.get('career_target', ''), items),
        info_lines, 'career_items.csv',
        ['Category', 'Item', 'Target Date', '% Complete', 'Status', 'Notes'],
        csv_rows,
    )


def _build_impact_bundle_prompt_text(customer_name, items):
    categories_present = sorted(set(it.get('category') for it in items if it.get('category')))
    lines = [
        "You are assisting in assessing the strength of a client's real-world impact evidence. "
        "Be a rigorous, honest evaluator; do not inflate the assessment and do not invent "
        "evidence that is not listed in impact_items.csv. Note which items are 'BB Supported' "
        "(BaoBunny directly helped produce them) versus pre-existing achievements.",
        '',
        f'Client: {customer_name}',
        '',
        "impact_items.csv (attached) lists this client's impact achievements. Each row has: "
        "Category, Item, Metric/Value, BB Supported, Date, Notes.",
        '',
        'For EACH category below that has at least one item in the CSV, provide:',
        '  1. An estimated strength score (0-100%) based only on the listed items.',
        '  2. A short (2-3 sentence) assessment of current strength.',
        '  3. Specific gaps or weaknesses.',
        '  4. Concrete, actionable next steps.',
        '',
        "Categories present in this client's data:",
    ]
    for cat in categories_present:
        lines.append(f'  - {cat}: {ai_estimator.IMPACT_CATEGORY_DEFINITIONS.get(cat, "")}')
    if not categories_present:
        lines.append('  (No impact items logged yet — assess general standing instead.)')
    lines += [
        '',
        'Present your response as one section per category, in the order listed above, '
        'each with the four numbered items, then a one-line count of how many items are BB Supported.',
    ]
    return '\n'.join(lines)


def _build_impact_bundle_zip(customer, items):
    info_lines = [
        f"Customer: {customer.get('name', '')}",
        f"Company/Project: {customer.get('company_project', '')}",
        f"Bundle Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    csv_rows = [[
        it.get('category') or '', it.get('title') or '', it.get('metric_value') or '',
        'Yes' if it.get('bb_supported') else 'No', it.get('item_date') or '', it.get('notes') or '',
    ] for it in items]
    return _build_bundle_zip(
        _build_impact_bundle_prompt_text(customer.get('name', ''), items),
        info_lines, 'impact_items.csv',
        ['Category', 'Item', 'Metric/Value', 'BB Supported', 'Date', 'Notes'],
        csv_rows,
    )


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
    ss.career_items_df = _career_items_from_db(cust['id'])
    ss.impact_items_df = _impact_items_from_db(cust['id'])
    ss.career_target = str(cust.get('career_target') or '')
    ss.resume_doc_url = str(cust.get('resume_doc_url') or '')
    ss.resume_folder_url = str(cust.get('resume_folder_url') or '')
    ss.cv_doc_url = str(cust.get('cv_doc_url') or '')
    ss.cv_folder_url = str(cust.get('cv_folder_url') or '')
    ss.petition_doc_url = str(cust.get('petition_doc_url') or '')
    ss.petition_folder_url = str(cust.get('petition_folder_url') or '')
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
    ss.career_items_df = _blank_career_items()
    ss.impact_items_df = _blank_impact_items()
    ss.career_target = ''
    ss.resume_doc_url = ''
    ss.resume_folder_url = ''
    ss.cv_doc_url = ''
    ss.cv_folder_url = ''
    ss.petition_doc_url = ''
    ss.petition_folder_url = ''
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
    ss.career_items_df = _blank_career_items()
    ss.impact_items_df = _blank_impact_items()
    ss.career_target = ''
    ss.resume_doc_url = ''
    ss.resume_folder_url = ''
    ss.cv_doc_url = ''
    ss.cv_folder_url = ''
    ss.petition_doc_url = ''
    ss.petition_folder_url = ''
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


def _do_save_career_items(career_df):
    ss = st.session_state
    if not ss.customer_id:
        ss.status = 'Error: Save customer first before saving career items.'
        ss.status_type = 'error'
        return
    rows = []
    for _, r in career_df.iterrows():
        title = str(r.get('Item', '')).strip()
        if not title:
            continue
        pct = _sf(r.get('% Complete'), 0.0) or 0.0
        pct = max(0.0, min(100.0, pct))
        rows.append({
            'category': str(r.get('Category', '') or 'Milestone').strip(),
            'title': title,
            'target_date': str(r.get('Target Date', '')).strip(),
            'percent_complete': pct,
            'status': str(r.get('Status', '') or 'Not Started').strip(),
            'notes': str(r.get('Notes', '')).strip(),
        })
    db.replace_career_items(ss.customer_id, rows)
    ss.career_items_df = _career_items_from_db(ss.customer_id)
    ss.editor_v += 1
    ss.status = f'Saved {len(rows)} career item(s).'
    ss.status_type = 'success'


def _do_save_impact_items(impact_df):
    ss = st.session_state
    if not ss.customer_id:
        ss.status = 'Error: Save customer first before saving impact items.'
        ss.status_type = 'error'
        return
    rows = []
    for _, r in impact_df.iterrows():
        title = str(r.get('Item', '')).strip()
        if not title:
            continue
        rows.append({
            'category': str(r.get('Category', '') or 'Other').strip(),
            'title': title,
            'metric_value': str(r.get('Metric/Value', '')).strip(),
            'bb_supported': bool(r.get('BB Supported', False)),
            'item_date': str(r.get('Date', '')).strip(),
            'notes': str(r.get('Notes', '')).strip(),
        })
    db.replace_impact_items(ss.customer_id, rows)
    ss.impact_items_df = _impact_items_from_db(ss.customer_id)
    ss.editor_v += 1
    ss.status = f'Saved {len(rows)} impact item(s).'
    ss.status_type = 'success'


def _do_save_customer_links(**fields):
    ss = st.session_state
    if not ss.customer_id:
        ss.status = 'Error: Save customer first before saving document links.'
        ss.status_type = 'error'
        return
    db.update_customer_links(ss.customer_id, **fields)
    ss.status = 'Links saved.'
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
tab_sheet, tab_payments, tab_career, tab_impact, tab_immigration, tab_contractors, tab_history = st.tabs(
    ['New Timesheet', 'Payments', 'Career', 'Impact', 'Immigration', 'Contractors', 'History']
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

# ── Tab: Career ───────────────────────────────────────────────────────────────
with tab_career:
    if not ss.customer_id:
        st.info('Select or create a customer to track their career growth.')
    else:
        st.subheader(f'Career — {ss.cust_name}')
        st.caption('Track milestones, goals, strategic plan, proposed activities, and skills/experience gained toward the target role.')

        st.text_input('Target Role (dream job / position / promotion)', key='career_target')

        with st.expander('Documents (Google Drive)', expanded=not (ss.resume_doc_url or ss.resume_folder_url)):
            st.text_input('Resume (Drive link)', key='resume_doc_url')
            st.text_input('Supporting Docs Folder (Drive link)', key='resume_folder_url')
            if st.button('Save Links', key='save_career_links'):
                _do_save_customer_links(
                    career_target=ss.career_target.strip(),
                    resume_doc_url=ss.resume_doc_url.strip(),
                    resume_folder_url=ss.resume_folder_url.strip(),
                )
                st.rerun()
            lcol1, lcol2 = st.columns(2)
            with lcol1:
                if ss.resume_doc_url:
                    st.link_button('Open Resume ↗', ss.resume_doc_url, use_container_width=True)
            with lcol2:
                if ss.resume_folder_url:
                    st.link_button('Open Supporting Docs Folder ↗', ss.resume_folder_url, use_container_width=True)

        career_df = st.data_editor(
            ss.career_items_df,
            use_container_width=True,
            hide_index=True,
            num_rows='dynamic',
            column_config={
                'Category': st.column_config.SelectboxColumn('Category', options=CAREER_CATEGORIES, width='medium'),
                'Item': st.column_config.TextColumn('Item', width='large'),
                'Target Date': st.column_config.TextColumn('Target Date (e.g. Jan-26-2026)', width='medium'),
                '% Complete': st.column_config.NumberColumn('% Complete', min_value=0, max_value=100, step=5, width='small'),
                'Status': st.column_config.SelectboxColumn('Status', options=MILESTONE_STATUSES, width='small'),
                'Notes': st.column_config.TextColumn('Notes', width='large'),
            },
            key=f'career_editor_{ss.editor_v}',
        )

        if st.button('Save Career Items', type='primary'):
            _do_save_career_items(career_df)
            st.rerun()

        st.divider()

        saved_career = db.get_career_items(ss.customer_id)
        active_career = [it for it in saved_career if (it.get('title') or '').strip()]

        if not active_career:
            st.info('No career items saved yet — add rows above and click Save Career Items.')
        else:
            overall = sum(it.get('percent_complete', 0) or 0 for it in active_career) / len(active_career)
            st.metric('Overall Progress', f'{overall:.0f}%')

            st.subheader('Progress Table')
            progress_view = pd.DataFrame([{
                'Item': it['title'],
                'Category': it.get('category') or '',
                'Target Date': it.get('target_date', ''),
                'Status': it.get('status', ''),
                'Progress': (it.get('percent_complete', 0) or 0) / 100.0,
            } for it in active_career])
            st.dataframe(
                progress_view, use_container_width=True, hide_index=True,
                column_config={'Progress': st.column_config.ProgressColumn('Progress', min_value=0, max_value=1, format='%.0f%%')},
            )

            st.subheader('Progress by Category')
            cat_avgs = {}
            for cat in CAREER_CATEGORIES:
                cat_items = [it for it in active_career if it.get('category') == cat]
                if cat_items:
                    cat_avgs[cat] = sum(it.get('percent_complete', 0) or 0 for it in cat_items) / len(cat_items)
            if cat_avgs:
                st.bar_chart(pd.DataFrame({'% Complete': cat_avgs}), horizontal=True, height=max(150, 40 * len(cat_avgs)))

            # ── AI Estimation ─────────────────────────────────────────────────
            st.subheader('AI Estimation')
            career_tagged = sorted(set(it.get('category') for it in active_career if it.get('category')))

            st.markdown('**Option 1 — Assess in-app (OpenAI)**')
            if not ai_estimator.is_configured():
                st.info('No OpenAI key configured. Add `openai_api_key` to your Streamlit secrets to enable in-app assessments.')
            else:
                st.caption('Reads the items in a category and estimates overall strength toward the target role, gaps, and next steps.')
                career_ai_category = st.selectbox('Category to assess', options=career_tagged, key='career_ai_category_select')

                if st.button('Get AI Assessment', key='career_ai_button'):
                    cat_items = [it for it in active_career if it.get('category') == career_ai_category]
                    with st.spinner(f'Assessing {career_ai_category}...'):
                        try:
                            result = ai_estimator.estimate_career_category(
                                career_ai_category, cat_items, ss.cust_name, ss.career_target
                            )
                            ss.career_ai_result = result
                            ss.career_ai_category_done = career_ai_category
                        except Exception as e:
                            ss.career_ai_result = None
                            st.error(f'AI assessment failed: {e}')

                if ss.get('career_ai_result') and ss.get('career_ai_category_done') == career_ai_category:
                    result = ss.career_ai_result
                    st.metric('AI-Estimated Strength', f"{result.get('strength_percent', 0)}%")
                    st.write(result.get('summary', ''))
                    gcol, scol = st.columns(2)
                    with gcol:
                        st.markdown('**Gaps**')
                        for g in (result.get('gaps') or []):
                            st.markdown(f'- {g}')
                    with scol:
                        st.markdown('**Suggestions**')
                        for s in (result.get('suggestions') or []):
                            st.markdown(f'- {s}')

            st.divider()
            st.markdown('**Option 2 — Download a bundle for manual upload (ChatGPT, Claude.ai, etc.)**')
            st.caption('No API key needed. Regenerated fresh from the currently saved career items every time you download.')
            career_bundle_zip = _build_career_bundle_zip(
                {'name': ss.cust_name, 'career_target': ss.career_target, 'company_project': ss.cust_company},
                active_career,
            )
            safe_name = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in (ss.cust_name or 'customer')).strip().replace(' ', '_')
            st.download_button(
                '⬇ Download Career Bundle (ZIP)', data=career_bundle_zip,
                file_name=f'{safe_name}_career_bundle.zip', mime='application/zip',
            )

# ── Tab: Impact ───────────────────────────────────────────────────────────────
with tab_impact:
    if not ss.customer_id:
        st.info('Select or create a customer to track their impact metrics.')
    else:
        st.subheader(f'Impact — {ss.cust_name}')
        st.caption('Track real-world impact: media coverage, citations, GitHub stars/downloads, white papers, honors, etc.')
        st.caption('Check "BB Supported" for achievements BaoBunny directly helped produce.')

        with st.expander('Documents (Google Drive)', expanded=not (ss.cv_doc_url or ss.cv_folder_url)):
            st.text_input('CV (Drive link)', key='cv_doc_url')
            st.text_input('Supporting Docs Folder (Drive link)', key='cv_folder_url')
            if st.button('Save Links', key='save_impact_links'):
                _do_save_customer_links(
                    cv_doc_url=ss.cv_doc_url.strip(),
                    cv_folder_url=ss.cv_folder_url.strip(),
                )
                st.rerun()
            lcol1, lcol2 = st.columns(2)
            with lcol1:
                if ss.cv_doc_url:
                    st.link_button('Open CV ↗', ss.cv_doc_url, use_container_width=True)
            with lcol2:
                if ss.cv_folder_url:
                    st.link_button('Open Supporting Docs Folder ↗', ss.cv_folder_url, use_container_width=True)

        impact_df = st.data_editor(
            ss.impact_items_df,
            use_container_width=True,
            hide_index=True,
            num_rows='dynamic',
            column_config={
                'Category': st.column_config.SelectboxColumn('Category', options=IMPACT_CATEGORIES, width='medium'),
                'Item': st.column_config.TextColumn('Item', width='large'),
                'Metric/Value': st.column_config.TextColumn('Metric/Value', width='small'),
                'BB Supported': st.column_config.CheckboxColumn('BB Supported', width='small'),
                'Date': st.column_config.TextColumn('Date (e.g. Jan-26-2026)', width='medium'),
                'Notes': st.column_config.TextColumn('Notes', width='large'),
            },
            key=f'impact_editor_{ss.editor_v}',
        )

        if st.button('Save Impact Items', type='primary'):
            _do_save_impact_items(impact_df)
            st.rerun()

        st.divider()

        saved_impact = db.get_impact_items(ss.customer_id)
        active_impact = [it for it in saved_impact if (it.get('title') or '').strip()]

        if not active_impact:
            st.info('No impact items saved yet — add rows above and click Save Impact Items.')
        else:
            bb_count = sum(1 for it in active_impact if it.get('bb_supported'))
            ic1, ic2 = st.columns(2)
            ic1.metric('Total Achievements', len(active_impact))
            ic2.metric('BB-Supported', f'{bb_count} / {len(active_impact)}')

            st.subheader('By Category')
            cat_counts = {}
            for it in active_impact:
                cat_counts[it.get('category') or 'Other'] = cat_counts.get(it.get('category') or 'Other', 0) + 1
            st.bar_chart(pd.DataFrame({'Count': cat_counts}), horizontal=True, height=max(150, 40 * len(cat_counts)))

            bb_items = [it for it in active_impact if it.get('bb_supported')]
            if bb_items:
                st.subheader('BB-Supported Highlights')
                for it in bb_items:
                    val = f" — {it.get('metric_value')}" if it.get('metric_value') else ''
                    st.markdown(f"- **{it['title']}**{val} _({it.get('category', '')})_")

            # ── AI Estimation ─────────────────────────────────────────────────
            st.subheader('AI Estimation')
            impact_tagged = sorted(set(it.get('category') for it in active_impact if it.get('category')))

            st.markdown('**Option 1 — Assess in-app (OpenAI)**')
            if not ai_estimator.is_configured():
                st.info('No OpenAI key configured. Add `openai_api_key` to your Streamlit secrets to enable in-app assessments.')
            else:
                st.caption('Reads the achievements in a category and estimates overall strength, gaps, and next steps.')
                impact_ai_category = st.selectbox('Category to assess', options=impact_tagged, key='impact_ai_category_select')

                if st.button('Get AI Assessment', key='impact_ai_button'):
                    cat_items = [it for it in active_impact if it.get('category') == impact_ai_category]
                    with st.spinner(f'Assessing {impact_ai_category}...'):
                        try:
                            result = ai_estimator.estimate_impact_category(impact_ai_category, cat_items, ss.cust_name)
                            ss.impact_ai_result = result
                            ss.impact_ai_category_done = impact_ai_category
                        except Exception as e:
                            ss.impact_ai_result = None
                            st.error(f'AI assessment failed: {e}')

                if ss.get('impact_ai_result') and ss.get('impact_ai_category_done') == impact_ai_category:
                    result = ss.impact_ai_result
                    st.metric('AI-Estimated Strength', f"{result.get('strength_percent', 0)}%")
                    st.write(result.get('summary', ''))
                    gcol, scol = st.columns(2)
                    with gcol:
                        st.markdown('**Gaps**')
                        for g in (result.get('gaps') or []):
                            st.markdown(f'- {g}')
                    with scol:
                        st.markdown('**Suggestions**')
                        for s in (result.get('suggestions') or []):
                            st.markdown(f'- {s}')

            st.divider()
            st.markdown('**Option 2 — Download a bundle for manual upload (ChatGPT, Claude.ai, etc.)**')
            st.caption('No API key needed. Regenerated fresh from the currently saved impact items every time you download.')
            impact_bundle_zip = _build_impact_bundle_zip(
                {'name': ss.cust_name, 'company_project': ss.cust_company},
                active_impact,
            )
            safe_name = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in (ss.cust_name or 'customer')).strip().replace(' ', '_')
            st.download_button(
                '⬇ Download Impact Bundle (ZIP)', data=impact_bundle_zip,
                file_name=f'{safe_name}_impact_bundle.zip', mime='application/zip',
            )

# ── Tab: Immigration ────────────────────────────────────────────────────────
with tab_immigration:
    if not ss.customer_id:
        st.info('Select or create a customer to track immigration milestones.')
    else:
        st.subheader(f'Immigration — {ss.cust_name}')
        st.caption('Track EB1A / NIW evidence milestones, target dates, and completion status for this customer.')
        st.caption('Category tags each milestone to an EB1A criterion or NIW prong for the progress charts below.')

        with st.expander('Documents (Google Drive)', expanded=not (ss.petition_doc_url or ss.petition_folder_url)):
            st.text_input('Petition Letter (Drive link)', key='petition_doc_url')
            st.text_input('Supporting Exhibits Folder (Drive link)', key='petition_folder_url')
            if st.button('Save Links', key='save_immigration_links'):
                _do_save_customer_links(
                    petition_doc_url=ss.petition_doc_url.strip(),
                    petition_folder_url=ss.petition_folder_url.strip(),
                )
                st.rerun()
            lcol1, lcol2 = st.columns(2)
            with lcol1:
                if ss.petition_doc_url:
                    st.link_button('Open Petition Letter ↗', ss.petition_doc_url, use_container_width=True)
            with lcol2:
                if ss.petition_folder_url:
                    st.link_button('Open Exhibits Folder ↗', ss.petition_folder_url, use_container_width=True)

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

            # ── AI Estimation ────────────────────────────────────────────────
            st.subheader('AI Estimation')
            tagged_categories = sorted(set(
                m.get('category') for m in active
                if (m.get('category') or '') in (NIW_PRONGS + EB1A_CRITERIA)
            ))

            st.markdown('**Option 1 — Assess in-app (OpenAI)**')
            if not ai_estimator.is_configured():
                st.info(
                    'No OpenAI key configured. Add `openai_api_key` to your Streamlit secrets '
                    'to enable in-app AI strength/gap assessments per category. '
                    'See ai_estimator.py for the exact secrets format.'
                )
            elif not tagged_categories:
                st.info('Tag at least one milestone with an EB1A criterion or NIW prong category above to enable AI assessment.')
            else:
                st.caption('Reads the milestones tagged to a category and estimates overall strength, gaps, and next steps. Nothing is auto-applied to your data — review before acting on it.')
                ai_category = st.selectbox('Category to assess', options=tagged_categories, key='ai_category_select')

                if st.button('Get AI Assessment'):
                    cat_milestones = [m for m in active if m.get('category') == ai_category]
                    with st.spinner(f'Assessing {ai_category}...'):
                        try:
                            result = ai_estimator.estimate_category(ai_category, cat_milestones, ss.cust_name)
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

            st.divider()
            st.markdown('**Option 2 — Download a bundle for manual upload (ChatGPT, Claude.ai, etc.)**')
            st.caption(
                'No API key needed for this option. Downloads a ZIP with a ready-made prompt, '
                "this customer's info, and all their milestones — upload the ZIP's contents "
                'directly into any chatbot to get a manual assessment. Regenerated fresh from '
                'the currently saved milestones every time you download.'
            )
            bundle_zip = _build_ai_bundle_zip(
                {
                    'name': ss.cust_name,
                    'company_project': ss.cust_company,
                    'contract_note': ss.cust_contract_note,
                    'footnote': ss.cust_footnote,
                },
                active,
            )
            safe_bundle_name = ''.join(
                c if c.isalnum() or c in (' ', '-', '_') else '_' for c in (ss.cust_name or 'customer')
            ).strip().replace(' ', '_')
            st.download_button(
                '⬇ Download AI Estimation Bundle (ZIP)',
                data=bundle_zip,
                file_name=f'{safe_bundle_name}_niw_eb1a_bundle.zip',
                mime='application/zip',
            )

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
