import math


def haversine(lat1, lng1, lat2, lng2):
    """Avstånd i km mellan två koordinater."""
    r = 6371.0
    p = math.pi / 180
    a = (
        0.5
        - math.cos((lat2 - lat1) * p) / 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lng2 - lng1) * p)) / 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def grid(bounds, dlat, dlng):
    """Dela upp en bounding box i ett rutnät av (lat_min, lng_min, lat_max, lng_max)."""
    lat_min, lng_min, lat_max, lng_max = bounds
    boxes = []
    lat = lat_min
    while lat < lat_max:
        lng = lng_min
        while lng < lng_max:
            boxes.append(
                (
                    round(lat, 4),
                    round(lng, 4),
                    round(min(lat + dlat, lat_max), 4),
                    round(min(lng + dlng, lng_max), 4),
                )
            )
            lng += dlng
        lat += dlat
    return boxes
