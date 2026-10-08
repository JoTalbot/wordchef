from wordchef_game.content import CONTENT_DIGEST
from wordchef_prolepsis.bridge import WordChefRuntime


def test_execution_events_are_bound_to_content_digest() -> None:
    events = (
        {"type": "TEST_EVENT", "payload": {"value": 7}},
        {"type": "EMPTY_PAYLOAD"},
    )
    bound = WordChefRuntime._bind_content(events)

    assert all(event["payload"]["content_digest"] == CONTENT_DIGEST for event in bound)
    assert bound[0]["payload"]["value"] == 7


def test_existing_content_digest_is_not_overwritten() -> None:
    bound = WordChefRuntime._bind_content((
        {"type": "TEST_EVENT", "payload": {"content_digest": "historical"}},
    ))
    assert bound[0]["payload"]["content_digest"] == "historical"
