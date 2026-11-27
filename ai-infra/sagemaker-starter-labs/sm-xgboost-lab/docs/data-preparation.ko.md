[English](data-preparation.md) | 한국어

# 데이터 준비 가이드

이 샘플의 `data_prep.py`는 공개 신용카드 데이터를 다운로드해 SageMaker built-in
XGBoost로 학습할 수 있는 CSV로 변환합니다. 데이터가 30,000행으로 작기 때문에 별도의
Processing Job 없이 로컬 Python에서 준비하고, 실제 학습만 SageMaker에서 실행합니다.

## 사용하는 공개 데이터

[UCI Default of Credit Card Clients dataset (ID `350`)](https://archive.ics.uci.edu/dataset/350/default+of+credit+card+clients)은
대만 신용카드 고객 30,000명의 과거 결제 정보로 **다음 달 대금 연체 여부**를 예측하는
이진 분류 데이터셋입니다.

| 항목 | 값 |
|---|---|
| 관측치 | 30,000명 |
| Feature | 신용한도, 인구통계 정보, 6개월 상환 상태·청구금액·납부금액 23개 |
| 레이블 `0` | 다음 달 연체하지 않음, 23,364건 |
| 레이블 `1` | 다음 달 연체, 6,636건 |
| UCI DOI | `10.24432/C55S3H` |

연체 고객은 전체의 약 22.1%입니다. 모든 고객을 비연체로 예측해도 accuracy가 약
77.9%에 이르므로, 이 예제는 두 클래스를 순위로 얼마나 잘 구분하는지 측정하는 ROC-AUC를
학습 지표로 사용합니다.

이 데이터는 2005년 대만 고객을 바탕으로 발표된 오래된 연구 데이터입니다. SageMaker
학습 흐름을 익히기에는 적합하지만, 현재 한국 고객의 신용위험이나 운영 성능을 대표하지는
않습니다.

## Feature 이해하기

UCI API는 feature 이름을 `X1`부터 `X23`까지로 반환합니다. `data_prep.py`는 이를 원 논문의
변수명으로 명시적으로 바꿉니다.

| Feature | 설명 |
|---|---|
| `LIMIT_BAL` | 고객에게 부여된 신용한도(NT dollar) |
| `SEX` | 성별 코드. 원 데이터는 `1=남성`, `2=여성` |
| `EDUCATION` | 교육 수준 코드. `1=대학원`, `2=대학교`, `3=고등학교`, `4=기타` |
| `MARRIAGE` | 혼인 상태 코드. `1=기혼`, `2=미혼`, `3=기타` |
| `AGE` | 나이 |
| `PAY_0`, `PAY_2` ... `PAY_6` | 2005년 9월부터 4월까지 월별 상환 상태. 양수가 클수록 연체 개월 수가 큼 |
| `BILL_AMT1` ... `BILL_AMT6` | 같은 6개월의 월별 청구금액 |
| `PAY_AMT1` ... `PAY_AMT6` | 같은 6개월의 월별 실제 납부금액 |

`PAY_0` 다음이 `PAY_2`인 것은 원 데이터의 이름을 그대로 따랐기 때문입니다. 상환 상태와
범주형 컬럼에는 논문에 설명되지 않은 추가 코드도 일부 있습니다. 이 샘플은 공개
데이터를 임의로 보정하지 않고 숫자 그대로 사용합니다.

이 샘플은 여러 항목을 함께 입력하는 tabular 모델을 단순한 형태로 보여 주기 위해
`SEX`, `EDUCATION`, `MARRIAGE`, `AGE`도 학습에 포함합니다. 다만 실제 신용
의사결정에서는 인구통계 변수를 feature로 사용할 수 있는지 법무·공정성 정책을 먼저
검토하고, 집단별 성능과 차별 가능성을 평가해야 합니다.

범주 코드를 숫자 그대로 사용하는 방식 역시 교육용으로 단순화한 것입니다. 운영 모델에서는
one-hot encoding, XGBoost categorical feature 등의 후보를 비교하고, 학습과 추론에
동일한 변환을 적용해야 합니다.

## 코드가 확인하는 항목

`load_credit_default_data()`는 Training Job을 생성하기 전에 로컬에서 다음 항목을 확인합니다.

1. UCI가 `X1`부터 `X23`까지 정확히 23개의 feature를 반환하는지 확인합니다.
2. 모든 feature를 `float32`로, 레이블을 `int32`로 변환합니다.
3. 결측값과 무한대 값이 없는지 확인합니다.
4. 레이블 값이 정확히 `0`과 `1`뿐인지 확인합니다.

공개 데이터의 스키마가 바뀌거나 문자열이 섞여 있으면 S3 업로드와 Training 비용이 발생하기
전에 실패합니다.

## Train과 validation 분할

`stratified_split()`은 클래스별로 따로 샘플링하여 train과 validation의 연체 비율을
비슷하게 유지합니다. `RANDOM_SEED=42`이므로 원본 데이터가 같으면 항상 같은 분할이
만들어집니다.

| 분할 | 전체 | 비연체 `0` | 연체 `1` |
|---|---:|---:|---:|
| Train | 24,000 | 18,691 | 5,309 |
| Validation | 6,000 | 4,673 | 1,327 |

이 데이터에는 고객별 기준 시점을 구분할 timestamp가 없으므로 80/20 무작위 분할을
사용합니다. 실제 신용위험 모델에서는 보통 학습 기간 이후의 기간을 OOT(out-of-time)
test로 남겨 두고 시간에 따른 성능 저하를 확인합니다. 같은 고객이나 계정의 행이 여러 개라면
고객 단위로도 분리해야 합니다.

## SageMaker Training CSV

SageMaker built-in XGBoost는 CSV의 첫 번째 컬럼을 레이블로 해석하며 헤더는 읽지 않습니다.
따라서 학습 파일은 다음 순서로 만듭니다.

```text
label,LIMIT_BAL,SEX,EDUCATION,MARRIAGE,AGE,PAY_0,...,PAY_AMT6
```

실제 `train.csv`와 `validation.csv`에는 헤더가 없습니다.

```text
0,20000,2,2,1,24,2,...,0
1,120000,2,2,2,26,-1,...,2000
```

두 파일은 서로 다른 SageMaker 입력 채널로 전달됩니다.

```text
train      -> model parameter 학습
validation -> 학습 중 validation:auc 계산
```

S3에는 실행별 prefix 아래에 저장합니다.

```text
s3://<bucket>/sagemaker-xgboost-lab/credit-card-default/runs/<run-id>/data/
```

`data-summary.json`에는 데이터셋 이름과 ID, feature 순서, target의 의미, 분할별 행 수와
클래스 수, random seed를 기록합니다. 같은 파일을 S3와 MLflow 아티팩트에 남겨, 어떤
데이터 구성으로 모델을 만들었는지 확인할 수 있게 합니다.

## Batch Transform 입력

실제 추론 시점에는 정답을 알 수 없으므로 Batch Transform CSV에는 레이블을 넣지 않습니다.
나머지 23개 feature의 순서는 학습 때와 정확히 같아야 합니다.

```text
20000,2,2,1,24,2,...,0
120000,2,2,2,26,-1,...,2000
```

이 샘플은 동작 확인을 위해 validation feature를 추론 입력으로 재사용합니다.
출력의 각 값은 같은 행에 대한 **다음 달 연체 확률 점수**입니다. 이 값을
정확한 PD로 해석하려면 calibration을 별도로 평가해야 합니다. 운영 환경에서는 scoring
시점의 새 고객 데이터를 사용하고, 실제 상환 결과가 확정된 뒤 예측값과 결합해
성능과 calibration을 평가합니다.

## 실제 데이터로 교체할 때

먼저 `load_credit_default_data()`를 실제 S3나 데이터 웨어하우스 입력으로 바꾸고, 다음
항목을 명확히 정합니다.

1. 고객, 기준 시점, 레이블 정의를 문서와 코드에 고정합니다.
2. 미래의 상환 결과가 feature에 섞이는 target leakage를 막습니다.
3. 결측값 처리, 범주형 인코딩, feature 순서를 학습과 추론에서 공유합니다.
4. train, validation, OOT test 기간과 중복 고객 처리 기준을 정합니다.
5. AUC뿐 아니라 임계값별 승인율, 연체율, recall, calibration, 비즈니스 비용을 확인합니다.
6. 민감하거나 대리변수가 될 수 있는 feature의 사용 근거와 집단별 성능을 검토합니다.

데이터 준비에 SQL join, 대규모 변환, 정기 실행이 필요해지면 이 로직을 기존 ETL이나
SageMaker Processing/Pipelines로 옮기는 것을 권장합니다. 샘플 단계에서는 로컬 준비와 S3
아티팩트를 조합하는 방식이 가장 이해하기 쉽고 비용도 적게 듭니다.
