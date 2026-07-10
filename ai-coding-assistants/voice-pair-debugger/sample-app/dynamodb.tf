# --- DynamoDB Table ---
resource "aws_dynamodb_table" "users" {
  name         = "${var.service}-users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }
}


# Seed some test data
resource "aws_dynamodb_table_item" "user1" {
  table_name = aws_dynamodb_table.users.name
  hash_key   = aws_dynamodb_table.users.hash_key
  item = jsonencode({
    user_id = { S = "u-001" }
    name    = { S = "Alice" }
    email   = { S = "alice@example.com" }
  })
}

resource "aws_dynamodb_table_item" "user2" {
  table_name = aws_dynamodb_table.users.name
  hash_key   = aws_dynamodb_table.users.hash_key
  item = jsonencode({
    user_id = { S = "u-002" }
    name    = { S = "Bob" }
    email   = { S = "bob@example.com" }
  })
}
