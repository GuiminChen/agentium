from __future__ import annotations

from agentium.channels import ChannelEnvelope, InMemoryChannelAdapter
from agentium.infra.mq import InMemoryMessageQueue, Message


def test_in_memory_channel_adapter_separates_sync_and_async_io() -> None:
    adapter = InMemoryChannelAdapter(channel_name="cli")
    envelope = ChannelEnvelope(
        channel_name="cli",
        run_id="run-channel-1",
        payload={"text": "hello"},
        async_delivery=True,
    )

    receipt = adapter.send(envelope)

    assert receipt.accepted is True
    assert receipt.delivery_mode == "async"
    assert adapter.drain() == [envelope]


def test_in_memory_message_queue_preserves_fifo_order() -> None:
    queue = InMemoryMessageQueue()
    queue.publish("runs", Message(key="one", payload={"n": 1}))
    queue.publish("runs", Message(key="two", payload={"n": 2}))

    assert queue.consume("runs", limit=2) == [
        Message(key="one", payload={"n": 1}),
        Message(key="two", payload={"n": 2}),
    ]
