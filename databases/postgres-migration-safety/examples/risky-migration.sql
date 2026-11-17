-- Example input for the skill: a Prisma-generated migration that looks routine
-- and would cause an outage on a busy production table.
-- prisma/migrations/20261117093000_orders_v2/migration.sql
--
-- See examples/review.md for the skill's review of this file.

-- AlterTable
ALTER TABLE "orders" ADD COLUMN "public_id" UUID NOT NULL DEFAULT gen_random_uuid(),
ADD COLUMN "status" TEXT NOT NULL DEFAULT 'pending',
ALTER COLUMN "total" SET DATA TYPE DECIMAL(12,2);

-- AlterTable
ALTER TABLE "orders" RENAME COLUMN "user_id" TO "customer_id";

-- CreateIndex
CREATE UNIQUE INDEX "orders_public_id_key" ON "orders"("public_id");

-- CreateIndex
CREATE INDEX "orders_customer_id_idx" ON "orders"("customer_id");

-- AddForeignKey
ALTER TABLE "orders" ADD CONSTRAINT "orders_customer_id_fkey" FOREIGN KEY ("customer_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
