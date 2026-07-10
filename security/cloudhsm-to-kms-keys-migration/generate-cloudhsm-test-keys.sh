for i in {1..20}; do
	cloudhsm-cli key generate-asymmetric-pair ec \
		--public-label "mvgx-ec-pub-$i" \
		--private-label "mvgx-ec-priv-$i" \
		--curve secp256k1 \
		--public-attributes encrypt=true verify=true \
		--private-attributes decrypt=true sign=true 
done

for i in {1..20}; do
  cloudhsm-cli key generate-asymmetric-pair rsa \
    --public-label "mvgx-rsa-pub-$i" \
    --private-label "mvgx-rsa-priv-$i" \
    --modulus-size-bits 2048 \
    --public-exponent 65537 \
		--public-attributes encrypt=true verify=true \
		--private-attributes decrypt=true sign=true 		
done

