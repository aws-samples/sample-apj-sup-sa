"""
Shared helpers for cross-Region and cross-account Amazon Connect migrations.

Credentials model
-----------------
* Source side: the Lambda execution role's own credentials, in the Region
  taken from the source instance ARN.
* Target side: always the target writer role when one is supplied, assumed
  with sts:AssumeRole and a mandatory ExternalId, in the Region taken from
  the target instance ARN. Without a role, the target must be in the same
  account as the tool (and the tool role then needs write permissions,
  which the shipped least privilege role does not have).

The ExternalId is never logged or returned.
"""
import functools
import re
import threading
import time
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError

# Standard retry mode retries throttling errors with backoff before the
# error reaches our code. Anything that still fails is treated as fatal.
BOTO_CONFIG = Config(retries={'mode': 'standard', 'max_attempts': 8})

# Default Amazon Connect API rate quotas (requests per second, per account and
# Region; Service Quotas, adjustable) for the Describe calls the API endpoints
# run in parallel. Pacing at the default keeps parallel calls from turning into
# throttling retries with backoff, which cost more time than they save.
CONNECT_DESCRIBE_RATES = {
    'describe_contact_flow': 2,         # L-41C7214A
    'describe_contact_flow_module': 2,  # L-56B34560
    'describe_queue': 10,               # L-FAC57D08
    'describe_quick_connect': 10,       # L-2DE43297
    'describe_user': 10,                # L-EE6F0D82
}


class RateLimiter:
    """Thread safe pacing: at most `rate` calls per second, spread evenly (no burst)."""

    def __init__(self, rate, clock=time.monotonic, sleep=time.sleep):
        self.interval = 1.0 / rate
        self._clock, self._sleep = clock, sleep
        self._next = None
        self._lock = threading.Lock()

    def wait(self, deadline=None):
        """Block until the next free slot and return True; False at once if that slot is at or after deadline.

        A declined slot is not reserved, so it does not delay later callers.
        """
        with self._lock:
            now = self._clock()
            slot = now if self._next is None else max(now, self._next)
            if deadline is not None and slot >= deadline:
                return False
            self._next = slot + self.interval
        if slot > now:
            self._sleep(slot - now)
        return True

ACCESS_DENIED_CODES = frozenset({
    'AccessDenied',
    'AccessDeniedException',
    'UnauthorizedOperation',
    'UnauthorizedException',
    'AuthorizationError',
})

THROTTLING_CODES = frozenset({
    'Throttling',
    'ThrottlingException',
    'ThrottledException',
    'TooManyRequestsException',
    'RequestLimitExceeded',
    'RequestThrottled',
    'RequestThrottledException',
    'SlowDown',
})

# An invalid or unrecognised security token. It means the credentials are wrong, never that a
# Region lacks a feature, so it fails the step (and the run) like AccessDenied.
INVALID_TOKEN_CODES = frozenset({
    'UnrecognizedClientException',
})

# STS ExternalId constraints: 2 to 1224 characters from this set.
EXTERNAL_ID_PATTERN = re.compile(r'^[\w+=,.@:/-]{2,1224}$')

ROLE_ARN_PATTERN = re.compile(r'^arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]{1,512}$')

# Region codes are checked by format only, never against a list, so a Region that
# Amazon Connect Customer launches later works without a code change.
REGION_PATTERN = re.compile(r'^[a-z]{2,}(?:-[a-z]+)+-\d+$')

_UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE
)


class MigrationStepError(Exception):
    """An error that must fail the current migration step.

    Raised for AccessDenied, throttling that survived retries, and invalid
    credential configuration. Callers must not swallow it.
    """

    def __init__(self, message, code=None, action=None, resource=None):
        super().__init__(message)
        self.code = code
        self.action = action
        self.resource = resource


class ArnFormatError(ValueError):
    """An ARN in the request is malformed. The text holds fixed wording and the ARN part only."""


