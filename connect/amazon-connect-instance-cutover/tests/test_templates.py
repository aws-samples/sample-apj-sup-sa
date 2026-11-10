"""Template consistency checks (local only)."""
import json
import pathlib
import re

from cfnlint.decode import cfn_yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFN = ROOT / 'cloudformation'
SHARED_MODULES = {'region_utils', 'arn_mapping', 'migration_report', 'migration_filters'}
# Modules bundled into one function zip only (scripts/deploy.sh EXTRA).
EXTRA_MODULES = {'verify_migration': {'verify_target'}}


def functions():
    """{function name with ${Environment}: handler module} across all templates."""
    found = {}
    for path in CFN.glob('*.yaml'):
        template = cfn_yaml.load(str(path))
        for resource in template.get('Resources', {}).values():
            if resource.get('Type') != 'AWS::Lambda::Function':
                continue
            props = resource['Properties']
            name = props['FunctionName']['Fn::Sub']
            found[name] = (props['Handler'].split('.')[0], props['Code'])
    return found


def test_every_state_machine_function_is_defined_in_a_template():
    definition = (ROOT / 'step-functions' / 'migration-state-machine.json').read_text()
    called = set(re.findall(r'(connect-migration-[a-z0-9-]+-\$\{Environment\})', definition))
    defined = set(functions())
    assert called and called <= defined, sorted(called - defined)


def test_every_function_package_has_a_source_file():
    for name, (module, code) in functions().items():
        assert (ROOT / 'lambda' / f'{module}.py').exists(), name
        key = code['S3Key']['Fn::Sub']
        assert key == f'lambda/${{CodeVersion}}/{module}.zip', (name, key)
        assert module not in SHARED_MODULES


def test_no_inline_lambda_code_left():
    for name, (_, code) in functions().items():
        assert 'ZipFile' not in code, name


def test_deploy_script_bundles_every_local_module_a_function_imports():
    deploy = (ROOT / 'scripts' / 'deploy.sh').read_text()
    shared = set(re.findall(r'lambda/(\w+)\.py', re.search(r'^SHARED=\((.*)\)$', deploy, re.M).group(1)))
    assert shared == SHARED_MODULES
    local = {p.stem for p in (ROOT / 'lambda').glob('*.py')}
    for path in (ROOT / 'lambda').glob('*.py'):
        imported = set(re.findall(r'^\s*(?:from|import)\s+(\w+)', path.read_text(), re.M)) & local
        bundled = SHARED_MODULES | EXTRA_MODULES.get(path.stem, set()) | {path.stem}
        assert imported <= bundled, (path.name, sorted(imported - bundled))


def test_api_methods_require_cognito_except_preflight():
    template = cfn_yaml.load(str(CFN / '2-backend-api.yaml'))
    methods = [r['Properties'] for r in template['Resources'].values() if r['Type'] == 'AWS::ApiGateway::Method']
    assert len(methods) == 14
    for method in methods:
        if method['HttpMethod'] == 'OPTIONS':
            assert method['AuthorizationType'] == 'NONE' and method['Integration']['Type'] == 'MOCK'
        else:
            assert method['HttpMethod'] == 'POST'
            assert method['AuthorizationType'] == 'COGNITO_USER_POOLS'
    assert "'*'" not in json.dumps(template)


def test_no_hardcoded_accounts_or_regions_in_templates_and_code():
    pattern = re.compile(r'\b\d{12}\b|\b(?:us|eu|ap|ca|sa|me|af|il)-(?:east|west|north|south|central|northeast|southeast)-\d\b')
    paths = list(CFN.glob('*.yaml')) + list((ROOT / 'lambda').glob('*.py')) + [
        ROOT / 'frontend' / 'index.html', ROOT / 'scripts' / 'deploy.sh', ROOT / 'scripts' / 'deploy-target-role.sh',
        ROOT / 'step-functions' / 'migration-state-machine.json']
    offenders = {str(p.relative_to(ROOT)): pattern.findall(p.read_text()) for p in paths}
    # S3 API rule, not a deployment choice: CreateBucket in us-east-1 must omit LocationConstraint.
    offenders['lambda/create_prompts.py'] = [m for m in offenders['lambda/create_prompts.py'] if m != 'us-east-1']
    assert {k: v for k, v in offenders.items() if v} == {}


