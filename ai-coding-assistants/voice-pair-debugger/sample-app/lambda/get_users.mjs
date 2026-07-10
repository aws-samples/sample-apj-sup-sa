import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, ScanCommand } from '@aws-sdk/lib-dynamodb';

const client = new DynamoDBClient({});
const dynamodb = DynamoDBDocumentClient.from(client);
const TABLE_NAME = process.env.TABLE_NAME;

export const handler = async (event) => {
  try {
    const response = await dynamodb.send(new ScanCommand({ TableName: TABLE_NAME }));
    const items = response.Items ?? [];

    const users = items.map((item) => {
      if (item.userId === undefined) {
        throw new Error("Missing required attribute 'userId' on user item");
      }
      return {
        id: item.userId,
        name: item.name,
        email: item.email
      };
    });

    return {
      statusCode: 200,
      body: JSON.stringify({ users })
    };
  } catch (error) {
    console.error(`ERROR: ${error}`);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: error.message })
    };
  }
};
