# Live World Map for Linux

## What is this?

A Docker container-based system that features a number of data acquisition scripts for Clouds, Isobars, Wind,
Storm tracking, Earthquakes, Volcanoes, and Shipping before utilizing `xplanet` to render an image of The World
or part of it, for your desktop. There is also a daemon which will monitor the folder this image is generated
in, and update your desktop wallpaper with what is essentially a live view of what's happening on the planet.

![World Map Example](docs/worldmap-example.jpg)

## How do I use this?

### Clone the repository:

    cd /your/preferred/workspace
    git clone -v https://github.com/paulwaite87/WorldMap

### Prerequisites: Docker Installation

Before running this project, you must have Docker and Docker Compose installed on your system. 
For Ubuntu users, it is highly recommended to install Docker via the official Docker repository 
rather than the default apt archives to ensure you have the latest version compatible with 
modern systemd and container features. You can verify your installation by running 
docker --version in your terminal.

If you need some guidance on this a good place to look is
here https://www.digitalocean.com/community/tutorials/how-to-install-and-use-docker-on-ubuntu-20-04

Despite the '20-04' at the end of the link, this tutorial is also fine for later versions of Ubuntu.

To avoid having to use sudo with every command, ensure your user is added to the docker group. 
After installation, run `sudo usermod -aG docker $USER` and log out and back in for the changes 
to take effect. This will allow you to manage containers and orchestration seamlessly while 
working within the repository.

### Initial Setup
Configuration files live in the `config` folder, so get into that folder.

Copy `worldmap.conf.example` to your own local `worldmap.conf`. This is the file you will want
to tinker with, not `xplanet.conf` (unless you know what you are doing).

This configuration file is in .ini format. Each section controls one of the processes involved 
in producing the map, and each has an `enabled` flag. If that is set to `False` the process 
will be skipped. Out of the box, the system will have the shipping processes skipped, because an 
API Key is needed for that data (easily obtained, see below).

### Building and running
All main actions you will want to perform with the system can be done via `make`. Have a
look in the `Makefile` for the possible targets/actions you can use. There are quite a few
more than the ones mentioned below.

The map machinery runs in Docker containers. To start it all up (this will pull and 
build everything first, if not already built) use the following command from the root
directory of the repo you just cloned.

    make run

To see what it's doing just use:

    make logs

On your first run of the system it will create and initialise a Postgresql/PostGIS database. This
database will be seeded with a few regions, which can be used to restrict where you populate
ships on the map. That can be useful if you have a lot of vessels being displayed. You can add
as many regions as you want.

If this all worked as it should, you will see the logs showing that both the `harvester` and
the `map_builder` are working.

A healthy repeating cycle will look something like this in the logs:
 
    harvester    | 2026-05-03 19:42:02,661 [INFO] worldmap.harvester: Ship-harvester Service: Starting weighted global rotation
    harvester    | 2026-05-03 19:42:02,683 [INFO] worldmap.harvester: Slice 9 [Australia / NZ / Japan / West Pacific]
    harvester    | 2026-05-03 19:42:03,950 [INFO] worldmap.harvester: Harvesting for 360s
    harvester    | 2026-05-03 19:42:03,950 [INFO] worldmap.harvester: Slice 9 [Australia / NZ / Japan / West Pacific]
    map_builder  | 2026-05-03 19:42:07,872 [INFO] worldmap.map_builder: Map-builder scheduler run started
    map_builder  | 2026-05-03 19:42:07,872 [INFO] worldmap.map_builder: Running scheduled task: 'clouds'
    map_builder  | 2026-05-03 19:42:08,627 [INFO] worldmap.map_builder: Running scheduled task: 'isobars'
    map_builder  | 2026-05-03 19:42:19,155 [INFO] worldmap.map_builder: Running scheduled task: 'composite'
    map_builder  | 2026-05-03 19:42:19,321 [INFO] worldmap.map_builder: Running scheduled task: 'quakes'
    map_builder  | 2026-05-03 19:42:20,469 [INFO] worldmap.map_builder: Running scheduled task: 'shipping'
    map_builder  | 2026-05-03 19:42:21,480 [INFO] worldmap.map_builder: Running scheduled task: 'xplanet'
    map_builder  | 2026-05-03 19:42:21,805 [INFO] worldmap.map_builder: Map-builder scheduler run finished
    harvester    | 2026-05-03 19:48:04,479 [INFO] worldmap.harvester: Updated 109 static, 612 positions
    harvester    | 2026-05-03 19:48:05,388 [INFO] worldmap.harvester: Slice 0 [Mid-Pacific (East)]
    harvester    | 2026-05-03 19:48:06,393 [INFO] worldmap.harvester: Harvesting for 150s
    harvester    | 2026-05-03 19:48:06,393 [INFO] worldmap.harvester: Slice 0 [Mid-Pacific (East)]
    harvester    | 2026-05-03 19:50:37,023 [INFO] worldmap.harvester: Updated 6 static, 36 positions
    harvester    | 2026-05-03 19:50:37,836 [INFO] worldmap.harvester: Slice 1 [Eastern Pacific / Americas West]
    harvester    | 2026-05-03 19:50:39,348 [INFO] worldmap.harvester: Harvesting for 210s
    harvester    | 2026-05-03 19:50:39,348 [INFO] worldmap.harvester: Slice 1 [Eastern Pacific / Americas West]
    harvester    | 2026-05-03 19:54:09,540 [INFO] worldmap.harvester: Updated 304 static, 1718 positions
    ....

