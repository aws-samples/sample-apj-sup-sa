# Quick Setup Guide for CloudHSM

AWS CloudHSM provides robust hardware-based key storage for organizations requiring enhanced security and compliance in the cloud. This guide walks you through the step-by-step process of setting up a CloudHSM cluster in AWS, from creation to initialization and key management. By following these instructions, you'll establish a secure cryptographic infrastructure that can be integrated with your applications and AWS services.

## Setup CloudHSM

### Create and Initialize CloudHSM

This step creates a CloudHSM cluster with 1 HSM instance in Singapore region.

#### Step 1: Create CloudHSM Cluster

Create a CloudHSM cluster and take note of its `ClusterId` value.

```bash
aws cloudhsmv2 create-cluster \
--hsm-type hsm2m.medium \
--mode FIPS \
--subnet-ids subnet-09bb7fdaf05e437bf \
--region ap-southeast-1
```

Result from the command.

```json
{
	"Cluster": {
		"BackupPolicy": "DEFAULT",
		"BackupRetentionPolicy": {
			"Type": "DAYS",
			"Value": "90"
		},
		"ClusterId": "cluster-pgyrbxp7vpi",
		"CreateTimestamp": "2025-07-11T15:38:38.603000+08:00",
		"Hsms": [],
		"HsmType": "hsm2m.medium",
		"State": "CREATE_IN_PROGRESS",
		"SubnetMapping": {
			"ap-southeast-1a": "subnet-09bb7fdaf05e437bf"
		},
		"VpcId": "vpc-0544788d30aeff7a0",
		"NetworkType": "IPV4",
		"Certificates": {},
		"Mode": "FIPS"
	}
}
```

#### Step 2: Create HSM in the Cluster

Run following command twice to create a HSM instance in the cluster. Replace cluster-id with the correct cluster-id.

```bash
aws cloudhsmv2 create-hsm \
--cluster-id cluster-pgyrbxp7vpi \
--availability-zone ap-southeast-1a \
--region ap-southeast-1
```

Result from the command.

```json
{
	"Hsm": {
		"AvailabilityZone": "ap-southeast-1a",
		"ClusterId": "cluster-pgyrbxp7vpi",
		"SubnetId": "subnet-09bb7fdaf05e437bf",
		"HsmId": "hsm-bscclnb4rgc",
		"HsmType": "hsm2m.medium",
		"State": "CREATE_IN_PROGRESS"
	}
}
```

#### Step 3: Wait for the HSM to be Active

Check the status of the cluster with following command. Replace the ClusterIds with value from previous step.

```bash
aws cloudhsmv2 describe-clusters --filters clusterIds=cluster-pgyrbxp7vpi --region ap-southeast-1
```

The cluster state is currently `UNINITIALIZED` and the HSM instance is in `ACTIVE` state. We will initialize the cluster in next few steps.

```json
{
	...
	"ClusterId": "cluster-pgyrbxp7vpi",
	"CreateTimestamp": "2025-07-11T15:38:38.603000+08:00",
	"Hsms": [
		{
			"AvailabilityZone": "ap-southeast-1a",
			"ClusterId": "cluster-pgyrbxp7vpi",
			"SubnetId": "subnet-09bb7fdaf05e437bf",
			"EniId": "eni-02e9cefea8bdb2371",
			"EniIp": "10.0.15.211",
			"HsmId": "hsm-bscclnb4rgc",
			"HsmType": "hsm2m.medium",
			"State": "ACTIVE"
		}
	],
	"HsmType": "hsm2m.medium",
	"SecurityGroup": "sg-01b7eb8b5aebf0112",
	"State": "UNINITIALIZED"
	...
}
```

#### Step 4: Create Certificate Authority for Cluster Initialization

Create a self-signed certificate authority (CA).

```bash
openssl genrsa -out customerCA.key 2048
openssl req -new -x509 -days 3652 -key customerCA.key -out customerCA.crt -subj "/CN=CloudHSM-CA/O=YourOrganization/C=SG"
```

#### Step 5: Get the Cluster's CSR and Sign It

