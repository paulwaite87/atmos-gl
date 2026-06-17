#!/usr/bin/env python3
import re
import urllib.request


def list_nasa_layers():
    # The 'GetCapabilities' URL for the EPSG:4326 (WGS84) endpoint
    url = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?SERVICE=WMS&REQUEST=GetCapabilities"

    print("Connecting to NASA GIBS to fetch current layer list...")
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            content = response.read().decode("utf-8")

            # Use regex to find all <Name> tags within <Layer> blocks
            # We specifically look for things containing 'Pressure' or 'MSLP'
            layers = re.findall(
                r"<Layer[^>]*>.*?<Name>(.*?)</Name>", content, re.DOTALL
            )

            pressure_layers = [
                layer for layer in layers if "Pressure" in layer or "MSLP" in layer
            ]

            if not pressure_layers:
                print("No pressure layers found. Here are the first 10 general layers:")
                for layer in layers[:10]:
                    print(f" - {layer}")
            else:
                print(f"\nFound {len(pressure_layers)} Pressure-related layers:")
                for layer in pressure_layers:
                    print(f" -> {layer}")

    except Exception as e:
        print(f"Failed to query NASA: {e}")


if __name__ == "__main__":
    list_nasa_layers()