class SanitizedStepError(Exception):
    """Mixin of every exception a state machine handler re-raises (see step_handler).

    The text is the exception type, the AWS error code (if any) and the action,
    never the original message, str(e), resource names or IDs: Step Functions
    copies a function error into the execution history (the Catch Cause), and
    Lambda copies it into the log and into dead letter queue attributes.
    """

    def __init__(self, error_type, code=None, action=None):
        text = f"{error_type}: error code {code or 'none'}, action {action or 'none'}"
        Exception.__init__(self, text)
        self.error_type, self.code, self.action, self.resource = error_type, code, action, None
        # ClientError compatible, so error_code() and operation_name still work on it.
        self.response = {'Error': {'Code': code or ''}}
        self.operation_name = action

    def __str__(self):
        return self.args[0]


_SANITIZED_CLASSES = {}


def _sanitized_class(original_class):
    """A subclass of the original exception class with the same name.

    The Lambda Python runtime reports type(err).__name__ as the errorType, which
    Step Functions matches against Retry and Catch ErrorEquals, so the name must
    not change. Subclassing keeps isinstance() for callers in the same process.
    """
    cls = _SANITIZED_CLASSES.get(original_class)
    if cls is None:
        namespace = {'__module__': original_class.__module__, '__qualname__': original_class.__qualname__}
        try:
            cls = type(original_class.__name__, (SanitizedStepError, original_class), namespace)
        except TypeError:  # an exception class that cannot be combined: keep the name only
            cls = type(original_class.__name__, (SanitizedStepError,), namespace)
        _SANITIZED_CLASSES[original_class] = cls
    return cls


def _sanitized_instance(original_class, error_type, code, action):
    return _sanitized_class(original_class)(error_type, code, action)


def sanitized_error(err):
    """The re-raisable form of err: same class name, type, error code and action only."""
    if isinstance(err, SanitizedStepError):
        return err
    code = err.code if isinstance(err, MigrationStepError) else error_code(err)
    action = err.action if isinstance(err, MigrationStepError) else getattr(err, 'operation_name', None)
    return _sanitized_instance(type(err), type(err).__name__, code or None, action or None)


def step_handler(handler):
    """Decorator for every state machine Lambda handler: unexpected errors leave sanitized.

    Results are returned unchanged. Any exception is re-raised as sanitized_error(err)
    with its context suppressed, so its message (which can echo request values such
    as usernames, emails or resource names) never reaches the execution history,
    the log or a dead letter queue. tests/test_step_errors.py fails if a state
    machine handler is not wrapped.
    """
    @functools.wraps(handler)
    def wrapper(event, context):
        try:
            return handler(event, context)
        except Exception as err:
            raise sanitized_error(err) from None
    wrapper.sanitizes_errors = True
    return wrapper


def error_code(err):
    """Return the AWS error code of a ClientError, or '' for other errors."""
    if isinstance(err, ClientError):
        return err.response.get('Error', {}).get('Code', '')
    return ''


# Logging policy: log resource IDs, ARNs, counts, action names and error codes
# only. Never log names, usernames, emails, phone numbers, attribute or data table
# values, flow content or whole API payloads. tests/test_log_hygiene.py enforces it.
REDACTED = '<redacted>'


def redact(_value):
    """Stand in for a value that must not be logged (names, contact data, payloads)."""
    return REDACTED


ACCOUNT_PLACEHOLDER = '<account>'
# 12 or more digits in a row: an AWS account ID (longer runs are masked whole,
# so no 12 digit run survives).
_ACCOUNT_ID_RUN = re.compile(r'\d{12,}')


def mask_account_ids(value):
    """Loggable form of a value that embeds an account ID, such as a bucket name or S3 URI.

    Only for log text: callers keep using the real value for API calls.
    """
    return _ACCOUNT_ID_RUN.sub(ACCOUNT_PLACEHOLDER, f'{value}')


def describe_error(err):
    """Loggable summary of an exception: error code and operation, never the message.

    AWS error messages can echo request values (for example a username), so
    only the code and operation are kept.
    """
    if isinstance(err, MigrationStepError):
        return f"{err.code or 'MigrationStepError'} ({err.action or 'unknown action'})"
    if isinstance(err, ClientError):
        return f"{error_code(err) or 'ClientError'} ({getattr(err, 'operation_name', 'unknown operation')})"
    return type(err).__name__


