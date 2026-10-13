import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { RemovalPolicy } from 'aws-cdk-lib';
import * as path from 'path';

export class Storage extends Construct {
  public readonly bucket: s3.Bucket;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    this.bucket = new s3.Bucket(this, 'DocsBucket', {
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    new s3deploy.BucketDeployment(this, 'SeedDocuments', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '../../seed/documents'))],
      destinationBucket: this.bucket,
    });
  }
}
