import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, DeleteCommand } from '@aws-sdk/lib-dynamodb';

const client = new DynamoDBClient({});
const dynamodb = DynamoDBDocumentClient.from(client);
const TABLE_NAME = process.env.TABLE_NAME;

export const handler = async (event) => {
  try {
    const userId = event.pathParameters?.id;
    if (!userId) {
      throw new Error('Missing path parameter: id');
    }

    await dynamodb.send(
      new DeleteCommand({
        TableName: TABLE_NAME,
        Key: { user_id: userId }
      })
    );

    return {
      statusCode: 204,
      body: ''
    };
  } catch (error) {
    console.error(`ERROR: ${error}`);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: error.message })
    };
  }
};
