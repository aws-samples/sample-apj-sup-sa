"""
Database Initialization Custom Resource Lambda
Loads schema, data, and views into Aurora PostgreSQL via RDS Data API.

Can be invoked:
1. Via CloudFormation Custom Resource (automatic deployment)
2. Via direct Lambda invoke (workshop mode)
"""
import boto3
import json
import os
import re
import urllib.request

# Configuration from environment
REGION = os.environ.get('AWS_REGION', 'us-west-2')

rds_client = boto3.client('rds-data', region_name=REGION)
s3_client = boto3.client('s3', region_name=REGION)

# Column type mappings for proper casting
UUID_COLS = {'plan_id', 'account_id', 'user_id', 'customer_id', 'unicorn_id', 'booking_id', 
             'transaction_id', 'availability_id', 'updated_by', 'parent_transaction_id', 'tracker_id'}
INT_COLS = {'user_limit', 'current_user_count', 'employee_count', 'year_of_making', 'seat_capacity', 'failed_login_attempts'}
DECIMAL_COLS = {'storage_limit_gb', 'monthly_price', 'current_storage_usage_gb', 'horn_length_cm', 'max_speed_kmh',
                'fuel_capacity', 'hourly_rate', 'purchase_price', 'base_hourly_rate', 'total_cost', 'late_return_hours',
                'damage_cost_estimate', 'amount', 'tax_amount', 'tax_rate', 'hourly_price'}
BOOL_COLS = {'is_custom', 'is_active', 'is_available', 'is_recurring', 'is_completed'}
TIMESTAMP_COLS = {'created_at', 'updated_at', 'activated_at', 'suspended_at', 'terminated_at', 'last_login_at',
                  'locked_until', 'expected_available_at', 'start_datetime', 'end_datetime', 'actual_start_datetime',
                  'actual_end_datetime', 'processed_at', 'refunded_at', 'datetime'}
DATE_COLS = {'next_billing_date', 'trial_end_date', 'last_service_date', 'next_service_due', 'purchase_date'}
ENUM_COLS = {
    'status': {'accounts': 'account_status_enum', 'unicorn_availability': 'unicorn_availability_status_enum', 'transactions': 'transaction_status_enum'},
    'billing_cycle': 'billing_cycle_enum',
    'billing_preference': 'billing_preference_enum',
    'role': 'user_role_enum',
    'customer_type': 'customer_type_enum',
    'transaction_type': 'transaction_type_enum',
    'payment_method': 'payment_method_enum'
}


def get_cast(col, table):
    """Get SQL type cast for a column."""
    if col in UUID_COLS:
        return '::uuid'
    if col in INT_COLS:
        return '::integer'
    if col in DECIMAL_COLS:
        return '::decimal'
    if col in BOOL_COLS:
        return '::boolean'
    if col in TIMESTAMP_COLS:
        return '::timestamp'
    if col in DATE_COLS:
        return '::date'
    if col in ENUM_COLS:
        enum_type = ENUM_COLS[col]
        if isinstance(enum_type, dict):
            return f'::{enum_type.get(table, "text")}'
        return f'::{enum_type}'
    return ''


def execute_sql(resource_arn, secret_arn, database, sql, params=None):
    """Execute SQL statement via RDS Data API."""
    kwargs = {
        "resourceArn": resource_arn,
        "secretArn": secret_arn,
        "database": database,
        "sql": sql
    }
    if params:
        kwargs["parameters"] = params
    return rds_client.execute_statement(**kwargs)


def execute_batch_sql(resource_arn, secret_arn, database, sql, param_sets):
    """Execute batch SQL statement via RDS Data API."""
    return rds_client.batch_execute_statement(
        resourceArn=resource_arn,
        secretArn=secret_arn,
        database=database,
        sql=sql,
        parameterSets=param_sets
    )


def enable_pgvector(resource_arn, secret_arn, database):
    """Enable pgvector extension."""
    print("Enabling pgvector extension...")
    execute_sql(resource_arn, secret_arn, database, "CREATE EXTENSION IF NOT EXISTS vector")
    print("[OK] pgvector extension enabled")


