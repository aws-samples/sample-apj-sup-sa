# Database Detection - Detailed Steps

## Purpose
Identify all database systems, ORMs, and data layer technologies used in the project.

---

## Step 1: Detect Database Drivers/Clients in Dependencies

### Node.js (check package.json)
| Package | Database | Type |
|---------|----------|------|
| `pg`, `pg-pool` | PostgreSQL | Relational |
| `mysql2`, `mysql` | MySQL | Relational |
| `better-sqlite3`, `sql.js` | SQLite | Relational |
| `mongodb`, `mongoose` | MongoDB | Document |
| `redis`, `ioredis` | Redis | Key-Value |
| `@elastic/elasticsearch` | Elasticsearch | Search |
| `@aws-sdk/client-dynamodb` | DynamoDB | Key-Value/Document |
| `cassandra-driver` | Cassandra | Column-Family |
| `neo4j-driver` | Neo4j | Graph |

### Python (check requirements.txt/pyproject.toml)
| Package | Database | Type |
|---------|----------|------|
| `psycopg2`, `asyncpg` | PostgreSQL | Relational |
| `mysqlclient`, `aiomysql` | MySQL | Relational |
| `pymongo`, `motor` | MongoDB | Document |
| `redis`, `aioredis` | Redis | Key-Value |
| `elasticsearch` | Elasticsearch | Search |
| `boto3` (dynamodb) | DynamoDB | Key-Value/Document |
| `sqlalchemy` | Multiple (check engine) | ORM |
| `django.db` | Multiple (check settings) | ORM |

### Java (check pom.xml/build.gradle)
| Package | Database | Type |
|---------|----------|------|
| `postgresql` | PostgreSQL | Relational |
| `mysql-connector-java` | MySQL | Relational |
| `spring-data-mongodb` | MongoDB | Document |
| `spring-data-redis` | Redis | Key-Value |
| `spring-data-elasticsearch` | Elasticsearch | Search |
| `spring-data-jpa` | Multiple | ORM |
| `hibernate-core` | Multiple | ORM |

---

## Step 2: Detect ORM/Query Builders

| ORM | Detection | Database Support |
|-----|-----------|-----------------|
| Prisma | `@prisma/client` in deps + `prisma/schema.prisma` | Postgres, MySQL, SQLite, MongoDB |
| TypeORM | `typeorm` in deps | Postgres, MySQL, SQLite, others |
| Sequelize | `sequelize` in deps | Postgres, MySQL, SQLite, MSSQL |
| Drizzle | `drizzle-orm` in deps | Postgres, MySQL, SQLite |
| Knex | `knex` in deps | Postgres, MySQL, SQLite, others |
| SQLAlchemy | `sqlalchemy` in requirements | All major RDBMS |
| Django ORM | `django` + models.py files | All major RDBMS |
| Hibernate | `hibernate-core` or `spring-boot-starter-data-jpa` | All major RDBMS |
| ActiveRecord | `rails` + `activerecord` | All major RDBMS |
| GORM | `gorm.io/gorm` in go.mod | Postgres, MySQL, SQLite |
| Diesel | `diesel` in Cargo.toml | Postgres, MySQL, SQLite |

---

## Step 3: Detect Migration Framework

| Framework | Detection | Files |
|-----------|-----------|-------|
| Prisma Migrate | `prisma/migrations/` directory | `migration.sql` files |
| Alembic | `alembic/` directory, `alembic.ini` | Version files |
| Flyway | `db/migration/` or `flyway` in deps | `V{n}__*.sql` files |
| Liquibase | `changelog` files, `liquibase` in deps | XML/YAML/SQL |
| Knex | `migrations/` + `knexfile` | JS/TS files |
| Rails | `db/migrate/` | Ruby files |
| Django | `*/migrations/` directories | Python files |
| TypeORM | `src/migrations/` or `migrations/` | TS/JS files |
| Goose | `migrations/` + `goose` | SQL files |

---

## Step 4: Detect Database Configuration

```bash
# Look for database config (DO NOT OUTPUT VALUES)
grep -rn "DATABASE_URL\|DB_HOST\|DB_PORT\|DB_NAME\|POSTGRES_\|MYSQL_\|MONGO_" \
  --include=".env.example" --include="*.example" --include="docker-compose*.yml" .

# Docker compose services
grep -A5 "image:.*postgres\|image:.*mysql\|image:.*mongo\|image:.*redis" docker-compose*.yml 2>/dev/null
```

---

## Step 5: Classification

```markdown
## Database Detection Results

| # | Database | Type | ORM/Driver | Migration Tool | Confidence |
|---|----------|------|-----------|---------------|-----------|
| 1 | {db} | {type} | {orm} | {migration} | {HIGH/MED} |

### Database Layer Summary
- **Primary datastore**: {database} via {orm}
- **Caching layer**: {redis/memcached/none}
- **Search engine**: {elasticsearch/none}
- **Queue/Streaming**: {kafka/sqs/rabbitmq/none}
```

---

## Step 6: Decision

If databases detected → Phase 3 (Database & Query Optimization) will execute.
If no database detected → Phase 3 will be skipped.
