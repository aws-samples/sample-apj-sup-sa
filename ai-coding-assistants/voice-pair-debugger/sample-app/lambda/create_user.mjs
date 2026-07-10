import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, PutCommand } from '@aws-sdk/lib-dynamodb';
import { randomUUID } from 'node:crypto';

const client = new DynamoDBClient({});
const dynamodb = DynamoDBDocumentClient.from(client);
const TABLE_NAME = process.env.TABLE_NAME;

export const handler = async (event) => {
  try {
    const body = JSON.parse(event.body ?? '{}');
    const userId = `u-${randomUUID().replace(/-/g, '').slice(0, 6)}`;

    await dynamodb.send(
      new PutCommand({
        TableName: TABLE_NAME,
        Item: {
          user_id: userId,
          name: body.name ?? 'Unknown',
          email: body.email ?? ''
        }
      })
    );

    return {
      statusCode: 201,
      body: JSON.stringify({ user_id: userId, message: 'User created' })
    };
  } catch (error) {
    console.error(`ERROR: ${error}`);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: error.message })
    };
  }
};
