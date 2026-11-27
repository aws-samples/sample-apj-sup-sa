FROM python:3.11-slim

RUN pip install --no-cache-dir \
    numpy==2.4.6 \
    pandas==2.3.3 \
    scikit-learn==1.9.0 \
    xgboost-cpu==3.0.5

ENTRYPOINT ["python3"]