In following command, replace clusterId with your cluster ID. Get the CSR from the cluster, and sign the CSR with the CA from previous step.

```bash
aws cloudhsmv2 describe-clusters --filters clusterIds=cluster-pgyrbxp7vpi --query "Clusters[0].Certificates.ClusterCsr" --output text --region ap-southeast-1 > ClusterCsr.csr

openssl x509 -req -days 3652 -in ClusterCsr.csr -CA customerCA.crt -CAkey customerCA.key -CAcreateserial -out CustomerHsmCertificate.crt
```

By now, here are the certificate files generated.

```text
-rw-r--r-- 1 ubuntu ubuntu 1026 Jul 11 09:59 ClusterCsr.csr
-rw-r--r-- 1 ubuntu ubuntu   41 Jul 11 09:59 CustomerCA.srl
-rw-r--r-- 1 ubuntu ubuntu 1281 Jul 11 09:59 CustomerHsmCertificate.crt
-rw-r--r-- 1 ubuntu ubuntu 1229 Jul 11 09:59 customerCA.crt
-rw------- 1 ubuntu ubuntu 1704 Jul 11 09:59 customerCA.key
```

#### Step 6: Initialize the Cluster with the Signed Certificate

In following command, replace cluster-id with your cluster ID.

```bash
aws cloudhsmv2 initialize-cluster \
--cluster-id cluster-pgyrbxp7vpi \
--region ap-southeast-1 \
--signed-cert file://CustomerHsmCertificate.crt \
--trust-anchor file://customerCA.crt
```

Result:

```json
{
	"State": "INITIALIZE_IN_PROGRESS",
	"StateMessage": "Cluster is initializing. State will change to INITIALIZED upon completion."
}
```

#### Step 7: Wait for Initialization to Complete

Check the status of the cluster using following command. Update clusterIds with your cluster ID.

```bash
aws cloudhsmv2 describe-clusters --filters clusterIds=cluster-pgyrbxp7vpi --query "Clusters[0].[State,Hsms[0].State,Hsms[0].HsmId]" --region ap-southeast-1
```

Result:

```json
["INITIALIZE_IN_PROGRESS", "ACTIVE", "hsm-bscclnb4rgc"]
```

#### Step 8: Get HSM IP Address for Client Configuration

```bash
aws cloudhsmv2 describe-clusters --filters clusterIds=cluster-pgyrbxp7vpi --query "Clusters[0].Hsms[0].EniIp" --output text --region ap-southeast-1
```

Result:

```
10.0.15.211
```

### Activate CloudHSM Cluster

We need to activate the CloudHSM cluster using CloudHSM CLI.

#### Step 1: Install and Configure CloudHSM CLI

Depends on your OS, you can download and install CloudHSM CLI by referring to this guide https://docs.aws.amazon.com/cloudhsm/latest/userguide/gs_cloudhsm_cli-install.html.

Check the Ubuntu version

```bash
lsb_release -a
```

For example, for Ubuntu 24,

1. Download installation file and install it.

```sh
wget https://s3.amazonaws.com/cloudhsmv2-software/CloudHsmClient/Noble/cloudhsm-cli_latest_u24.04_amd64.deb
sudo apt install ./cloudhsm-cli_latest_u24.04_amd64.deb
```

2. Add `/opt/cloudhsm/bin` to path.

```sh
export PATH=$PATH:/opt/cloudhsm/bin
```

3. Specify the IP address of the HSM(s) in your cluster. Replace the IP with the IP of your HSM instance, which you get in previous step.

```sh
sudo /opt/cloudhsm/bin/configure-cli -a 10.0.15.211
```

#### Step 2: Activate the CloudHSM Cluster

Reference: https://docs.aws.amazon.com/cloudhsm/latest/userguide/activate-cluster.html

1. Make sure the EC2 instance you are in can connect to CloudHSM Instance at port 2223. If not, update the security group of CloudHSM cluster to allow access from EC2 IP at port 2223-2225.

```
nc -vz <CloudHSM_IP> 2223
```

2. Copy the issuing certificate `customerCA.crt` to the default location for cloudhsm-cli, i.e. the `/opt/cloudhsm/etc/` folder.

