"""Message templates, merge fields, and the built-in follow-up sequence.

The copy here is the actual pitch a vending operator makes: the machine is free
to the host, you stock and service it, they get a cut, and there is no contract.
That is the offer that gets a yes, so it is what the default templates say.

Merge fields use ``{field}`` and are filled from the prospect and your own
business details. An unknown field renders as a visible ``[field]`` marker
rather than raising, so a typo in a custom template shows up in the preview
instead of breaking a send.
"""

from __future__ import annotations

from string import Formatter
from typing import Any, Dict, List

from ..locations.categories import BY_KEY
from ..locations.scoring import machine_recommendation
from ..models import Site
from .compliance import SenderIdentity

MERGE_FIELDS = {
    "business_name": "The prospect's business name",
    "contact_name": "Contact name, or 'there' when unknown",
    "category": "What kind of site it is (e.g. Laundromat)",
    "address": "Street address",
    "city": "The prospect's city",
    "machine_types": "What you'd stock there (e.g. snack, cold drink)",
    "fit_reason": "The strongest reason this site scores well",
    "my_business": "Your business name",
    "my_name": "Your name",
    "my_phone": "Your phone number",
    "my_email": "Your email address",
    "my_city": "Your city",
    "commission_line": "What the host gets -- only used if they ask",
}


class _SafeDict(dict):
    """Renders unknown merge fields as a visible marker instead of raising."""

    def __missing__(self, key: str) -> str:
        return f"[{key}]"


def render(text: str, context: Dict[str, Any]) -> str:
    if not text:
        return ""
    try:
        return Formatter().vformat(text, (), _SafeDict(context))
    except (ValueError, IndexError):
        # A stray brace in hand-edited copy should not break the preview.
        return text


def build_context(site: Site, identity: SenderIdentity, contact_name: str = "") -> Dict[str, str]:
    """Merge-field values for one prospect."""
    spec = BY_KEY.get(site.category)
    city = ""
    if site.tags:
        city = site.tags.get("addr:city", "")
    if not city and site.address and "," in site.address:
        city = site.address.split(",")[1].strip()

    return {
        "business_name": site.name,
        "contact_name": (contact_name or "").strip() or "there",
        "category": (spec.label if spec else site.category_label).lower(),
        "address": site.address or "",
        "city": city or identity.city,
        "machine_types": machine_recommendation(site) or "snack and cold drink",
        "fit_reason": _fit_reason(site),
        "my_business": identity.business_name,
        "my_name": identity.sender_name,
        "my_phone": identity.phone,
        "my_email": identity.sender_email,
        "my_city": identity.city,
        "commission_line": identity.commission_line,
    }


def _fit_reason(site: Site) -> str:
    """The most persuasive line from the score, phrased for the recipient.

    The scorer's own reasons are written for the operator ("captive audience"),
    which is not something to say to the person you are pitching. This picks a
    reason and softens it.
    """
    friendly = {
        "no store": "there isn't much else close by for a snack or a cold drink",
        "24/7": "you're open around the clock",
        "already mapped": "",
        "rooms": "it looks like a good-sized property",
        "units": "it looks like a good-sized property",
    }
    for reason in site.reasons or []:
        lowered = reason.lower()
        for needle, phrasing in friendly.items():
            if needle in lowered and phrasing:
                return phrasing
    spec = BY_KEY.get(site.category)
    if spec and spec.dwell >= 8.5:
        return "people tend to be there a while"
    return "it looks like a good fit"


# --------------------------------------------------------------- the defaults

INTRO_SEQUENCE = "intro"

