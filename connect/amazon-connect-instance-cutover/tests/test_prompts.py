"""Temporary prompt bucket: ownership check and hardening (stubbed S3, no AWS)."""
import json
import re

import pytest

import create_prompts

ACCOUNT = '222222222222'
REGION = 'ap-southeast-2'
BUCKET = f'connect-migration-prompts-temp-{ACCOUNT}-{REGION}'


def expect_hardening(stub):
    stub.add_response('put_public_access_block', {}, {
        'Bucket': BUCKET, 'ExpectedBucketOwner': ACCOUNT,
        'PublicAccessBlockConfiguration': {'BlockPublicAcls': True, 'IgnorePublicAcls': True,
                                           'BlockPublicPolicy': True, 'RestrictPublicBuckets': True}})
    stub.add_response('put_bucket_encryption', {}, {
        'Bucket': BUCKET, 'ExpectedBucketOwner': ACCOUNT,
        'ServerSideEncryptionConfiguration': {'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]}})
    stub.add_response('put_bucket_policy', {}, {
        'Bucket': BUCKET, 'ExpectedBucketOwner': ACCOUNT, 'Policy': json.dumps({
            'Version': '2012-10-17',
            'Statement': [{'Sid': 'DenyInsecureTransport', 'Effect': 'Deny', 'Principal': '*', 'Action': 's3:*',
                           'Resource': [f'arn:aws:s3:::{BUCKET}', f'arn:aws:s3:::{BUCKET}/*'],
                           'Condition': {'Bool': {'aws:SecureTransport': 'false'}}}]})})


def test_new_bucket_is_created_and_hardened(stubbed):
    s3, stub = stubbed('s3', REGION)
    stub.add_client_error('head_bucket', service_error_code='404', http_status_code=404,
                          expected_params={'Bucket': BUCKET, 'ExpectedBucketOwner': ACCOUNT})
    stub.add_response('create_bucket', {}, {'Bucket': BUCKET, 'CreateBucketConfiguration': {'LocationConstraint': REGION}})
    stub.add_response('put_bucket_lifecycle_configuration', {}, {
        'Bucket': BUCKET, 'LifecycleConfiguration': {'Rules': [{
            'ID': 'DeleteTempPrompts', 'Status': 'Enabled', 'Filter': {'Prefix': 'prompts/'}, 'Expiration': {'Days': 1}}]}})
    expect_hardening(stub)
    assert create_prompts.get_or_create_temp_bucket(s3, REGION, ACCOUNT) == BUCKET


def test_existing_bucket_is_verified_as_ours_and_hardened_again(stubbed):
    s3, stub = stubbed('s3', REGION)
    stub.add_response('head_bucket', {}, {'Bucket': BUCKET, 'ExpectedBucketOwner': ACCOUNT})
    expect_hardening(stub)
    assert create_prompts.get_or_create_temp_bucket(s3, REGION, ACCOUNT) == BUCKET


TWELVE_DIGITS = re.compile(r'\d{12}')
MASKED_BUCKET = f'connect-migration-prompts-temp-<account>-{REGION}'


def test_existing_bucket_log_line_masks_the_account_id(stubbed, capsys):
    s3, stub = stubbed('s3', REGION)
    stub.add_response('head_bucket', {}, {'Bucket': BUCKET, 'ExpectedBucketOwner': ACCOUNT})
    expect_hardening(stub)
    assert create_prompts.get_or_create_temp_bucket(s3, REGION, ACCOUNT) == BUCKET
    out = capsys.readouterr().out
    assert f'Using existing bucket: {MASKED_BUCKET}' in out
    assert not TWELVE_DIGITS.search(out)


def test_new_bucket_log_lines_mask_the_account_id(stubbed, capsys):
    s3, stub = stubbed('s3', REGION)
    stub.add_client_error('head_bucket', service_error_code='404', http_status_code=404,
                          expected_params={'Bucket': BUCKET, 'ExpectedBucketOwner': ACCOUNT})
    stub.add_response('create_bucket', {}, {'Bucket': BUCKET, 'CreateBucketConfiguration': {'LocationConstraint': REGION}})
    stub.add_response('put_bucket_lifecycle_configuration', {}, {
        'Bucket': BUCKET, 'LifecycleConfiguration': {'Rules': [{
            'ID': 'DeleteTempPrompts', 'Status': 'Enabled', 'Filter': {'Prefix': 'prompts/'}, 'Expiration': {'Days': 1}}]}})
    expect_hardening(stub)
    assert create_prompts.get_or_create_temp_bucket(s3, REGION, ACCOUNT) == BUCKET
    out = capsys.readouterr().out
    assert f'Creating bucket: {MASKED_BUCKET}' in out
    assert f'Created bucket with lifecycle policy: {MASKED_BUCKET}' in out
    assert not TWELVE_DIGITS.search(out)


class FakeResponse:
    def __init__(self, data):
        self.data, self.timeout = data, None

    def read(self, size):
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, data):
        self.data, self.calls = data, []

    def open(self, url, timeout=None):
        self.calls.append((url, timeout))
        return FakeResponse(self.data)


@pytest.mark.parametrize('url', ['http://bucket.s3.amazonaws.com/p.wav', 'file:///etc/passwd',
                                 'ftp://example.com/p.wav', '', None, 'https:///no-host'])
def test_download_rejects_anything_but_https(url, tmp_path, monkeypatch):
    monkeypatch.setattr(create_prompts.urllib.request, 'build_opener',
                        lambda *a: pytest.fail('must not open a connection'))
    with pytest.raises(create_prompts.PromptDownloadError):
        create_prompts.download_prompt_file(url, tmp_path / 'p.wav')


def test_download_https_with_timeout(tmp_path, monkeypatch):
    opener = FakeOpener(b'RIFF....WAVE')
    monkeypatch.setattr(create_prompts.urllib.request, 'build_opener', lambda *a: opener)
    target = tmp_path / 'p.wav'
    create_prompts.download_prompt_file('https://bucket.s3.amazonaws.com/p.wav?X-Amz-Signature=x', target)
    assert target.read_bytes() == b'RIFF....WAVE'
    assert opener.calls[0][1] == create_prompts.DOWNLOAD_TIMEOUT_SECONDS


def test_download_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(create_prompts, 'MAX_PROMPT_BYTES', 10)
    monkeypatch.setattr(create_prompts.urllib.request, 'build_opener', lambda *a: FakeOpener(b'x' * 11))
    with pytest.raises(create_prompts.PromptDownloadError):
        create_prompts.download_prompt_file('https://bucket.s3.amazonaws.com/p.wav', tmp_path / 'p.wav')


def test_redirect_to_http_is_refused():
    handler = create_prompts._HttpsOnlyRedirects()
    with pytest.raises(create_prompts.PromptDownloadError):
        handler.redirect_request(None, None, 302, 'Found', {}, 'http://example.com/p.wav')


class FakeS3:
    def __init__(self):
        self.uploads = []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.uploads.append((bucket, key, ExtraArgs))


def test_prompt_name_never_reaches_the_s3_key_or_logs(tmp_path, monkeypatch, capsys):
    name = 'Holiday greeting for Jane Doe'
    s3 = FakeS3()

    class Connect:
        def get_paginator(self, _):
            class P:
                def paginate(self, **kwargs):
                    return [{'PromptSummaryList': []}]
            return P()

        def create_prompt(self, **kwargs):
            return {'PromptId': 'dst-prompt-1', 'PromptARN': 'arn'}

    class Session:
        def client(self, *a, **k):
            return s3

    monkeypatch.setattr(create_prompts, 'create_destination_session', lambda *a: Session())
    monkeypatch.setattr(create_prompts, 'create_destination_client',
                        lambda *a, **k: (Connect(), '22222222-2222-4222-8222-222222222222', REGION))
    monkeypatch.setattr(create_prompts, 'get_or_create_temp_bucket', lambda *a: BUCKET)
    monkeypatch.setattr(create_prompts, 'download_prompt_file', lambda url, path: open(path, 'wb').write(b'RIFF'))
    result = create_prompts.lambda_handler({
        'destinationInstanceId': 'arn:aws:connect:ap-southeast-2:222222222222:instance/22222222-2222-4222-8222-222222222222',
        'prompts': [{'Name': name, 'PromptId': 'src-prompt-7', 'PromptPresignedUrl': 'https://bucket.s3.amazonaws.com/p'}],
    }, None)
    key = s3.uploads[0][1]
    assert key.startswith('prompts/22222222-2222-4222-8222-222222222222/src-prompt-7/') and key.endswith('.wav')
    assert 'Holiday' not in key and 'Jane' not in key
    out = capsys.readouterr().out
    assert 'Holiday' not in out and 'Jane' not in out
    assert result['promptMapping'] == {'src-prompt-7': 'dst-prompt-1'}


def run_one_prompt(monkeypatch, tmp_path, download, s3=None):
    """Run the handler for one prompt with tempfile pointed at tmp_path; return (result, temp dirs made)."""
    s3 = s3 or FakeS3()
    made = []

    def fake_mkdtemp(prefix=''):
        path = tmp_path / f'{prefix}{len(made)}'
        path.mkdir(mode=0o700)
        made.append(path)
        return str(path)

    class Connect:
        def get_paginator(self, _):
            class P:
                def paginate(self, **kwargs):
                    return [{'PromptSummaryList': []}]
            return P()

        def create_prompt(self, **kwargs):
            return {'PromptId': 'dst-prompt-1', 'PromptARN': 'arn'}

    class Session:
        def client(self, *a, **k):
            return s3

    monkeypatch.setattr(create_prompts.tempfile, 'mkdtemp', fake_mkdtemp)
    monkeypatch.setattr(create_prompts, 'create_destination_session', lambda *a: Session())
    monkeypatch.setattr(create_prompts, 'create_destination_client',
                        lambda *a, **k: (Connect(), '22222222-2222-4222-8222-222222222222', REGION))
    monkeypatch.setattr(create_prompts, 'get_or_create_temp_bucket', lambda *a: BUCKET)
    monkeypatch.setattr(create_prompts, 'download_prompt_file', download)
    result = create_prompts.lambda_handler({
        'destinationInstanceId': 'arn:aws:connect:ap-southeast-2:222222222222:instance/22222222-2222-4222-8222-222222222222',
        'prompts': [{'Name': 'p', 'PromptId': 'src-prompt-7', 'PromptPresignedUrl': 'https://bucket.s3.amazonaws.com/p'}],
    }, None)
    return result, made


def test_handler_log_lines_mask_the_account_id_but_use_the_real_bucket(tmp_path, monkeypatch, capsys):
    s3 = FakeS3()
    result, _ = run_one_prompt(monkeypatch, tmp_path, lambda url, path: open(path, 'wb').write(b'RIFF'), s3)
    assert s3.uploads[0][0] == BUCKET  # the API call keeps the real bucket name
    out = capsys.readouterr().out
    bucket_lines = [line for line in out.splitlines()
                    if line.startswith(('Using temporary S3 bucket:', 'Uploaded prompt to S3:'))]
    assert len(bucket_lines) == 2, out
    assert bucket_lines[0] == f'Using temporary S3 bucket: {MASKED_BUCKET}'
    assert bucket_lines[1].startswith(f'Uploaded prompt to S3: s3://{MASKED_BUCKET}/prompts/')
    assert ACCOUNT not in bucket_lines[1].split('/prompts/')[0]
    assert not TWELVE_DIGITS.search(bucket_lines[0])
    assert result['promptMapping'] == {'src-prompt-7': 'dst-prompt-1'}


def test_prompt_file_goes_to_a_tempfile_directory_that_is_removed(tmp_path, monkeypatch):
    seen = []

    def download(url, path):
        seen.append(path)
        with open(path, 'wb') as out:
            out.write(b'RIFF')

    result, made = run_one_prompt(monkeypatch, tmp_path, download)
    assert len(made) == 1 and seen == [str(made[0] / 'prompt.wav')]
    assert not made[0].exists()
    assert result['promptMapping'] == {'src-prompt-7': 'dst-prompt-1'}


def test_temp_directory_is_removed_when_the_download_fails(tmp_path, monkeypatch):
    def download(url, path):
        with open(path, 'wb') as out:
            out.write(b'partial')
        raise create_prompts.PromptDownloadError('too large')

    result, made = run_one_prompt(monkeypatch, tmp_path, download)
    assert len(made) == 1 and not made[0].exists()
    assert result['promptMapping'] == {}


def test_temp_directory_is_removed_when_the_upload_fails(tmp_path, monkeypatch):
    class FailingS3(FakeS3):
        def upload_file(self, path, bucket, key, ExtraArgs=None):
            raise OSError('disk')

    result, made = run_one_prompt(monkeypatch, tmp_path, lambda url, path: open(path, 'wb').close(), FailingS3())
    assert len(made) == 1 and not made[0].exists()
    assert result['promptMapping'] == {}


def test_no_hard_coded_tmp_path_in_lambda_code():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent / 'lambda'
    offenders = [p.name for p in root.glob('*.py') if re.search(r"['\"]/tmp\b", p.read_text())]
    assert offenders == []
