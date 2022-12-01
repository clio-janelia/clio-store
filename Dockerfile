FROM python:3.9-slim
ENV APP_HOME /
WORKDIR $APP_HOME
COPY . ./
RUN pip install -r requirements.txt
CMD exec uvicorn main:app --host=0.0.0.0 --port=$PORT