BUILTIN_TEMPLATES: List[Dict[str, Any]] = [
    {
        "key": "intro_email",
        "name": "Intro email",
        "channel": "email",
        "sequence_key": INTRO_SEQUENCE,
        "step": 0,
        "delay_days": 0,
        "subject": "Vending machine for {business_name}?",
        "body": """Hi {contact_name},

I'm {my_name} with {my_business}, a local vending route here in {my_city}. I came across {business_name} on {address} and thought it might be a good fit -- {fit_reason}.

Here's how it works, and it's simpler than people expect:

- We install the machine at no cost to you.
- We stock it, service it, and handle any repairs or refunds.
- No contract. If it isn't earning its space, we pull it out.

Based on the location I'd start with {machine_types}.

Worth a five-minute conversation? I'm happy to stop by and take a look at the space -- no obligation either way.

Thanks,
{my_name}
{my_business}
{my_phone}""",
    },
    {
        "key": "followup_1",
        "name": "Follow-up (day 4)",
        "channel": "email",
        "sequence_key": INTRO_SEQUENCE,
        "step": 1,
        "delay_days": 4,
        "subject": "Re: Vending machine for {business_name}?",
        "body": """Hi {contact_name},

Following up on my note from last week about putting a {machine_types} machine in at {business_name}.

The short version: no cost to you, we handle stocking and service, and there's no contract.

If it's a no, just say so and I'll leave you alone -- I'd rather know than keep emailing. If you'd like to see the machine first, I can bring photos or specs.

Thanks,
{my_name}
{my_business}
{my_phone}""",
    },
    {
        "key": "followup_2",
        "name": "Last note (day 11)",
        "channel": "email",
        "sequence_key": INTRO_SEQUENCE,
        "step": 2,
        "delay_days": 11,
        "subject": "Closing the loop -- vending at {business_name}",
        "body": """Hi {contact_name},

Last note from me on this, then I'll get out of your inbox.

If a no-cost {machine_types} machine at {business_name} is ever worth a look -- this year or next -- keep my number. Happy to help whenever the timing is better.

Thanks for your time,
{my_name}
{my_business}
{my_phone}""",
    },
    {
        "key": "walk_in_script",
        "name": "Walk-in script",
        "channel": "script",
        "sequence_key": "",
        "step": 0,
        "delay_days": 0,
        "subject": "Walk-in at {business_name}",
        "body": """WALK-IN -- {business_name}
{address}

Ask for: the owner or whoever handles the building.
If they're out: get a name and the best time to come back. Leave a card. Don't pitch the front desk.

OPENER
"Hi -- I'm {my_name} with {my_business}. We run vending machines around {my_city}.
I'm not selling you anything; I place machines at no cost. Do you have two minutes?"

THE OFFER
- Free to you. We buy, install, stock and service it.
- No contract. Doesn't work out, we haul it off.
- For a spot like this I'd start with {machine_types}.

Do NOT bring up commission. Lead with free and no hassle -- that is the whole
pitch, and most hosts say yes without ever asking about a cut.

ONLY IF THEY ASK "what's in it for me?" / "do I get a cut?"
"Yes -- {commission_line}. I'll put it in writing before anything goes in."

IF THEY ALREADY HAVE ONE
"No problem -- who services it? How's it been?"
(Broken, empty, or a slow refill is your opening. Ask when the contract is up.)

COMMON OBJECTIONS
"No room."      -> "It needs about 3 feet of wall and a standard outlet. Mind if I look?"
"Have to ask."  -> "Of course. Who should I follow up with, and when's good?"
"Not interested." -> "Fair enough. Can I leave a card in case that changes?"

BEFORE YOU LEAVE
[ ] Decision maker's name and number
[ ] Where the machine would physically go, and is there an outlet
[ ] Rough headcount / daily foot traffic
[ ] Agreed next step and date""",
    },
    {
        "key": "call_script",
        "name": "Phone script",
        "channel": "script",
        "sequence_key": "",
        "step": 0,
        "delay_days": 0,
        "subject": "Call {business_name}",
        "body": """CALL -- {business_name}
{my_phone} calling {address}

"Hi, this is {my_name} with {my_business} here in {my_city}. Am I catching you at a bad time?"
(If yes: "When's better? I'll call back then." Then actually call back.)

"We place and service vending machines. I'm calling about {business_name} specifically --
{fit_reason}, so it looked like a fit.

There's no cost to you. We install it, keep it stocked and serviced, and there's
no contract -- if it's not pulling its weight we take it out.

Would it make sense for me to stop by and look at the space?"

DON'T mention commission unless they ask.
IF THEY ASK: "Yes -- {commission_line}. I'll put it in writing before anything
goes in."

LOG THE OUTCOME when you hang up -- who you spoke to, what they said,
and the next step with a date.""",
    },
]


def install_builtins(db) -> int:
    """Load the built-in templates, without clobbering user edits.

    An untouched built-in is refreshed to the current wording, so improvements
    to the copy reach existing installs -- otherwise a database created before
    a change keeps the old text forever, which is how an install can carry on
    sending a pitch that was deliberately rewritten.

    A template the user has edited is never overwritten. The ``builtin`` flag
    is what tells them apart: saving one from the Templates page clears it.
    """
    installed = 0
    for template in BUILTIN_TEMPLATES:
        existing = db.get_template(template["key"])
        if existing and not existing.get("builtin"):
            continue                      # the user made it theirs; leave it
        if existing and existing.get("body") == template.get("body") \
                and existing.get("subject") == template.get("subject"):
            continue                      # already current, nothing to do
        db.upsert_template({**template, "builtin": True})
        installed += 1
    return installed


def sequence_steps(db, sequence_key: str = INTRO_SEQUENCE) -> List[Dict[str, Any]]:
    """The templates in one sequence, in send order."""
    steps = [
        t for t in db.list_templates(channel="email")
        if (t.get("sequence_key") or "") == sequence_key
    ]
    return sorted(steps, key=lambda t: int(t.get("step", 0)))
