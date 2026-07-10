# CloudHSM to KMS Keys Migration

The AWS blog post ["How to migrate asymmetric keys from CloudHSM to AWS KMS"](https://aws.amazon.com/blogs/security/how-to-migrate-asymmetric-keys-from-cloudhsm-to-aws-kms/) provides detailed, step-by-step guidance on how to securely migrate asymmetric key (such as an RSA or ECC private key) from AWS CloudHSM to AWS Key Management Service (KMS). While it provides a step-by-step method for migrating a single asymmetric key from CloudHSM to AWS KMS, it doesn't offer a systematic process for bulk migration of many keys.

This project provides a set of scripts to facilitate the bulk migration of asymmetric cryptographic keys (RSA and ECC keys) from AWS CloudHSM to AWS Key Management Service (KMS). The project includes separate migration scripts for EC and RSA keys, with comprehensive logging and result tracking.

## Key Features

### Test Key Generation

- `generate-cloudhsm-test-keys.sh`: Creates test ECC key pairs in CloudHSM for testing the migration process

### Key Discovery and Listing

- `list-cloudhsm-keys.sh`: **Unified script** for listing both private and public keys with pattern filtering
  - Usage: `./list-cloudhsm-keys.sh [KEY_CLASS] [OUTPUT_FILE] [PATTERN]`
  - Supports `private-key` or `public-key` classes with customizable output files and regex patterns
- `list-cloudhsm-private-keys.sh`: **Wrapper script** for backward compatibility (calls unified script)
  - Usage: `./list-cloudhsm-private-keys.sh [PATTERN] [OUTPUT_FILE]`
- `list-cloudhsm-public-keys.sh`: **Wrapper script** for backward compatibility (calls unified script)
  - Usage: `./list-cloudhsm-public-keys.sh [PATTERN] [OUTPUT_FILE]`
- `count-cloudhsm-keys-by-regex.sh`: Counts keys matching a specific pattern with statistics by type and class
  - Usage: `./count-cloudhsm-keys-by-regex.sh [PATTERN]`

### Batch Processing Tools

- `count-key-types-from-json.py`: Analyzes key types and counts in JSON files for migration planning
  - Usage: `python3 count-key-types-from-json.py private_keys.json`
  - Provides summary statistics by key type (EC, RSA, etc.)
- `split-keys-in-json.py`: Splits large key JSON files into manageable batches for safer migration
  - Usage: Run script to split `private_keys.json` into batches of 50 keys per file in `split_keys_output/` directory
  - Organizes keys by type (EC, RSA) and creates numbered batch files
  - Reduces risk of migration failures and enables parallel processing

### Bulk Key Migration

**Separate migration scripts for different key types:**

- `migrate-cloudhsm-ec-keys-to-kms.sh`: Migrates **EC (Elliptic Curve) keys**

  - Supports secp256k1 (ECC_SECG_P256K1) and prime256v1/secp256r1 (ECC_NIST_P256) curves
  - Uses `ec-point` attribute for key pair matching
  - Uses ECDSA_SHA_256 signing algorithm for verification

- `migrate-cloudhsm-rsa-keys-to-kms.sh`: Migrates **RSA keys**
  - Supports RSA-2048, RSA-3072, and RSA-4096 key sizes
  - Uses `modulus` attribute for key pair matching
  - Uses RSASSA_PKCS1_V1_5_SHA_256 signing algorithm for verification

**Common migration workflow:**

- Reads private key information from JSON files
- Creates appropriate KMS keys based on key type and specifications
- Obtains wrapping parameters from AWS KMS
- Imports the KMS wrapping key into CloudHSM
- Wraps the private key inside CloudHSM using the imported wrapping key
- Imports the wrapped key material into AWS KMS
- Validates the migration by performing a sign/verify test operation
- Tracks successful and failed migrations in timestamped result files

### Advanced Features

- **Organized file management**: All intermediate files stored in `staging/` directory
- **Comprehensive logging**: Detailed logs with timestamps for debugging and audit
- **Clean console output**: Only key status shown on console, details in log files
- **Graceful error handling**: Continues processing if individual keys fail
- **Public key fallback**: Skips keys without corresponding public keys
- **Result tracking**: CSV format results with timestamps

### Key Cleanup

- `delete-cloudhsm-keys-by-regex.sh`: Efficiently deletes keys from CloudHSM matching a specific pattern
  - Uses key-reference for direct deletion (faster than label-based lookups)
  - Usage: `./delete-cloudhsm-keys-by-regex.sh [PATTERN]`
  - Supports regex patterns with safe default behavior

## Pre-requisites

This guide only works on Windows or Linux since CloudHSM-CLI is not available in Mac.

This project assumes that the CloudHSM cluster has been setup and activated. Crypto-user has been created. CloudHSM-CLI is installed.

**New to CloudHSM?** See our [Quick Setup Guide for CloudHSM](docs/Quick%20Setup%20Guide%20for%20CloudHSM.md) which provides step-by-step instructions for setting up a CloudHSM cluster from scratch, including VPC configuration, cluster creation, HSM initialization, and crypto-user setup.

1. Add CloudHSM bin folder to PATH so that we can use `cloudhsm-cli` instead of `/opt/cloudhsm/bin/cloudhsm-cli`.

```
export PATH=$PATH:/opt/cloudhsm/bin
```

2. Set CloudHSM User in environment variables. Replace username and password accordingly. This is so that you can call `cloudhsm-cli` directly in terminal.

```
export CLOUDHSM_ROLE=crypto-user
export CLOUDHSM_PIN="user2:Mylife123"
```

## Usage Steps

### 1. Generate Test Keys (Optional)

Generate test ECC key pairs in CloudHSM for testing the migration process:

```bash
./generate-cloudhsm-test-keys.sh
```

### 2. Discover and List Keys

**Option A: Using the unified script (recommended):**

```bash
# List all private keys with default output
./list-cloudhsm-keys.sh

# List all public keys to default file
./list-cloudhsm-keys.sh public-key

# List keys with custom output file and pattern
./list-cloudhsm-keys.sh private-key "my_private_keys.json" "^test-*"
./list-cloudhsm-keys.sh public-key "my_public_keys.json" "^prod-*"
```

**Option B: Using individual scripts (backward compatibility):**

```bash
# List with default settings
./list-cloudhsm-private-keys.sh
./list-cloudhsm-public-keys.sh

# List with custom patterns and output files
./list-cloudhsm-private-keys.sh "^test-*" "test_private_keys.json"
./list-cloudhsm-public-keys.sh "^prod-*" "prod_public_keys.json"
```

**Count keys by pattern:**

```bash
# Count all keys
./count-cloudhsm-keys-by-regex.sh

# Count keys matching specific patterns
./count-cloudhsm-keys-by-regex.sh "^test-*"
./count-cloudhsm-keys-by-regex.sh ".*-backup$"
```

This creates:

- `private_keys.json`: Contains all private keys with metadata (default)
- `public_keys.json`: Contains all public keys with metadata (default)
- Or custom JSON files based on your specified output filenames

**Key matching logic:**

- **EC keys**: Private and public keys are matched using the `ec-point` attribute
- **RSA keys**: Private and public keys are matched using the `modulus` attribute
- Each key is uniquely identified by its `key-reference` value

### 3. Analyze and Prepare for Batch Migration (Recommended for Large Datasets)

**⚠️ Important**: For large numbers of keys, batch processing is strongly recommended to reduce risk and enable better progress tracking.

**Step 3a: Analyze Key Distribution**

```bash
# Get an overview of key types and counts
python3 count-key-types-from-json.py private_keys.json
```

Example output:

```
Key Type Counts:
--------------------
ec: 150
rsa: 75
--------------------
Total: 225
```

**Step 3b: Split Keys into Manageable Batches**

```bash
# Split keys into batches of 50 keys per file
python3 split-keys-in-json.py
```

This creates a `split_keys_output/` directory with organized batch files:

```
split_keys_output/
├── ec_keys_part_001.json      # EC keys batch 1 (50 keys)
├── ec_keys_part_002.json      # EC keys batch 2 (50 keys)
├── ec_keys_part_003.json      # EC keys batch 3 (50 keys)
├── rsa_keys_part_001.json     # RSA keys batch 1 (50 keys)
└── rsa_keys_part_002.json     # RSA keys batch 2 (25 keys)
```

**Benefits of batch processing:**

- **Reduced risk**: If migration fails, only one batch is affected
- **Progress tracking**: Clear visibility of completed vs remaining batches
- **Parallel processing**: Can run multiple batches simultaneously if needed
- **Resource management**: Prevents overwhelming CloudHSM or KMS with large operations

### 4. Run Key Migration

```bash
# Migrate EC key batches one by one
./migrate-cloudhsm-ec-keys-to-kms.sh split_keys_output/ec_keys_part_001.json
./migrate-cloudhsm-ec-keys-to-kms.sh split_keys_output/ec_keys_part_002.json

# Migrate RSA key batches one by one
./migrate-cloudhsm-rsa-keys-to-kms.sh split_keys_output/rsa_keys_part_001.json
./migrate-cloudhsm-rsa-keys-to-kms.sh split_keys_output/rsa_keys_part_002.json
```

**Batch Migration Best Practices:**

- Process one batch at a time to monitor progress and handle any issues
- Check result files after each batch to ensure successful migration
- Keep track of completed batches to avoid reprocessing
- Monitor CloudHSM and KMS service limits and throttling

**Console output example:**

```
CloudHSM Key: ec-priv-16, 0x000000000004392a ---------
  KMS Key: arn:aws:kms:region:account:key/59a01311-ea72-4f91-bd90-cb7f6511aec0

CloudHSM Key: rsa-priv-4, 0x0000000000000128 ---------
  KMS Key: arn:aws:kms:region:account:key/1685b41b-9b99-4ce0-b4e1-6945c4feb160
```

### 5. Review Results

Migration results are saved with timestamps for each batch:

```bash
# Each batch creates its own result files
cat result_success_ec_keys_part_001_YYYYMMDD_HHMM.txt
cat result_success_ec_keys_part_002_YYYYMMDD_HHMM.txt
cat result_failed_ec_keys_part_001_YYYYMMDD_HHMM.txt

# Combine all successful migrations across batches
cat result_success_*_YYYYMMDD_HHMM.txt > all_successful_migrations.txt

# Count total successes and failures
wc -l result_success_*.txt
wc -l result_failed_*.txt
```

**Migration completion summary:**
Each migration script now shows completion statistics:

```
------ Migration Complete: 45 successful, 5 failed --------
```

### 5. Clean Up Test Keys (Optional)

Remove test keys from CloudHSM after testing:

```bash
# Delete all keys (use with caution!)
./delete-cloudhsm-keys-by-regex.sh

# Delete keys matching specific patterns
./delete-cloudhsm-keys-by-regex.sh "^test-*"       # Keys starting with "test-"
./delete-cloudhsm-keys-by-regex.sh ".*-backup$"    # Keys ending with "-backup"
./delete-cloudhsm-keys-by-regex.sh "^mvgx-*"       # Keys starting with "mvgx-"

# Delete keys for specific testing scenarios
./delete-cloudhsm-keys-by-regex.sh "^ec-test-*"    # EC test keys
./delete-cloudhsm-keys-by-regex.sh "^rsa-test-*"   # RSA test keys
```

**⚠️ Warning**: Be very careful with deletion patterns. Always test your regex pattern with the count script first:

```bash
# Preview what will be deleted
./count-cloudhsm-keys-by-regex.sh "^test-*"
# Then delete if the count looks correct
./delete-cloudhsm-keys-by-regex.sh "^test-*"
```

## File Organization

After running the migration scripts, your directory will contain:

```
├── staging/                           # Intermediate files for each key
│   ├── 0x123_WrappingPublicKey.*
│   ├── 0x123_ImportToken.*
│   ├── 0x123_EncryptedKeyMaterial.bin
│   └── 0x123_public_key.pem
├── split_keys_output/                 # Batch processing output (if used)
│   ├── ec_keys_part_001.json         # EC keys batch 1
│   ├── ec_keys_part_002.json         # EC keys batch 2
│   ├── rsa_keys_part_001.json        # RSA keys batch 1
│   └── rsa_keys_part_002.json        # RSA keys batch 2
├── private_keys.json                  # All private keys from CloudHSM
├── public_keys.json                   # All public keys from CloudHSM
├── result_success_*_YYYYMMDD_HHMM.txt # Successful migrations (CSV)
├── result_failed_*_YYYYMMDD_HHMM.txt  # Failed migrations (CSV)
├── log_YYYYMMDD_HHMM.log             # Detailed execution logs
├── count-key-types-from-json.py       # Key analysis utility
└── split-keys-in-json.py              # Batch splitting utility
```

**For batch migrations, results are organized by batch:**

- Each batch creates separate result and log files with timestamps
- File naming includes the source JSON filename for easy tracking
- Use `cat result_success_*.txt` to combine results across all batches

## Inside Migration Script

### Step 1: Create a KMS key without key material in AWS KMS

This is a new KMS key which will be migrated from a CloudHSM Key. The key material will be provided by the existing key in CloudHSM.

```
export KMS_KEY_ID=$(aws kms create-key --origin EXTERNAL --key-spec ECC_NIST_P256 --key-usage SIGN_VERIFY --query 'KeyMetadata.KeyId' --output text)

echo $KMS_KEY_ID
```

### Step 2: Download the wrapping public key and import token from AWS KMS

We can download a wrapping key for above KMS key. The wrapping key will be imported into CloudHSM to encrypt the target key in CloudHMS before it is exported.

```
aws kms get-parameters-for-import \
  --key-id "$KMS_KEY_ID" \
  --wrapping-algorithm RSAES_OAEP_SHA_256 \
  --wrapping-key-spec RSA_4096 \
  --query "[ImportToken, PublicKey]" \
  --output text \
  | awk '{print $1 > "ImportToken.b64"; print $2 > "WrappingPublicKey.b64"}'

```

Decode the base64 encoding

```
openssl enc -d -base64 -A -in WrappingPublicKey.b64 -out WrappingPublicKey.bin

ls -la WrappingPublicKey*
```

Convert the wrapping public key from DER to PEM format

```
openssl rsa -pubin -in WrappingPublicKey.bin -inform DER -outform PEM -out WrappingPublicKey.pem

ls -la WrappingPublicKey*
```

### Step 3: Import the wrapping key provided by AWS KMS into CloudHSM

You may change the label value. You will need to use the same label value to refer to the wrapping key.

```
cloudhsm-cli key import pem --path ./WrappingPublicKey.pem --label kms-wrapping-key --key-type-class rsa-public --attributes wrap=true
```

### Step 4: Wrap the private key inside CloudHSM with the imported wrapping public key from AWS KMS

Save the wrapped key.

```
cloudhsm-cli key wrap rsa-oaep \
    --payload-filter "key-reference=$KEY_REF" \
    --wrapping-filter "key-reference=$WRAP_KEY_REF" \
    --hash-function sha256 --mgf mgf1-sha256 \
    --path $WRAPPED_KEY_FILE
```

### Step 5: Import the wrapped key material to AWS KMS

Once the key is wrapped, import it to AWS KMS using the import token:

```
aws kms import-key-material \
    --key-id $KMS_KEY_ID \
    --encrypted-key-material fileb://$WRAPPED_KEY_FILE \
    --import-token fileb://$IMPORT_TOKEN_BIN \
    --expiration-model KEY_MATERIAL_DOES_NOT_EXPIRE
```

### Step 6: Test the migrated key by signing and verifying

To ensure the migration was successful, perform a sign/verify test:

1. First, find the corresponding public key in CloudHSM:

```
PUBLIC_KEY_REF=$(cat public_keys.json | jq -r --arg EC_POINT "$KEY_EC_POINT" '.data.matched_keys[] | select(.attributes."ec-point" == $EC_POINT) | ."key-reference"')
```

2. Export the public key to PEM format:

```
cloudhsm-cli key generate-file --encoding pem --path $PUBLIC_KEY_PEM_FILE --filter "key-reference=$PUBLIC_KEY_REF"
```

3. Create a test message and sign it with the KMS key:

```
echo -n 'Testing My Imported Key!' | openssl base64 -out test_msg_base64.txt
aws kms sign --key-id $KMS_KEY_ID --message fileb://test_msg_base64.txt --message-type RAW --signing-algorithm ECDSA_SHA_256 | jq -r '.Signature' > test_msg_signature.sig
openssl enc -d -base64 -in test_msg_signature.sig -out test_msg_signature.bin
```

4. Verify the signature using the public key from CloudHSM:

```
openssl dgst -sha256 -verify $PUBLIC_KEY_PEM_FILE -signature test_msg_signature.bin test_msg_base64.txt
```

### Step 7: Record migration results

The script tracks the success or failure of each key migration:

- For successful migrations, the CloudHSM key reference, label, and corresponding KMS key ID are recorded in `result_success_<input_filename>_<timestamp>.txt`
- For failed migrations, the CloudHSM key reference and label are recorded in `result_failed_<input_filename>_<timestamp>.txt`

This allows for easy tracking of which keys were successfully migrated and which ones may need attention.

### Managing CloudHSM Key Capacity

**⚠️ Important Consideration**: The migration process inserts a new RSA public key (wrapping key) into CloudHSM for each key migration. This will cause the total number of keys in CloudHSM to increase significantly during bulk migrations.

When CloudHSM reaches its key capacity limit, the migration script may fail. CloudHSM has specific [key capacity limits](https://docs.aws.amazon.com/cloudhsm/latest/userguide/ki-all.html#ki-all-7) that vary by HSM type.

**Recommended Solution**: Periodically identify and delete wrapping keys during batch migrations to prevent capacity issues. These wrapping keys are safe to delete after successful key migration since they are only needed during the import process.

**Identifying wrapping keys:**
All wrapping keys created by the migration scripts use the label prefix `kms_wrapping_key_for_`. You can count and manage them using:

```bash
# Count existing wrapping keys
./count-cloudhsm-keys-by-regex.sh kms_wrapping_key_for_.*

# Delete wrapping keys between migration batches
./delete-cloudhsm-keys-by-regex.sh kms_wrapping_key_for_.*
```

**Best Practice**: 
- Monitor wrapping key count during large migrations
- Delete wrapping keys after completing each batch of migrations
- Ensure successful migration verification before deleting wrapping keys

### Clean up

After the operation, multiple wrapping keys are imported into CloudHSM as public keys. In the migration script, they are set with the same prefix `kms_wrapping_key_for_*`. You may clean up these wrapping keys from CloudHSM after operation using the commands shown above.

## Disclaimer

This code is developed for personal interest and educational purposes only. It is **NOT intended for production use**.

**Use at your own risk.** The authors and contributors:

- Make no warranties or guarantees about the functionality, security, or reliability of this code
- Are not responsible for any data loss, security breaches, or other damages that may result from its use
- Strongly recommend thorough testing in non-production environments before any production consideration
- Advise consulting with AWS security and cryptography experts before implementing any key migration strategies

Key migration involves sensitive cryptographic operations that can permanently affect your security infrastructure. Always follow AWS best practices and your organization's security policies.