# Create steps: errors that can mean "a resource with this name already exists".
# Connect returns InvalidParameterException for some existing resources (for
# example security profiles), not only DuplicateResourceException.
EXISTS_ERROR_CODES = frozenset({'DuplicateResourceException', 'ResourceConflictException',
                                'InvalidParameterException', 'ConflictException'})


def error_code_of(err):
    """AWS error code of a ClientError, otherwise the exception class name."""
    return error_code(err) or type(err).__name__


def failure_entry(original_id, err=None, reason=None):
    """One failedItems entry for a create step: ID and error code only, never names or values."""
    entry = {'originalId': original_id, 'status': 'failed',
             'errorCode': error_code_of(err) if err is not None else 'NotCreated'}
    if reason:
        entry['reason'] = reason
    return entry


def is_access_denied(err):
    return error_code(err) in ACCESS_DENIED_CODES


def is_throttling(err):
    return error_code(err) in THROTTLING_CODES


def fatal_error(err, resource=None):
    """Build (and log) a MigrationStepError from an AccessDenied or throttling ClientError.

    Keeps only the error code, the action name and the resource ID. The AWS
    error message is dropped from both the log line and the exception text:
    it can echo request values (names, usernames, policy details).
    """
    code = error_code(err)
    action = getattr(err, 'operation_name', None) or 'unknown action'
    if code in ACCESS_DENIED_CODES:
        error_type = 'AccessDenied'
    elif code in INVALID_TOKEN_CODES:
        error_type = 'InvalidToken'
    else:
        error_type = 'Throttling'
    resource_id = resource or 'unknown resource'
    print(f"FATAL: {error_type} ({code}) calling {action} on {resource_id}")
    return MigrationStepError(f"{error_type} ({code}) calling {action} on {resource_id}",
                              code=code, action=action, resource=resource)


def error_text(err):
    """Error text for an API response or a step output: never the AWS error message.

    MigrationStepError and ArnFormatError texts are built by this module from
    error codes, action names, resource IDs or ARNs and fixed wording, so they
    are returned as is. Anything else (ClientError, other exceptions) becomes
    its error code and operation via describe_error.
    """
    if isinstance(err, (MigrationStepError, ArnFormatError)):
        return str(err)
    return describe_error(err)


def reraise_if_fatal(err, resource=None):
    """Re-raise errors that must fail the step; return normally for anything else.

    Use as the first statement of a broad exception handler so that IAM gaps,
    invalid security tokens and throttling are never reported as an empty
    result or a skipped item.
    """
    if isinstance(err, MigrationStepError):
        raise err
    code = error_code(err)
    if code in ACCESS_DENIED_CODES or code in INVALID_TOKEN_CODES or code in THROTTLING_CODES:
        raise fatal_error(err, resource) from err


# A feature the Region does not offer (for example AI agents in a Region without them). Amazon Connect
# Customer does not document one error code for this per feature, and it cannot be verified without API
# calls, so one constant set is used: the code AWS returns for an operation it does not serve there.
# AccessDenied is never in it: an IAM gap and a Region gap look the same, so that stays fatal. Neither is
# UnrecognizedClientException: it means an invalid security token (INVALID_TOKEN_CODES), which fails.
FEATURE_UNAVAILABLE_CODES = frozenset({'UnknownOperationException'})
FEATURE_NOT_AVAILABLE = 'FeatureNotAvailableInRegion'
# Fixed wording per feature key (the keys of the start API 'features' input).
FEATURE_LABELS = {
    'rules': 'rules',
    'evaluationForms': 'performance evaluations',
    'dataTables': 'data tables',
    'views': 'views',
    'predefinedAttributes': 'predefined attributes',
    'vocabularies': 'custom vocabularies',
    'aiAgents': 'AI agents',
    'aiPrompts': 'AI prompts',
    'aiGuardrails': 'AI guardrails',
    'lexBots': 'Amazon Lex V2 bots',
}


def _host(url):
    return urlsplit(url or '').hostname or ''


