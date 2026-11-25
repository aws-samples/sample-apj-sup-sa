import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import { BedrockMeterConfig } from '../config';

export interface NotificationProps {
  config: BedrockMeterConfig;
}

export class Notification extends Construct {
  public readonly topic: sns.ITopic;

  constructor(scope: Construct, id: string, props: NotificationProps) {
    super(scope, id);

    const { config } = props;

    this.topic = new sns.Topic(this, 'AlertsTopic', {
      topicName: config.alertsTopicName,
      displayName: config.alertsDisplayName,
    });

    for (const email of config.adminEmails) {
      this.topic.addSubscription(new subscriptions.EmailSubscription(email));
    }
  }
}
