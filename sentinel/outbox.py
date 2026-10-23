"""What a request owes once it has answered: answer first, deliver after.

A member's app sign-in and an acknowledgement are committed before the
request answers, and each owes a message: the new-device notice (decision
21c) and, for a CRITICAL, the emergency contact's closing note (decision 1).
Both used to go out BEFORE the answer, through mail and SMS providers whose
timeouts are per socket operation - smtplib's 15 seconds apply to the
connect, the greeting, EHLO, STARTTLS, the login and each step of the send.
So a slow provider held the answer past the app's 15-second deadline for a
change that had already been made (found 2026-10-23): the app said "Can't
reach Minas" about a sign-in that had created a device and spent the
member's code, and "Not acknowledged" about an acknowledgement that had
already stopped the escalation.

The portal serves each request inside `answering()`. A delivery made through
`later()` while it runs is queued, and once the answer has been written the
queue runs - in the order it was filled, each delivery under its own guard
as before - on one daemon thread, which releases its database connection
when it is done (#79). Outside a request - the CLI's `sentinel ack`, the
dashboard, the engine, a test calling a writer directly - `later()` delivers
at once, exactly as before: a one-shot process would otherwise exit with the
message unsent.

`wait_until_sent()` is for tests: a bounded wait for every delivery queued so
far, so a test that asserts a notice went out after the answer waits for it
rather than racing the thread.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager

logger = logging.getLogger("sentinel.outbox")

# The queue of the request this thread is serving, if it is serving one.
_request = threading.local()


class _Owed:
    """Deliveries queued and not yet finished, across every request."""

    def __init__(self) -> None:
        self.count = 0
        self.changed = threading.Condition()

    def add(self, n: int) -> None:
        with self.changed:
            self.count += n
            self.changed.notify_all()


_owed = _Owed()


def later(deliver, *args) -> None:
    """`deliver(*args)` once the request this thread is serving has
    answered; at once when it is serving none."""
    queue = getattr(_request, "queue", None)
    if queue is None:
        deliver(*args)
        return
    # Counted now, before the answer is written, so a test that has the
    # answer in hand always finds the delivery owed.
    _owed.add(1)
    queue.append((deliver, args))


@contextmanager
def answering(db):
    """One request. What it queues through `later()` starts when the block
    ends, after the answer - whether the block ended cleanly or not: the
    sign-in or the acknowledgement it owes a message for has been committed
    either way."""
    outer = getattr(_request, "queue", None)
    queue: list = []
    _request.queue = queue
    try:
        yield
    finally:
        _request.queue = outer
        if queue:
            _start(db, queue)


def _start(db, queue: list) -> None:
    try:
        threading.Thread(target=_deliver, args=(db, queue), name="outbox", daemon=True).start()
    except RuntimeError as exc:
        # No thread to be had (the container's pids_limit, say). The answer
        # has gone, so delivering here costs the member nothing; not
        # delivering at all would cost them the message.
        logger.error("could not start a delivery thread (%s); delivering on the request's own", exc)
        _deliver(db, queue)


def _deliver(db, queue: list) -> None:
    try:
        for deliver, args in queue:
            try:
                deliver(*args)
            except Exception:  # noqa: BLE001 - each delivery guards itself; one that raises anyway must not cost the next
                logger.exception("a delivery queued after the answer failed")
    finally:
        # The connection first, then the count: a test that waited for the
        # count may rely on the connection being gone.
        db.release_thread_connection()
        _owed.add(-len(queue))


def wait_until_sent(timeout: float) -> bool:
    """True once every delivery queued so far has finished, False if
    `timeout` seconds pass first."""
    with _owed.changed:
        return _owed.changed.wait_for(lambda: _owed.count == 0, timeout)