def is_feature_unavailable(err, feature_client=None):
    """True when err means the Region does not offer the feature, so the step skips it with a follow up.

    Run reraise_if_fatal first: AccessDenied and throttling never reach this check. An
    EndpointConnectionError only counts when it names the endpoint of feature_client, the feature's
    own service (for example qconnect or lexv2-models). A connect endpoint that cannot be reached is
    a failure, never a missing feature.
    """
    if error_code(err) in FEATURE_UNAVAILABLE_CODES:
        return True
    if isinstance(err, EndpointConnectionError) and feature_client is not None:
        meta = feature_client.meta
        if meta.service_model.endpoint_prefix == 'connect':
            return False
        host = _host(meta.endpoint_url)
        return bool(host) and _host(err.kwargs.get('endpoint_url')) == host
    return False


class FeatureUnavailable(Exception):
    """Raised by a lookup helper when the Region does not offer the feature; holds the error code only."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def raise_if_feature_unavailable(err, feature_client=None):
    """In a broad handler, after reraise_if_fatal: turn a missing feature into FeatureUnavailable."""
    if is_feature_unavailable(err, feature_client):
        raise FeatureUnavailable(error_code_of(err)) from None


def feature_unavailable_follow_up(feature, region, side, code, count=None):
    """One manualFollowUp entry: fixed wording, the feature key, the side, the Region and the error code."""
    label = FEATURE_LABELS.get(feature, feature)
    entry = {
        'type': FEATURE_NOT_AVAILABLE, 'feature': feature, 'side': side, 'region': region, 'errorCode': code,
        'action': (f"The {side} Region {region} does not offer {label} in Amazon Connect Customer, so this "
                   "step skipped them. See the Amazon Connect Customer Regions page. Recreate them by hand "
                   "if the Region offers the feature later, or use a Region that offers it."),
    }
    if count is not None:
        entry['count'] = count
    return entry


def feature_skipped_result(feature, region, side, code, count=None, **result):
    """Step result for a feature the Region does not offer: SKIPPED, no failed item, one follow up.

    count is the number of items a create step could not create; with nothing to create (0) there
    is no follow up. A list step passes no count: the source items could not be read.
    """
    print(f"{feature} not available in the {side} Region {region} ({code}): step skipped")
    follow_up = [] if count == 0 else [feature_unavailable_follow_up(feature, region, side, code, count)]
    return {
        'statusCode': 200,
        'status': 'SKIPPED',
        'featureNotAvailable': {'feature': feature, 'side': side, 'region': region, 'errorCode': code},
        **result,
        'manualFollowUp': follow_up,
    }


def _clean_arn(arn):
    return arn.strip().replace('\n', '').replace('\r', '')


def _connect_arn_parts(instance_arn):
    instance_arn = _clean_arn(instance_arn)
    if not re.match(r'^arn:aws[a-z-]*:connect:', instance_arn):
        raise ArnFormatError(f"Invalid Connect instance ARN format: {instance_arn}")
    parts = instance_arn.split(':')
    if len(parts) < 6:
        raise ArnFormatError(f"Invalid ARN format: {instance_arn}")
    return parts


def extract_partition_from_arn(instance_arn):
    return _connect_arn_parts(instance_arn)[1]


def extract_region_from_arn(instance_arn):
    """Extract the Region from a Connect instance ARN."""
    region = _connect_arn_parts(instance_arn)[3]
    if not region:
        raise ArnFormatError(f"No region found in ARN: {instance_arn}")
    if not REGION_PATTERN.match(region):
        raise ArnFormatError(f"Invalid Region format in ARN: {region}")
    return region


def sdk_region_warning(instance_arn):
    """Soft warning when this SDK's endpoint data does not list the Region for Amazon Connect Customer.

    Returns fixed text with the Region and partition, or None. It never blocks a run:
    the SDK still builds the standard regional endpoint for a Region it does not list.
    """
    partition = extract_partition_from_arn(instance_arn)
    region = extract_region_from_arn(instance_arn)
    try:
        known = boto3.session.Session().get_available_regions('connect', partition_name=partition)
    except Exception:  # endpoint data unreadable: no warning rather than a failed start
        return None
    if region in known:
        return None
    return (f"Region {region} is not in this SDK's endpoint data for Amazon Connect Customer "
            f"(partition {partition}). The run continues with the standard regional endpoint.")


def extract_account_id_from_arn(instance_arn):
    """Extract the AWS account ID from a Connect instance ARN."""
    account_id = _connect_arn_parts(instance_arn)[4]
    if not account_id or not account_id.isdigit() or len(account_id) != 12:
        raise ArnFormatError(f"Invalid account ID in ARN: {account_id}")
    return account_id


def extract_instance_id_from_arn(instance_arn):
    """Extract the instance ID from a Connect instance ARN."""
    instance_arn = _clean_arn(instance_arn)
    _connect_arn_parts(instance_arn)
    match = re.search(r'instance/([0-9a-f-]+)', instance_arn, re.IGNORECASE)
    if not match:
        raise ArnFormatError(f"Invalid instance ARN format - no instance ID found: {instance_arn}")
    instance_id = match.group(1)
    if not _UUID_PATTERN.match(instance_id):
        raise ArnFormatError(f"Invalid instance ID format in ARN: {instance_id}")
    return instance_id


def is_cross_account(source_arn, destination_arn):
    return extract_account_id_from_arn(source_arn) != extract_account_id_from_arn(destination_arn)


def _sts_client():
    """Factory so tests can substitute a stubbed client."""
    return boto3.client('sts', config=BOTO_CONFIG)


def get_current_account_id():
    return _sts_client().get_caller_identity()['Account']


def _secrets_client(region):
    """Factory so tests can substitute a stubbed client."""
    return boto3.client('secretsmanager', region_name=region, config=BOTO_CONFIG)


SECRET_ARN_PATTERN = re.compile(r'^arn:aws[a-z-]*:secretsmanager:([a-z0-9-]+):\d{12}:secret:[\w/+=.@-]+$')


RAW_EXTERNAL_ID_KEY = 'externalId'


def resolve_external_id(source):
    """Return the ExternalId for a migration step event, never logging it.

    Fails closed: migration steps resolve the value only through
    'externalIdSecretArn' (the per execution secret the start API creates, so
    the value never appears in the Step Functions execution input or history).
    An event that carries a raw 'externalId' key is refused, whatever its value,
    so a direct start that skipped the secret cannot leak the value into the
    execution history. Returns None when the event has no secret ARN (same
    account runs without a target role).

    API handlers called directly by the UI use external_id_from_api_request.
    """
    if RAW_EXTERNAL_ID_KEY in (source or {}):
        raise MigrationStepError(
            "Migration steps do not accept a raw 'externalId'. Start the migration through "
            "the start API, or create the ExternalId secret first and pass 'externalIdSecretArn'.",
            code='RawExternalIdRejected',
        )
    secret_arn = (source or {}).get('externalIdSecretArn')
    if secret_arn:
        match = SECRET_ARN_PATTERN.match(secret_arn)
        if not match:
            raise MigrationStepError('externalIdSecretArn is not a Secrets Manager secret ARN',
                                     code='InvalidExternalIdSecret')
        try:
            return _secrets_client(match.group(1)).get_secret_value(SecretId=secret_arn)['SecretString']
        except ClientError as err:
            if is_access_denied(err) or is_throttling(err):
                raise fatal_error(err, secret_arn) from err
            raise MigrationStepError(f"Cannot read the ExternalId secret: {error_code(err)}",
                                     code='ExternalIdSecretUnavailable', action='GetSecretValue',
                                     resource=secret_arn) from err
    return None


def external_id_from_api_request(body):
    """Return the raw ExternalId from a UI request body (API handlers only), never logging it.

    Only for the API Gateway handlers the UI calls directly (start, quota check,
    flow pre check, verification). The value lives only in that one request and
    is never stored. It never reads a secret, so a caller cannot point an API
    handler at another run's ExternalId secret. Migration steps must use
    resolve_external_id; tests/test_external_id_secret.py enforces the split.
    """
    return (body or {}).get(RAW_EXTERNAL_ID_KEY) or None


def validate_external_id(external_id):
    if not external_id:
        raise MigrationStepError(
            'ExternalId is required when a target role is used. '
            'Enter the ExternalId configured on the target role trust policy.',
            code='MissingExternalId',
        )
    if not EXTERNAL_ID_PATTERN.match(external_id):
        # Do not echo the value back.
        raise MigrationStepError(
            'ExternalId has an invalid format (2 to 1224 characters, letters, digits and +=,.@:/-).',
            code='InvalidExternalId',
        )


def assume_cross_account_role(destination_arn, cross_account_role_arn, external_id, session_suffix=''):
    """Assume the target writer role with an ExternalId and return temporary credentials.

    Every target side AssumeRole in the tool goes through this function.
    """
    if not cross_account_role_arn or not ROLE_ARN_PATTERN.match(cross_account_role_arn):
        raise MigrationStepError(
            f"Invalid target role ARN: {cross_account_role_arn!r}", code='InvalidRoleArn'
        )
    validate_external_id(external_id)
    dest_account = extract_account_id_from_arn(destination_arn)
    session_name = f"ConnectMigration-{dest_account}{session_suffix}"[:64]

    print(f"Assuming target role: {cross_account_role_arn}")
    try:
        response = _sts_client().assume_role(
            RoleArn=cross_account_role_arn,
            RoleSessionName=session_name,
            ExternalId=external_id,
            DurationSeconds=3600,
        )
    except ClientError as err:
        if is_access_denied(err) or is_throttling(err):
            raise fatal_error(err, cross_account_role_arn) from err
        raise
    return response['Credentials']


def create_destination_session(destination_arn, cross_account_role_arn=None, external_id=None,
                               session_suffix=''):
    """Return a boto3 Session for the target account, in the target Region.

    With a role ARN the role is always assumed (also for same account
    migrations), because the tool's own role is read only.
    """
    region = extract_region_from_arn(destination_arn)
    if cross_account_role_arn:
        creds = assume_cross_account_role(
            destination_arn, cross_account_role_arn, external_id, session_suffix
        )
        return boto3.Session(
            aws_access_key_id=creds['AccessKeyId'],
            aws_secret_access_key=creds['SecretAccessKey'],
            aws_session_token=creds['SessionToken'],
            region_name=region,
        )

    current_account = get_current_account_id()
    dest_account = extract_account_id_from_arn(destination_arn)
    if current_account != dest_account:
        raise MigrationStepError(
            f"Cross-account migration detected (tool account: {current_account}, "
            f"destination account: {dest_account}) but no crossAccountRoleArn provided.",
            code='MissingTargetRole',
        )
    print('No target role supplied; using the tool role for the target (same account).')
    return boto3.Session(region_name=region)


def create_connect_client_from_arn(instance_arn, credentials=None):
    """Create a Connect client for the Region in the instance ARN.

    Returns (client, instance_id, region).
    """
    region = extract_region_from_arn(instance_arn)
    instance_id = extract_instance_id_from_arn(instance_arn)
    print(f"Creating Connect client for region: {region}, instance: {instance_id}")
    kwargs = {'region_name': region, 'config': BOTO_CONFIG}
    if credentials:
        kwargs.update(
            aws_access_key_id=credentials['AccessKeyId'],
            aws_secret_access_key=credentials['SecretAccessKey'],
            aws_session_token=credentials['SessionToken'],
        )
    return boto3.client('connect', **kwargs), instance_id, region


def create_destination_client(destination_arn, cross_account_role_arn=None, external_id=None,
                              session=None):
    """Create a Connect client for the destination instance.

    Returns (client, instance_id, region). Pass an existing session from
    create_destination_session to avoid a second AssumeRole.
    """
    print(f"Creating destination client from ARN: {destination_arn}")
    region = extract_region_from_arn(destination_arn)
    instance_id = extract_instance_id_from_arn(destination_arn)
    if session is None:
        session = create_destination_session(destination_arn, cross_account_role_arn, external_id)
    client = session.client('connect', region_name=region, config=BOTO_CONFIG)
    return client, instance_id, region
