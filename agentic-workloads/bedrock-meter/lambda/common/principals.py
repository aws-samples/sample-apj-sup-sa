import re

USER_ARN_RE = re.compile(
    r'^arn:aws:iam::(?P<account>\d{12}):user/(?P<path>[A-Za-z0-9+=,.@_/-]+)$'
)
ROLE_ARN_RE = re.compile(
    r'^arn:aws:iam::(?P<account>\d{12}):role/(?P<path>[A-Za-z0-9+=,.@_/-]+)$'
)

# Assumed-role ARN: arn:aws:sts::<account>:assumed-role/<RoleName>/<session>. Only the
# RoleName is stable — the session suffix varies per assume-role call, so caller-identity
# checks must match on the role name, never the full ARN. This is CALLER-only (admin gate);
# enforcement TARGETS are still IAM users (validate_arn rejects sts:: ARNs).
ASSUMED_ROLE_ARN_RE = re.compile(
    r'^arn:aws:sts::(?P<account>\d{12}):assumed-role/'
    r'(?P<role>[A-Za-z0-9+=,.@_/-]+)/(?P<session>[^/]+)$'
)

# Strict IAM username (path-less). RFC: same chars as ARN segment, max 64.
# Forbids '/' to reject IAM paths and ARN-shaped input by accident.
USERNAME_RE = re.compile(r'^[A-Za-z0-9+=,.@_-]{1,64}$')


class UnsupportedPrincipalError(ValueError):
    """Raised for non-IAM-User principals (e.g. assumed-role / SSO)."""


def is_iam_user_arn(arn: str) -> bool:
    return isinstance(arn, str) and bool(USER_ARN_RE.match(arn))


def is_iam_role_arn(arn: str) -> bool:
    return isinstance(arn, str) and bool(ROLE_ARN_RE.match(arn))


def assumed_role_name(arn: str) -> str | None:
    """RoleName from an assumed-role ARN, or None if `arn` isn't one.

    The session suffix is non-deterministic, so callers match on the role name only.
    Non-raising (unlike validate_arn) — this is a CALLER-identity helper, not a target check.
    """
    if not isinstance(arn, str):
        return None
    m = ASSUMED_ROLE_ARN_RE.match(arn)
    return m.group('role').rsplit('/', 1)[-1] if m else None


def normalize_metering_principal(arn: str) -> str:
    """Collapse an STS role session to its stable IAM role ARN for metering."""
    if not isinstance(arn, str):
        return arn
    match = ASSUMED_ROLE_ARN_RE.match(arn)
    if not match:
        return arn
    return f"arn:aws:iam::{match.group('account')}:role/{match.group('role')}"


def is_enforceable_principal(arn: str, role_enforcement_enabled: bool = False) -> bool:
    return is_iam_user_arn(arn) or (role_enforcement_enabled and is_iam_role_arn(arn))


def validate_arn(arn: str) -> str:
    """Strict IAM User ARN check. Use at trust boundaries (API path params, etc.)."""
    if not isinstance(arn, str):
        raise ValueError(f'invalid ARN type: {type(arn).__name__}')
    if arn.startswith('arn:aws:sts::'):
        raise UnsupportedPrincipalError(f'SSO/assumed-role not supported: {arn!r}')
    if not USER_ARN_RE.match(arn):
        raise ValueError(f'invalid IAM user ARN: {arn!r}')
    return arn


def validate_username(name: str) -> str:
    """Strict username for API path params. Rejects IAM paths, slashes, special chars."""
    if not isinstance(name, str):
        raise ValueError(f'invalid username type: {type(name).__name__}')
    if not USERNAME_RE.match(name):
        raise ValueError(f'invalid username: {name!r} (must match [A-Za-z0-9+=,.@_-]{{1,64}})')
    return name


def username_to_arn(name: str, account_id: str) -> str:
    """Build an IAM User ARN from a path-less username."""
    validate_username(name)
    if not isinstance(account_id, str) or not account_id.isdigit() or len(account_id) != 12:
        raise ValueError(f'invalid account_id: {account_id!r}')
    return f'arn:aws:iam::{account_id}:user/{name}'


def arn_to_username(arn: str) -> str:
    """Extract the IAM username from an IAM User ARN.

    For ARNs with IAM paths (e.g. arn:aws:iam::123:user/team/alice), returns
    the leaf username 'alice'. Hot path / Athena store full ARN, so this is
    only used for display.
    """
    validate_arn(arn)
    return arn.rsplit('/', 1)[-1]


def extract_username(arn: str) -> str:
    """Final segment of the IAM User ARN, suitable for IAM API UserName param."""
    validate_arn(arn)
    return arn.rsplit('/', 1)[-1]


def extract_role_name(arn: str) -> str:
    if not is_iam_role_arn(arn):
        raise UnsupportedPrincipalError(f'not an IAM role ARN: {arn!r}')
    return arn.rsplit('/', 1)[-1]


def display_name(arn_or_name: str) -> str:
    """Human-readable last segment, accepts any ARN form or bare name."""
    if not isinstance(arn_or_name, str):
        return str(arn_or_name)
    return arn_or_name.rsplit('/', 1)[-1]
