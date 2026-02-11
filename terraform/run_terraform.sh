#!/bin/sh

run_terraform_stack() {
    set -x
    set -e
    echo "Running Stack: $1"
    echo "USING BUCKET $TF_STATE_BUCKET"
    dir=$( dirname -- "$1")
    var_file=$(basename "$1")

    # shellcheck disable=SC2164
    cd "$dir"
    terraform init -migrate-state \
      -backend-config=bucket="$TF_STATE_BUCKET" \
      -var-file "$var_file"

    terraform plan -input=false -var-file "$var_file" -out=tfplan

    if [ "$APPLY" = "Y" ]; then
        terraform apply -auto-approve tfplan
    else
      echo "SKIPPING APPLY.."
    fi

    rm -rf tfplan

    echo "Finished stack: $var_file"
}

export TARGET=${1:-dev}
export TF_STATE_BUCKET="streamlit-chatbot-ui"
export APPLY=${2:-N}

if test -z "${COMMIT_SHA}"; then
  export COMMIT_SHA=$(git rev-parse HEAD)
fi

export TF_VAR_image_tag=${COMMIT_SHA}

run_terraform_stack "${TARGET}.tfvars"