def load_schema_from_s3(resource_arn, secret_arn, database, bucket, key):
    """Load database schema from S3."""
    print(f"Loading schema from s3://{bucket}/{key}...")
    
    response = s3_client.get_object(Bucket=bucket, Key=key)
    schema_sql = response['Body'].read().decode('utf-8')
    
    # Split by semicolons but handle dollar-quoted strings (for functions)
    statements = []
    current_stmt = []
    in_dollar_quote = False
    
    for line in schema_sql.split('\n'):
        stripped = line.strip()
        if stripped.startswith('--'):
            continue
        if '$$' in line and not in_dollar_quote:
            in_dollar_quote = True
        elif '$$' in line and in_dollar_quote:
            in_dollar_quote = False
        current_stmt.append(line)
        if stripped.endswith(';') and not in_dollar_quote:
            stmt = '\n'.join(current_stmt).strip()
            if stmt and not stmt.startswith('--'):
                statements.append(stmt)
            current_stmt = []
    
    success_count = 0
    for i, stmt in enumerate(statements):
        if not stmt.strip():
            continue
        try:
            execute_sql(resource_arn, secret_arn, database, stmt)
            success_count += 1
        except Exception as e:
            if 'already exists' not in str(e).lower():
                print(f"  Warning: Statement {i + 1} failed: {str(e)[:100]}")
    
    print(f"[OK] Schema loaded: {success_count} statements executed")
    return success_count


def load_csv_from_s3(resource_arn, secret_arn, database, bucket, key, table, columns=None):
    """Load CSV data from S3 into a table."""
    import csv
    import re as _re_tbl
    from io import StringIO

    print(f"  Loading {table} from s3://{bucket}/{key}...")

    # Idempotency: if the table is already populated, skip the load. On a stack
    # UPDATE (vs first CREATE) the data already exists, so re-INSERTing every row
    # just hits duplicate-key errors on every batch — harmless but very slow
    # (the 30k-row availability table alone can exceed the Lambda's 15-min timeout
    # and fail the deploy). A fast count guard keeps demo UPDATEs quick. (table is
    # an internal, schema-controlled identifier; still validate it defensively.)
    if _re_tbl.match(r'^[A-Za-z_][A-Za-z0-9_]*$', table):
        try:
            res = execute_sql(resource_arn, secret_arn, database,
                              f"SELECT COUNT(*) AS n FROM {table}")  # nosec B608 - identifier validated above
            existing = res.get("records", [{}])
            n = existing[0][0].get("longValue", 0) if existing and existing[0] else 0
            if n and int(n) > 0:
                print(f"  Skipping {table} - already has {n} rows (idempotent re-run)")
                return 0
        except Exception as e:  # noqa: BLE001 - if the count fails, fall through and load
            print(f"  (count check for {table} failed: {str(e)[:80]}; loading anyway)")

    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        csv_content = response['Body'].read().decode('utf-8')
    except s3_client.exceptions.NoSuchKey:
        print(f"  Skipping {key} - not found")
        return 0
    
    reader = csv.DictReader(StringIO(csv_content))
    rows = list(reader)
    
    if not rows:
        print(f"  Skipping {table} - empty")
        return 0
    
    cols = columns or list(rows[0].keys())
    # The VALUES are bound parameters (never interpolated); only the table/column
    # IDENTIFIERS are formatted into the statement, and those come from the bundled
    # schema/CSV headers — not user input. Still, validate them against a strict
    # identifier pattern so a malformed CSV header can never inject SQL. (B608)
    _ident = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
    if not _ident.match(table):
        raise ValueError(f"unsafe table identifier: {table!r}")
    bad = [c for c in cols if not _ident.match(c)]
    if bad:
        raise ValueError(f"unsafe column identifier(s): {bad!r}")
    placeholders = ', '.join([f":{c}{get_cast(c, table)}" for c in cols])
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"  # nosec B608 - identifiers validated above; values are bound params
    
    BATCH_SIZE = 100
    count = 0
    
    for batch_start in range(0, len(rows), BATCH_SIZE):
        batch_rows = rows[batch_start:batch_start + BATCH_SIZE]
        param_sets = []
        
        for row in batch_rows:
            params = []
            for c in cols:
                val = row.get(c, '')
                if val == '' or val is None:
                    params.append({"name": c, "value": {"isNull": True}})
                else:
                    params.append({"name": c, "value": {"stringValue": str(val)}})
            param_sets.append(params)
        
        try:
            execute_batch_sql(resource_arn, secret_arn, database, sql, param_sets)
            count += len(batch_rows)
        except Exception as e:
            print(f"    Batch error: {str(e)[:80]}")
            # Fall back to single inserts
            for row in batch_rows:
                params = []
                for c in cols:
                    val = row.get(c, '')
                    if val == '' or val is None:
                        params.append({"name": c, "value": {"isNull": True}})
                    else:
                        params.append({"name": c, "value": {"stringValue": str(val)}})
                try:
                    execute_sql(resource_arn, secret_arn, database, sql, params)
                    count += 1
                except Exception:  # noqa: BLE001 - per-row best-effort load; skip a bad/duplicate row and continue
                    pass
    
    print(f"  [OK] Loaded {count} rows into {table}")
    return count


