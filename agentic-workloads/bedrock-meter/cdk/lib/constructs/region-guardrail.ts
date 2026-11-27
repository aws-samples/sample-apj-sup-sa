import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface RegionGuardrailProps {
  bedrockRegion: string;
}

export class RegionGuardrail extends Construct {
  public readonly policy: iam.IManagedPolicy;

  constructor(scope: Construct, id: string, props: RegionGuardrailProps) {
    super(scope, id);

    this.policy = new iam.ManagedPolicy(this, 'Policy', {
      description: `Optional deny guardrail for Bedrock runtime calls outside ${props.bedrockRegion}`,
      statements: [
        new iam.PolicyStatement({
          effect: iam.Effect.DENY,
          actions: [
            'bedrock:Invoke*',
            'bedrock:Converse*',
            'bedrock:RetrieveAndGenerate*',
            'bedrock:InvokeAgent*',
          ],
          resources: ['*'],
          conditions: {
            StringNotEquals: {
              'aws:RequestedRegion': props.bedrockRegion,
            },
          },
        }),
      ],
    });
  }
}
