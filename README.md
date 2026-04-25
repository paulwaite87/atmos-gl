# Live World Map for Linux

## What is this?

A Docker container-based system that features a number of data acquisition scripts for Clouds, Isobars, Storm tracking,
Earthquakes, Volcanoes, and Shipping before utilizing `xplanet` to render the image for your desktop. There is also a
daemon which will monitor the folder this image is generated in, and update your desktop wallpaper with what is
essentially a live view of what's happening on the planet.

![World Map Example](docs/worldmap-example.jpg)

## How do I use this?

### Clone the repository:

    cd /your/preferred/workspace
    git clone -v https://github.com/paulwaite87/WorldMap

### Prerequisites: Docker Installation

Before running this project, you must have Docker and Docker Compose installed on your system. For Ubuntu users, it is
highly recommended to install Docker via the official Docker repository rather than the default apt archives to ensure
you have the latest version compatible with modern systemd and container features. You can verify your installation by
running docker --version in your terminal.

If you need some guidance on this a good place to look is
here https://www.digitalocean.com/community/tutorials/how-to-install-and-use-docker-on-ubuntu-20-04

Despite the '20-04' at the end of the link, this tutorial is also fine for later versions of Ubuntu.

To avoid having to use sudo with every command, ensure your user is added to the docker group. After installation, run
`sudo usermod -aG docker $USER` and log out and back in for the changes to take effect. This will allow you to manage
containers and orchestration seamlessly while working within the repository.

### Configuration
Enable your configuration, and an initial ship static data cache as described here.

Configuration files live in the `config` folder. You can change the way the system behaves with those.

The first thing to do is copy `worldmap.conf.example` to your own local `worldmap.conf`. This is the file you will want to tinker with, not `xplanet.conf` (unless you know what you are doing).

This configuration file is in .ini format. Each section controls one of the processes involved in producing the map, and each has an `enabled` flag. If that is set to `False` the process will be skipped. Out of the box, the system will have the shipping processes skipped, because an API Key is needed for that data (easily obtained, see below).

The second thing to do is copy `data/ship_cache.json.example` to `data/ship_cache.json`. This will give you a head start in cacheing ship static data as it has around 18,000 ships logged.

Here are a few notes about a couple of these sections.

#### Section [xplanet]
First of all edit `config/worldmap.conf` and go down to the `[xplanet]` section. There you should set the `geometry` to match your desktop, and `longitude` such that it centres the map over your location. Of course the latter is optional.

#### Section [daemon]
At the top you will see a section named `daemon`. The daemon which does all the work is `daemon.py` and it reads this section to figure out how to operate.

There are two processes that the daemon executes, the first being the map builder (`map_bilder.py`) which generates a new World map image. This runs from the time specified as `morning` in the config file until the time specified in `evening`.

The second process is a ship data harvester (`tasks/harvester.py`). The harvester runs from evening through to morning if it is enabled. What this does is listen for ship broadcasting their `ShipStaticData` message. That message tells us some basics about the ship like name, dimensions, type, draught etc. and we store the retrieved information in `data/ship_cache.json`. That file is read by the map updater so it can fill in the details of any ships it discovers in the areas we are looking in (see the bbox setting in the [shipping_markers] section of the config file).

There are already around 18,000 ships recorded in that cache file, but there are many more at sea, hence the option to scan the globe for these repeatedly. The static data messages are only broadcast infrequently so we just keep on until we grab most if not all of them.

The two sleep settings are the interval in seconds to sleep after each process has run.

### Obtaining an API Key for Shipping data
Ships broadcast data in the form of messages continuously at regular intervals. The main message they emit is a PositionReport which contains information as to latitude and longitude, current heading and speed. This message is usually fairly frequent. The other message of interest to us is the ShipStaticData which has details of the ship itself such as name, size, draught, type and IMO number (International Maritime Organization number). This message is broadcast much less frequently, but the data is extremely useful to identify the type of vessel and its current loading state (draught).

To obtain the API Key for accessing the data streams carrying these messages, head on over to https://aisstream.io/documentation on that page you will see a link to `Sign In` (https://aisstream.io/authenticate) which will ask you to sign in to their Github. Obviously if you don't have a Github account you will have to sign up for that first.

The process of obtaining the API Key is easy once you are signed in. There is a link `API Keys` and you can create one there. Copy the key, and then back in the root directory copy `.env.tmpl` to a new file named `.env`. Edit that file and replace the placeholder there with your newly acquired API Key. You will now be able to edit `config/worldmap.conf` and set the `enabled` flags to True in the `[shipping]` and `[shipping_harvester]` sections.

### Running the updater
The update machinery all runs in the Docker container. To start it all up (this will pull and build everything first, if not already built) use the following command from the root directory of the repo you just cloned.

    docker compose up -d

To see what it's doing just something like:

    docker logs -f worldmap

If this all works as it should, you will see the logs showing it is generating what's needed. A healthy cycle will look something like this in the logs:
 
    Beginning map refresh
    create_map_logger: INFO: data/cloud_map.jpg is new enough
    create_map_logger: INFO: finished in 0.2 s
    Latest GFS run: 20260421 18Z
    Reading GRIB2 file...
    Isobar map saved: ./data/global_isobars.png
    Markers generated for 1 storms (Lats: 8.0 to 29.6)
    https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.csv
    Updated 7 quakes (Mag >= 5.0) in /opt/project/data/quake_markers.txt
    Loaded 15392 ships. Filtering for: ['Tanker', 'Cargo']
    Success! Processed 65 markers.

The resulting World map will be placed in the `data` folder. It will be prefixed with a Unix timestamp (after the previous image is removed). The different image names each time help certain desktops refresh as they should when you update the wallpaper.

Note that the map update will itself indulge in a bit of Ship Static Data harvesting. It does that after listening for ship position status data, but not for very long. The static data is broadcast much less frequently than position data,so it isn't worth hanging around very long when updating the map. The harvester does a much better job of that.

### Some further notes

If you don't want a given section displayed (eg volcanoes are pretty much static day-to-day and just clutter the map, so I generally don't display them) you can disable them by setting `enabled = False` in the `[volcanoes]` section of `config/worldmap.conf`.

Storms will drop off the map when the `expiry_days` (see worldmap.conf [storm_markers] section) is exceeded.

With shipping icons there are basically two variants Cargo (has a 'C' in the middle) and Tankers ('T' in the middle).
They each have their own default colours, but these can also vary if the system detects their draught (loading) has
decreased.

Tip: If you have `show_only_active_ships` set to True, shipping with speeds less than 1.0 knots, or flagged as anchored or moored are NOT displayed. This avoids masses of ship
icons overlaying each other in port locations making a mess on the map.

You can have a look at the symbols used for these and other markers on the map by viewing the images in the `symbols` folder.

### Running the harvester

As mentioned above this is done by the same daemon. You just have to set `enabled` True in the `[shipping_harvester]` section (once you have an API Key).

Set the time you want the harvester to take over from the map updater. The two processes are mutually exclusive, so I have provided `morning` and `evening` times for you.

The map updater runs between morning and evening. The harvester, if enabled, runs overnight until morning. If it isn't enabled, nothing happens overnight.

### Wallpaper updates

There is a daemon to run on your local host which should hopefully update your background/wallpaper image. You run it by running `wallpaper_updater.sh` from the command line. This sets up a Virtual Env and runs the updater in a loop until you Ctrl-C out of it.

This daemon is, in theory, able to update for a number of desktops including Gnome, KDE, Linux Mint Cinammon and Mate, and XFCE. However these things being as they are, I expect there will be issues with YOUR particular desktop, so I am open to improvements!
