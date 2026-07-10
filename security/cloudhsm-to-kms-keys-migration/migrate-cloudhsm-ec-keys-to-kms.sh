#!/bin/bash
set -eo pipefail

export PATH=$PATH:/opt/cloudhsm/bin

# CloudHSM config
PRIVATE_KEYS_FILE="${1:-private_keys.json}"
# PUBLIC_KEYS_FILE="${2:-public_keys.json}"

# Create staging directory for intermediate files
STAGING_DIR="staging"
mkdir -p $STAGING_DIR

TIMESTAMP="$(date '+%Y%m%d_%H%M')"
FILENAME="${PRIVATE_KEYS_FILE##*/}"      # Gets just the filename
FILENAME_NO_EXT="${FILENAME%.*}"         # Removes the extension
RESULT_FILE_KEYS_FAILED="result_failed_${FILENAME_NO_EXT%.*}_$TIMESTAMP.txt"
RESULT_FILE_KEYS_SUCCESS="result_success_${FILENAME_NO_EXT%.*}_$TIMESTAMP.txt"

# Create log file with timestamp
LOG_FILE="log_$TIMESTAMP.log"

# Setup result files
# echo "CloudHSM_KEY_REF,CLOUDHSM_KEY_LABEL,REASON" > $RESULT_FILE_KEYS_FAILED
# echo "CloudHSM_KEY_REF,CLOUDHSM_KEY_LABEL,KMS_KEY_ID" > $RESULT_FILE_KEYS_SUCCESS

