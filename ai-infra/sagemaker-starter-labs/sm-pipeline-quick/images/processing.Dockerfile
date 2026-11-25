FROM python:3.11-slim

RUN pip install --no-cache-dir \
    awswrangler==3.17.1 \
    boto3==1.43.63 \
    numpy==2.4.6 \
    pandas==2.3.3 \
    pyarrow==23.0.1 \
    scikit-learn==1.9.0 \
    xgboost==3.0.5

ENTRYPOINT ["python3"]
