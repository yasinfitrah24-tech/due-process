"""Plain sentences written by code from the signals. No model is involved, so they cannot say more than the signals do."""


def why(found):
    """One or two sentences on what the visit found, or None if there is no evidence."""
    values = {s["id"]: s["value"] for s in found or []}
    if not values:
        return None
    if values["form_to_foreign_host"] and values["password_field"]:
        first = "A form on the page sends data to another host, and the page asks for a password."
    elif values["form_to_foreign_host"]:
        first = "A form on the page sends data to another host. The page asks for no password."
    elif values["password_field"]:
        first = "The page asks for a password, and no form sends data to another host."
    else:
        first = "The visit found no form that sends data to another host."

    extras = []
    if values.get("hidden_text_present"):
        extras.append("holds hidden text")
    if values.get("egress_blocked"):
        extras.append("tried to reach an address it may not reach")
    if values.get("download_quarantined"):
        extras.append("started a download that was held and never opened")
    if not extras:
        return first
    listed = extras[0] if len(extras) == 1 else ", ".join(extras[:-1]) + " and " + extras[-1]
    return f"{first} The page also {listed}."
