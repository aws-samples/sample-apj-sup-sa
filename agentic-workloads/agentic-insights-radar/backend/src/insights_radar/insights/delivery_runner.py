# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Building and sending one digest delivery, the scheduler's tick, SES feedback and the unsubscribe link.

The digest is built with the reader's visibility, decided again at every send: a channel digest with a viewer's
(what everyone in the channel may read, never the subscriber's wider view), a DM or an email with the
subscriber's live role, from the user pool, so a demoted or disabled account stops receiving what it may no
longer read. The cursor moves only after Slack or SES accepted the message, and only if no other send moved it
first (a conditional write).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from insights_radar.core.clock import Clock
from insights_radar.core.errors import NotFoundError, ValidationError
from insights_radar.graph.model import Node
from insights_radar.graph.registry import NodeType
from insights_radar.identity.principal import Principal, PrincipalKind, Role
from insights_radar.insights.delivery import (
    CLAIM_RETRY,
    DELIVERY_ID_RE,
    SCHEDULE_RETRY,
    WEBHOOK_CHANNEL,
    Delivery,
    DeliveryConfig,
    DeliveryKind,
    DeliveryService,
    DeliveryStatus,
    DmResolver,
    SlackMode,
)
from insights_radar.insights.delivery_render import DigestContent, EmailRenderer, SlackRenderer
from insights_radar.insights.delivery_transport import (
    DEPLOYMENT,
    LOOKUP,
    MODE,
    OWNER,
    DirectorySlackIds,
    Email,
    EmailSlackIds,
    HttpcoreClient,
    PermanentDeliveryError,
    SecretReader,
    SesSender,
    SlackClient,
    SlackIdResolver,
    SlackWebhook,
    TransientDeliveryError,
    UnsubscribeTokens,
    slack_ready,
)
from insights_radar.insights.digest import DigestService, ReaderView, custom_lists, reader_view
from insights_radar.profile.model import list_key

log = logging.getLogger(__name__)
UNSUBSCRIBE_PATH = "/api/digest/unsubscribe"
_SUBJECT_RE = re.compile(r"^[A-Za-z0-9_.:@+-]{1,200}$")
# The scheduled time a queue message is for, and the message that first took it (see DeliveryService.take_send).
_SLOT_RE = re.compile(r"^[A-Za-z0-9:._+-]{1,64}$")
MAX_REQUEUES = 8  # times one send is put back on the queue for a Slack rate limit before the receives count
TAG_MAX = 256  # an SES message tag value: letters, digits, _ and - only, at most 256 characters
_TAG_RE = re.compile(r"^(?:[A-Za-z0-9-]|_[0-9a-f]{2}){1,256}$")
MODULES_MEMO_SECONDS = 5.0  # the module list one send reads, for its reader's view and its lists
ADVANCE_ATTEMPTS = 3  # writes of the cursor after a send went out, before it is logged and left
# A send starts only with this much of the sender's run time left: one that would run out after Slack or SES took
# the digest would be received again and sent twice (scan cycle 3). Slack's slowest path (a fresh token, a directory
# lookup, conversations.open and chat.postMessage) fits well inside it.
SEND_RESERVE_MS = 45_000  # of the sender's 120 s (infra SEND_TIMEOUT_SECONDS): 75 s to build the digest (scan cycle 4)


def subscriber_tag(subject: str) -> str | None:
    """The subject as an SES tag value, reversibly: every character but a letter, a digit or - (``_`` included)
    is ``_`` and its two hex digits, so ``local:a_b`` is ``local_3aa_5fb`` and a UUID stays as it is. None when the
    subject is not ASCII or the tag would be longer than SES allows (scan cycle 1, L4)."""
    if not subject.isascii():
        return None
    tag = re.sub(r"[^A-Za-z0-9-]", lambda m: f"_{ord(m.group()):02x}", subject)
    return tag if 0 < len(tag) <= TAG_MAX else None


def subject_of_tag(tag: str) -> str | None:
    """The subject a ``subscriber`` tag names, or None for a value ``subscriber_tag`` never writes."""
    if not _TAG_RE.fullmatch(tag):
        return None
    subject = re.sub(r"_([0-9a-f]{2})", lambda m: chr(int(m.group(1), 16)), tag)
    return subject if _SUBJECT_RE.fullmatch(subject) else None


@dataclass(frozen=True, slots=True)
class Audience:
    """Who reads a digest: what is hidden from them, the lists they never see, and whether the subscriber's
    watched records are in it (not in a channel post: one person's watch list is not for the whole channel)."""

    hidden: Callable[[Node], bool]
    unseen: frozenset[NodeType]
    watches: bool = True


@dataclass(frozen=True, slots=True)
class Prepared:
    delivery: Delivery
    content: DigestContent
    cursor: str  # the digest's ``until``: where the cursor goes once this is sent

    @property
    def total(self) -> int:
        return self.content.total


class Outlets(Protocol):
    """The clients a send uses, built only when a send needs them."""

    @property
    def slack(self) -> SlackClient: ...

    @property
    def webhook(self) -> SlackWebhook: ...

    @property
    def slack_ids(self) -> SlackIdResolver: ...

    @property
    def ses(self) -> SesSender: ...

    @property
    def unsubscribe(self) -> UnsubscribeTokens: ...

    def slack_configured(self) -> bool:
        """Whether the Slack secret holds a real token or webhook URL (not the stack's placeholder)."""
        ...


class AwsOutlets:
    """The real clients: Secrets Manager for the Slack secret and the unsubscribe key, SES v2, STS for the directory
    role. The API function answers within 29 s, so its Slack calls get a shorter timeout."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._config: DeliveryConfig = ctx.settings.digest
        self._timeout = 4.0 if getattr(ctx, "request_scoped", False) else 10.0

    @cached_property
    def _secrets(self) -> SecretReader:
        return SecretReader(self._ctx.client("secretsmanager"))

    @cached_property
    def _http(self) -> HttpcoreClient:
        return HttpcoreClient()

    def _slack_secret(self, fresh: bool, *names: str) -> str:
        webhook = self._config.slack_mode is SlackMode.WEBHOOK

        def ready(raw: str) -> bool:  # a placeholder is never kept: the real value is read once it is stored
            return slack_ready(SecretReader.field(raw, *names), webhook)

        return SecretReader.field(self._secrets.get(self._config.slack_secret_arn, fresh, ready), *names)

    def slack_configured(self) -> bool:
        webhook = self._config.slack_mode is SlackMode.WEBHOOK
        names = ("webhook_url", "url") if webhook else ("slack_bot_token", "token")
        try:
            return slack_ready(self._slack_secret(False, *names), webhook)
        except (PermanentDeliveryError, TransientDeliveryError):
            return False  # unreadable: offered once it can be read

    @cached_property
    def slack(self) -> SlackClient:
        return SlackClient(
            lambda fresh: self._slack_secret(fresh, "slack_bot_token", "token"), self._http, self._timeout
        )

    @cached_property
    def webhook(self) -> SlackWebhook:
        return SlackWebhook(lambda: self._slack_secret(False, "webhook_url", "url"), self._http, self._timeout)

    @cached_property
    def slack_ids(self) -> SlackIdResolver:
        if self._config.dm_resolver.value == "directory":
            return DirectorySlackIds(
                self._config.directory_endpoint,
                self._config.directory_role_arn,
                self._ctx.client("sts"),
                self._http,
                self._ctx.settings.region,
                api_key=self._directory_api_key,
                timeout=min(self._timeout, 5.0),
            )
        return EmailSlackIds(self.slack)

    def _directory_api_key(self) -> str | None:
        """The directory lookup's API key, kept as ``directory_api_key`` in the Slack secret's JSON next to the bot
        token (scan cycle 4): the sender and the API already read that secret, so no new secret or grant is needed."""
        raw = self._secrets.get(self._config.slack_secret_arn).strip()  # re-read on its own every few minutes
        if not raw.startswith("{"):
            return None
        return SecretReader.field(raw, "directory_api_key") or None

    @cached_property
    def ses(self) -> SesSender:
        return SesSender(self._ctx.client("sesv2"), self._config.email_sender, self._config.ses_config_set)

    @cached_property
    def unsubscribe(self) -> UnsubscribeTokens:
        return UnsubscribeTokens(
            lambda: SecretReader.field(self._secrets.get(self._config.unsub_secret_arn), "key", "unsubscribe_key"),
            self._ctx.clock,
        )


_TOTAL_ATTR = "digest_total"
_UNSENT_ATTR = "digest_unsent"


def tag_digest_total(err: BaseException, total: int, *, unsent: bool = False) -> None:
    """Put the size of the digest a failed test send had built on its error, which goes on unchanged, and whether
    it failed before the send was attempted (so nothing can have gone out)."""
    setattr(err, _TOTAL_ATTR, total)
    setattr(err, _UNSENT_ATTR, unsent)


def nothing_sent(err: BaseException) -> bool:
    """True when :func:`tag_digest_total` marked ``err`` as raised before the send was attempted."""
    return getattr(err, _UNSENT_ATTR, False) is True


def digest_total(err: BaseException) -> int:
    """The size :func:`tag_digest_total` put on ``err``, or 0."""
    value = getattr(err, _TOTAL_ATTR, 0)
    return value if isinstance(value, int) else 0


class DeliveryRunner:
    def __init__(
        self,
        *,
        config: DeliveryConfig,
        deliveries: DeliveryService,
        digests: DigestService,
        clock: Clock,
        outlets: Outlets,
        audience: Callable[[Delivery], Audience],
        lists: Callable[[], tuple[dict[str, str], dict[str, str], tuple[str, ...], frozenset[str]]],
        app_name: Callable[[], str],
        channel_listed: Callable[[str], bool],
    ) -> None:
        self._config = config
        self._deliveries = deliveries
        self._digests = digests
        self._clock = clock
        self._outlets = outlets
        self._audience = audience
        self._lists = lists
        self._app_name = app_name
        self._channel_listed = channel_listed

    @classmethod
    def of(cls, ctx: Any) -> DeliveryRunner:
        """Wired from an application context (the API's, a worker's, a test's)."""
        config: DeliveryConfig = ctx.settings.digest

        memo: list[tuple[float, list[Any]]] = []

        def modules() -> list[Any]:
            # One send reads the module list once, for the reader's view and for the lists alike (scan cycle 2).
            now = time.monotonic()
            if not memo or now - memo[0][0] > MODULES_MEMO_SECONDS:
                memo[:] = [(now, ctx.modules.list_all())]
            return memo[0][1]

        def view(d: Delivery, role: Role) -> ReaderView:
            reader = Principal(kind=PrincipalKind.USER, subject=d.subject, role=role)
            return reader_view(reader, lambda: ctx.modules.private_writer_slugs(modules()))

        def audience(d: Delivery) -> Audience:
            # Every kind stops with its subscriber's account, a channel post included (matrix BUG-1).
            try:
                user = ctx.users.get(d.username)
            except NotFoundError as err:
                raise PermanentDeliveryError("account_gone", "your account no longer exists", category=OWNER) from err
            if user.subject and user.subject != d.subject:
                # A new account that took a deleted one's username does not inherit its deliveries (scan cycle 2).
                raise PermanentDeliveryError("account_gone", "your account no longer exists", category=OWNER)
            if not user.enabled:
                raise PermanentDeliveryError("account_disabled", "your account is disabled", category=OWNER)
            if d.kind is DeliveryKind.SLACK_CHANNEL:
                # Everyone in the channel reads it: a viewer's view, never the subscriber's, and without the
                # subscriber's own watch list (scan cycle 1, M3).
                viewer = view(d, Role.VIEWER)
                return Audience(viewer.hidden, viewer.unseen, watches=False)
            # An email, or a DM found by email, goes on only while the account still has that address.
            address = (d.address or "").lower()
            if (d.kind is DeliveryKind.EMAIL or "@" in address) and (not user.email or user.email.lower() != address):
                raise PermanentDeliveryError(
                    "address_changed",
                    "your email address changed: delete this delivery and add it again",
                    category=OWNER,
                )
            own = view(d, user.role)
            return Audience(own.hidden, own.unseen)

        def lists() -> tuple[dict[str, str], dict[str, str], tuple[str, ...], frozenset[str]]:
            profile = ctx.profile.get(cached=True)
            custom = custom_lists(modules())
            keys = [t.value for t in (NodeType.SIGNAL, NodeType.TREND, NodeType.GAP, NodeType.CONTENT_IDEA)]
            keys += [f"insight:{k}" for k in sorted(custom)]
            labels: dict[str, str] = {}
            ranks: dict[str, int] = {}
            off: set[str] = set()
            for k in keys:
                pkey = k if k.startswith("insight:") else list_key(NodeType(k))
                entry = profile.list_profile(pkey) if pkey else None
                if entry is not None:
                    labels[k] = entry.label
                    ranks[k] = entry.order
                    if not entry.visible:
                        off.add(k)
                else:  # a custom list the profile does not name: the module's own label
                    labels[k] = custom.get(k.removeprefix("insight:"), k)
            order = tuple(sorted(keys, key=lambda k: (ranks.get(k, 100), keys.index(k))))
            return custom, labels, order, frozenset(off)

        return cls(
            config=config,
            deliveries=ctx.deliveries,
            digests=ctx.digests,
            clock=ctx.clock,
            outlets=ctx.delivery_outlets,
            audience=audience,
            lists=lists,
            app_name=lambda: ctx.profile.get(cached=True).app_name,
            channel_listed=lambda cid: ctx.deliveries.channel(cid) is not None,
        )

    # ---- one delivery ---------------------------------------------------------------------------------

    def prepare(self, d: Delivery) -> Prepared:
        """The digest this delivery would send now, from its own cursor, for its reader.

        The checks run in one order, here only: the kind is switched on; the owner's account (so a gone owner is
        counted whatever the target, scan cycle 7); the target (so an empty digest does not hide a dead one, scan
        cycle 6); and only then the digest, so none is built for a dead target (scan cycles 8 and 9)."""
        if not self._config.allows(d.kind):
            raise PermanentDeliveryError("not_enabled", "this kind of delivery is switched off on this deployment")
        audience = self._audience(d)
        self.check_target(d)
        custom, labels, order, off = self._lists()
        # A list the deployment hides is not read at all: it is not delivered, does not count, and a cut in it
        # does not hold the cursor back (scan cycle 1, M1).
        unseen = audience.unseen | {NodeType(k) for k in off if not k.startswith("insight:")}
        shown = {kind: label for kind, label in custom.items() if f"insight:{kind}" not in off}
        digest = self._digests.digest(
            d.subject,
            since=None,
            hidden=audience.hidden,
            custom=shown,
            unseen=frozenset(unseen),
            read_point=(d.cursor,),
            watches=audience.watches,
            off_kinds=frozenset(k.removeprefix("insight:") for k in off if k.startswith("insight:")),
        )
        # The profile's names win over a module's own nav label, as in the app (scan cycle 1, L1).
        content = DigestContent(self._app_name(), self._config.app_url, digest, {**digest["labels"], **labels}, order)
        return Prepared(d, content, str(digest["until"]))

    def dispatch(self, p: Prepared) -> None:
        d = p.delivery
        if d.kind is DeliveryKind.EMAIL:
            self._email(p)
            return
        message = SlackRenderer().render(p.content)
        if d.kind is DeliveryKind.SLACK_DM:
            user = self._outlets.slack_ids.slack_id(d.address or "")
            self._outlets.slack.post(self._outlets.slack.open_dm(user), message)
        elif self._config.slack_mode is SlackMode.WEBHOOK:
            self._outlets.webhook.post(message)
        else:
            self._outlets.slack.post(str(d.channel_id), message)

    def check_target(self, d: Delivery) -> None:
        """Whether a Slack delivery's target still works with this deployment, in one place (scan cycle 6).

        ``prepare`` checks it after the owner's account and before the digest is built, so an empty digest does not
        hide the fault, and none is built for a dead target. Only the owner's own cause counts (an administrator
        took the channel off the list, the operator moved DMs to another resolver); a mode the operator can switch
        back (bot or webhook) is shown, never counted.
        """
        if d.kind is DeliveryKind.EMAIL:
            return
        if d.kind is DeliveryKind.SLACK_DM:
            # The key was stored for the resolver of its day: an email for ``email``, an alias for ``directory``.
            # After the operator switched resolvers it is never handed to the other one (scan cycle 2).
            if ("@" in (d.address or "")) != (self._config.dm_resolver is DmResolver.EMAIL):
                raise PermanentDeliveryError(
                    "resolver_changed",
                    "this deployment now finds your Slack account another way: delete this DM delivery and add it"
                    " again",
                    category=OWNER,
                )
            return
        webhook_target = d.channel_id == WEBHOOK_CHANNEL
        if self._config.slack_mode is SlackMode.WEBHOOK:
            if not webhook_target:  # picked for the bot; the webhook posts to its own channel (BUG-2)
                raise PermanentDeliveryError(
                    "channel_removed",
                    "this channel is not available with the webhook: add the delivery again",
                    category=MODE,
                )
        elif webhook_target:  # made for the webhook, now sent by the bot: the operator can switch back
            raise PermanentDeliveryError(
                "channel_removed",
                "this delivery was made for the webhook channel: add the delivery again",
                category=MODE,
            )
        elif not d.channel_id or not self._channel_listed(d.channel_id):
            raise PermanentDeliveryError(
                "channel_removed", "an administrator removed this channel from the list", category=OWNER
            )

    def _email(self, p: Prepared) -> None:
        d = p.delivery
        app = self._config.app_url
        token = self._outlets.unsubscribe.issue(d.subject, d.id)
        mail = EmailRenderer().render(p.content, f"{app}/unsubscribe?t={token}", f"{app}/whats-new")
        tags = {"delivery": d.id}
        subscriber = subscriber_tag(d.subject)
        if subscriber is None:  # a bounce of this mail cannot be traced back; the mail itself still goes
            log.warning(json.dumps({"event": "digest.untagged", "delivery": d.id}))
        else:
            tags["subscriber"] = subscriber
        self._outlets.ses.send(
            Email(
                to=str(d.address),
                subject=mail.subject,
                text=mail.text,
                html=mail.html,
                unsubscribe_url=f"{app}{UNSUBSCRIBE_PATH}?t={token}",
                tags=tags,
            )
        )

    def deliver(
        self,
        subject: str,
        delivery_id: str,
        *,
        due: str | None = None,
        token: str | None = None,
        remaining_ms: Callable[[], int] | None = None,
    ) -> str:
        """A scheduled send of one delivery: its outcome. A transient failure is recorded and raised, so the queue
        tries again; a permanent one is recorded and not retried. With the scheduled time (``due``) and the queue
        message (``token``), a second message for the same time sends nothing (scan cycle 1, L2)."""
        d = self._deliveries.find(subject, delivery_id)
        if d is None or not d.sendable:
            return "skipped"
        if not self._config.allows(d.kind):
            return "off"
        if due and token and not self._deliveries.take_send(subject, delivery_id, due, token):
            log.warning(json.dumps({"event": "digest.duplicate", "delivery": delivery_id}))
            return "duplicate"
        try:
            p = self.prepare(d)
            if p.total:
                if remaining_ms is not None and remaining_ms() < SEND_RESERVE_MS:
                    # Building the digest took most of the run: try again in a fresh one before anything is sent.
                    raise TransientDeliveryError("out_of_time", "the sender ran short of time")
                # Read again just before sending: a complaint, bounce or switch-off that landed while the digest was
                # built stops it, so no mail goes to an address that just complained (scan cycle 14).
                fresh = self._deliveries.find(subject, delivery_id)
                if fresh is None or not fresh.sendable:
                    return "skipped"
                self.dispatch(p)
        except PermanentDeliveryError as err:
            if err.category in (MODE, DEPLOYMENT):
                # Not the owner's: shown, never counted, so it switches nobody off and sends resume once the
                # operator changes it back or fixes it (scan cycles 3 and 7). The sender returns normally, so this
                # log line is what the DigestDeploymentFaults alarm counts (scan cycle 6); a mode is not alarmed.
                event = "digest.delivery_fault" if err.category == MODE else "digest.deployment_fault"
                log.warning(json.dumps({"event": event, "delivery": delivery_id, "code": err.code}))
                self._deliveries.record(subject, delivery_id, DeliveryStatus.ERROR, err.message)
                return f"error:{err.code}"
            lookup = err.category == LOOKUP
            if lookup:
                # One missing person's misses stop once the delivery is switched off, so they cannot hold the
                # DigestLookupMisses alarm in ALARM; a broken lookup alarms on its first miss, days before the
                # longer limit switches anyone off (scan cycles 10 and 11).
                log.warning(json.dumps({"event": "digest.lookup_miss", "delivery": delivery_id, "code": err.code}))
            # Counted: a delivery that keeps failing for good is switched off with the reason (scan cycle 2).
            if self._deliveries.fail(subject, delivery_id, err.message, lookup=lookup):
                return f"off:{err.code}"
            return f"error:{err.code}"
        except TransientDeliveryError as err:
            self._deliveries.record(subject, delivery_id, DeliveryStatus.ERROR, f"{err.message} (will retry)")
            raise
        status = DeliveryStatus.OK if p.total else DeliveryStatus.EMPTY
        # Slack or SES took the digest: from here on nothing may make the queue send it again (scan cycle 2).
        # A cursor write that keeps failing is logged and left; the next digest then repeats these items.
        mark = uuid.uuid4().hex  # one per send, so a retry knows its own committed write (scan cycles 20, 21)
        for attempt in range(1, ADVANCE_ATTEMPTS + 1):
            try:
                moved = self._deliveries.advance(d, p.cursor, status, token=mark)
            except Exception:
                if attempt < ADVANCE_ATTEMPTS:
                    continue
                log.exception(json.dumps({"event": "digest.advance_failed", "delivery": delivery_id}))
                return status.value
            if not moved:
                # Another sender moved the cursor first, or the owner deleted the delivery: advance() says which
                # from its own failed write, so nothing after the send can fail the message (scan cycles 17, 18).
                event = "digest.deleted" if moved is None else "digest.cursor_race"
                log.warning(json.dumps({"event": event, "delivery": delivery_id}))
            return status.value
        raise AssertionError("unreachable")  # pragma: no cover

    def test(self, d: Delivery) -> dict[str, Any]:
        """ "Send test now": the current digest once, without moving the cursor (the caller took the rate limit)."""
        total = 0
        sending = False
        try:
            p = self.prepare(d)  # a dead target is reported even when nothing is new (scan cycle 7)
            total = p.total
            if not total:
                return {"sent": False, "total": 0}
            fresh = self._deliveries.find(d.subject, d.id)  # a complaint meanwhile stops it too (scan cycle 15)
            if fresh is None:
                return {"sent": False, "total": total, "error": "this delivery was deleted"}
            if fresh.suppressed:  # the route audits this, then gives its own 409 for a stopped delivery (c16, c18)
                return {"sent": False, "total": total, "suppressed": True}
            sending = True  # from here a timeout or a 5xx may mean it went out (scan cycle 23)
            self.dispatch(p)
        except (PermanentDeliveryError, TransientDeliveryError) as err:
            return {"sent": False, "total": total, "error": err.message}
        except Exception as err:
            # The route audits the size it had built; the error itself goes on unchanged, so a throttle is still
            # a 503 (scan cycles 19 and 20).
            tag_digest_total(err, total, unsent=not sending)
            raise
        return {"sent": True, "total": total}

    # ---- the scheduler ------------------------------------------------------------------------------------

    def schedule(self, sqs: Any, now: datetime) -> dict[str, int]:
        """Queue every delivery that is due and move it on to its next time. The time moves first (a conditional
        write, so two ticks never queue one delivery twice); a message the queue refused puts it back."""
        out = {"queued": 0, "skipped": 0, "failed": 0}
        if not self._config.any or not self._config.queue_url:
            return out
        for due in self._deliveries.due(now):
            try:
                claimed = self._deliveries.claim(due, now)
            except Exception as err:
                # One row that cannot be read or moved on is counted and left; the rest of the tick goes on (scan
                # cycle 1, M2). A schedule that cannot be worked out (a time zone a tzdata update dropped) is its
                # owner's: shown to them, not alarmed (scan cycle 10). Anything else, a throttled or refused write
                # (retried in an hour) or a value or code the row cannot be read with (retried in a day, so one bad
                # row alarms once a day and a code fault on every row alarms at once), is the deployment's and
                # alarmed as digest.claim_failed (scan cycles 11 and 12).
                bad_schedule = isinstance(err, ValidationError)
                store_fault = isinstance(err, (ClientError, BotoCoreError))
                if bad_schedule:
                    log.warning(json.dumps({"event": "digest.schedule_invalid", "delivery": due.id}))
                else:
                    log.exception(json.dumps({"event": "digest.claim_failed", "delivery": due.id}))
                out["failed"] += 1
                try:  # out of the head of the due index, so it cannot hold back the rows behind it (scan cycle 2)
                    wait = CLAIM_RETRY if store_fault else SCHEDULE_RETRY
                    if self._deliveries.defer(due, now, wait) and bad_schedule:
                        # Its schedule cannot be worked out (most often a time zone a tzdata update dropped): shown
                        # to the owner, who can pick another zone, but not counted, since the cause may be the
                        # deployment's. Only when the row was moved: an owner who just fixed it sees no stale error
                        # (scan cycle 4).
                        self._deliveries.record(
                            due.subject, due.id, DeliveryStatus.ERROR, f"its schedule cannot be worked out: {err}"
                        )
                except Exception:
                    log.exception(json.dumps({"event": "digest.defer_failed", "delivery": due.id}))
                continue
            if claimed is None:
                out["skipped"] += 1
                continue
            try:
                sqs.send_message(
                    QueueUrl=self._config.queue_url,
                    # The claim names this message for the duplicate check. It travels in the body, so the message
                    # keeps it when it is redriven from the dead-letter queue under a new message id (scan cycle 3).
                    MessageBody=json.dumps(
                        {"subject": due.subject, "id": due.id, "due": due.next_at, "claim": uuid.uuid4().hex}
                    ),
                )
            except Exception:
                log.exception(json.dumps({"event": "digest.enqueue_failed", "delivery": due.id}))
                try:
                    self._deliveries.unclaim(due, claimed)
                except Exception:  # it waits for its next time; the cursor holds, so nothing is skipped
                    log.exception(json.dumps({"event": "digest.unclaim_failed", "delivery": due.id}))
                out["failed"] += 1
                continue
            out["queued"] += 1
        return out


@dataclass(frozen=True, slots=True)
class Queued:
    """What a queue message names: the delivery, the scheduled time it is for, the message that first took that
    time (a message put back for a Slack rate limit carries it on), and how often it was put back."""

    subject: str
    id: str
    due: str | None = None
    claim: str | None = None
    requeues: int = 0

    def body(self, claim: str) -> str:
        """The message that puts this send back on the queue once more."""
        return json.dumps(
            {"subject": self.subject, "id": self.id, "due": self.due, "claim": claim, "requeues": self.requeues + 1}
        )


def parse_message(body: str) -> Queued | None:
    """The delivery a queue message names, or None for anything else. An unreadable time or claim is left out
    (the send goes on without the duplicate check)."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    subject, delivery_id = data.get("subject"), data.get("id")
    if not isinstance(subject, str) or not _SUBJECT_RE.fullmatch(subject):
        return None
    if not isinstance(delivery_id, str) or not DELIVERY_ID_RE.fullmatch(delivery_id):
        return None

    def slot(name: str) -> str | None:
        value = data.get(name)
        return value if isinstance(value, str) and _SLOT_RE.fullmatch(value) else None

    requeues = data.get("requeues")
    count = requeues if isinstance(requeues, int) and not isinstance(requeues, bool) and requeues > 0 else 0
    return Queued(subject, delivery_id, slot("due"), slot("claim"), min(count, MAX_REQUEUES))


class Feedback:
    """SES bounce and complaint events (configuration set -> SNS): the delivery they name is suppressed. Only a
    permanent bounce or a complaint counts, and only for the address the delivery sends to."""

    def __init__(self, deliveries: DeliveryService) -> None:
        self._deliveries = deliveries

    def handle(self, event: dict[str, Any]) -> dict[str, int]:
        out = {"suppressed": 0, "ignored": 0}
        for record in event.get("Records") or []:
            raw = ((record or {}).get("Sns") or {}).get("Message", "")
            if self._one(raw):
                out["suppressed"] += 1
            else:
                out["ignored"] += 1
        return out

    def _one(self, raw: Any) -> bool:
        try:
            msg = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            return False
        if not isinstance(msg, dict):
            return False
        kind = str(msg.get("eventType") or msg.get("notificationType") or "")
        if kind == "Bounce":
            bounce = msg.get("bounce") or {}
            if bounce.get("bounceType") != "Permanent":
                return False
            reason = f"the address bounced ({str(bounce.get('bounceSubType', 'permanent'))[:40]})"
        elif kind == "Complaint":
            reason = "the digest was reported as spam"
        else:
            return False
        mail = msg.get("mail") or {}
        tags = mail.get("tags") or {}

        def tag(name: str) -> str:
            value = tags.get(name)
            return str(value[0]) if isinstance(value, list) and value else ""

        delivery_id, subject = tag("delivery"), subject_of_tag(tag("subscriber"))
        if not DELIVERY_ID_RE.fullmatch(delivery_id) or subject is None:
            return False
        # The subscriber tag is the subject, encoded as subscriber_tag wrote it (a UUID is as it is).
        d = self._deliveries.find(subject, delivery_id)
        if d is None or d.kind is not DeliveryKind.EMAIL:
            return False
        to = {str(a).lower() for a in mail.get("destination") or []}
        if (d.address or "").lower() not in to:
            return False
        return self._deliveries.switch_off(d.subject, d.id, suppress=True, reason=reason)
