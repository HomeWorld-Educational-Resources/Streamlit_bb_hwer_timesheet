"""
AI-powered assessment of NIW prong / EB1A criterion strength.

Supports both Claude (Anthropic) and GPT (OpenAI) — whichever the user has
configured. API keys are read ONLY from Streamlit secrets, never hardcoded
and never committed to git.

Add to .streamlit/secrets.toml locally, and to the same keys under
Streamlit Community Cloud's "Manage app -> Settings -> Secrets":

    anthropic_api_key = "sk-ant-..."
    openai_api_key = "sk-..."

    # Optional — pin a specific model version. Provider model names change
    # fairly often; if a request starts failing with a "model not found"
    # error, check the provider's docs for the current model id and set it
    # here rather than editing code.
    anthropic_model = "claude-sonnet-5"
    openai_model = "gpt-5-mini"
"""
import json

import streamlit as st

DEFAULT_ANTHROPIC_MODEL = 'claude-sonnet-5'
DEFAULT_OPENAI_MODEL = 'gpt-5-mini'

PROVIDER_ANTHROPIC = 'Claude (Anthropic)'
PROVIDER_OPENAI = 'OpenAI (GPT)'

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


def available_providers():
    """Which providers have a usable key configured in Streamlit secrets."""
    providers = []
    try:
        if st.secrets.get('anthropic_api_key'):
            providers.append(PROVIDER_ANTHROPIC)
    except Exception:
        pass
    try:
        if st.secrets.get('openai_api_key'):
            providers.append(PROVIDER_OPENAI)
    except Exception:
        pass
    return providers


def _build_prompt(category, milestones, customer_name):
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
        "assess how strong this category currently is for the petition. "
        "Respond with ONLY a JSON object — no markdown, no code fences, no commentary before "
        "or after it — in exactly this shape:",
        '{"strength_percent": <integer 0-100>, '
        '"summary": "<2-3 sentence assessment of overall strength>", '
        '"gaps": ["<specific missing evidence or weakness>", ...], '
        '"suggestions": ["<concrete next step to strengthen this category>", ...]}',
        "Limit gaps and suggestions to at most 4 items each; keep each item specific and actionable.",
    ]
    return '\n'.join(lines)


def _parse_json_response(text):
    text = text.strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def estimate_category(provider, category, milestones, customer_name):
    """Returns {strength_percent, summary, gaps, suggestions}. Raises on failure —
    caller is expected to catch and display the error."""
    prompt = _build_prompt(category, milestones, customer_name)

    if provider == PROVIDER_ANTHROPIC:
        import anthropic
        key = st.secrets.get('anthropic_api_key')
        if not key:
            raise RuntimeError('anthropic_api_key is not set in Streamlit secrets.')
        model = st.secrets.get('anthropic_model', DEFAULT_ANTHROPIC_MODEL)
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=model,
            max_tokens=800,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text')
        return _parse_json_response(text)

    elif provider == PROVIDER_OPENAI:
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
        text = resp.choices[0].message.content
        return _parse_json_response(text)

    raise ValueError(f'Unknown provider: {provider}')
