import { Stack, StackProps, CfnOutput } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { Network } from './constructs/network';
import { Database } from './constructs/database';
import { Storage } from './constructs/storage';
import { Auth } from './constructs/auth';
import { McpRuntime } from './constructs/mcp-runtime';
import { SeedDatabase } from './constructs/seed-database';

export class DataStack extends Stack {
  public readonly network: Network;
  public readonly database: Database;
  public readonly storage: Storage;
  public readonly auth: Auth;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    this.network = new Network(this, 'Network');
    this.database = new Database(this, 'Database', { vpc: this.network.vpc });
    this.storage = new Storage(this, 'Storage');
    this.auth = new Auth(this, 'Auth');

    new McpRuntime(this, 'McpRuntime', {
      cluster: this.database.cluster,
      readOnlySecret: this.database.readOnlySecret,
      bucket: this.storage.bucket,
      ssmPrefix: '/internalagent/',
    });

    new SeedDatabase(this, 'SeedDatabase', {
      cluster: this.database.cluster,
      readOnlySecret: this.database.readOnlySecret,
    });

    new CfnOutput(this, 'VpcId', { value: this.network.vpc.vpcId });
    new CfnOutput(this, 'ClusterArn', { value: this.database.cluster.clusterArn });
    new CfnOutput(this, 'SecretArn', { value: this.database.cluster.secret!.secretArn });
    new CfnOutput(this, 'BucketName', { value: this.storage.bucket.bucketName });
  }
}
