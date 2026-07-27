#!/usr/bin/env bash
set -e
sudo systemctl start containerd
sudo systemctl start kubelet
echo "Monitoring infrastructure started"
