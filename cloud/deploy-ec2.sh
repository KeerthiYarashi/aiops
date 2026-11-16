#!/usr/bin/env bash
set -euo pipefail

cd /opt/verify-ops
if [[ $(uname -m) != x86_64 ]]; then
  echo "This deployment script expects the x86_64 Amazon Linux 2023 instance."
  exit 1
fi

sudo dnf install -y docker amazon-cloudwatch-agent python3.12
sudo systemctl enable --now docker
sudo install -d /usr/local/lib/docker/cli-plugins
if ! sudo docker compose version >/dev/null 2>&1; then
  sudo curl -fLS --retry 3 \
    https://github.com/docker/compose/releases/download/v5.6.0/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  sudo chmod 755 /usr/local/lib/docker/cli-plugins/docker-compose
fi
buildx_version=$(sudo docker buildx version 2>/dev/null | awk '{print $2}' || true)
buildx_version=${buildx_version#v}
buildx_version=${buildx_version%%-*}
if [[ ! $buildx_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
   [[ $(printf '%s\n' 0.17.0 "$buildx_version" | sort -V | head -n 1) != 0.17.0 ]]; then
  sudo curl -fLS --retry 3 \
    https://github.com/docker/buildx/releases/download/v0.39.0/buildx-v0.39.0.linux-amd64 \
    -o /usr/local/lib/docker/cli-plugins/docker-buildx
  sudo chmod 755 /usr/local/lib/docker/cli-plugins/docker-buildx
fi
sudo docker buildx version

mkdir -p logs evaluation
touch logs/app.jsonl
sudo docker compose up -d --build api prometheus
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 -s -c file:/opt/verify-ops/cloud/cloudwatch-agent.json

python3.12 -B -c 'from chaos.run_experiments import wait_for_ready; wait_for_ready()'
python3.12 -B -m chaos.run_catalog_demo
sudo docker compose ps
echo "EC2 deployment and catalog smoke test passed."
