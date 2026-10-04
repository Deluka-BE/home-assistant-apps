"""GPL-2.0-only. Register text routes on the existing dashboard Flask app."""
from flask import request
from werkzeug.exceptions import BadRequest, UnsupportedMediaType
from text_oled import validate_message


def register(app, oled):
    prefix = '/api/v1.0/'

    @app.route(prefix + 'set-oled-text', methods=['POST'])
    def set_oled_text():
        if request.content_length is not None and request.content_length > 4096:
            return {'status': False, 'error': 'Message too large'}, 413
        try:
            # Limit reads even for a request without Content-Length.
            import json
            if not request.is_json:
                raise UnsupportedMediaType()
            raw = request.stream.read(4097)
            if len(raw) > 4096:
                return {'status': False, 'error': 'Message too large'}, 413
            body = json.loads(raw)
            validate_message(body)
            if oled is None:
                return {'status': False, 'error': 'OLED unavailable'}, 503
            return {'status': True, 'data': oled.set_text(body)}
        except (BadRequest, UnsupportedMediaType):
            return {'status': False, 'error': 'Send a JSON object with Content-Type: application/json'}, 400
        except ValueError as exc:
            return {'status': False, 'error': str(exc)}, 400
        except RuntimeError as exc:
            return {'status': False, 'error': str(exc)}, 409

    @app.route(prefix + 'clear-oled-text', methods=['POST'])
    def clear_oled_text():
        if oled is None:
            return {'status': False, 'error': 'OLED unavailable'}, 503
        return {'status': True, 'data': oled.clear_text()}

    @app.route(prefix + 'get-oled-text', methods=['GET'])
    def get_oled_text():
        if oled is None:
            return {'status': False, 'error': 'OLED unavailable'}, 503
        return {'status': True, 'data': oled.text_status()}
