[English](data-preparation.md) | 한국어

# Data 준비 가이드

이 sample의 `data_prep.py`는 공개 신용카드 데이터를 내려받아 SageMaker built-in
XGBoost가 학습할 수 있는 CSV로 바꿉니다. 데이터가 30,000행으로 작기 때문에 별도
Processing Job 없이 local Python에서 준비하고, 실제 학습만 SageMaker에서 실행합니다.

## 사용하는 공개 데이터

[UCI Default of Credit Card Clients dataset (ID `350`)](https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients)은
대만 신용카드 고객 30,000명의 과거 결제 정보로 **다음 달 대금 연체 여부**를 예측하는
binary classification 데이터입니다.

| 항목 | 값 |
|---|---|
| 관측치 | 30,000명 |
| Feature | 신용한도, 인구통계 정보, 6개월 상환 상태·청구금액·납부금액 23개 |
| Label `0` | 다음 달 연체하지 않음, 23,364건 |
| Label `1` | 다음 달 연체, 6,636건 |
| UCI DOI | `10.24432/C55S3H` |

연체 고객은 전체의 약 22.1%입니다. Accuracy만 보면 모두 비연체라고 예측해도 약
77.9%가 나오므로, 이 예제는 두 class의 순위를 얼마나 잘 구분하는지 보는 ROC-AUC를
학습 metric으로 사용합니다.

이 데이터는 2005년 대만 고객을 바탕으로 발표된 오래된 연구 데이터입니다. SageMaker
학습 흐름을 익히기에는 적합하지만 현재 한국 고객의 신용위험이나 운영 성능을 대표하지
않습니다.

## Feature 이해하기

UCI API는 feature 이름을 `X1`부터 `X23`까지 반환합니다. `data_prep.py`는 원 논문의
변수명으로 명시적으로 바꿉니다.

| Feature | 쉬운 설명 |
|---|---|
| `LIMIT_BAL` | 고객에게 부여된 신용한도(NT dollar) |
| `SEX` | 성별 코드. 원 데이터는 `1=남성`, `2=여성` |
| `EDUCATION` | 교육 수준 코드. `1=대학원`, `2=대학교`, `3=고등학교`, `4=기타` |
| `MARRIAGE` | 혼인 상태 코드. `1=기혼`, `2=미혼`, `3=기타` |
| `AGE` | 나이 |
| `PAY_0`, `PAY_2` ... `PAY_6` | 2005년 9월부터 4월까지 월별 상환 상태. 양수가 클수록 연체 개월 수가 큼 |
| `BILL_AMT1` ... `BILL_AMT6` | 같은 6개월의 월별 청구금액 |
| `PAY_AMT1` ... `PAY_AMT6` | 같은 6개월의 월별 실제 납부금액 |

`PAY_0` 다음이 `PAY_2`인 것은 원 데이터의 이름을 그대로 따른 것입니다. 상환 상태와
범주형 column에는 논문 설명 밖의 추가 코드도 일부 존재합니다. 이 sample은 공개
데이터를 임의로 보정하지 않고 숫자 그대로 사용합니다.

`SEX`, `EDUCATION`, `MARRIAGE`, `AGE`도 학습에 포함합니다. 이를 통해 여러 항목을
한꺼번에 입력하는 tabular model을 단순하게 경험할 수 있습니다. 다만 실제 신용
의사결정에서는 인구통계 변수를 feature로 사용할 수 있는지 법무·공정성 정책을 먼저
검토하고, 집단별 성능과 차별 가능성을 평가해야 합니다.

범주 코드를 그대로 숫자로 사용하는 것도 교육용 단순화입니다. 운영 모델에서는
one-hot encoding, XGBoost categorical feature 등 후보를 비교하고 Training과 inference에
동일한 변환을 적용해야 합니다.

## Code가 확인하는 것

`load_credit_default_data()`는 다음을 Training Job 생성 전에 local에서 확인합니다.

1. UCI가 `X1`부터 `X23`까지 정확히 23개 feature를 반환하는지 확인합니다.
2. 모든 feature를 `float32`, label을 `int32`로 변환합니다.
3. missing value와 무한대 값이 없는지 확인합니다.
4. label이 정확히 `0`과 `1`인지 확인합니다.

