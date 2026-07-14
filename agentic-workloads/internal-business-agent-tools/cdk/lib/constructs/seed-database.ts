import { Construct } from 'constructs';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as iam from 'aws-cdk-lib/aws-iam';
import { CustomResource, Duration, Stack } from 'aws-cdk-lib';
import * as path from 'path';

export interface SeedDatabaseProps {
  cluster: rds.DatabaseCluster;
  readOnlySecret: secretsmanager.ISecret;
}

export class SeedDatabase extends Construct {
  constructor(scope: Construct, id: string, props: SeedDatabaseProps) {
    super(scope, id);

    const { cluster, readOnlySecret } = props;
    const readOnlyPassword = readOnlySecret.secretValueFromJson('password').unsafeUnwrap();

    const statements = [
      `CREATE TABLE IF NOT EXISTS customers (
        id SERIAL PRIMARY KEY,
        company_name VARCHAR(200) NOT NULL,
        department VARCHAR(100),
        contact_person VARCHAR(100),
        contract_start DATE,
        contract_end DATE,
        contract_value INTEGER,
        status VARCHAR(50) DEFAULT 'active'
      )`,
      `CREATE TABLE IF NOT EXISTS internal_projects (
        id SERIAL PRIMARY KEY,
        project_name VARCHAR(200) NOT NULL,
        owner VARCHAR(100),
        department VARCHAR(100),
        status VARCHAR(50) DEFAULT 'planning',
        budget INTEGER,
        start_date DATE,
        end_date DATE,
        description TEXT
      )`,
      `INSERT INTO customers (company_name, department, contact_person, contract_start, contract_end, contract_value, status) SELECT '株式会社テクノソリューション', '営業部', '田中太郎', '2024-04-01', '2025-03-31', 12000000, 'active' WHERE NOT EXISTS (SELECT 1 FROM customers WHERE company_name = '株式会社テクノソリューション')`,
      `INSERT INTO customers (company_name, department, contact_person, contract_start, contract_end, contract_value, status) SELECT 'グローバルシステムズ合同会社', 'IT部門', '鈴木花子', '2024-01-01', '2024-12-31', 8500000, 'active' WHERE NOT EXISTS (SELECT 1 FROM customers WHERE company_name = 'グローバルシステムズ合同会社')`,
      `INSERT INTO customers (company_name, department, contact_person, contract_start, contract_end, contract_value, status) SELECT 'スタートアップラボ株式会社', '経営企画', '佐藤健一', '2024-07-01', '2025-06-30', 3200000, 'active' WHERE NOT EXISTS (SELECT 1 FROM customers WHERE company_name = 'スタートアップラボ株式会社')`,
      `INSERT INTO customers (company_name, department, contact_person, contract_start, contract_end, contract_value, status) SELECT 'ネクストイノベーション株式会社', '開発部', '高橋美咲', '2023-10-01', '2024-09-30', 15000000, 'expired' WHERE NOT EXISTS (SELECT 1 FROM customers WHERE company_name = 'ネクストイノベーション株式会社')`,
      `INSERT INTO internal_projects (project_name, owner, department, status, budget, start_date, end_date, description) SELECT '社内AI基盤構築', '山田次郎', 'エンジニアリング', 'in_progress', 50000000, '2024-04-01', '2025-03-31', 'Bedrock を活用した社内AI基盤の構築プロジェクト' WHERE NOT EXISTS (SELECT 1 FROM internal_projects WHERE project_name = '社内AI基盤構築')`,
      `INSERT INTO internal_projects (project_name, owner, department, status, budget, start_date, end_date, description) SELECT '顧客管理システム刷新', '木村美穂', 'IT部門', 'planning', 30000000, '2024-10-01', '2025-09-30', 'レガシーCRMから新システムへの移行' WHERE NOT EXISTS (SELECT 1 FROM internal_projects WHERE project_name = '顧客管理システム刷新')`,
      `INSERT INTO internal_projects (project_name, owner, department, status, budget, start_date, end_date, description) SELECT 'データ分析基盤', '中村拓也', 'データサイエンス', 'in_progress', 20000000, '2024-01-01', '2024-12-31', 'S3 + Athena によるデータレイク構築' WHERE NOT EXISTS (SELECT 1 FROM internal_projects WHERE project_name = 'データ分析基盤')`,
      `INSERT INTO internal_projects (project_name, owner, department, status, budget, start_date, end_date, description) SELECT 'セキュリティ強化施策', '渡辺由美', '情報セキュリティ', 'completed', 8000000, '2023-07-01', '2024-06-30', 'ゼロトラスト導入と監査体制整備' WHERE NOT EXISTS (SELECT 1 FROM internal_projects WHERE project_name = 'セキュリティ強化施策')`,
      // Create a READ-ONLY database user for the MCP server (least privilege)
      `DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'readonly_user') THEN CREATE USER readonly_user WITH PASSWORD '${readOnlyPassword}'; END IF; END $$`,
      `GRANT CONNECT ON DATABASE internal_db TO readonly_user`,
      `GRANT USAGE ON SCHEMA public TO readonly_user`,
      `GRANT SELECT ON customers, internal_projects TO readonly_user`,
    ];

    let previous: cr.AwsCustomResource | undefined;

    for (let i = 0; i < statements.length; i++) {
      const sdkCall: cr.AwsSdkCall = {
        service: 'RDSDataService',
        action: 'executeStatement',
        parameters: {
          resourceArn: cluster.clusterArn,
          secretArn: cluster.secret!.secretArn,
          database: 'internal_db',
          sql: statements[i],
        },
        physicalResourceId: cr.PhysicalResourceId.of(`seed-stmt-${i}`),
      };

      const resource = new cr.AwsCustomResource(this, `Stmt${i}`, {
        onCreate: sdkCall,
        onUpdate: sdkCall,
        policy: cr.AwsCustomResourcePolicy.fromStatements([
          new iam.PolicyStatement({
            actions: ['rds-data:ExecuteStatement'],
            resources: [cluster.clusterArn],
          }),
          new iam.PolicyStatement({
            actions: ['secretsmanager:GetSecretValue'],
            resources: [cluster.secret!.secretArn],
          }),
        ]),
      });

      resource.node.addDependency(cluster);
      if (previous) {
        resource.node.addDependency(previous);
      }
      previous = resource;
    }
  }
}
