import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, GetCommand } from '@aws-sdk/lib-dynamodb';

const client = new DynamoDBClient({});
const dynamodb = DynamoDBDocumentClient.from(client);
const TABLE_NAME = process.env.TABLE_NAME;

export const handler = async (event) => {
  try {
    const userId = event.pathParameters?.userId;
    if (!userId) {
      throw new Error('Missing path parameter: userId');
    }

    const response = await dynamodb.send(
      new GetCommand({
        TableName: TABLE_NAME,
        Key: { user_id: userId }
      })
    );

    if (!response.Item) {
      return {
        statusCode: 404,
        body: JSON.stringify({ error: 'User not found' })
      };
    }

    return {
      statusCode: 200,
      body: JSON.stringify({ user: response.Item })
    };
  } catch (error) {
    console.error(`ERROR: ${error}`);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: error.message })
    };
  }
};
