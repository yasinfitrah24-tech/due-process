"""The status line says whether its amount is still held by the ledger or was really spent."""
from app import db
from tests.test_model_pipeline import URL_A, client, log, replies, run  # noqa: F401


def test_an_open_call_shows_reserved_and_a_finished_call_shows_spent(client):  # noqa: F811
    c = run(client, URL_A)
    assert " spent." in client.get(f"/cases/{c['id']}").text

    usage_id, refusal = db.reserve_usage(c["id"], "documents", "m", 0.00502, None, None, None, None)
    assert refusal is None
    page = client.get(f"/cases/{c['id']}").text
    assert "USD 0.00502 reserved." in page and " spent." not in page

    db.finish_usage(usage_id, 100, 50, 0.00205, 4.2)
    page = client.get(f"/cases/{c['id']}").text
    assert "USD 0.00205 spent." in page and " reserved." not in page


def test_a_failed_call_holds_nothing(client):  # noqa: F811
    c = run(client, URL_A)
    usage_id, _ = db.reserve_usage(c["id"], "documents", "m", 0.00502, None, None, None, None)
    db.finish_usage(usage_id, 0, 0, 0.0, 1.5, ok=False)
    assert db.case_usage(c["id"])["open_calls"] == 0
