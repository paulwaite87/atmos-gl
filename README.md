# Live World Map for Linux

## What is this?

A Docker container-based system that features a number of data acquisition scripts for Clouds, Isobars, Wind,
Rain, Lightning Strikes, Storm tracking, Earthquakes, Volcanoes, Sea Surface Temperature (SST), Ocean Currents
and Shipping before utilizing `xplanet` to render it all as an image of The World or part of it, for your desktop.

There is also a daemon which will monitor the folder this image is generated in, and update your desktop wallpaper
with what is essentially a live view of what's happening on the planet.

### Global example
![World Map Example](docs/worldmap-example.jpg)

### Regional example
![Regional Map Example](docs/worldmap-region-example.jpg)

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
to tinker with to filter what gets displayed and also how it gets displayed. Don't worry, there
is a handy configuration tool for that which is described below.

For those who like to hand-edit file this configuration file is in .ini format. Each section
controls one of the processes involved in producing the map, and each has an `enabled` flag.
If that is set to `False` the process will be skipped. Out of the box, the system will have 
the shipping processes skipped, because an API Key is needed for that data (easily obtained, see below). 
The same applies to the weather scanner process.

Now that you have your `worldmap.conf` in place, let's get it all up and running.

### Building and running
All main actions you will want to perform with the system from the command line can be
done via `make`. Have a look in the `Makefile` for the possible targets/actions you can use.
There are quite a few. To list all possible make targets:

    make help

The map machinery runs in Docker containers. To start it all up (this will pull and 
build everything first, if not already built) use the following command from the root
directory of the repo you just cloned.

    make run

That will run everything in the background. To see what it's doing just use:

    make logs

On your first run of the system it will create and initialise a Postgresql/PostGIS database. This
If this all worked as it should you will see the logs showing the `map_builder` is working. 

A healthy repeating cycle might look something like this. Obviously the below example
shows shipping and weather scanner output, which you won't see out of the box unless you
already acquired API keys and enabled them.

    shipping_collector  | 2026-05-15 15:47:11,624 [INFO] worldmap.shipping_collector: Shipping Collector Service: Starting weighted global rotation
    weather_scanner     | 2026-05-15 15:47:11,931 [INFO] worldmap.weather_scanner: Weather Scanner Service: Starting regional scans.
    map_builder         | 2026-05-15 15:53:23,623 [INFO] worldmap.map_builder: Map-builder scheduler run started
    map_builder         | 2026-05-15 15:53:23,623 [INFO] worldmap.map_builder: Running scheduled task: 'clouds'
    map_builder         | 2026-05-15 15:53:23,808 [INFO] worldmap.map_builder: Running scheduled task: 'isobars'
    map_builder         | 2026-05-15 15:53:24,427 [INFO] worldmap.map_builder: Running scheduled task: 'precipitation'
    map_builder         | 2026-05-15 15:53:24,543 [INFO] worldmap.map_builder: Running scheduled task: 'currents'
    map_builder         | 2026-05-15 15:53:25,050 [INFO] worldmap.map_builder: Running scheduled task: 'composite'
    map_builder         | 2026-05-15 15:53:28,969 [INFO] worldmap.map_builder: Running scheduled task: 'storms'
    map_builder         | 2026-05-15 15:53:30,686 [INFO] worldmap.tasks.storms: Storm CSV cache is up to date.
    map_builder         | 2026-05-15 15:53:30,689 [INFO] worldmap.tasks.storms: Storm markers are up to date. Skipping.
    map_builder         | 2026-05-15 15:53:30,691 [INFO] worldmap.map_builder: Running scheduled task: 'lightning'
    map_builder         | 2026-05-15 15:53:30,794 [INFO] worldmap.tasks.lightning: Placed 27 strikes
    map_builder         | 2026-05-15 15:53:30,795 [INFO] worldmap.map_builder: Running scheduled task: 'quakes'
    map_builder         | 2026-05-15 15:53:31,696 [INFO] worldmap.map_builder: Running scheduled task: 'shipping'
    map_builder         | 2026-05-15 15:53:32,794 [INFO] worldmap.tasks.shipping: Shipping update complete. Placed 18463 ships in region.
    map_builder         | 2026-05-15 15:53:32,829 [INFO] worldmap.map_builder: Running scheduled task: 'xplanet'
    map_builder         | 2026-05-15 15:53:33,566 [INFO] worldmap.tasks.renderer: Successfully generated map: ./data/1778817212-regionmap.jpg
    map_builder         | 2026-05-15 15:53:33,566 [INFO] worldmap.map_builder: Map-builder scheduler run finished

