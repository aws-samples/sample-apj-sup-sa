#!/usr/bin/env python3
"""
Workshop Deployment Validation Tests

Validates that the CloudFormation workshop deployment is working correctly.
Run with: python3 -m pytest infrastructure/tests/test_workshop_deployment.py -v
"""
import boto3
import pytest
import os

REGION = os.environ.get('AWS_REGION', 'us-west-2')
STACK_NAME = os.environ.get('STACK_NAME', 'agentic-analytics-workshop')


@pytest.fixture(scope='module')
def stack_outputs():
    """Get CloudFormation stack outputs."""
    cfn = boto3.client('cloudformation', region_name=REGION)
    response = cfn.describe_stacks(StackName=STACK_NAME)
    outputs = {o['OutputKey']: o['OutputValue'] for o in response['Stacks'][0].get('Outputs', [])}
    return outputs


@pytest.fixture(scope='module')
def rds_client():
    return boto3.client('rds-data', region_name=REGION)


class TestDatabase:
    """Test database initialization."""
    
    EXPECTED_TABLES = ['accounts', 'customers', 'unicorns', 'bookings', 'transactions', 'users']
    EXPECTED_MIN_COUNTS = {
        'accounts': 2,
        'customers': 100,
        'unicorns': 50,
        'bookings': 1000,
        'transactions': 1000,
        'users': 5
    }
    
    def test_tables_exist(self, stack_outputs, rds_client):
        """Verify all expected tables exist."""
        sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        response = rds_client.execute_statement(
            resourceArn=stack_outputs['AuroraResourceArn'],
            secretArn=stack_outputs['DatabaseSecretArn'],
            database=stack_outputs['DatabaseName'],
            sql=sql
        )
        tables = [r[0]['stringValue'] for r in response['records']]
        
        for table in self.EXPECTED_TABLES:
            assert table in tables, f"Table {table} not found"
    
    def test_tables_have_data(self, stack_outputs, rds_client):
        """Verify tables have minimum expected row counts."""
        for table, min_count in self.EXPECTED_MIN_COUNTS.items():
            response = rds_client.execute_statement(
                resourceArn=stack_outputs['AuroraResourceArn'],
                secretArn=stack_outputs['DatabaseSecretArn'],
                database=stack_outputs['DatabaseName'],
                sql=f"SELECT COUNT(*) FROM {table}"
            )
            count = response['records'][0][0]['longValue']
            assert count >= min_count, f"Table {table} has {count} rows, expected >= {min_count}"


class TestGlue:
    """Test Glue Data Catalog."""
    
    def test_database_exists(self, stack_outputs):
        """Verify Glue database exists."""
        glue = boto3.client('glue', region_name=REGION)
        response = glue.get_database(Name=stack_outputs['GlueDatabaseName'])
        assert response['Database']['Name'] == stack_outputs['GlueDatabaseName']
    
    def test_tables_cataloged(self, stack_outputs):
        """Verify tables are cataloged in Glue."""
        glue = boto3.client('glue', region_name=REGION)
        response = glue.get_tables(DatabaseName=stack_outputs['GlueDatabaseName'])
        tables = [t['Name'] for t in response['TableList']]
        
        # Should have base tables + views
        assert len(tables) >= 10, f"Expected >= 10 Glue tables, got {len(tables)}"


class TestBedrockKB:
    """Test Bedrock Knowledge Base."""
    
    def test_kb_active(self, stack_outputs):
        """Verify KB is in ACTIVE status."""
        bedrock = boto3.client('bedrock-agent', region_name=REGION)
        response = bedrock.get_knowledge_base(knowledgeBaseId=stack_outputs['KnowledgeBaseId'])
        assert response['knowledgeBase']['status'] == 'ACTIVE'
    
    def test_data_source_available(self, stack_outputs):
        """Verify data source is AVAILABLE."""
        bedrock = boto3.client('bedrock-agent', region_name=REGION)
        response = bedrock.list_data_sources(knowledgeBaseId=stack_outputs['KnowledgeBaseId'])
        
        assert len(response['dataSourceSummaries']) > 0, "No data sources found"
        assert response['dataSourceSummaries'][0]['status'] == 'AVAILABLE'
    
    def test_retrieval_works(self, stack_outputs):
        """Verify KB retrieval returns results."""
        bedrock_runtime = boto3.client('bedrock-agent-runtime', region_name=REGION)
        response = bedrock_runtime.retrieve(
            knowledgeBaseId=stack_outputs['KnowledgeBaseId'],
            retrievalQuery={'text': 'What tables are in the database?'}
        )
        
        assert len(response['retrievalResults']) > 0, "No retrieval results"
        assert len(response['retrievalResults'][0]['content']['text']) > 100


class TestCognito:
    """Test Cognito User Pool."""
    
    def test_user_pool_exists(self, stack_outputs):
        """Verify Cognito User Pool exists."""
        cognito = boto3.client('cognito-idp', region_name=REGION)
        response = cognito.describe_user_pool(UserPoolId=stack_outputs['CognitoUserPoolId'])
        assert response['UserPool']['Id'] == stack_outputs['CognitoUserPoolId']
    
    def test_users_created(self, stack_outputs):
        """Verify test users were created."""
        cognito = boto3.client('cognito-idp', region_name=REGION)
        response = cognito.list_users(UserPoolId=stack_outputs['CognitoUserPoolId'])
        
        assert len(response['Users']) >= 5, f"Expected >= 5 users, got {len(response['Users'])}"


class TestCodeEditor:
    """Test EC2 Code Editor (workshop mode only)."""
    
    def test_instance_running(self, stack_outputs):
        """Verify EC2 instance is running."""
        if 'CodeEditorUrl' not in stack_outputs:
            pytest.skip("Code Editor not deployed (not workshop mode)")
        
        ec2 = boto3.client('ec2', region_name=REGION)
        response = ec2.describe_instances(
            Filters=[
                {'Name': 'tag:Name', 'Values': ['*code-editor*']},
                {'Name': 'instance-state-name', 'Values': ['running']}
            ]
        )
        
        instances = response['Reservations']
        assert len(instances) > 0, "No running code editor instance found"
    
    def test_cloudfront_url_accessible(self, stack_outputs):
        """Verify CloudFront URL is set."""
        if 'CodeEditorUrl' not in stack_outputs:
            pytest.skip("Code Editor not deployed (not workshop mode)")
        
        url = stack_outputs['CodeEditorUrl']
        assert url.startswith('https://'), f"Invalid URL: {url}"
        assert 'cloudfront.net' in url


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
