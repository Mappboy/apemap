import math


def create_bounding_box(center, zoom):
    lat, lon = center["lat"], center["lon"]
    lat_delta = 360 / (2**zoom) * 0.5
    lon_delta = 360 / (2**zoom) * 0.5 / math.cos(math.radians(lat))
    sw = (lat - lat_delta, lon - lon_delta)
    ne = (lat + lat_delta, lon + lon_delta)
    bbox = [
        [sw[0], sw[1]],
        [sw[0], ne[1]],
        [ne[0], ne[1]],
        [ne[0], sw[1]],
        [sw[0], sw[1]],
    ]
    return bbox
