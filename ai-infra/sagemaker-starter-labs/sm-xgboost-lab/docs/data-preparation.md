English | [한국어](data-preparation.ko.md)

# Data Preparation Guide

In this sample, `data_prep.py` downloads a public credit card dataset and converts it
into CSV files that SageMaker built-in XGBoost can train on. Because the dataset is small
(30,000 rows), it is prepared in local Python without a separate Processing Job, and only
the actual training runs on SageMaker.

## Public dataset used

The [UCI Default of Credit Card Clients dataset (ID `350`)](https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients) is
a binary classification dataset that uses the past payment history of 30,000 credit card
customers in Taiwan to predict **whether a customer will default on next month's payment**.

| Item | Value |
|---|---|
| Observations | 30,000 customers |
| Features | 23 features: credit limit, demographic information, and 6 months of repayment status, bill amounts, and payment amounts |
| Label `0` | No default next month, 23,364 rows |
| Label `1` | Default next month, 6,636 rows |
| UCI DOI | `10.24432/C55S3H` |

Defaulting customers make up about 22.1% of the total. Predicting that no one defaults
already yields about 77.9% accuracy, so this example uses ROC-AUC, which measures how well
the model ranks the two classes, as the training metric.

This is an older research dataset published from 2005 data on Taiwanese customers. It is
well suited for learning the SageMaker training workflow, but it does not represent the
credit risk or operational performance of current Korean customers.

## Understanding the features

The UCI API returns feature names `X1` through `X23`. `data_prep.py` explicitly renames
them to the variable names used in the original paper.

| Feature | Plain-language description |
|---|---|
| `LIMIT_BAL` | Credit limit granted to the customer (NT dollar) |
| `SEX` | Gender code. In the original data, `1=male`, `2=female` |
| `EDUCATION` | Education level code. `1=graduate school`, `2=university`, `3=high school`, `4=others` |
| `MARRIAGE` | Marital status code. `1=married`, `2=single`, `3=others` |
| `AGE` | Age |
| `PAY_0`, `PAY_2` ... `PAY_6` | Monthly repayment status from September back to April 2005. Larger positive values mean more months of delay |
| `BILL_AMT1` ... `BILL_AMT6` | Monthly bill amount for the same 6 months |
| `PAY_AMT1` ... `PAY_AMT6` | Monthly amount actually paid for the same 6 months |

`PAY_0` is followed by `PAY_2` because the original data's names are kept as-is. The
repayment status and categorical columns also contain some extra codes not described in
the paper. This sample does not arbitrarily correct the public data and uses the numbers
as they are.

`SEX`, `EDUCATION`, `MARRIAGE`, and `AGE` are also included in training. This provides a
simple hands-on experience with a tabular model that takes many attributes as input at
once. In real credit decisions, however, you must first review legal and fairness
policies to determine whether demographic variables may be used as features, and evaluate
per-group performance and potential for discrimination.

Using the category codes directly as numbers is also a simplification for teaching
purposes. For a production model, compare candidates such as one-hot encoding and XGBoost
categorical features, and apply the same transformation in both Training and inference.

## What the code validates

`load_credit_default_data()` checks the following locally before creating a Training Job.

1. Verifies that UCI returns exactly 23 features, `X1` through `X23`.
2. Converts all features to `float32` and the label to `int32`.
3. Verifies that there are no missing or infinite values.
4. Verifies that labels are exactly `0` and `1`.

If the public dataset schema changes or strings are mixed in, the run fails before any S3
upload or Training costs are incurred.

## Splitting train and validation

`stratified_split()` samples from each class separately so that train and validation keep
a similar default rate. Because `RANDOM_SEED=42`, the same source data always produces the
same split.

| Split | Total | No default `0` | Default `1` |
|---|---:|---:|---:|
| Train | 24,000 | 18,691 | 5,309 |
| Validation | 6,000 | 4,673 | 1,327 |

This dataset has no timestamp to distinguish an observation point per customer, so an 80/20
random split is used. Real credit risk models typically hold out a period later than the
training period as an OOT (out-of-time) test to check performance degradation over time.
If the same customer or account has multiple rows, you should also split at the customer
level.

## SageMaker Training CSV

SageMaker built-in XGBoost interprets the first column of the CSV as the label and does
not read a header. The Training files are therefore built in the following order.

```text
label,LIMIT_BAL,SEX,EDUCATION,MARRIAGE,AGE,PAY_0,...,PAY_AMT6
```

The actual `train.csv` and `validation.csv` files have no header.

```text
0,20000,2,2,1,24,2,...,0
1,120000,2,2,2,26,-1,...,2000
```

The two files are passed as separate SageMaker input channels.

```text
train      -> learn model parameters
validation -> compute validation:auc during training
```

They are stored in S3 under a per-run prefix.

```text
s3://<bucket>/sagemaker-xgboost-lab/credit-card-default/runs/<run-id>/data/
```

`data-summary.json` records the dataset name and ID, feature order, target meaning, row
and class counts per split, and the random seed. The same file is written to S3 and as an
MLflow Artifact so you can see which data configuration was used to build a model.

## Batch Transform input

At actual inference time the ground truth is unknown, so the Batch Transform CSV does not
include the label. The order of the remaining 23 features must exactly match Training.

```text
20000,2,2,1,24,2,...,0
120000,2,2,2,26,-1,...,2000
```

To verify that everything works, this sample reuses the validation features as inference
input. Each output value is the **probability score of default next month** for the same
row. Do not interpret it directly as an exact PD; evaluate calibration separately. In
production, use new customer data at scoring time, and once actual repayment outcomes are
confirmed, join them with the predictions to evaluate performance and calibration.

## Replacing with real data

To start, it is enough to replace `load_credit_default_data()` with real S3 or warehouse
input and clarify the following items first.

1. Fix the customer, observation point, and label definition in both documentation and code.
2. Prevent target leakage, where future repayment outcomes leak into features.
3. Share missing-value handling, categorical encoding, and feature order between Training and inference.
4. Define the train, validation, and OOT test periods and the rules for handling duplicate customers.
5. Look beyond AUC at approval rate, default rate, recall, calibration, and business cost per threshold.
6. Review the justification for using sensitive features or features that may act as proxies, and check per-group performance.

When data preparation requires SQL joins, large-scale transformations, or scheduled runs,
move this logic into your existing ETL or SageMaker Processing/Pipelines. At this sample's
stage, local preparation plus S3 artifacts is the easiest to understand and the least
costly.