def load_all_data(resource_arn, secret_arn, database, bucket, data_prefix):
    """Load all CSV data in foreign-key order."""
    print("Loading CSV data...")
    
    tables = [
        ("subscription_plans", "subscription_plans.csv", None),
        ("accounts", "accounts.csv", None),
        ("users", "users.csv", None),
        ("customers", "customers.csv", [
            "customer_id", "account_id", "customer_type", "first_name", "last_name",
            "organization_name", "email", "phone_number", "address_line1", "address_line2",
            "city", "state_province", "postal_code", "country", "billing_preference",
            "created_at", "updated_at", "department", "title"
        ]),
        ("unicorns", "unicorns.csv", None),
        ("unicorn_availability", "unicorn_availability.csv", None),
        ("bookings", "bookings.csv", None),
        ("transactions", "transactions.csv", None),
        ("subscription_tracker", "subscription_tracker.csv", None),
    ]
    
    total_loaded = 0
    for table, filename, columns in tables:
        key = f"{data_prefix}/{filename}" if data_prefix else filename
        count = load_csv_from_s3(resource_arn, secret_arn, database, bucket, key, table, columns)
        total_loaded += count
    
    print(f"[OK] Data loading complete: {total_loaded} total rows")
    return total_loaded


def create_app_user_role(resource_arn, secret_arn, database):
    """Create a non-owner app_user role for application Lambdas.
    
    This role is NOT the table owner, so PostgreSQL RLS policies are automatically
    enforced. The postgres role (table owner) bypasses RLS and should only be used
    for schema migrations and emergency access.
    """
    import secrets
    import string
    
    password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    
    # Check if role already exists
    result = rds_client.execute_statement(
        resourceArn=resource_arn, secretArn=secret_arn, database=database,
        sql="SELECT 1 FROM pg_roles WHERE rolname = 'app_user'"
    )
    
    if result.get('records'):
        print("[OK] app_user role already exists, updating password")
        rds_client.execute_statement(
            resourceArn=resource_arn, secretArn=secret_arn, database=database,
            sql=f"ALTER ROLE app_user PASSWORD '{password}'"
        )
    else:
        print("Creating app_user role...")
        rds_client.execute_statement(
            resourceArn=resource_arn, secretArn=secret_arn, database=database,
            sql=f"CREATE ROLE app_user LOGIN PASSWORD '{password}'"
        )
    
    # Grant permissions
    for stmt in [
        "GRANT CONNECT ON DATABASE timely_unicorn TO app_user",
        "GRANT USAGE ON SCHEMA public TO app_user",
        "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_user",
        "GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_user",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO app_user",
    ]:
        rds_client.execute_statement(
            resourceArn=resource_arn, secretArn=secret_arn, database=database, sql=stmt
        )
    
    print("[OK] app_user role created with SELECT/INSERT/UPDATE on all tables")
    
    # Store credentials in Secrets Manager
    sm_client = boto3.client('secretsmanager', region_name=REGION)
    secret_name = f"agentic-analytics/aurora/app-credentials"
    secret_value = json.dumps({
        'username': 'app_user',
        'password': password,
        'host': os.environ.get('AURORA_ENDPOINT', ''),
        'port': 5432,
        'dbname': database
    })
    
    try:
        sm_client.create_secret(Name=secret_name, SecretString=secret_value)
        print(f"[OK] Stored app_user credentials in secret: {secret_name}")
    except sm_client.exceptions.ResourceExistsException:
        sm_client.update_secret(SecretId=secret_name, SecretString=secret_value)
        print(f"[OK] Updated app_user credentials in secret: {secret_name}")
    
    # Get the secret ARN
    secret_info = sm_client.describe_secret(SecretId=secret_name)
    app_secret_arn = secret_info['ARN']
    print(f"[OK] App secret ARN: {app_secret_arn}")
    
    return app_secret_arn


