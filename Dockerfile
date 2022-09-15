FROM gcr.io/distroless/base-debian10:nonroot

FROM mambaorg/micromamba:0.25.1

WORKDIR /home/mambauser 
COPY . /home/mambauser

COPY --chown=$MAMBA_USER:$MAMBA_USER ./environment.yml /tmp/env.yaml

RUN micromamba install -y -n base -f /tmp/env.yaml && \
    micromamba clean --all --yes

CMD uvicorn main:app --host=0.0.0.0 --port=$PORT