The `map_builder` is the main process which puts together all the elements which get displayed 
on the map. Again, this process is endlessly repeating, so your map will change through the 
day as the elements are updated.

### Configuration UI
There is a handy configuration tool available so you don't have to manually edit the
configuration file `config/worldmap.conf`.

After firing the system up as decribed above, go to the following in your web browser:
http://localhost:8180/

You should see this screen, and be able to change system behaviour there easily.

![Configuration UI](docs/worldmap-configuration.png)

### Desktop Geometry and Location
In the above screen, edit your desktop geometry frst of all. Or if you like the
manual editing approach, edit your `config/worldmap.conf` and look in the first
`[common]` section. There you should set the `desktop_geometry` accordingly.

The other geometry setting is `target_geometry` which controls the resolution
of what we download. I find that 4096x2048 is a good value.

The `Region` setting controls what part of the World the map is displaying. The
list of regions is in the database and can be modified by you (see below).

The `Show` tab controls what gets shown on the map. If elements are disabled here,
then the applicable sections on the other tabs are also disabled and the settings on
them hidden, to avoid clutter.

### Regions
The database will be seeded with a few regions, which can be used to zoom in on where 
you want to populate elements on the map. You can add as many regions as you want. See 
the `config/database/001_create_dbs.sql` for existing INSERT statements to copy.

For the coords, go to https://tools.mofei.life/bbox#1/0/0 and navigate to wherever is 
centre of the region you want on the World map there. Zoom in and then pull a bounding-box 
with SHIFT-drag. In the WGS84 box `Copy` the bounding box coords and paste those (minus
the square brackets) into your INSERT. The co-ordinate ordering is already correct. Give 
your INSERT a new appropriate label, then copy that SQL statement onto your clipboard 
and execute this command:

    make psql

That will get you into the WorldMap database PSQL shell. Paste your INSERT into that and
hit enter. Bingo, a brand new region. The WorldMap configurator should read your new region
and allow you to select it. The system will pull a dedicated region map at your specified
target geometry so there is no degradation of resolution when you display a small region
of the World. Perfect day/night maps care of NASA Blue Marble every time!

### Obtaining an API Key for Shipping data
The `shipping_collector` needs an API Key to access the AIS stream carrying shipping messages.

