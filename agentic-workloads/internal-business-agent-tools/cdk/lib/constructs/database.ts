import { Construct } from 'constructs';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { RemovalPolicy } from 'aws-cdk-lib';

export interface DatabaseProps {
  vpc: ec2.IVpc;
}

export class Database extends Construct {
  public readonly cluster: rds.DatabaseCluster;

  constructor(scope: Construct, id: string, props: DatabaseProps) {
    super(scope, id);

    this.cluster = new rds.DatabaseCluster(this, 'Cluster', {
      engine: rds.DatabaseClusterEngine.auroraPostgres({
        version: rds.AuroraPostgresEngineVersion.VER_16_4,
      }),
      writer: rds.ClusterInstance.serverlessV2('Writer', {
        scaleWithWriter: true,
      }),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      defaultDatabaseName: 'internal_db',
      enableDataApi: true,
      removalPolicy: RemovalPolicy.DESTROY,
      serverlessV2MinCapacity: 0.5,
      serverlessV2MaxCapacity: 2,
    });
  }
}