def init_database(resource_arn, secret_arn, database, bucket, schema_key, data_prefix):
    """Main initialization function."""
    print("=" * 60)
    print("Database Initialization")
    print("=" * 60)
    
    enable_pgvector(resource_arn, secret_arn, database)
    load_schema_from_s3(resource_arn, secret_arn, database, bucket, schema_key)
    load_all_data(resource_arn, secret_arn, database, bucket, data_prefix)
    app_secret_arn = create_app_user_role(resource_arn, secret_arn, database)
    
    print("=" * 60)
    print("Database initialization complete!")
    print("=" * 60)
    return app_secret_arn


def send_cfn_response(event, context, status, data=None, reason=None):
    """Send response to CloudFormation."""
    response_body = {
        'Status': status,
        'Reason': reason or f'See CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': event.get('PhysicalResourceId', context.log_stream_name),
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data or {}
    }
    
    # CFN always supplies an https presigned-S3 ResponseURL; verify the scheme
    # before opening it (closes the B310 file://-scheme risk).
    if not event['ResponseURL'].lower().startswith('https://'):
        raise ValueError('ResponseURL must be https')
    req = urllib.request.Request(
        event['ResponseURL'],
        data=json.dumps(response_body).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='PUT'
    )
    urllib.request.urlopen(req)  # nosec B310 - https scheme verified above


def lambda_handler(event, context):
    """Lambda handler for both CFN Custom Resource and direct invocation."""
    print(f"Event: {json.dumps(event)}")
    
    # Check if this is a CFN Custom Resource request
    is_cfn = 'RequestType' in event and 'ResponseURL' in event
    
    try:
        if is_cfn:
            request_type = event['RequestType']
            props = event['ResourceProperties']
            
            if request_type == 'Delete':
                # No-op on delete - preserve data
                send_cfn_response(event, context, 'SUCCESS')
                return {'status': 'success', 'message': 'Delete - no action taken'}
            
            # Create or Update
            resource_arn = props['AuroraClusterArn']
            secret_arn = props['DatabaseSecretArn']
            database = props.get('DatabaseName', 'timely_unicorn')
            bucket = props['ArtifactsBucket']
            schema_key = props.get('SchemaKey', 'schema/schema.sql')
            data_prefix = props.get('DataPrefix', 'data')
            
        else:
            # Direct invocation (workshop mode)
            resource_arn = event.get('AuroraClusterArn') or os.environ.get('AURORA_CLUSTER_ARN')
            secret_arn = event.get('DatabaseSecretArn') or os.environ.get('DATABASE_SECRET_ARN')
            database = event.get('DatabaseName', os.environ.get('DATABASE_NAME', 'timely_unicorn'))
            bucket = event.get('ArtifactsBucket') or os.environ.get('ARTIFACTS_BUCKET')
            schema_key = event.get('SchemaKey', 'schema/schema.sql')
            data_prefix = event.get('DataPrefix', 'data')
        
        # Run initialization
        app_secret_arn = init_database(resource_arn, secret_arn, database, bucket, schema_key, data_prefix)
        
        if is_cfn:
            send_cfn_response(event, context, 'SUCCESS', {
                'DatabaseInitialized': 'true',
                'AppDatabaseSecretArn': app_secret_arn or ''
            })
        
        return {'status': 'success', 'message': 'Database initialized successfully', 'AppDatabaseSecretArn': app_secret_arn}
        
    except Exception as e:
        print(f"Error: {str(e)}")
        if is_cfn:
            send_cfn_response(event, context, 'FAILED', reason=str(e))
        raise
