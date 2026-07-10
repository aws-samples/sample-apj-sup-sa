#!/bin/bash

# Test script for Timely-Unicorn database schema creation
# This script drops and recreates the test database, then runs the schema creation script
# The database used for this is timely_unicorn_test, while the actual DB is in timely_unicorn database.

# Database configuration
DB_NAME="timely_unicorn_test"
DB_USER="$(whoami)"

echo "Testing Timely-Unicorn database schema creation..."

# Check if database exists and drop it if it does
echo "Dropping existing test database (if it exists)..."
dropdb --if-exists $DB_NAME

# Create new database
echo "Creating new test database..."
createdb $DB_NAME

# Run schema creation script
echo "Running schema creation script..."
psql -d $DB_NAME -f ../schema.sql

# Check if schema creation was successful
if [ $? -eq 0 ]; then
    echo "Schema creation completed successfully!"
    
    # List tables to verify
    echo "Listing created tables..."
    psql -d $DB_NAME -c "\dt"
    
    # List views to verify
    echo "Listing created views..."
    psql -d $DB_NAME -c "\dv"
else
    echo "Schema creation failed!"
    exit 1
fi

echo "Test completed."
