"""Puts what the reporter alleges next to what the evidence shows. For display only: nothing reads this back."""
from app import explain

FORM_CATEGORIES = ("phishing", "impersonation")


def rows(claim, found, evidence, vision):
    """claim and vision are model outputs, or None. Returns [{"label", "alleged", "shows", "kind"}]."""
    if not claim:
        return []
    values = {s["id"]: s["value"] for s in found or []}
    if not values:
        category = ("Nothing reproduced.", None)
    elif claim["category"] in FORM_CATEGORIES:
        category = (f"Form to a foreign host: {values['form_to_foreign_host']}. "
                    f"Password field: {values['password_field']}.", "observed")
    else:
        category = ("This desk does not test this category.", None)

    brand = claim["alleged_brand"]
    titles = [p["title"] for p in evidence.get("pages", []) if p.get("title")]
    seen = [t for t in titles if brand and brand.lower() in t.lower()]
    imitated = (vision or {}).get("imitated_brand")
    if seen:
        brand_shows = (f"In the page title: {seen[-1]}", "observed")
    elif imitated:
        brand_shows = (f"Vision model reads: {imitated}", "model_claim")
    else:
        brand_shows = ("Not seen in page titles.", None)

    behavior = (explain.why(found) or "Nothing reproduced.", "observed" if values else None)
    return [
        {"label": "Category", "alleged": claim["category"], "shows": category[0], "kind": category[1]},
        {"label": "Brand", "alleged": brand or "none named", "shows": brand_shows[0], "kind": brand_shows[1]},
        {"label": "Behavior", "alleged": claim["alleged_behavior"], "shows": behavior[0], "kind": behavior[1]},
    ]