To obtain one, head on over to https://aisstream.io/documentation on that page you will see 
a link to `Sign In` (https://aisstream.io/authenticate) which will ask you to sign in to their 
Github. Obviously if you don't have a Github account you will have to sign up for that first.

The process of obtaining the API Key is easy once you are signed in. There is a link `API Keys` 
and you can create one there. Copy the key, and then back in the root directory copy `.env.tmpl` 
to a new file named `.env`. Edit that file and replace the `AIS_API_KEY` placeholder there
with your newly minted API Key. You will now be able to edit `config/worldmap.conf` and set
the `enabled` flags to True in the `[shipping]` and `[shipping_collector]` sections.

### Obtaining an API Key for Weather/Lightning Strikes
This is for the `weather_scanner` and it's a similar deal, but also easy. You just need to
create an account on https://openweathermap.org and the link to acquire an API Key is right
there on the homepage. Just be aware it will take some hours before the key is made active.

In your `.env` file do as above and put the key in for the `OPENWEATHER_API_KEY` setting.

Once the `weather_scanner` process is enabled and running, you will find that the table
in the database called `lightning_strikes` will acquire data, though it also gets culled
every few hours (`expiry_hours` setting in that section) so won't get too populated.

A `make status` command will show the number of strikes in each region.

### Shipping Data Acquisition
Ships broadcast data in the form of messages continuously at regular intervals. The main message 
they emit is a `PositionReport` which contains information as to latitude and longitude, current 
heading and speed. This message is usually fairly frequent. The other message of interest to us 
is the `ShipStaticData` which has details of the ship itself such as name, size, draught, type and 
IMO number (International Maritime Organization number). This message is broadcast much less 
frequently, but the data is extremely useful to identify the type of vessel and its current 
loading state (draught).

The `shipping_collector` listens for both types of message and will gradually populate your
database `ships` table with them. It does this by slicing the globe up into 10 segments by
longitude, and then listening in each slice defined as a bounding box. The listen duration
varies according to how busy each slice is expected to be, based on shipping lanes and the
area of ocean it's looking at.

At any given instant either a `ShipStaticData` or `PositionReport` message might come in. If it's
a `PositionReport` the message is fairly specific to position, heading, speed etc. and contains
no details about the ship itself. The `shipping_collector` will look for an existing `ships`
record in our database with the same `mmsi` identifier, and if found add the new position info.
It also logs the position in the tracking table `ship_position` so we can display vessel tracks.
If it doesn't find an existing `ships` record it creates a `shadow` record with scant data about
the ship, basically just the name and the `mmsi` identifier. At some point we would hope to 
back-fill that data when a `ShipStaticData` is acquired for it.

The `map_builder` (see below) is independent of all this and just displays ships in the database 
which happen to be in the region(s) you have specified you want to display (or the whole World 
if you left that list empty).

One useful command for shipping is:

    make status

That will print out some status info about ships in each region, ship totals and also lightning
strikes per region.

### The Map Builder
Apart from shipping there are, of course, other elements to the map display. 
The full list is:

* Clouds
* Isobars
* Rainfall
* Wind speed & direction
* Sea surface temperature (absolute or anomaly)
* Ocean currents
* Wave height & direction
* Air temperature (absolute or anomaly)
* Lightning strikes
* Active storms
* Earthquakes
* Volcanoes
* Shipping

Each of these has its own section in the `worldmap.conf` file and in the above UI.
Hopefully the settings in each section are fairly self-explanatory. The one which
is common to all is of course the `enabled` flag which will turn the display of
each on or off.

In the web UI, the `Show` tab controls what gets shown on the map. If something is
disabled, then the following tabs will have that section hidden, to avoid cluttering
the interface.

These elements are also updated according to a frequency determined by a `runs_per_day` 
setting. This is to restrict load on the remote servers, which only update their
data every few hours at most anyway.

You can, however, force the system to refresh the map using the following:

    make force-map-refresh

Though it should be noted that this will not recessarily result in data being refreshed
from the upstream source. Where possible the system will do a HEAD request to find out
if the remote data is newer than what we already have locally. If it isn't then we
will just refresh the map using the locally cached data.

If you really want a fresh start, then `sudo rm data/*` should do the trick! And if,
for some reason you want to refresh the regional maps then `sudo rm data/regions/*`.

### Some further notes
Volcanoes are pretty much static day-to-day and can end up just cluttering up the map, 
so I generally don't display them) you can disable them by setting `enabled = False`.

Storms will drop off the map when the `expiry_days` (see worldmap.conf [storm_markers] 
section) is exceeded.

If you select `Disc` ship icons there are basically two variants: Cargo (has a 'C' in
the middle) and Tankers ('T' in the middle). They each have their own default colours,
but these can also vary if the system detects their draught (loading) has decreased.

If you select `Arrows` for the ship icons then there is a colour code: red for tankers,
green for cargo, violet for passenger/other. Also the arrows will point in the direction
that the vessel is heading currently.

Tip: If you have `filter_ships_underway` set to True, shipping with speeds less than 
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
