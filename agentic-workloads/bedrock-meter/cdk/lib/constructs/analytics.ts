import * as cdk from 'aws-cdk-lib';
import * as glue from 'aws-cdk-lib/aws-glue';
import * as athena from 'aws-cdk-lib/aws-athena';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface AnalyticsProps {
  archiveBucket: s3.IBucket;
  archiveKeyPrefix: string;
  databaseName?: string;
  tableName?: string;
  workgroupName?: string;
}

export class Analytics extends Construct {
  public readonly databaseName: string;
  public readonly tableName: string;
  public readonly workgroupName: string;
  public readonly resultBucket: s3.IBucket;

  constructor(scope: Construct, id: string, props: AnalyticsProps) {
    super(scope, id);

    const stack = cdk.Stack.of(this);
    this.databaseName = props.databaseName ?? 'bedrock_meter';
    this.tableName = props.tableName ?? 'invocations';
    this.workgroupName = props.workgroupName ?? 'bedrock-meter';

    new glue.CfnDatabase(this, 'GlueDatabase', {
      catalogId: stack.account,
      databaseInput: {
        name: this.databaseName,
        description: 'Bedrock invocation logs catalog',
      },
    });

    const tableLocation = `s3://${props.archiveBucket.bucketName}/${props.archiveKeyPrefix}AWSLogs/${stack.account}/BedrockModelInvocationLogs/${stack.region}/`;

    new glue.CfnTable(this, 'GlueTable', {
      catalogId: stack.account,
      databaseName: this.databaseName,
      tableInput: {
        name: this.tableName,
        tableType: 'EXTERNAL_TABLE',
        parameters: {
          classification: 'json',
          compressionType: 'gzip',
          typeOfData: 'file',
          'projection.enabled': 'true',
          'projection.year.type': 'integer',
          'projection.year.range': '2024,2099',
          'projection.month.type': 'integer',
          'projection.month.range': '1,12',
          'projection.month.digits': '2',
          'projection.day.type': 'integer',
          'projection.day.range': '1,31',
          'projection.day.digits': '2',
          'projection.hour.type': 'integer',
          'projection.hour.range': '0,23',
          'projection.hour.digits': '2',
          'storage.location.template': `${tableLocation}\${year}/\${month}/\${day}/\${hour}/`,
        },
        partitionKeys: [
          { name: 'year', type: 'string' },
          { name: 'month', type: 'string' },
          { name: 'day', type: 'string' },
          { name: 'hour', type: 'string' },
        ],
        storageDescriptor: {
          location: tableLocation,
          inputFormat: 'org.apache.hadoop.mapred.TextInputFormat',
          outputFormat: 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat',
          serdeInfo: {
            serializationLibrary: 'org.openx.data.jsonserde.JsonSerDe',
            parameters: {
              'ignore.malformed.json': 'true',
              'dots.in.keys': 'false',
            },
          },
          columns: [
            { name: 'schematype', type: 'string' },
            { name: 'schemaversion', type: 'string' },
            { name: 'timestamp', type: 'string' },
            { name: 'accountid', type: 'string' },
            { name: 'region', type: 'string' },
            { name: 'requestid', type: 'string' },
            { name: 'operation', type: 'string' },
            { name: 'modelid', type: 'string' },
            { name: 'identity', type: 'struct<arn:string>' },
            { name: 'requestmetadata', type: 'string' },
            {
              name: 'input',
              type: 'struct<inputcontenttype:string,inputtokencount:bigint,cachereadinputtokencount:bigint,cachewriteinputtokencount:bigint>',
            },
            {
              name: 'output',
              type: 'struct<outputcontenttype:string,outputtokencount:bigint>',
            },
          ],
        },
      },
    });

    this.resultBucket = new s3.Bucket(this, 'AthenaResultBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [{
        expiration: cdk.Duration.days(30),
        abortIncompleteMultipartUploadAfter: cdk.Duration.days(1),
      }],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    new athena.CfnWorkGroup(this, 'AthenaWorkgroup', {
      name: this.workgroupName,
      state: 'ENABLED',
      workGroupConfiguration: {
        enforceWorkGroupConfiguration: true,
        publishCloudWatchMetricsEnabled: true,
        bytesScannedCutoffPerQuery: 5 * 1024 * 1024 * 1024,
        resultConfiguration: {
          outputLocation: `s3://${this.resultBucket.bucketName}/results/`,
          encryptionConfiguration: { encryptionOption: 'SSE_S3' },
        },
      },
    });
  }

  grantQuery(grantee: iam.IGrantable, archiveBucket: s3.IBucket): void {
    const stack = cdk.Stack.of(this);
    grantee.grantPrincipal.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: [
        'athena:StartQueryExecution',
        'athena:GetQueryExecution',
        'athena:GetQueryResults',
        'athena:StopQueryExecution',
        'athena:GetWorkGroup',
      ],
      resources: [
        cdk.Arn.format({ service: 'athena', resource: 'workgroup', resourceName: this.workgroupName }, stack),
      ],
    }));
    grantee.grantPrincipal.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: [
        'glue:GetDatabase',
        'glue:GetTable',
        'glue:GetPartitions',
      ],
      resources: [
        cdk.Arn.format({ service: 'glue', resource: 'catalog' }, stack),
        cdk.Arn.format({ service: 'glue', resource: 'database', resourceName: this.databaseName }, stack),
        cdk.Arn.format({ service: 'glue', resource: 'table', resourceName: `${this.databaseName}/${this.tableName}` }, stack),
      ],
    }));
    archiveBucket.grantRead(grantee);
    this.resultBucket.grantReadWrite(grantee);
  }
}
