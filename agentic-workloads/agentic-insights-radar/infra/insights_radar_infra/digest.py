# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Digest delivery to Slack (a channel or a DM) and email (SES), built only when digestDelivery turns a
channel on; with everything off the stack has none of it.

* ``DigestSchedule`` (EventBridge, every 15 minutes) queries the subscriptions that are due on the
  ops table's sparse ``due`` index and puts one message per subscription on a KMS-encrypted queue.
* ``DigestSend`` (that queue, one message at a time, at most two at once) builds and sends a digest.
  A message that fails three receives goes to the digest dead-letter queue, which alarms.
* ``DigestFeedback`` (an encrypted SNS topic fed by the SES configuration set's bounce and complaint
  events) marks the subscription suppressed.

The Slack bot token or webhook URL and the unsubscribe-link HMAC key live in Secrets Manager on the
data key. The Slack secrets are created with a generated placeholder (never a token in the template,
and a later update never writes over the value the operator puts). Only the functions that send read
them: the sender and the API (which sends "Send test now" and resolves a DM's Slack ID).

``GET`` and ``POST /api/digest/unsubscribe`` are the only routes this adds without sign-in: a one-click
unsubscribe from an email client (RFC 8058) carries no session. The link's token is an HMAC of one
subscription id, so it can turn off that subscription and nothing else, and the route has its own low
throttle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from aws_cdk import Aws, Duration, Stack, Token
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ses as ses
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from insights_radar_infra.config import DigestDelivery
from insights_radar_infra.nag import (
    SLACK_STORE_REASON,
    UNSUBSCRIBE_KEY_REASON,
    suppress_digest_secret,
)

if TYPE_CHECKING:  # pragma: no cover
    from insights_radar_infra.backend import Backend

HANDLERS = "insights_radar.api.workers"
SCHEDULE_MINUTES = 15
SEND_TIMEOUT_SECONDS = 120  # the backend keeps 45 s of it for the send (SEND_RESERVE_MS), 75 s to build (scan cycle 4)
# Lambda's guidance for an SQS source: a visibility timeout of six times the function timeout, so a
# message is not received again while a slow attempt still runs.
VISIBILITY_TIMEOUT_SECONDS = 6 * SEND_TIMEOUT_SECONDS
MAX_RECEIVES = 3
SEND_CONCURRENCY = 2
UNSUBSCRIBE_PATH = "/api/digest/unsubscribe"
UNSUBSCRIBE_RATE = 5
UNSUBSCRIBE_BURST = 10
DMARC = '"v=DMARC1; p=reject;"'
# Ops-table partitions. A subscription and its cursor live in the subscriber's own partition
# (USER#<sub> / DELIVERY#<id>, next to the watches and read point a digest is built from); the
# scheduler reads the due index (partition DELIVERY_DUE). The sender also reads the profile (app name
# and list labels), the module list (gsi1 partition MODULES: custom lists and private writers, and
# the MODULES tombstones) and the admin's channel list (DIGEST / CHANNELS).
SUBSCRIPTIONS = ["USER#*"]
DUE_PARTITION = "DELIVERY_DUE"
SENDER_READS = ["PROFILE", "MODULES", "DIGEST"]
# The sender checks that a subscriber's account still exists, is enabled and keeps its address, and
# reads their role (what a DM or an email may show): read-only calls on this deployment's pool.
SENDER_USER_READS = [
    "cognito-idp:AdminGetUser",
    "cognito-idp:AdminListGroupsForUser",
    "cognito-idp:ListUsers",
]


def _generation(cid: str, n: int) -> str:
    """The construct ID of a secret's generation: unchanged at 0, so raising it from 0 replaces only that secret."""
    return cid if n == 0 else f"{cid}G{n}"


class Digest(Construct):
    def __init__(
        self,
        backend: Backend,
        cid: str,
        settings: DigestDelivery,
        api: lambda_.Function,
        failed_events: sqs.IQueue,
        site_url: str,
        zones: Mapping[str, route53.IHostedZone],
    ) -> None:
        super().__init__(backend, cid)
        data = backend.data
        prefix = backend.config.prefix
        self._settings = settings
        self._grants: list[iam.PolicyStatement] = []
        self.slack_secret: secretsmanager.Secret | None = None
        self.unsubscribe_secret: secretsmanager.Secret | None = None
        self.config_set_name: str | None = None
        self._topic: sns.Topic | None = None
        self.identity_arn = ""
        self._config_set_arn = ""

        # Both secrets exist for as long as digest delivery is on at all, whatever the channel modes: a
        # mode switch (bot and webhook, email off and on) then changes no secret, so the Slack value stays
        # where the operator put it and the unsubscribe key, so every link already sent, survives. Turning
        # digest delivery fully off, or changing a secret's generation (top-level options slackSecretGeneration
        # and unsubscribeKeyGeneration), retires one (scan cycles 60 to 63).
        slack_store = self._placeholder(
            _generation("SlackSecret", backend.config.slack_secret_generation),
            f"{prefix}: Slack bot token (xoxb-...) or incoming-webhook URL for digest delivery, as slackMode"
            " says: put the real value",
            backend,
            length=32,
        )
        unsubscribe_store = self._placeholder(
            _generation("UnsubscribeKey", backend.config.unsubscribe_key_generation),
            f"{prefix}: HMAC key of the digest emails' one-click unsubscribe links (generated)",
            backend,
            length=64,
        )
        suppress_digest_secret(slack_store, SLACK_STORE_REASON)
        suppress_digest_secret(unsubscribe_store, UNSUBSCRIBE_KEY_REASON)
        if settings.slack_mode != "off":
            self.slack_secret = slack_store
        if settings.email:
            self.unsubscribe_secret = unsubscribe_store
            self._email(backend, prefix, zones)

        self.dlq = sqs.Queue(
            self,
            "Dlq",
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=data.key,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        self.queue = sqs.Queue(
            self,
            "Queue",
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=data.key,
            enforce_ssl=True,
            visibility_timeout=Duration.seconds(VISIBILITY_TIMEOUT_SECONDS),
            retention_period=Duration.days(1),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=MAX_RECEIVES, queue=self.dlq
            ),
        )
        self.env = {
            "DIGEST_SLACK_MODE": settings.slack_mode,
            "DIGEST_DM_RESOLVER": settings.dm_resolver,
            "DIGEST_EMAIL_MODE": settings.email_mode,
            "DIGEST_EMAIL_SENDER": settings.email_sender or "",
            "DIGEST_SES_CONFIG_SET": self.config_set_name or "",
            "DIGEST_SLACK_SECRET_ARN": self.slack_secret.secret_arn
            if self.slack_secret
            else "",
            "DIGEST_UNSUB_SECRET_ARN": self.unsubscribe_secret.secret_arn
            if self.unsubscribe_secret
            else "",
            "DIGEST_QUEUE_URL": self.queue.queue_url,
            "DIGEST_DIRECTORY_ENDPOINT": settings.directory_endpoint or "",
            "DIGEST_DIRECTORY_ROLE_ARN": settings.directory_role_arn or "",
            "APP_URL": site_url,
        }

        schedule = backend.function(
            "DigestSchedule",
            f"{HANDLERS}.digest_schedule",
            f"Every {SCHEDULE_MINUTES} minutes: queue the digest deliveries that are due",
            timeout=120,
            env=self.env,
            dlq=failed_events,
            reserved=1,
        )
        backend.only_partitions(schedule, SUBSCRIPTIONS, read_only=[DUE_PARTITION])
        self.queue.grant_send_messages(schedule)
        self.rule_name = f"{prefix}-digest"
        events.Rule(
            self,
            "Schedule",
            rule_name=self.rule_name,
            schedule=events.Schedule.rate(Duration.minutes(SCHEDULE_MINUTES)),
            targets=[
                targets.LambdaFunction(
                    schedule, retry_attempts=0, dead_letter_queue=failed_events
                )
            ],
        )

        send = backend.function(
            "DigestSend",
            f"{HANDLERS}.digest_send",
            "Builds and sends one digest delivery (Slack or email)",
            timeout=SEND_TIMEOUT_SECONDS,
            env=self.env,
            reserved=SEND_CONCURRENCY,
        )
        backend.only_partitions(send, SUBSCRIPTIONS, read_only=SENDER_READS)
        data.graph_table.grant_read_data(send)  # the digest's records
        send.add_to_role_policy(
            iam.PolicyStatement(
                actions=SENDER_USER_READS, resources=[backend.user_pool_arn]
            )
        )
        send.add_event_source(
            sources.SqsEventSource(
                self.queue,
                batch_size=1,
                report_batch_item_failures=True,
                max_concurrency=SEND_CONCURRENCY,
            )
        )
        # A Slack rate limit puts the send back on the queue as a new, delayed message, so it does not use up
        # one of the MAX_RECEIVES receives (scan cycle 1).
        self.queue.grant_send_messages(send)
        self.send = send
        self.schedule = schedule

        # The API lists the options, takes the unsubscribe link and sends "Send test now" itself,
        # so it never writes to the queue (c2 security review: no queue grant for the API).
        for name, value in self.env.items():
            api.add_environment(name, value)
        for fn in (send, api):
            self._may_send(fn)

        self.feedback: lambda_.Function | None = None
        if self._topic is not None:
            feedback = backend.function(
                "DigestFeedback",
                f"{HANDLERS}.digest_feedback",
                "Marks a digest subscription suppressed on an SES bounce or complaint",
                env=self.env,
                dlq=failed_events,
            )
            backend.only_partitions(feedback, SUBSCRIPTIONS)
            feedback.add_event_source(sources.SnsEventSource(self._topic))
            self.feedback = feedback

    # ---- pieces ----------------------------------------------------------------------------------

    def _placeholder(
        self, cid: str, description: str, backend: Backend, length: int
    ) -> secretsmanager.Secret:
        """A generated value: CloudFormation generates it on create only, so the value an operator
        puts later is never overwritten by an update, and no token appears in the template. No fixed
        name: CloudFormation names each one, so a secret made again (digest delivery off and on, or a raised
        generation) never collides with a kept or deleted one's name (scan cycles 58 to 61)."""
        return secretsmanager.Secret(
            self,
            cid,
            description=description,
            encryption_key=backend.data.key,
            removal_policy=backend.data.removal,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=length, exclude_punctuation=True
            ),
        )

    def _email(
        self, backend: Backend, prefix: str, zones: Mapping[str, route53.IHostedZone]
    ) -> None:
        settings = self._settings
        key = backend.data.key
        region = Stack.of(self).region
        self.config_set_name = f"{prefix}-digest"
        config_set_arn = (
            f"arn:{Aws.PARTITION}:ses:{Aws.REGION}:{Aws.ACCOUNT_ID}:"
            f"configuration-set/{self.config_set_name}"
        )
        # The bounce and complaint events go to an encrypted topic. The event destination's L2 adds
        # the topic policy (SES may publish for this configuration set only); the key policy lets SES
        # use the data key for that and nothing else.
        topic = sns.Topic(self, "Feedback", master_key=key, enforce_ssl=True)
        key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowSesDigestFeedback",
                principals=[iam.ServicePrincipal("ses.amazonaws.com")],
                actions=["kms:GenerateDataKey", "kms:Decrypt"],
                resources=["*"],
                conditions={
                    "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                    "ArnEquals": {"aws:SourceArn": config_set_arn},
                },
            )
        )
        config_set = ses.ConfigurationSet(
            self,
            "ConfigurationSet",
            configuration_set_name=self.config_set_name,
            tls_policy=ses.ConfigurationSetTlsPolicy.REQUIRE,
            reputation_metrics=True,
            suppression_reasons=ses.SuppressionReasons.BOUNCES_AND_COMPLAINTS,
        )
        destination = config_set.add_event_destination(
            "FeedbackTopic",
            destination=ses.EventDestination.sns_topic(topic),
            events=[ses.EmailSendingEvent.BOUNCE, ses.EmailSendingEvent.COMPLAINT],
        )
        destination.node.add_dependency(topic)
        self._topic = topic

        if settings.ses_identity_arn:
            identity_region = settings.ses_identity_arn.split(":")[3]
            if not Token.is_unresolved(region) and identity_region != region:
                raise ValueError(
                    f"digestDelivery.sesIdentityArn is in {identity_region}; SES sends from the "
                    f"stack's Region ({region}), so the identity must be there"
                )
            self.identity_arn = settings.ses_identity_arn
        else:
            domain = str(settings.ses_domain)
            ses.EmailIdentity(
                self,
                "Identity",
                identity=ses.Identity.domain(domain),
                configuration_set=config_set,
            )
            self.identity_arn = f"arn:{Aws.PARTITION}:ses:{Aws.REGION}:{Aws.ACCOUNT_ID}:identity/{domain}"
            zone_id = settings.ses_hosted_zone_id or next(
                (
                    z.hosted_zone_id
                    for name, z in zones.items()
                    if domain == name or domain.endswith(f".{name}")
                ),
                None,
            )
            if zone_id is not None:
                self._dns(domain, zone_id)
            elif settings.ses_dmarc:
                # A DMARC policy that was asked for is never dropped without a word (scan cycle 4).
                raise ValueError(
                    "digestDelivery.sesDmarc needs a hosted zone for sesDomain: set sesHostedZoneId, or use a"
                    " domain in the stack's own domainName zone"
                )
        self._config_set_arn = config_set_arn

    def _dns(self, domain: str, zone_id: str) -> None:
        """Easy DKIM's three CNAMEs and, with sesDmarc, a DMARC policy that rejects unauthenticated
        mail. Names are absolute, so the zone may be the domain's own or a parent's."""
        identity = self.node.find_child("Identity")
        assert isinstance(identity, ses.EmailIdentity)
        for n, (name, value) in enumerate(
            (
                (identity.dkim_dns_token_name1, identity.dkim_dns_token_value1),
                (identity.dkim_dns_token_name2, identity.dkim_dns_token_value2),
                (identity.dkim_dns_token_name3, identity.dkim_dns_token_value3),
            ),
            start=1,
        ):
            route53.CfnRecordSet(
                self,
                f"Dkim{n}",
                hosted_zone_id=zone_id,
                name=name,
                type="CNAME",
                ttl="1800",
                resource_records=[value],
            )
        if not self._settings.ses_dmarc:
            return
        route53.CfnRecordSet(
            self,
            "Dmarc",
            hosted_zone_id=zone_id,
            name=f"_dmarc.{domain}",
            type="TXT",
            ttl="1800",
            resource_records=[DMARC],
        )

    def _may_send(self, fn: lambda_.Function) -> None:
        """Read the delivery secrets, send from the one sender address, and look up a DM's Slack ID."""
        settings = self._settings
        secrets = [
            s.secret_arn for s in (self.slack_secret, self.unsubscribe_secret) if s
        ]
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"], resources=secrets
            )
        )
        if settings.email:
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["ses:SendEmail"],
                    resources=[self.identity_arn, self._config_set_arn],
                    conditions={
                        "StringEquals": {"ses:FromAddress": settings.email_sender}
                    },
                )
            )
        if settings.directory_role_arn:
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"], resources=[settings.directory_role_arn]
                )
            )
