#!/usr/bin/env bash
set -e
sudo systemctl stop kubelet
sudo systemctl stop containerd
echo "Monitoring infrastructure stopped"