def test_buckets_are_private_encrypted_tls_only_and_logged():
    template = cfn_yaml.load(str(CFN / '1-frontend-infrastructure.yaml'))
    resources = template['Resources']
    buckets = {k: v['Properties'] for k, v in resources.items() if v['Type'] == 'AWS::S3::Bucket'}
    assert set(buckets) == {'WebsiteBucket', 'ArtifactsBucket', 'AccessLogsBucket'}
    for name, props in buckets.items():
        assert all(props['PublicAccessBlockConfiguration'].values()), name
        rule = props['BucketEncryption']['ServerSideEncryptionConfiguration'][0]
        assert rule['ServerSideEncryptionByDefault']['SSEAlgorithm'] == 'AES256', name
    policies = {v['Properties']['Bucket']['Ref']: v['Properties']['PolicyDocument']
                for v in resources.values() if v['Type'] == 'AWS::S3::BucketPolicy'}
    for name in buckets:
        deny = [s for s in policies[name]['Statement'] if s.get('Sid') == 'DenyInsecureTransport']
        assert deny and deny[0]['Condition'] == {'Bool': {'aws:SecureTransport': False}}, name
    for name in ('WebsiteBucket', 'ArtifactsBucket'):
        assert buckets[name]['VersioningConfiguration'] == {'Status': 'Enabled'}
        assert buckets[name]['LoggingConfiguration']['DestinationBucketName'] == {'Ref': 'AccessLogsBucket'}
    assert 'LoggingConfiguration' not in buckets['AccessLogsBucket']
    expiry = buckets['AccessLogsBucket']['LifecycleConfiguration']['Rules'][0]['ExpirationInDays']
    assert expiry == {'Ref': 'LogRetentionDays'} and template['Parameters']['LogRetentionDays']['Default'] == 90
    logging = resources['CloudFrontDistribution']['Properties']['DistributionConfig']['Logging']
    assert logging['Bucket'] == {'Fn::GetAtt': ['AccessLogsBucket', 'DomainName']}


def test_api_stage_traces_and_access_logging_is_opt_in_without_sensitive_fields():
    template = cfn_yaml.load(str(CFN / '2-backend-api.yaml'))
    params, resources = template['Parameters'], template['Resources']
    stage = resources['Stage']['Properties']
    assert stage['TracingEnabled'] is True
    assert params['EnableApiAccessLogging']['Default'] == 'false'
    assert params['ApiGatewayCloudWatchRoleArn']['Default'] == ''
    assert params['ApiAccessLogRetentionDays']['Default'] == 90
    assert resources['ApiGatewayAccount']['Condition'] == 'SetApiGatewayAccountRole'
    assert resources['ApiAccessLogGroup']['Condition'] == 'ApiAccessLogging'
    on_value = stage['AccessLogSetting']['Fn::If'][1]
    fields = set(json.loads(on_value['Format']))
    assert fields == {'requestId', 'ip', 'caller', 'httpMethod', 'resourcePath', 'status', 'responseLength',
                      'requestTime'}
    for forbidden in ('requestBody', 'body', 'authorization', 'claims', 'email'):
        assert forbidden not in on_value['Format'].lower()


def test_cloudfront_custom_domain_enforces_tls_12():
    template = cfn_yaml.load(str(CFN / '1-frontend-infrastructure.yaml'))
    assert template['Parameters']['CustomDomainName']['Default'] == ''
    assert template['Parameters']['AcmCertificateArn']['Default'] == ''
    config = template['Resources']['CloudFrontDistribution']['Properties']['DistributionConfig']
    condition, custom, default = config['ViewerCertificate']['Fn::If']
    assert condition == 'UseCustomDomain'
    assert custom['MinimumProtocolVersion'] == 'TLSv1.2_2021' and custom['SslSupportMethod'] == 'sni-only'
    assert default == {'CloudFrontDefaultCertificate': True}
    assert 'CustomDomainNeedsCertificate' in template['Rules']


def _csp(template, api_id):
    """The CSP the template produces, with stack values filled in as CloudFormation would."""
    policy = template['Resources']['SecurityHeadersPolicy']['Properties']['ResponseHeadersPolicyConfig']
    text, variables = policy['SecurityHeadersConfig']['ContentSecurityPolicy']['ContentSecurityPolicy']['Fn::Sub']
    stack = {'UserPoolDomain': 'pfx', 'AWS::Region': 'region-1', 'AWS::URLSuffix': 'amazonaws.com',
             'ApiId': api_id}
    condition, when_set, when_empty = variables['ApiSource']['Fn::If']
    assert condition == 'HasApiId'
    values = {'CognitoHost': variables['CognitoHost']['Fn::Sub'],
              'ApiSource': when_set['Fn::Sub'] if api_id else when_empty}
    resolve = lambda s: re.sub(r'\$\{([^}]+)\}', lambda m: stack[m.group(1)], s)  # noqa: E731
    text = re.sub(r'\$\{([^}]+)\}', lambda m: resolve(values[m.group(1)]), text)
    return {d.split()[0]: d.split()[1:] for d in (p.strip() for p in text.split(';')) if d}


