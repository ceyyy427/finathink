"""Public wire-failure identities; never include remote exception text."""


class ProviderTransportError(ValueError):
    CODES = frozenset({'NON_JSON', 'STREAM_INTERRUPTED', 'UNSUPPORTED_STREAM', 'RESPONSE_TOO_LARGE', 'TRANSPORT'})

    def __init__(self, code):
        if code not in self.CODES:
            raise ValueError('invalid provider transport failure')
        self.code = code
        super().__init__('provider response limit exceeded' if code == 'RESPONSE_TOO_LARGE' else 'provider ' + code.lower())
