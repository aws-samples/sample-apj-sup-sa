import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { RemovalPolicy, Stack } from 'aws-cdk-lib';

export class Network extends Construct {
  public readonly vpc: ec2.Vpc;
  public readonly runtimeSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      natGateways: 0,
      availabilityZones: ['ap-northeast-1a', 'ap-northeast-1c'],
      subnetConfiguration: [
        {
          name: 'Isolated',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
    });
    this.vpc.applyRemovalPolicy(RemovalPolicy.DESTROY);

    this.runtimeSecurityGroup = new ec2.SecurityGroup(this, 'RuntimeSg', {
      vpc: this.vpc,
      description: 'Security group for AgentCore Runtime',
      allowAllOutbound: true,
    });

    this.vpc.addInterfaceEndpoint('SsmEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SSM,
    });

    this.vpc.addInterfaceEndpoint('SecretsManagerEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
    });

    this.vpc.addInterfaceEndpoint('RdsDataEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.RDS_DATA,
    });

    this.vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });
  }
}
