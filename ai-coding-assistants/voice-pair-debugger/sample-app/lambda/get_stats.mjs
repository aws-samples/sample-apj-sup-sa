import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, ScanCommand } from '@aws-sdk/lib-dynamodb';

const client = new DynamoDBClient({});
const dynamodb = DynamoDBDocumentClient.from(client);
const TABLE_NAME = process.env.DYNAMO_TABLE;

export const handler = async (event) => {
  try {
    const response = await dynamodb.send(
      new ScanCommand({
        TableName: TABLE_NAME,
        Select: 'COUNT'
      })
    );

    return {
      statusCode: 200,
      body: JSON.stringify({ user_count: response.Count })
    };
  } catch (error) {
    console.error(`ERROR: ${error}`);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: error.message })
    };
  }
};