공개 데이터 schema가 바뀌거나 문자열이 섞이면 S3 upload와 Training 비용이 발생하기
전에 실패합니다.

## Train과 validation 분리

`stratified_split()`은 각 class에서 따로 sampling하여 train과 validation의 연체 비율을
비슷하게 유지합니다. `RANDOM_SEED=42`이므로 같은 원본 데이터에서는 같은 split이
만들어집니다.

| Split | 전체 | 비연체 `0` | 연체 `1` |
|---|---:|---:|---:|
| Train | 24,000 | 18,691 | 5,309 |
| Validation | 6,000 | 4,673 | 1,327 |

이 데이터에는 고객별 기준 시점을 구분할 timestamp가 없어 80/20 random split을
사용합니다. 실제 신용위험 모델은 보통 학습 기간보다 미래 기간을 OOT(out-of-time)
test로 남겨 시간에 따른 성능 저하를 확인합니다. 같은 고객이나 계정의 row가 여러 개면
customer 단위로도 분리해야 합니다.

## SageMaker Training CSV

SageMaker built-in XGBoost는 CSV의 첫 column을 label로 해석하며 header는 읽지 않습니다.
따라서 Training file은 다음 순서로 만듭니다.

```text
label,LIMIT_BAL,SEX,EDUCATION,MARRIAGE,AGE,PAY_0,...,PAY_AMT6
```

실제 `train.csv`와 `validation.csv`에는 header가 없습니다.

```text
0,20000,2,2,1,24,2,...,0
1,120000,2,2,2,26,-1,...,2000
```

두 file은 서로 다른 SageMaker input channel로 전달됩니다.

```text
train      -> model parameter 학습
validation -> 학습 중 validation:auc 계산
```

S3에는 실행별 prefix로 저장합니다.

```text
s3://<bucket>/sagemaker-xgboost-lab/credit-card-default/runs/<run-id>/data/
```

`data-summary.json`에는 dataset 이름과 ID, feature 순서, target 의미, split별 row와
class 수, random seed를 남깁니다. 같은 파일을 S3와 MLflow Artifact에 기록하여 어떤
데이터 구성으로 model을 만들었는지 확인할 수 있게 합니다.

## Batch Transform input

실제 inference 시점에는 정답을 모르므로 Batch Transform CSV에는 label을 넣지 않습니다.
나머지 23개 feature의 순서는 Training과 정확히 같아야 합니다.

```text
20000,2,2,1,24,2,...,0
120000,2,2,2,26,-1,...,2000
```

이 sample은 동작 확인을 위해 validation feature를 inference input으로 재사용합니다.
출력의 각 값은 같은 row에 대한 **다음 달 연체 probability score**입니다. 이를 바로
정확한 PD로 해석하지 말고 calibration을 별도로 평가해야 합니다. 운영에서는 scoring
시점의 새 고객 데이터를 사용하고, 실제 상환 결과가 확정된 뒤 prediction과 결합해
성능과 calibration을 평가합니다.

## 실제 데이터로 교체할 때

처음에는 `load_credit_default_data()`를 실제 S3 또는 warehouse input으로 바꾸고 다음
항목부터 명확히 하면 충분합니다.

1. 고객과 기준 시점, label 정의를 문서와 code에서 고정합니다.
2. 미래 상환 결과가 feature에 섞이는 target leakage를 막습니다.
3. missing value, categorical encoding과 feature 순서를 Training/inference에서 공유합니다.
4. train, validation, OOT test 기간과 중복 고객 처리 기준을 정합니다.
5. AUC뿐 아니라 threshold별 승인율, 연체율, recall, calibration과 business cost를 봅니다.
6. 민감하거나 대리변수가 될 수 있는 feature의 사용 근거와 집단별 성능을 검토합니다.

데이터 준비가 SQL join, 대규모 변환, 정기 실행을 필요로 할 때 이 logic을 기존 ETL이나
SageMaker Processing/Pipelines로 옮기면 됩니다. 이 sample 단계에서는 local 준비와 S3
artifact가 가장 이해하기 쉽고 비용도 작습니다.