```bash
sudo cp customerCA.crt /opt/cloudhsm/etc/
```

3. Run following command to start interacting with CloudHSM cluster.

```bash
cloudhsm-cli interactive
```

#### Step 3: Activate CloudHSM Cluster

We need to activate cluster before we can user it.

1. CloudHSM comes with 2 default users, `admin` and `app_user`.

```bash
aws-cloudhsm > user list
```

2. We need to activate the cluster by setting password for `admin` user.

```bash
aws-cloudhsm > cluster activate
```

After this is done, the role of `admin` user will change from `"role": "unactivated-admin"` to `"role": "admin"`.

3. Exit from cloudhsm-cli.

```bash
aws-cloudhsm > quit
```

## Working with CloudHSM using CloudHSM CLI

### Create an user with `cryto-user` Role

1. Login as admin user.

```bash
aws-cloudhsm > login --username admin --role admin
```

2. Create an user with `cryto-user` role.

```bash
aws-cloudhsm > user create --username user1 --role crypto-user
```

3. Quit from interactive terminal.

```bash
aws-cloudhsm > quit
```

### Use CloudHSM CLI in Bash

Instead of using CloudHSM CLI in bash mode instead of interactive mode. We need to set CloudHSM username, password and role in the environment variables.

1. Replace username and password accordingly.

```
export PATH=$PATH:/opt/cloudhsm/bin

export CLOUDHSM_ROLE=crypto-user
export CLOUDHSM_PIN="user1:Qwer1234"
```

2. Test the credential. Make sure it runs successfully.

```bash
cloudhsm-cli user list
```

### Create Sample Keys

1. Create 20 sample keys each for `rsa` and `ec` types.

```bash
for i in {1..20}; do
  cloudhsm-cli key generate-asymmetric-pair rsa \
    --public-label "rsa-pub-$i" \
    --private-label "rsa-priv-$i" \
    --modulus-size-bits 2048 \
    --public-exponent 65537;
done
```

```bash
for i in {1..20}; do
  cloudhsm-cli key generate-asymmetric-pair ec \
    --curve secp256k1 \
    --public-label "ec-pub-$i" \
    --private-label "ec-priv-$i";
done
```

2. List sample same keys. Take note that `key list` command returns result in batch.

```bash
cloudhsm-cli key list
```

### Delete Keys by Label

To delete a key, delete it by its `attributes.label` value.

```bash
cloudhsm-cli key delete --filter attr.label="$label"
```

List keys whose label matches a regex. Similarly, the returned key_labels is only a subset.

```bash
pattern="rsa-*"
key_labels=$(cloudhsm-cli key list --verbose | jq -r '.data.matched_keys[].attributes.label' | grep -E "$pattern")
echo $key_labels

while read -r label; do
	cloudhsm-cli key delete --filter attr.label="$label"
done <<< "$key_labels"
```

## Clean Up CloudHSM Cluster

1. List all HSM instances ID in the cluster.

```bash
aws cloudhsmv2 describe-clusters --filters clusterIds=cluster-pgyrbxp7vpi --region ap-southeast-1
```

2. Delete the HSM instance one by one.

```bash
# Delete the CloudHSM cluster (must delete HSM instances first)
aws cloudhsmv2 delete-hsm \
--cluster-id cluster-fhtbm47tsjo \
--hsm-id hsm-7qsltqrczv5 \
--region ap-southeast-1
```

3. Delete the cluster

```bash
# Delete cluster
aws cloudhsmv2 delete-cluster \
--cluster-id cluster-fhtbm47tsjo \
--region ap-southeast-1
```

## Conclusion

Setting up AWS CloudHSM might seem complex, but this guide breaks down the process into manageable steps. You've learned how to create and initialize a CloudHSM cluster, configure the necessary certificates, manage users, and work with cryptographic keys. Remember to clean up your resources when they're no longer needed to avoid unnecessary costs. With your CloudHSM environment now operational, you have a secure foundation for implementing cryptographic operations in your AWS workloads while maintaining control over your encryption keys.
