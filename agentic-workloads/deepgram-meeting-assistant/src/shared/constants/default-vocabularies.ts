import type { BuiltinVocabularyDefinition } from '@shared/types/vocabulary';

/**
 * AWS 서비스 용어집 (ko-KR)
 *
 * AWS Transcribe에서 인식이 어려운 AWS 서비스명.
 *
 * Phrase: 인식할 단어 (숫자/특수문자 없이 발음 기준)
 * DisplayAs: 표시할 형식
 * SoundsLike: 한국어에서 지원하지 않음 (영어 발음 힌트만 허용)
 */
export const AWS_VOCABULARY_KO: BuiltinVocabularyDefinition = {
  name: 'AWS 용어집 (한글)',
  languageCode: 'ko-KR',
  entries: [
    // 핵심 컴퓨팅/스토리지/네트워킹
    { phrase: '이씨투', displayAs: 'EC2' },
    { phrase: '에스쓰리', displayAs: 'S3' },
    { phrase: '람다', displayAs: 'Lambda' },
    { phrase: '이비에스', displayAs: 'EBS' },
    { phrase: '이엘비', displayAs: 'ELB' },
    { phrase: '에이엘비', displayAs: 'ALB' },
    { phrase: '엔엘비', displayAs: 'NLB' },
    { phrase: '브이피씨', displayAs: 'VPC' },
    { phrase: '클라우드프론트', displayAs: 'CloudFront' },
    { phrase: '라우트오십삼', displayAs: 'Route 53' },
    { phrase: '에이피아이게이트웨이', displayAs: 'API Gateway' },
    { phrase: '이케이에스', displayAs: 'EKS' },
    { phrase: '이씨에스', displayAs: 'ECS' },
    { phrase: '파게이트', displayAs: 'Fargate' },
    { phrase: '앱러너', displayAs: 'App Runner' },

    // 데이터베이스
    { phrase: '알디에스', displayAs: 'RDS' },
    { phrase: '오로라', displayAs: 'Aurora' },
    { phrase: '다이나모디비', displayAs: 'DynamoDB' },
    { phrase: '엘라스티캐시', displayAs: 'ElastiCache' },
    { phrase: '레드시프트', displayAs: 'Redshift' },
    { phrase: '도큐먼트디비', displayAs: 'DocumentDB' },
    { phrase: '넵튠', displayAs: 'Neptune' },
    { phrase: '메모리디비', displayAs: 'MemoryDB' },
    { phrase: '타임스트림', displayAs: 'Timestream' },

    // AI/ML 서비스
    { phrase: '베드록', displayAs: 'Bedrock' },
    { phrase: '세이지메이커', displayAs: 'SageMaker' },
    { phrase: '트랜스크라이브', displayAs: 'Transcribe' },
    { phrase: '폴리', displayAs: 'Polly' },
    { phrase: '레코그니션', displayAs: 'Rekognition' },
    { phrase: '컴프리헨드', displayAs: 'Comprehend' },
    { phrase: '텍스트랙트', displayAs: 'Textract' },
    { phrase: '트랜슬레이트', displayAs: 'Translate' },
    { phrase: '렉스', displayAs: 'Lex' },
    { phrase: '켄드라', displayAs: 'Kendra' },
    { phrase: '퍼스널라이즈', displayAs: 'Personalize' },
    { phrase: '포캐스트', displayAs: 'Forecast' },
    { phrase: '큐디벨로퍼', displayAs: 'Q Developer' },
    { phrase: '큐비즈니스', displayAs: 'Q Business' },
    { phrase: '코드위스퍼러', displayAs: 'CodeWhisperer' },
    { phrase: '파티록', displayAs: 'PartyRock' },

    // 보안/ID
    { phrase: '아이에이엠', displayAs: 'IAM' },
    { phrase: '코그니토', displayAs: 'Cognito' },
    { phrase: '케이엠에스', displayAs: 'KMS' },
    { phrase: '시크릿매니저', displayAs: 'Secrets Manager' },
    { phrase: '더블유에이에프', displayAs: 'WAF' },
    { phrase: '실드', displayAs: 'Shield' },
    { phrase: '가드듀티', displayAs: 'GuardDuty' },
    { phrase: '인스펙터', displayAs: 'Inspector' },
    { phrase: '메이시', displayAs: 'Macie' },
    { phrase: '시큐리티허브', displayAs: 'Security Hub' },

    // 관리/모니터링
    { phrase: '클라우드워치', displayAs: 'CloudWatch' },
    { phrase: '클라우드트레일', displayAs: 'CloudTrail' },
    { phrase: '클라우드포메이션', displayAs: 'CloudFormation' },
    { phrase: '씨디케이', displayAs: 'CDK' },
    { phrase: '시스템즈매니저', displayAs: 'Systems Manager' },
    { phrase: '컨피그', displayAs: 'Config' },
    { phrase: '오거나이제이션즈', displayAs: 'Organizations' },
    { phrase: '컨트롤타워', displayAs: 'Control Tower' },
    { phrase: '서비스카탈로그', displayAs: 'Service Catalog' },

    // 데이터 분석/스트리밍
    { phrase: '아테나', displayAs: 'Athena' },
    { phrase: '글루', displayAs: 'Glue' },
    { phrase: '키네시스', displayAs: 'Kinesis' },
    { phrase: '이엠알', displayAs: 'EMR' },
    { phrase: '퀵사이트', displayAs: 'QuickSight' },
    { phrase: '레이크포메이션', displayAs: 'Lake Formation' },
    { phrase: '오픈서치', displayAs: 'OpenSearch' },
    { phrase: '엠에스케이', displayAs: 'MSK' },

    // 기타 자주 사용
    { phrase: '스텝펑션즈', displayAs: 'Step Functions' },
    { phrase: '이벤트브리지', displayAs: 'EventBridge' },
    { phrase: '에스엔에스', displayAs: 'SNS' },
    { phrase: '에스큐에스', displayAs: 'SQS' },
    { phrase: '앰플리파이', displayAs: 'Amplify' },
    { phrase: '앱싱크', displayAs: 'AppSync' },
  ],
};

