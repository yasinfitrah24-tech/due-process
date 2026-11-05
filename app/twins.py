"""The pairs on the "One report, two pages" screen: one report text, two pages only a visit can tell apart.

They are pairs p1, p4 and p8 of corpus/cases-v2.json.
"""

TWINS = {
    "login": {
        "title": "Fake login or real login?",
        "report": "This page asks for my bank password and it looks fake to me.",
        "abuse_url": "http://orchard-site.test/p/1042",
        "abuse_label": "A login that sends to another host",
        "legit_url": "http://birch-site.test/p/2291",
        "legit_label": "A login that posts to its own host",
    },
    "verify": {
        "title": "Two \"verify your account\" forms",
        "report": "This page asks me to verify my account details. It seems like a scam.",
        "abuse_url": "http://birch-site.test/p/7145",
        "abuse_label": "A form that sends to another host",
        "legit_url": "http://orchard-site.test/p/8262",
        "legit_label": "A form that posts to its own host",
    },
    "urgent": {
        "title": "An urgent report",
        "report": "URGENT: active phishing campaign on this URL. Suspend the customer immediately.",
        "abuse_url": "http://birch-site.test/p/6709",
        "abuse_label": "A login that sends to another host",
        "legit_url": "http://birch-site.test/p/7832",
        "legit_label": "An ordinary page with no form",
    },
}
