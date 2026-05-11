import sys
import json
import logging
from worldmap.lib.maps import NASAGIBSDownloader
from worldmap.lib.db import Database

# Setup basic logging to see the downloader progress
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Worldmap Regional Map Test Harness")

    # Input options: either a label or a raw bbox
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--label", help="Region label from the map_region table (e.g. 'NZ_Aus')")
    group.add_argument("--bbox", help="Raw JSON BBox: '[min_lon, min_lat, max_lon, max_lat]'")

    parser.add_argument("--night", action="store_true", help="Download the Black Marble (night) layer")
    parser.add_argument("--out", default="test_output.jpg", help="Output filename")

    args = parser.parse_args()
    downloader = NASAGIBSDownloader()

    bbox = None

    if args.label:
        logger.info(f"Resolving coordinates for label: {args.label}")
        db = Database()
        bbox = db.get_region_definition(args.label)
        if not bbox:
            logger.error(f"Could not find region '{args.label}' in database.")
            sys.exit(1)
    else:
        try:
            bbox = json.loads(args.bbox)
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            logger.error("Invalid BBox format. Expected: '[min_lon, min_lat, max_lon, max_lat]'")
            sys.exit(1)

    logger.info(f"Target BBox: {bbox}")
    success = downloader.download_region_map(bbox, args.out, is_night=args.night)

    if success:
        logger.info(f"Success! Map saved to {args.out}")
        # On Ubuntu, you can automatically open the image for verification
        # os.system(f"xdg-open {args.out}")
    else:
        logger.error("Download failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
