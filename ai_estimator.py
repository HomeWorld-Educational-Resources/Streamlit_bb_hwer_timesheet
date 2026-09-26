"""
AI-powered assessment of NIW prong / EB1A criterion strength, using OpenAI.

The API key is read ONLY from Streamlit secrets, never hardcoded and never
committed to git.

Add to .streamlit/secrets.toml locally, and to the same key under
Streamlit Community Cloud's "Manage app -> Settings -> Secrets":

    openai_api_key = "sk-..."

    # Optional — pin a specific model version. OpenAI's model names change
    # fairly often; if a request starts failing with a "model not found"
    # error, check OpenAI's docs for the current model id and set it here
    # rather than editing code.
    openai_model = "gpt-5-mini"
"""
import json

import streamlit as st

DEFAULT_OPENAI_MODEL = 'gpt-5-mini'

CATEGORY_DEFINITIONS = {
    'EB1A - Awards':
        'Receipt of nationally or internationally recognized prizes/awards for excellence in the field.',
    'EB1A - Membership':
        'Membership in associations that require outstanding achievements of their members, '
        'as judged by recognized national or international experts.',
    'EB1A - Published Material About You':
        "Published material about the person and their work, in professional or major trade "
        "publications or other major media.",
    "EB1A - Judging Others' Work":
        'Participation, either individually or on a panel, as a judge of the work of others in '
        'the same or an allied field of specialization.',
    'EB1A - Original Contributions':
        'Original scientific, scholarly, artistic, athletic, or business-related contributions '
        'of major significance to the field.',
    'EB1A - Authorship (Scholarly Articles)':
        'Authorship of scholarly articles in the field, in professional or major trade '
        'publications or other major media.',
    'EB1A - Artistic Exhibitions/Showcases':
        "Display of the person's work at artistic exhibitions or showcases.",
    'EB1A - Critical/Leading Role':
        'Performance in a leading or critical role for organizations or establishments that '
        'have a distinguished reputation.',
    'EB1A - High Salary':
        'Command of a high salary or other significantly high remuneration relative to others '
        'in the field.',
    'EB1A - Commercial Success (Arts)':
        'Commercial success in the performing arts, as shown by box office receipts, record, '
        'cassette, compact disk, or video sales.',
    'NIW Prong 1 - Substantial Merit & National Importance':
        'The proposed endeavor has both substantial merit and national importance '
        '(Matter of Dhanasar, prong 1).',
    'NIW Prong 2 - Well Positioned to Advance':
        'The foreign national is well positioned to advance the proposed endeavor '
        '(Matter of Dhanasar, prong 2).',
    'NIW Prong 3 - Beneficial to Waive Job Offer':
        'On balance, it would be beneficial to the United States to waive the job offer / '
        'labor certification requirement (Matter of Dhanasar, prong 3).',
}

CAREER_CATEGORY_DEFINITIONS = {
    'Milestone': 'A specific achievement or checkpoint on the way to the target role.',
    'Goal': 'A stated objective the client is working toward.',
    'Strategic Plan': 'The overall plan or approach for reaching the target role.',
    'Proposed Activity': 'A planned action or project not yet completed.',
    'Skill/Experience Gained': 'A skill, credential, or experience the client has gained relevant to the target role.',
}

IMPACT_CATEGORY_DEFINITIONS = {
    'Media Coverage': 'Press, interviews, or media features about the client or their work.',
    'Paper Citations': "Citation counts or citation-worthy recognition of the client's publications.",
    'GitHub Stars': "Stars or community engagement on the client's open-source projects.",
    'Downloads': "Download or usage counts for the client's software, tools, or datasets.",
    'White Papers': "White papers or technical reports authored or co-authored by the client.",
    'Honors': 'Awards, honors, or formal recognitions received by the client.',
    'Other': 'Other measurable real-world impact not covered by the categories above.',
}


def is_configured():
    """Whether an OpenAI key is available in Streamlit secrets."""
    try:
        return bool(st.secrets.get('openai_api_key'))
    except Exception:
        return False


def _parse_json_response(text):
    text = text.strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _response_instructions():
    return (
        "Respond with ONLY a JSON object — no markdown, no code fences, no commentary before "
        "or after it — in exactly this shape:\n"
        '{"strength_percent": <integer 0-100>, '
        '"summary": "<2-3 sentence assessment of overall strength>", '
        '"gaps": ["<specific missing evidence or weakness>", ...], '
        '"suggestions": ["<concrete next step to strengthen this category>", ...]}\n'
        "Limit gaps and suggestions to at most 4 items each; keep each item specific and actionable."
    )