At this point, in summary, the `harvester` is a process which endlessly listens for messages
coming from vessels around the globe, so it is solely concerned with acquiring the data for
the shipping part of the World map.

The `map_builder` is the process which puts together all the elements (including shipping)
which get displayed on the map. Again, this process is endlessly repeating, so your map
will change through the day as the elements are updated.

### Desktop Geometry and Location
Edit your `config/worldmap.conf` and go down to the `[xplanet]` section. There you 
should set the `geometry` to match your desktop. You can also set `longitude` such that
it centres the map over your location. Of course the latter is optional.

### Obtaining an API Key for Shipping data
The `harvester` needs an API Key to access the AIS stream carrying shipping messages.

To obtain one, head on over to https://aisstream.io/documentation on that page you will see 
a link to `Sign In` (https://aisstream.io/authenticate) which will ask you to sign in to their 
Github. Obviously if you don't have a Github account you will have to sign up for that first.

The process of obtaining the API Key is easy once you are signed in. There is a link `API Keys` 
and you can create one there. Copy the key, and then back in the root directory copy `.env.tmpl` 
to a new file named `.env`. Edit that file and replace the placeholder there with your newly 
acquired API Key. You will now be able to edit `config/worldmap.conf` and set the `enabled` 
flags to True in the `[shipping]` and `[shipping_harvester]` sections.

### Shipping Data Acquisition
Ships broadcast data in the form of messages continuously at regular intervals. The main message 
they emit is a `PositionReport` which contains information as to latitude and longitude, current 
heading and speed. This message is usually fairly frequent. The other message of interest to us 
is the `ShipStaticData` which has details of the ship itself such as name, size, draught, type and 
IMO number (International Maritime Organization number). This message is broadcast much less 
frequently, but the data is extremely useful to identify the type of vessel and its current 
loading state (draught).

The `harvester` is specific to shipping data, and repeatedly listens for the relatively infrequent 
`ShipStaticData` messages and the more frequent `PositionReport` messages.

It will gradually populate your database `ships` table with them. It does this by slicing the 
globe up into 10 segments by longitude, and then listening in each slice defined as a bounding box. 
The listen duration varies according to how busy each slice is expected to be, based on shipping
lanes and the area of ocean it's looking at.

At any given instant either a `ShipStaticData` or `PositionReport` message might come in. If it's
a `PositionReport` the message is fairly specific to position, heading, speed etc. and contains
no details about the ship itself. The `harvester` will look for an existing `ships` record in
our database with the same `mmsi` identifier, and if found add the new position info. It also 
logs the position in the tracking table `ship_position` so we can display vessel tracks. If 
it doesn't find an existing `ships` record it creates a `shadow` record with scant data about 
the ship, basically just the name and the `mmsi` identifier. At some point we would hope to 
back-fill that data when a `ShipStaticData` is acquired for it.

The `map_builder` (see below) is independent of all this and just displays ships in the database 
which happen to be in the region(s) you have specified you want to display (or the whole World 
if you left that list empty).

One useful command for shipping is:

    make status

That will print out some status info about ships in each region, and ship totals.

### The Map Builder
Apart from shipping there are, of course, other elements to the map display. These are:

* Clouds
* Isobars
* Active storms
* Earthquakes
* Volcanoes

Each of these has its own section in the `worldmap.conf` file. Hopefully the settings in
each section are fairly self-explanatory. The one which is common to all is of course
the `enabled` flag which will turn the display of each on or off.

These elements are also updated according to a frequency determined by a `runs_per_day` 
setting in the applicable section of `worldmap.conf`. This is to restrict load on the
source servers, which only update their data every few hours usually anyway.

You can, however, force the system to refresh the map using the following:

    make force-map-refresh

### Some further notes

Volcanoes are pretty much static day-to-day and can end up just cluttering up the map, 
so I generally don't display them) you can disable them by setting `enabled = False`.

Storms will drop off the map when the `expiry_days` (see worldmap.conf [storm_markers] 
section) is exceeded.

With shipping icons there are basically two variants Cargo (has a 'C' in the middle) and 
Tankers ('T' in the middle). They each have their own default colours, but these can also 
vary if the system detects their draught (loading) has decreased.

Tip: If you have `filter_only_active_ships` set to True, shipping with speeds less than 
1.0 knots, or flagged as anchored or moored are NOT displayed. This avoids masses of ship
icons overlaying each other in port locations making a mess on the map.

There are also other filters in that section, so play around until you get the level of
detail you want.

### Wallpaper updates

The whole idea of this is to have a live desktop background. To update your wallpaper 
(fingers crossed!) execute the following command:

    make start-desktop

This kicks off a script which runs in the background, so to stop it:

    make stop-desktop

If you want to run it in foreground just run `./wallpaper-updater.sh`.

Also have a look at `wallpaper-update-daemon.py` for details. It works for my distro, but
since I can't test yours, it might not. Feel free to update the code and give the
repo a pull request!