/**
 * AWS 서비스 용어집 (en-US)
 *
 * Phrase: 인식할 단어 (숫자는 영문으로 풀어씀)
 * DisplayAs: 표시할 형식
 */
export const AWS_VOCABULARY_EN: BuiltinVocabularyDefinition = {
  name: 'AWS 용어집 (영어)',
  languageCode: 'en-US',
  entries: [
    // 핵심 컴퓨팅/스토리지/네트워킹
    { phrase: 'E C two', displayAs: 'EC2' },
    { phrase: 'S three', displayAs: 'S3' },
    { phrase: 'Lambda', displayAs: 'Lambda' },
    { phrase: 'EBS', displayAs: 'EBS' },
    { phrase: 'ELB', displayAs: 'ELB' },
    { phrase: 'ALB', displayAs: 'ALB' },
    { phrase: 'NLB', displayAs: 'NLB' },
    { phrase: 'VPC', displayAs: 'VPC' },
    { phrase: 'CloudFront', displayAs: 'CloudFront' },
    { phrase: 'Route fifty three', displayAs: 'Route 53' },
    { phrase: 'APIGateway', displayAs: 'API Gateway' },
    { phrase: 'EKS', displayAs: 'EKS' },
    { phrase: 'ECS', displayAs: 'ECS' },
    { phrase: 'Fargate', displayAs: 'Fargate' },
    { phrase: 'AppRunner', displayAs: 'App Runner' },

    // 데이터베이스
    { phrase: 'RDS', displayAs: 'RDS' },
    { phrase: 'Aurora', displayAs: 'Aurora' },
    { phrase: 'DynamoDB', displayAs: 'DynamoDB' },
    { phrase: 'ElastiCache', displayAs: 'ElastiCache' },
    { phrase: 'Redshift', displayAs: 'Redshift' },
    { phrase: 'DocumentDB', displayAs: 'DocumentDB' },
    { phrase: 'Neptune', displayAs: 'Neptune' },
    { phrase: 'MemoryDB', displayAs: 'MemoryDB' },
    { phrase: 'Timestream', displayAs: 'Timestream' },

    // AI/ML 서비스
    { phrase: 'Bedrock', displayAs: 'Bedrock' },
    { phrase: 'SageMaker', displayAs: 'SageMaker' },
    { phrase: 'Transcribe', displayAs: 'Transcribe' },
    { phrase: 'Polly', displayAs: 'Polly' },
    { phrase: 'Rekognition', displayAs: 'Rekognition' },
    { phrase: 'Comprehend', displayAs: 'Comprehend' },
    { phrase: 'Textract', displayAs: 'Textract' },
    { phrase: 'Translate', displayAs: 'Translate' },
    { phrase: 'Lex', displayAs: 'Lex' },
    { phrase: 'Kendra', displayAs: 'Kendra' },
    { phrase: 'Personalize', displayAs: 'Personalize' },
    { phrase: 'Forecast', displayAs: 'Forecast' },
    { phrase: 'QDeveloper', displayAs: 'Q Developer' },
    { phrase: 'QBusiness', displayAs: 'Q Business' },
    { phrase: 'CodeWhisperer', displayAs: 'CodeWhisperer' },
    { phrase: 'PartyRock', displayAs: 'PartyRock' },

    // 보안/ID
    { phrase: 'IAM', displayAs: 'IAM' },
    { phrase: 'Cognito', displayAs: 'Cognito' },
    { phrase: 'KMS', displayAs: 'KMS' },
    { phrase: 'SecretsManager', displayAs: 'Secrets Manager' },
    { phrase: 'WAF', displayAs: 'WAF' },
    { phrase: 'Shield', displayAs: 'Shield' },
    { phrase: 'GuardDuty', displayAs: 'GuardDuty' },
    { phrase: 'Inspector', displayAs: 'Inspector' },
    { phrase: 'Macie', displayAs: 'Macie' },
    { phrase: 'SecurityHub', displayAs: 'Security Hub' },

    // 관리/모니터링
    { phrase: 'CloudWatch', displayAs: 'CloudWatch' },
    { phrase: 'CloudTrail', displayAs: 'CloudTrail' },
    { phrase: 'CloudFormation', displayAs: 'CloudFormation' },
    { phrase: 'CDK', displayAs: 'CDK' },
    { phrase: 'SystemsManager', displayAs: 'Systems Manager' },
    { phrase: 'Config', displayAs: 'Config' },
    { phrase: 'Organizations', displayAs: 'Organizations' },
    { phrase: 'ControlTower', displayAs: 'Control Tower' },
    { phrase: 'ServiceCatalog', displayAs: 'Service Catalog' },

    // 데이터 분석/스트리밍
    { phrase: 'Athena', displayAs: 'Athena' },
    { phrase: 'Glue', displayAs: 'Glue' },
    { phrase: 'Kinesis', displayAs: 'Kinesis' },
    { phrase: 'EMR', displayAs: 'EMR' },
    { phrase: 'QuickSight', displayAs: 'QuickSight' },
    { phrase: 'LakeFormation', displayAs: 'Lake Formation' },
    { phrase: 'OpenSearch', displayAs: 'OpenSearch' },
    { phrase: 'MSK', displayAs: 'MSK' },

    // 기타 자주 사용
    { phrase: 'StepFunctions', displayAs: 'Step Functions' },
    { phrase: 'EventBridge', displayAs: 'EventBridge' },
    { phrase: 'SNS', displayAs: 'SNS' },
    { phrase: 'SQS', displayAs: 'SQS' },
    { phrase: 'Amplify', displayAs: 'Amplify' },
    { phrase: 'AppSync', displayAs: 'AppSync' },
  ],
};

/**
 * 기본 제공 용어집 목록
 */
export const BUILTIN_VOCABULARIES: BuiltinVocabularyDefinition[] = [
  AWS_VOCABULARY_KO,
  AWS_VOCABULARY_EN,
];

/**
 * 기본 용어집 ID 접두사 (builtin 용어집 구분용)
 */
export const BUILTIN_VOCABULARY_ID_PREFIX = 'builtin-';
