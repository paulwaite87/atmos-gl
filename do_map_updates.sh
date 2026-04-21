#!/bin/bash
set -e

# Set up common vars
source ./common.sh

echo "Beginning map refresh"

# Update cloud map
# The 'create_map' executable in .venv/bin comes from the package CreateCloudMap
${PYTHON3} ${SCRIPTS}/create_map --conf_file ${CONFIG}/cloudmap.conf

# Grab updated isobars image
# See [isobars] section in update_map.ini
${PYTHON3} ${SCRIPTS}/update_isobars --config=${WORLDMAP_CONFIG_FILE}

# Overlay isobars onto downloaded clouds map
composite ${DATA}/global_isobars.png ${DATA}/cloud_map.jpg ${DATA}/cloud_map_with_isobars.jpg

# Grab active storm systems
# See [storm_markers] section in update_map.ini
${PYTHON3} ${SCRIPTS}/update_storm_markers --config=${WORLDMAP_CONFIG_FILE}

# Grab recent earthquakes
# See [quake_markers] section in update_map.ini
${PYTHON3} ${SCRIPTS}/update_quake_markers --config=${WORLDMAP_CONFIG_FILE}

# Grab shipping
# See [shipping_markers] section in update_map.ini
${PYTHON3} ${SCRIPTS}/update_shipping --config=${WORLDMAP_CONFIG_FILE}

# Grab known volcanoes
# These just get in the way; disabled until an eruption occurs
# See [volcano_markers] section in update_map.ini
#${PYTHON3} ${SCRIPTS}/update_volcano_markers

# Render the world map centered approximately on New Zealand
# See the config/xplanet.conf file for what gets rendered
echo "Rendering World map ${WORLDMAP}"
rm -f ${DATA}/${WORLDMAP}

unixtime=$(date +%s)
rm -f ${DATA}/*${WORLDMAP}
exec xplanet \
  -conf ${CONFIG}/xplanet.conf \
	-projection rectangular -geometry ${WORLDMAP_GEOMETRY} \
	-longitude 175 \
	-output ${DATA}/${unixtime}-${WORLDMAP} --num_times 1

echo "Finished"