def _call_openai(prompt):
    """Returns {strength_percent, summary, gaps, suggestions}. Raises on failure —
    caller is expected to catch and display the error."""
    import openai
    key = st.secrets.get('openai_api_key')
    if not key:
        raise RuntimeError('openai_api_key is not set in Streamlit secrets.')
    model = st.secrets.get('openai_model', DEFAULT_OPENAI_MODEL)
    client = openai.OpenAI(api_key=key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=800,
    )
    return _parse_json_response(resp.choices[0].message.content)


def estimate_category(category, milestones, customer_name):
    """Immigration (EB1A/NIW) assessment."""
    definition = CATEGORY_DEFINITIONS.get(category, '')
    lines = [
        "You are assisting an immigration case team in assessing evidence strength for a "
        "client's petition. Be a rigorous, honest evaluator — do not inflate the assessment.",
        f"Client: {customer_name}",
        f"Category being assessed: {category}",
        f"Legal definition/requirement for this category: {definition}",
        "",
        "Below are the milestones (pieces of evidence or work items) logged under this "
        "category, each with its completion percentage, status, and notes:",
        "",
    ]
    for m in milestones:
        lines.append(
            f'- "{m.get("title")}" | Status: {m.get("status")} | '
            f'% Complete: {m.get("percent_complete", 0):g}% | '
            f'Target Date: {m.get("target_date") or "n/a"} | '
            f'Notes: {m.get("notes") or "none"}'
        )
    lines += [
        "",
        "Based ONLY on the evidence listed above (do not invent evidence that isn't listed), "
        "assess how strong this category currently is for the petition.",
        "",
        _response_instructions(),
    ]
    return _call_openai('\n'.join(lines))


def estimate_career_category(category, items, customer_name, target_role):
    """Career progress assessment for one category, toward a stated target role."""
    definition = CAREER_CATEGORY_DEFINITIONS.get(category, '')
    lines = [
        "You are a career coach assessing a client's progress toward a specific target role. "
        "Be a rigorous, honest evaluator — do not inflate the assessment.",
        f"Client: {customer_name}",
        f"Target role (dream job/position/promotion): {target_role or 'not specified'}",
        f"Category being assessed: {category}",
        f"What this category covers: {definition}",
        "",
        "Below are the items logged under this category, each with target date, completion "
        "percentage, status, and notes:",
        "",
    ]
    for it in items:
        lines.append(
            f'- "{it.get("title")}" | Status: {it.get("status")} | '
            f'% Complete: {it.get("percent_complete", 0):g}% | '
            f'Target Date: {it.get("target_date") or "n/a"} | '
            f'Notes: {it.get("notes") or "none"}'
        )
    lines += [
        "",
        "Based ONLY on the items listed above (do not invent evidence that isn't listed), "
        "assess how strong this category currently is toward the target role.",
        "",
        _response_instructions(),
    ]
    return _call_openai('\n'.join(lines))


def estimate_impact_category(category, items, customer_name):
    """Real-world impact evidence assessment for one category."""
    definition = IMPACT_CATEGORY_DEFINITIONS.get(category, '')
    lines = [
        "You are assisting in assessing the strength of a client's real-world impact evidence "
        "for this category. Be a rigorous, honest evaluator — do not inflate the assessment.",
        f"Client: {customer_name}",
        f"Category being assessed: {category}",
        f"What this category covers: {definition}",
        "",
        "Below are the achievements logged under this category, each with its metric/value, "
        "whether BaoBunny directly helped produce it (BB Supported), date, and notes:",
        "",
    ]
    for it in items:
        lines.append(
            f'- "{it.get("title")}" | Metric/Value: {it.get("metric_value") or "n/a"} | '
            f'BB Supported: {"Yes" if it.get("bb_supported") else "No"} | '
            f'Date: {it.get("item_date") or "n/a"} | '
            f'Notes: {it.get("notes") or "none"}'
        )
    lines += [
        "",
        "Based ONLY on the achievements listed above (do not invent evidence that isn't listed), "
        "assess how strong this category currently is as impact evidence.",
        "",
        _response_instructions(),
    ]
    return _call_openai('\n'.join(lines))
