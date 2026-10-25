"""Pinned candidate identity and explicit native label normalization."""
CANDIDATES = {
    'mobilenet': ('dronefreak/bdd100k-weather-mobilenetv4_conv_small',
                  '51a02f796c4a1ac07ec7e35a40a619af83c90854'),
    'prithiv': ('prithivMLmods/Weather-Image-Classification',
                '07db1be6dbd384c67e36400b3fd0acb0988bd50c'),
}
LABELS = {
    'clear': 'sunny', 'partly cloudy': 'partly_cloudy', 'overcast': 'cloudy',
    'rainy': 'rain', 'snowy': 'snow', 'foggy': 'fog', 'unknown': 'unknown',
    'cloudy/overcast': 'cloudy', 'foggy/hazy': 'fog', 'rain/strom': 'rain',
    'rain/storm': 'rain', 'snow/frosty': 'snow', 'sun/clear': 'sunny',
}


def normalize(probabilities, threshold):
    import math
    if not probabilities or any(not math.isfinite(p) or not 0 <= p <= 1
                                for p in probabilities.values()):
        raise ValueError('Invalid model probabilities')
    if abs(sum(probabilities.values()) - 1) > 0.01:
        raise ValueError('Invalid probability sum')
    native = max(probabilities, key=probabilities.get)
    confidence = probabilities[native]
    mapped = LABELS.get(native.lower().strip(), 'unknown')
    return {'native_label': native, 'mapped_state': mapped,
            'normalized_state': mapped if confidence >= threshold else 'unknown',
            'confidence': confidence, 'probabilities': probabilities}