def test_cloudfront_sends_security_headers_with_a_csp_for_the_page_api_and_cognito():
    template = cfn_yaml.load(str(CFN / '1-frontend-infrastructure.yaml'))
    resources = template['Resources']
    behavior = resources['CloudFrontDistribution']['Properties']['DistributionConfig']['DefaultCacheBehavior']
    assert behavior['ResponseHeadersPolicyId'] == {'Ref': 'SecurityHeadersPolicy'}
    config = resources['SecurityHeadersPolicy']['Properties']['ResponseHeadersPolicyConfig']
    headers = config['SecurityHeadersConfig']
    assert headers['StrictTransportSecurity']['AccessControlMaxAgeSec'] >= 31536000
    assert headers['StrictTransportSecurity']['Override'] is True
    assert headers['ContentTypeOptions'] == {'Override': True}
    assert headers['FrameOptions'] == {'FrameOption': 'DENY', 'Override': True}
    assert headers['ReferrerPolicy']['ReferrerPolicy'] == 'no-referrer'
    assert headers['ContentSecurityPolicy']['Override'] is True
    assert '${Environment}' in config['Name']['Fn::Sub']
    assert template['Parameters']['ApiId']['Default'] == ''

    csp = _csp(template, 'abcde12345')
    assert csp['default-src'] == ["'none'"] and csp['frame-ancestors'] == ["'none'"]
    assert csp['object-src'] == ["'none'"] and csp['base-uri'] == ["'none'"]
    assert csp['script-src'] == ["'self'", "'unsafe-inline'"] and csp['style-src'] == ["'self'", "'unsafe-inline'"]
    # Exactly the Cognito domain of this stack and this stack's API, derived from stack values.
    assert csp['connect-src'] == ['https://pfx.auth.region-1.amazoncognito.com',
                                  'https://abcde12345.execute-api.region-1.amazonaws.com']
    assert _csp(template, '')['connect-src'] == ['https://pfx.auth.region-1.amazoncognito.com']
    assert not any('*' in source for sources in csp.values() for source in sources)
    # The Cognito host matches the domain the page is configured with.
    assert (template['Outputs']['CognitoDomain']['Value']['Fn::Sub']
            == '${UserPoolDomain}.auth.${AWS::Region}.amazoncognito.com'
            == resources['SecurityHeadersPolicy']['Properties']['ResponseHeadersPolicyConfig'][
                'SecurityHeadersConfig']['ContentSecurityPolicy']['ContentSecurityPolicy']['Fn::Sub'][1][
                'CognitoHost']['Fn::Sub'])


def test_the_page_loads_nothing_the_csp_would_block():
    page = (ROOT / 'frontend' / 'index.html').read_text()
    # Scripts: config.js from the same origin and the inline script only; no other external resource.
    assert re.findall(r'<script[^>]*src="([^"]+)"', page) == ['config.js']
    assert not re.search(r'<link[^>]+href=|@import|<iframe|<object|<embed|<form', page)
    # The only absolute URLs the page builds are the Cognito domain (token call, sign in, sign out).
    assert set(re.findall(r'https://\$\{([^}]+)\}', page)) == {'CONFIG.cognitoDomain'}
    assert 'CONFIG.apiBaseUrl' in page  # API calls go to the configured API (connect-src)


def test_deploy_sets_the_api_id_on_the_frontend_stack_after_the_api_stack():
    script = (ROOT / 'scripts' / 'deploy.sh').read_text()
    first, api, second = (script.index('"ApiId=${EXISTING_API_ID}"'), script.index('2-backend-api.yaml'),
                          script.index('"ApiId=${API_ID}"'))
    assert first < api < second


def test_no_lex_v1_handling_is_left():
    """Lex V1 (Lex Classic) is not supported: no V1 listing, association, detection or step input."""
    pattern = re.compile(r"LexVersion['\"]?\s*[:=]\s*['\"]V1|lexV1|lex_v1|associatedV1|totalV1|v1Count|"
                         r"LexBot=|['\"]LexBot['\"]")
    paths = list((ROOT / 'lambda').glob('*.py')) + [ROOT / 'step-functions' / 'migration-state-machine.json',
                                                     ROOT / 'frontend' / 'index.html']
    offenders = {str(p.relative_to(ROOT)): pattern.findall(p.read_text()) for p in paths}
    assert {k: v for k, v in offenders.items() if v} == {}
