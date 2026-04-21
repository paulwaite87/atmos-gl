#!/bin/bash
set -e

# Get the home/root application directory
HOME="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CONFIG=${HOME}/config
WORLDMAP_CONFIG_FILE=${CONFIG}/worldmap.conf
DATA=${HOME}/data
SCRIPTS=${HOME}/scripts
VENV=${HOME}/.venv
PYTHON3=${VENV}/bin/python3
WORLDMAP_GEOMETRY="1920x1200"
WORLDMAP="worldmap.jpg"

export HOME CONFIG WORLDMAP_CONFIG_FILE DATA SCRIPTS VENV PYTHON3 WORLDMAP_GEOMETRY WORLDMAP
