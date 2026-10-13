import * as cdk from "aws-cdk-lib/core";
import { Construct } from "constructs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as path from "path";

/**
 * WebStack — static hosting for the Aisle frontend.
 *
 *   private S3 bucket (OAC, no public access)
 *   → CloudFront distribution (HTTPS only, SPA 403/404 → /index.html)
 *   → BucketDeployment of frontend/dist
 *
 * The frontend is built with VITE_SESSION_API_URL baked in. We read that value
 * from SSM (/aisle/session/url, exported by ApiStack) if present, else fall back
 * to the known deployed start endpoint so this stack is independently deployable.
 *
 * Exports /aisle/web/url.
 */
export interface WebStackProps extends cdk.StackProps {
  /** Path to the built frontend (frontend/dist). */
  readonly distPath: string;
}

export class WebStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: WebStackProps) {
    super(scope, id, props);

    const bucket = new s3.Bucket(this, "SiteBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      // Demo bucket — destroy with the stack.
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const distribution = new cloudfront.Distribution(this, "SiteDistribution", {
      defaultRootObject: "index.html",
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      // SPA routing: serve index.html for client-side routes / missing keys.
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: "/index.html" },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: "/index.html" },
      ],
      priceClass: cloudfront.PriceClass.PRICE_CLASS_ALL,
      comment: "Aisle voice grocery assistant — demo frontend",
    });

    new s3deploy.BucketDeployment(this, "DeploySite", {
      sources: [s3deploy.Source.asset(props.distPath)],
      destinationBucket: bucket,
      distribution,
      distributionPaths: ["/*"], // invalidate index.html on each deploy
    });

    new ssm.StringParameter(this, "WebUrlParam", {
      parameterName: "/aisle/web/url",
      stringValue: `https://${distribution.distributionDomainName}`,
    });

    new cdk.CfnOutput(this, "SiteUrl", {
      value: `https://${distribution.distributionDomainName}`,
    });
    new cdk.CfnOutput(this, "BucketName", { value: bucket.bucketName });
  }
}