# Process each EC private key only
jq -c '.data.matched_keys[] | select(.attributes["key-type"] == "ec")' $PRIVATE_KEYS_FILE | while read -r KEY; do
    # Extract key details    
    # KEY_REF: Private key key-reference value
    KEY_REF=$(echo $KEY | jq -r '."key-reference"')
    # KEY_LABEL: Private key attributes.label value
    KEY_LABEL=$(echo $KEY | jq -r '.attributes.label')
    # EC_POINT: Private key attribute.ec-point value, which will be used to find respective public key key-reference
    # KEY_EC_POINT=$(echo $KEY | jq -r '.attributes.["ec-point"]')
    # CURVE: Private key attribute.curve value, which will be used to determine KMS key spec
    KEY_CURVE=$(echo $KEY | jq -r '.attributes.curve')

    echo "CloudHSM Key: $KEY_LABEL, $KEY_REF ---------"
    echo "----------------------------------------------" >> $LOG_FILE
    echo "CloudHSM Key: $KEY_LABEL, $KEY_REF, $KEY_CURVE" >> $LOG_FILE

    # # Step 0: Skip if corresponding public key not exists
    # PUBLIC_KEY_REF=$(cat $PUBLIC_KEYS_FILE | jq -r --arg EC_POINT "$KEY_EC_POINT" '.data.matched_keys[] | select(.attributes."ec-point" == $EC_POINT) | ."key-reference"')
    
    # if [ "$PUBLIC_KEY_REF" = "null" ] || [ -z "$PUBLIC_KEY_REF" ]; then
    #     echo "  No corresponding public key found"
    #     echo "No corresponding public key found in $PUBLIC_KEYS_FILE, skipping key $KEY_REF" >> $LOG_FILE
    #     echo $KEY_REF,$KEY_LABEL,"No corresponding public key found" >> $RESULT_FILE_KEYS_FAILED
    #     continue
    # fi

    # Step 1: Create KMS key for ECC
    # Determine key spec based on curve
    if [ "$KEY_CURVE" = "secp256k1" ]; then
        KEY_SPEC="ECC_SECG_P256K1"
    elif [ "$KEY_CURVE" = "prime256v1" ] || [ "$KEY_CURVE" = "secp256r1" ]; then
        KEY_SPEC="ECC_NIST_P256"
    else
        echo "Unsupported curve: $KEY_CURVE for key $KEY_REF" >> $LOG_FILE
        echo $KEY_REF,$KEY_LABEL,"Unsupported curve: $KEY_CURVE for key $KEY_REF" >> $RESULT_FILE_KEYS_FAILED
        continue
    fi
    
    echo "Using key spec $KEY_SPEC for curve $KEY_CURVE" >> $LOG_FILE
    KMS_KEY_ID=$(aws kms create-key --origin EXTERNAL --key-spec $KEY_SPEC --key-usage SIGN_VERIFY \
        --description "Imported from CloudHSM: $KEY_LABEL" \
        --query 'KeyMetadata.KeyId' --output text 2>> $LOG_FILE)
    echo "Created KMS key: $KMS_KEY_ID" >> $LOG_FILE
    echo "  KMS Key: $KMS_KEY_ID"

    # Step 2: Get wrapping parameters and save to named files
    WRAPPING_PUBLIC_KEY_B64="$STAGING_DIR/${KEY_REF}_WrappingPublicKey.b64"
    WRAPPING_PUBLIC_KEY_BIN="$STAGING_DIR/${KEY_REF}_WrappingPublicKey.bin"
    WRAPPING_PUBLIC_KEY_PEM="$STAGING_DIR/${KEY_REF}_WrappingPublicKey.pem"
    IMPORT_TOKEN_B64="$STAGING_DIR/${KEY_REF}_ImportToken.b64"
    IMPORT_TOKEN_BIN="$STAGING_DIR/${KEY_REF}_ImportToken.bin"
    
    # Save wrapping key parameters to files
    #  Wrapping is always RSA-based regardless of the target key type being imported
    aws kms get-parameters-for-import \
        --key-id "$KMS_KEY_ID" \
        --wrapping-algorithm RSAES_OAEP_SHA_256 \
        --wrapping-key-spec RSA_4096 \
        --query "[ImportToken, PublicKey]" \
        --output text 2>> $LOG_FILE \
        | awk '{print $1 > "'$IMPORT_TOKEN_B64'"; print $2 > "'$WRAPPING_PUBLIC_KEY_B64'"}'

    echo "Saved wrapping parameters to $WRAPPING_PUBLIC_KEY_B64 and $IMPORT_TOKEN_B64" >> $LOG_FILE

    # decode the token from base64 to binary format before it can be used
    openssl enc -d -base64 -A -in $IMPORT_TOKEN_B64 -out $IMPORT_TOKEN_BIN 2>> $LOG_FILE

    # Decode the base64 encoding to binary DER format
    openssl enc -d -base64 -A -in $WRAPPING_PUBLIC_KEY_B64 -out $WRAPPING_PUBLIC_KEY_BIN 2>> $LOG_FILE
    
    # Convert the wrapping public key from DER to PEM format
    openssl rsa -pubin -in $WRAPPING_PUBLIC_KEY_BIN -inform DER -outform PEM -out $WRAPPING_PUBLIC_KEY_PEM 2>> $LOG_FILE
    
    echo "Converted wrapping key to PEM format: $WRAPPING_PUBLIC_KEY_PEM" >> $LOG_FILE

    # Step 3: Import wrapping key to CloudHSM
    WRAP_LABEL="kms_wrapping_key_for_${KEY_REF}"
    
    # Import the wrapping key
    import_key_output=$(cloudhsm-cli key import pem --path $WRAPPING_PUBLIC_KEY_PEM --label $WRAP_LABEL --key-type-class rsa-public --attributes wrap=true 2>> $LOG_FILE)
    WRAP_KEY_REF=$(echo "$import_key_output" | jq -r '.data.key["key-reference"]')
    echo "$import_key_output" >> $LOG_FILE

    echo "Imported wrapping key to CloudHSM with label = $WRAP_LABEL, key-reference = $WRAP_KEY_REF" >> $LOG_FILE

    # Step 4: Wrap the private key inside CloudHSM
    WRAPPED_KEY_FILE="$STAGING_DIR/${KEY_REF}_EncryptedKeyMaterial.bin"

    cloudhsm-cli key wrap rsa-oaep \
        --payload-filter "key-reference=$KEY_REF" \
        --wrapping-filter "key-reference=$WRAP_KEY_REF" \
        --hash-function sha256 --mgf mgf1-sha256 \
        --path $WRAPPED_KEY_FILE >> $LOG_FILE 2>&1
    
    echo "Wrapped key saved to: $WRAPPED_KEY_FILE" >> $LOG_FILE
    
    # Step 5: Import to KMS
    aws kms import-key-material \
        --key-id $KMS_KEY_ID \
        --encrypted-key-material fileb://$WRAPPED_KEY_FILE \
        --import-token fileb://$IMPORT_TOKEN_BIN \
        --expiration-model KEY_MATERIAL_DOES_NOT_EXPIRE >> $LOG_FILE 2>&1

    echo "Successfully imported key $KEY_LABEL to KMS key $KMS_KEY_ID" >> $LOG_FILE

    # Wait for key to become enabled
    echo "Waiting for key to become enabled..."
    while true; do
        KEY_STATE=$(aws kms describe-key --key-id $KMS_KEY_ID --query 'KeyMetadata.KeyState' --output text)
        echo "Key state: $KEY_STATE"
        if [ "$KEY_STATE" = "Enabled" ]; then
            # Additional wait to ensure key is fully ready for operations
            sleep 5
            break
        elif [ "$KEY_STATE" = "PendingImport" ]; then
            sleep 2
        else
            echo "Unexpected key state: $KEY_STATE"
            echo $KEY_REF,$KEY_LABEL,"Key import failed - state: $KEY_STATE" >> $RESULT_FILE_KEYS_FAILED
            continue 2  # Skip to next key
        fi
    done

    # Step 6: Test Keys in KMS using Public Key in CloudHSM

    ### Sign Test Message with CloudHSM ###
    # Create a simple message and encode it in base64 in a text file
    echo -n 'Hello World' > message.txt
    # cat message.txt | openssl base64 -out message_base64.txt

    # Sign using CloudHSM private key
    echo "Signing using CloudHSM private key..."
    # signature.sig contains base64-encoded signature; signaturee.bin contains binary signature
    cloudhsm-cli crypto sign ecdsa --key-filter "key-reference=$KEY_REF" --hash-function sha256 --data-path message.txt --data-type raw | jq -r '.data.signature' > signature.sig
    # Decode the base64 signature to binary format
    openssl enc -d -base64 -in signature.sig -out signature.bin

    # # Option 1: Verify using CloudHSM public key
    # cloudhsm-cli crypto verify ecdsa --key-filter "key-reference=$PUBLIC_KEY_REF" --hash-function sha256 --data-path message.txt --signature-path signature.bin --data-type raw > verification.txt
    # # Check verification result
    # result=$( [ "$(jq -r '.error_code' verification.txt)" -eq 0 ] && echo "success" || echo "failure" )
    # echo "Verification using CloudHSM public key: $result"

    # # Option 2: Verify using OpenSSL and PEM file from CloudHSM public key
    # # Convert raw signature to DER format for OpenSSL
    openssl asn1parse -genconf <(echo -e "asn1=SEQUENCE:sig\n[sig]\nr=INTEGER:0x$(head -c 32 signature.bin | xxd -p -c 32)\ns=INTEGER:0x$(tail -c 32 signature.bin | xxd -p -c 32)") -out signature.der
    # # Export public key to PEM
    # cloudhsm-cli key generate-file --encoding pem --path public_key_cloudhsm.pem --filter "key-reference=$PUBLIC_KEY_REF"
    # # Verify using PEM file and DER signature
    # openssl dgst -sha256 -verify public_key_cloudhsm.pem -signature signature.der message.txt > verification.txt
    # result=$(grep -q "Verified OK" verification.txt && echo "success" || echo "failure")
    # echo "Verification using PEM of CloudHSM public key: $result"

    # Option 3: Verify using KMS Public Key with retry logic
    result="failure"
    max_attempts=5
    for attempt in $(seq 1 $max_attempts); do
        echo "Verification attempt $attempt/$max_attempts using KMS public key..."
        
        if aws kms verify --key-id $KMS_KEY_ID --message fileb://message.txt --message-type RAW --signing-algorithm ECDSA_SHA_256 --signature fileb://signature.der --output json > verification.txt 2>&1; then
            result=$(cat verification.txt | jq -r 'if .SignatureValid then "success" else "failure" end')
            echo "Verification using KMS public key: $result"
            break
        else
            if grep -qi "pending import\|invalid.*state" verification.txt; then
                echo "Key still not ready for operations, waiting and retrying..."
                sleep $((attempt * 3))  # Exponential backoff: 3, 6, 9, 12, 15 seconds
            else
                echo "Verification failed with error:"
                cat verification.txt
                break
            fi
        fi
        
        if [ $attempt -eq $max_attempts ]; then
            echo "Max retry attempts reached, verification failed"
            result="failure"
        fi
    done

    # # Option 4: Verify using OpenSSL and PEM file from KMS public key
    # aws kms get-public-key --key-id $KMS_KEY_ID --query 'PublicKey' --output text | base64 --decode > public_key.der
    # # Convert public key from KMS in DER (binary) format to PEM (Base64-encoded), use OpenSSL
    # openssl ec -pubin -inform DER -in public_key.der -outform PEM -out public_key.pem
    # openssl dgst -sha256 -verify public_key.pem -signature signature.der message.txt > verification.txt
    # result=$(grep -q "Verified OK" verification.txt && echo "success" || echo "failure")
    # echo "Verification using PEM of KMS public key: $result"

    # Step 7: Save results to CSV file
    if [ "$result" != "success" ]; then
        echo "FAILED to process CloudHSM key $KEY_REF, $KEY_LABEL" | tee -a $LOG_FILE
        echo $KEY_REF,$KEY_LABEL,"Verification failed: $result" >> $RESULT_FILE_KEYS_FAILED
    else
        echo "SUCCEED to process CloudHSM key $KEY_REF, $KEY_LABEL" | tee -a $LOG_FILE
        echo $KEY_REF,$KEY_LABEL,$KMS_KEY_ID >> $RESULT_FILE_KEYS_SUCCESS
    fi

done

# Count results
SUCCESSFUL_COUNT=0
FAILED_COUNT=0

if [ -f "$RESULT_FILE_KEYS_SUCCESS" ]; then
    SUCCESSFUL_COUNT=$(wc -l < "$RESULT_FILE_KEYS_SUCCESS" | tr -d ' ')
fi

if [ -f "$RESULT_FILE_KEYS_FAILED" ]; then
    FAILED_COUNT=$(wc -l < "$RESULT_FILE_KEYS_FAILED" | tr -d ' ')
fi

echo "------ Migration Complete: $SUCCESSFUL_COUNT successful, $FAILED_COUNT failed --------" | tee -a $LOG_FILE


