class RoutingError(Exception):
    status = 502

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class UpstreamUnavailable(RoutingError):
    status = 502


class UpstreamTimeout(RoutingError):
    status = 504


class GeocodingFailed(RoutingError):
    status = 400


class NoRouteFound(RoutingError):
    status = 422
