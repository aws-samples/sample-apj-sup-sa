// Example only. scripts/deploy.sh generates frontend/config.js from the
// CloudFormation stack outputs and uploads it next to index.html. The real
// config.js is git ignored. Do not put account specific values in this file.
window.APP_CONFIG = {
  apiBaseUrl: 'https://API_ID.execute-api.REGION.amazonaws.com/prod',
  cognitoDomain: 'PREFIX.auth.REGION.amazoncognito.com',
  userPoolClientId: 'COGNITO_APP_CLIENT_ID',
  sourceReaderRoleArn: 'arn:aws:iam::SOURCE_ACCOUNT_ID:role/ConnectMigrationSourceReader',
  targetRoleArn: 'arn:aws:iam::TARGET_ACCOUNT_ID:role/ConnectMigrationTargetWriter',
  phoneNumberMoveEnabled: false
};
