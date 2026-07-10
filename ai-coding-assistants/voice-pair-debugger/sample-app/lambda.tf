# --- Lambda: create_user (working correctly) ---
data "archive_file" "create_user" {
  type        = "zip"
  source_file = "${path.module}/lambda/create_user.mjs"
  output_path = "${path.module}/.build/create_user.zip"
}

resource "aws_lambda_function" "create_user" {
  function_name    = "${var.service}-create-user"
  role             = aws_iam_role.lambda.arn
  handler          = "create_user.handler"
  runtime          = "nodejs24.x"
  filename         = data.archive_file.create_user.output_path
  source_code_hash = data.archive_file.create_user.output_base64sha256
  timeout          = 10

  # Demo guard: the API is public and unauthenticated, so cap concurrency to
  # bound runaway cost and abuse. Not a production setting.
  reserved_concurrent_executions = 1

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.users.name
    }
  }
}

resource "aws_cloudwatch_log_group" "create_user" {
  name              = "/aws/lambda/${aws_lambda_function.create_user.function_name}"
  retention_in_days = 7
}


# --- Lambda: get_users (has a deliberate bug) ---
data "archive_file" "get_users" {
  type        = "zip"
  source_file = "${path.module}/lambda/get_users.mjs"
  output_path = "${path.module}/.build/get_users.zip"
}

resource "aws_lambda_function" "get_users" {
  function_name    = "${var.service}-get-users"
  role             = aws_iam_role.lambda.arn
  handler          = "get_users.handler"
  runtime          = "nodejs24.x"
  filename         = data.archive_file.get_users.output_path
  source_code_hash = data.archive_file.get_users.output_base64sha256
  timeout          = 10

  # Demo guard: the API is public and unauthenticated, so cap concurrency to
  # bound runaway cost and abuse. Not a production setting.
  reserved_concurrent_executions = 1

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.users.name
    }
  }
}

resource "aws_cloudwatch_log_group" "get_users" {
  name              = "/aws/lambda/${aws_lambda_function.get_users.function_name}"
  retention_in_days = 7
}


# --- Lambda: get_user (has a deliberate bug) ---
data "archive_file" "get_user" {
  type        = "zip"
  source_file = "${path.module}/lambda/get_user.mjs"
  output_path = "${path.module}/.build/get_user.zip"
}

resource "aws_lambda_function" "get_user" {
  function_name    = "${var.service}-get-user"
  role             = aws_iam_role.lambda.arn
  handler          = "get_user.handler"
  runtime          = "nodejs24.x"
  filename         = data.archive_file.get_user.output_path
  source_code_hash = data.archive_file.get_user.output_base64sha256
  timeout          = 10

  # Demo guard: the API is public and unauthenticated, so cap concurrency to
  # bound runaway cost and abuse. Not a production setting.
  reserved_concurrent_executions = 1

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.users.name
    }
  }
}

resource "aws_cloudwatch_log_group" "get_user" {
  name              = "/aws/lambda/${aws_lambda_function.get_user.function_name}"
  retention_in_days = 7
}


# --- Lambda: delete_user (has a deliberate bug) ---
data "archive_file" "delete_user" {
  type        = "zip"
  source_file = "${path.module}/lambda/delete_user.mjs"
  output_path = "${path.module}/.build/delete_user.zip"
}

resource "aws_lambda_function" "delete_user" {
  function_name    = "${var.service}-delete-user"
  role             = aws_iam_role.lambda.arn
  handler          = "delete_user.handler"
  runtime          = "nodejs24.x"
  filename         = data.archive_file.delete_user.output_path
  source_code_hash = data.archive_file.delete_user.output_base64sha256
  timeout          = 10

  # Demo guard: the API is public and unauthenticated, so cap concurrency to
  # bound runaway cost and abuse. Not a production setting.
  reserved_concurrent_executions = 1

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.users.name
    }
  }
}

resource "aws_cloudwatch_log_group" "delete_user" {
  name              = "/aws/lambda/${aws_lambda_function.delete_user.function_name}"
  retention_in_days = 7
}


# --- Lambda: get_stats (has a deliberate bug) ---
data "archive_file" "get_stats" {
  type        = "zip"
  source_file = "${path.module}/lambda/get_stats.mjs"
  output_path = "${path.module}/.build/get_stats.zip"
}

resource "aws_lambda_function" "get_stats" {
  function_name    = "${var.service}-get-stats"
  role             = aws_iam_role.lambda.arn
  handler          = "get_stats.handler"
  runtime          = "nodejs24.x"
  filename         = data.archive_file.get_stats.output_path
  source_code_hash = data.archive_file.get_stats.output_base64sha256
  timeout          = 10

  # Demo guard: the API is public and unauthenticated, so cap concurrency to
  # bound runaway cost and abuse. Not a production setting.
  reserved_concurrent_executions = 1

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.users.name
    }
  }
}

resource "aws_cloudwatch_log_group" "get_stats" {
  name              = "/aws/lambda/${aws_lambda_function.get_stats.function_name}"
  retention_in_days = 7
}
