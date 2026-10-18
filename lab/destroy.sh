#!/usr/bin/env bash
cd "$(dirname "$0")" && containerlab destroy -t elice.clab.yml --cleanup
