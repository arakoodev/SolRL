FROM hashicorp/terraform:1.6.6

RUN apk add --no-cache bash curl jq python3 py3-pip

WORKDIR /workspace/infra/localstack

ENTRYPOINT ["/bin/sh", "-lc"]
CMD ["terraform init && terraform validate && terraform test"]
